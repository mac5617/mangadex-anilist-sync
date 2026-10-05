"""Edits to your AniList list outside syncs: scores (List → Rank) and Paused/Dropped (List → Stalled).

Like writer.py and add_entry.py, this module may contain a mutation. Its rules:
- it only edits an entry that exists, addressed by its entry `id` (never `mediaId`), so nothing can be added;
- it sends only `scoreRaw` (1-100), `notes`, or one of the literal statuses PAUSED and DROPPED;
- every change is logged in list_edit, and the local copy of the entry follows what AniList answered.
SaveMediaListEntry with absolute values is idempotent, so the client's retry of a failure is safe.
"""

from __future__ import annotations

import logging
from typing import Any

from mdal.clients.anilist import AniListClient, AniListComplexityError, AniListGraphQLError
from mdal.db.repo import Repo

log = logging.getLogger(__name__)

SELECTION = "{ id mediaId status score(format: POINT_100) }"
EDIT_STATUSES = ("PAUSED", "DROPPED")


class ListEditError(Exception):
    pass


def score_document(n: int) -> str:
    params = ", ".join(f"$e{i}: Int, $s{i}: Int" for i in range(n))
    fields = " ".join(f"m{i}: SaveMediaListEntry(id: $e{i}, scoreRaw: $s{i}) {SELECTION}" for i in range(n))
    return f"mutation ({params}) {{ {fields} }}"


def status_document(status: str) -> str:
    if status not in EDIT_STATUSES:
        raise ListEditError(f"refusing to set status {status!r}")
    return f"mutation ($e: Int) {{ SaveMediaListEntry(id: $e, status: {status}) {SELECTION} }}"


NOTES_MAX = 5000
NOTES_DOCUMENT = f"mutation ($e: Int, $n: String) {{ SaveMediaListEntry(id: $e, notes: $n) {SELECTION} }}"
READ_NOTES = "query ($e: Int) { MediaList(id: $e) { id notes } }"


def _entry(repo: Repo, media_id: int) -> Any:
    entry = repo.al_entries().get(media_id)
    if entry is None:
        raise ListEditError("This series isn't on your AniList list.")
    return entry


async def save_scores(client: AniListClient, repo: Repo, scores: dict[int, int],
                      batch: int = 10) -> tuple[list[int], dict[int, str]]:
    """Send scores (0-100, AniList's POINT_100) for entries on your list. Returns (saved media ids, {failed: reason})."""
    entries = repo.al_entries()
    todo = [(m, max(1, min(100, int(round(s))))) for m, s in scores.items() if m in entries]
    failed = {m: "not on your AniList list" for m in scores if m not in entries}
    saved: list[int] = []
    start = 0
    while start < len(todo):
        chunk = todo[start:start + batch]
        variables: dict[str, Any] = {}
        for i, (m, s) in enumerate(chunk):
            variables[f"e{i}"], variables[f"s{i}"] = entries[m]["entry_id"], s
        try:
            data, errors = await client.graphql(score_document(len(chunk)), variables), []
        except AniListComplexityError:
            if batch == 1:
                raise
            batch = max(1, batch // 2)
            continue
        except AniListGraphQLError as exc:
            data, errors = exc.data or {}, exc.errors
        problems = {str((e.get("path") or [""])[0]): str(e.get("message") or "error") for e in errors}
        for i, (m, s) in enumerate(chunk):
            result = (data or {}).get(f"m{i}")
            old = entries[m]["score"]
            if result and f"m{i}" not in problems:
                repo.update_al_entry(m, score=result.get("score") or s)
                repo.log_edit(m, entries[m]["entry_id"], "score", old, s, "done")
                saved.append(m)
            else:
                failed[m] = problems.get(f"m{i}") or "AniList returned no result"
                repo.log_edit(m, entries[m]["entry_id"], "score", old, s, "failed", failed[m])
        start += len(chunk)
    return saved, failed


async def read_notes(client: AniListClient, repo: Repo, media_id: int) -> str:
    """Your notes on one entry, from AniList (1 request), kept locally."""
    entry = _entry(repo, media_id)
    data = await client.graphql(READ_NOTES, {"e": entry["entry_id"]})
    notes = ((data.get("MediaList") or {}).get("notes")) or ""
    repo.update_al_entry(media_id, notes=notes)
    return notes


async def save_notes(client: AniListClient, repo: Repo, media_id: int, notes: str) -> None:
    """Replace your notes on one entry. Raises ListEditError when AniList refuses."""
    entry = _entry(repo, media_id)
    notes = notes.strip()[:NOTES_MAX]
    try:
        await client.graphql(NOTES_DOCUMENT, {"e": entry["entry_id"], "n": notes})
    except AniListGraphQLError as exc:
        repo.log_edit(media_id, entry["entry_id"], "notes", entry["notes"], notes, "failed", str(exc))
        raise ListEditError(f"AniList refused the notes: {exc}") from exc
    repo.update_al_entry(media_id, notes=notes)
    repo.log_edit(media_id, entry["entry_id"], "notes", entry["notes"], notes, "done")


async def set_status(client: AniListClient, repo: Repo, media_id: int, status: str) -> None:
    """Mark one entry Paused or Dropped. Raises ListEditError when it can't."""
    query = status_document(status)
    entry = _entry(repo, media_id)
    try:
        data = await client.graphql(query, {"e": entry["entry_id"]})
    except AniListGraphQLError as exc:
        repo.log_edit(media_id, entry["entry_id"], "status", entry["status"], status, "failed", str(exc))
        raise ListEditError(f"AniList refused the change: {exc}") from exc
    result = data.get("SaveMediaListEntry") or {}
    repo.update_al_entry(media_id, status=result.get("status") or status)
    repo.log_edit(media_id, entry["entry_id"], "status", entry["status"], status, "done")
