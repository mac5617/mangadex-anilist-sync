"""Writing and verifying approved items (architecture §9, story 16).

This module and `sync/add_entry.py` are the only places that may contain a mutation.
Safety rules enforced here:
- updates target an existing list entry by `id` and send only `progress` and, for approved completions,
  the literal status `COMPLETED`; a status is never changed to anything else;
- adds (approved `add` rows) create an entry by `mediaId` with the literal status `CURRENT` or `COMPLETED`,
  and are dropped if the re-read shows the series is already on the list (that would be an update in disguise);
- AniList is re-read right before writing, and anything that would lower progress is dropped;
- each batch's results are committed before the next batch is sent, so a resume never re-sends `done` items.
SaveMediaListEntry with absolute values is idempotent, so the client's retry of a 5xx/network failure is safe.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mdal.clients.anilist import AniListClient, AniListComplexityError, AniListGraphQLError
from mdal.db.repo import Repo, now_iso
from mdal.fetch.anilist_list import fetch_list

log = logging.getLogger(__name__)

SELECTION = "{ id mediaId progress status }"


@dataclass(frozen=True)
class WriteOp:
    md_id: str
    entry_id: int | None     # None = add a new entry for media_id
    progress: int | None     # None = status-only
    complete: bool           # send status: COMPLETED
    media_id: int | None = None


def is_add(item: Any) -> bool:
    return item["al_entry_id"] is None and item["al_media_id"] is not None


def is_status_only(item: Any) -> bool:
    return item["md_progress"] is not None and item["al_progress"] is not None and item["md_progress"] <= item["al_progress"]


def sends_completion(item: Any) -> bool:
    return item["set_status"] == "COMPLETED" and bool(item["status_approved"])


def op_for(item: Any) -> WriteOp:
    if is_add(item):
        return WriteOp(item["md_id"], None, item["md_progress"], sends_completion(item), media_id=item["al_media_id"])
    return WriteOp(
        md_id=item["md_id"],
        entry_id=item["al_entry_id"],
        progress=None if is_status_only(item) else item["md_progress"],
        complete=sends_completion(item),
    )


def recheck(item: Any, adding: bool, entry: Any, site: str = "AniList") -> dict[str, Any]:
    """Pre-write re-read for one pending item (shared by both writers). Returns the item fields to update.

    `entry` is the series' list entry as just re-read (None = not on the list). For an add, any entry
    means the series was listed meanwhile: drop it, since saving would update that entry instead.
    Anything that would lower progress, or a status-only change that no longer applies, is dropped.
    """
    if adding:
        if entry is None:
            return {}
        return {"write_state": "dropped", "verify_note":
                f"dropped: already on your {site} list now ({entry['status']}, progress {entry['progress']}); not added"}
    if entry is None:
        return {"write_state": "dropped", "verify_note": f"dropped: the entry is no longer on your {site} list"}
    now, status = entry["progress"], entry["status"]
    fields: dict[str, Any] = {"al_status_before": status, "al_progress": now}
    completing = sends_completion(item) and status != "COMPLETED"
    if status == "COMPLETED" and sends_completion(item):
        fields["status_approved"] = 0  # already completed: nothing to change
    if is_status_only(item):
        if status == "COMPLETED":
            fields.update(write_state="dropped", verify_note=f"dropped: already COMPLETED on {site}")
        elif now != item["md_progress"]:
            fields.update(write_state="dropped", verify_note=f"dropped: {site} progress moved to {now}")
    elif now >= item["md_progress"]:
        if completing and now == item["md_progress"]:
            fields["verify_note"] = f"{site} already at {now}; only marking completed"
        else:
            fields.update(write_state="dropped", verify_note=f"dropped: {site} is now at {now} (≥ {item['md_progress']})")
    return fields


def verify_note(item: Any, entry: Any, site: str = "AniList") -> tuple[str, bool]:
    """Compare one written item with its re-read entry. Returns (note, has_problems)."""
    problems: list[str] = []
    if entry is None:
        problems.append("entry missing after write")
    else:
        expected_progress = item["al_progress"] if is_status_only(item) else item["md_progress"]
        if entry["progress"] != expected_progress:
            problems.append(f"progress is {entry['progress']}, expected {expected_progress}")
        if item["al_status_before"] is None:  # added by this run
            expected_status = "COMPLETED" if sends_completion(item) else "CURRENT"
            if entry["status"] != expected_status:
                problems.append(f"{site} stored status {entry['status']}, expected {expected_status}")
        else:
            expected_status = "COMPLETED" if sends_completion(item) else item["al_status_before"]
            if entry["status"] != expected_status:
                problems.append(f"status changed by {site}: {item['al_status_before']}→{entry['status']}")
    return ("; ".join(problems) if problems else "verified"), bool(problems)


def with_note(previous: str | None, note: str) -> str:
    return f"{previous}; {note}" if previous else note


def mutation_document(ops: list[WriteOp]) -> tuple[str, dict[str, Any]]:
    """One aliased SaveMediaListEntry per op. The status is a literal, never a variable."""
    params: list[str] = []
    fields: list[str] = []
    variables: dict[str, Any] = {}
    for i, op in enumerate(ops):
        if op.entry_id is None:
            # New entry. The status is one of two fixed literals; nothing from data reaches the document.
            params += [f"$a{i}: Int", f"$p{i}: Int"]
            variables[f"a{i}"], variables[f"p{i}"] = op.media_id, op.progress
            status = "COMPLETED" if op.complete else "CURRENT"
            fields.append(f"m{i}: SaveMediaListEntry(mediaId: $a{i}, status: {status}, progress: $p{i}) {SELECTION}")
            continue
        args = [f"id: $e{i}"]
        params.append(f"$e{i}: Int")
        variables[f"e{i}"] = op.entry_id
        if op.progress is not None:
            args.append(f"progress: $p{i}")
            params.append(f"$p{i}: Int")
            variables[f"p{i}"] = op.progress
        if op.complete:
            args.append("status: COMPLETED")
        fields.append(f"m{i}: SaveMediaListEntry({', '.join(args)}) {SELECTION}")
    return f"mutation ({', '.join(params)}) {{ {' '.join(fields)} }}", variables


async def send_batch(client: AniListClient, ops: list[WriteOp]) -> dict[str, tuple[str, str | None, int | None]]:
    """Send one batch. Returns md_id -> ('done', None, entry_id) | ('failed', message, None).

    Raises AniListComplexityError (nothing was executed) and other client errors unchanged.
    """
    if not ops:
        return {}
    query, variables = mutation_document(ops)
    try:
        data = await client.graphql(query, variables)
        errors: list[dict[str, Any]] = []
    except AniListComplexityError:
        raise
    except AniListGraphQLError as exc:
        data, errors = exc.data or {}, exc.errors
    by_alias: dict[str, str] = {}
    general: list[str] = []
    for e in errors:
        path = e.get("path") or []
        msg = str(e.get("message") or "error")
        if path and isinstance(path[0], str):
            by_alias[path[0]] = msg
        else:
            general.append(msg)
    results: dict[str, tuple[str, str | None, int | None]] = {}
    for i, op in enumerate(ops):
        alias = f"m{i}"
        if alias in by_alias:
            results[op.md_id] = ("failed", by_alias[alias], None)
        elif (data or {}).get(alias):
            results[op.md_id] = ("done", None, data[alias].get("id"))
        else:
            results[op.md_id] = ("failed", "; ".join(general) or "AniList returned no result for this entry", None)
    return results


class Writer:
    def __init__(
        self,
        repo: Repo,
        client: AniListClient,
        user_id: Callable[[], Awaitable[int]],
        progress: Callable[[str], None] = lambda _msg: None,
    ) -> None:
        self.repo, self.client, self.user_id, self.progress = repo, client, user_id, progress

    # ---- step 1: pre-write re-read --------------------------------------
    async def prewrite(self, run_id: int) -> None:
        pending = self.repo.items_in_state(run_id, "pending")
        if not pending:
            return
        self.progress("re-reading your AniList list before writing")
        await fetch_list(self.client, self.repo, await self.user_id())
        by_media = self.repo.al_entries()
        entries = {r["entry_id"]: r for r in by_media.values()}
        updates: list[tuple[str, dict[str, Any]]] = []
        for item in pending:
            adding = is_add(item)
            # An add is checked by media id: saving by mediaId would update an existing entry (never).
            entry = by_media.get(item["al_media_id"]) if adding else entries.get(item["al_entry_id"])
            updates.append((item["md_id"], recheck(item, adding, entry)))
        self.repo.update_items(run_id, updates)

    # ---- steps 2–6: batches ----------------------------------------------
    async def write(self, run_id: int) -> None:
        await self.prewrite(run_id)
        pending = self.repo.items_in_state(run_id, "pending")
        ops = [op_for(i) for i in pending]
        # An approved row that ends up with nothing to send (status-only whose completion was unticked).
        empty = [op.md_id for op in ops if op.progress is None and not op.complete]
        if empty:
            self.repo.update_items(run_id, [(m, {"write_state": "dropped", "verify_note": "dropped: nothing left to send"}) for m in empty])
        ops = [op for op in ops if op.md_id not in set(empty)]
        batches_sent = 0
        while ops:
            batch_size = max(1, int(self.repo.get_setting("anilist_write_batch")))
            batch, rest = ops[:batch_size], ops[batch_size:]
            total = batches_sent + math.ceil(len(ops) / batch_size)
            self.progress(f"batch {batches_sent + 1}/{total}, next request in ~{self.client.queue.min_interval:.0f} s")
            try:
                results = await send_batch(self.client, batch)
            except AniListComplexityError:
                if batch_size == 1:
                    raise
                smaller = max(1, batch_size // 2)
                log.warning("AniList rejected a batch of %s for complexity; retrying with %s", batch_size, smaller)
                self.repo.set_setting("anilist_write_batch", smaller)
                continue
            written_at = now_iso()
            adds = {op.md_id for op in batch if op.entry_id is None}
            self.repo.update_items(run_id, [
                (md_id, ({"write_state": state, "written_at": written_at}
                         | ({"al_entry_id": entry_id} if md_id in adds and entry_id else {}))
                 if state == "done" else {"write_state": state, "verify_note": f"write failed: {msg}"})
                for md_id, (state, msg, entry_id) in results.items()
            ])
            batches_sent += 1
            ops = rest

    # ---- verifying ---------------------------------------------------------
    async def verify(self, run_id: int) -> list[str]:
        """Re-read the list and compare. Returns the notes written (for the first-write banner)."""
        done = self.repo.items_in_state(run_id, "done")
        if not done:
            return []
        self.progress("verifying on AniList")
        await fetch_list(self.client, self.repo, await self.user_id())
        entries = {r["entry_id"]: r for r in self.repo.al_entries().values()}
        notes: list[str] = []
        updates: list[tuple[str, dict[str, Any]]] = []
        for item in done:
            note, problem = verify_note(item, entries.get(item["al_entry_id"]))
            if problem:
                notes.append(f"{item['md_id']}: {note}")
            updates.append((item["md_id"], {"verify_note": with_note(item["verify_note"], note)}))
        self.repo.update_items(run_id, updates)
        return notes
