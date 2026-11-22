"""Your AniList list, for list edits made in Shiori (the main site).

Like sync/writer.py, this module may contain a mutation. Its rules:
- it edits an entry that exists, addressed by its entry `id` (never `mediaId`), so an edit can't add one;
- the one add (SaveMediaListEntry by `mediaId`) is for a confirmed match not on your list, with a status in
  ADD_STATUSES; your list is re-read first, since saving by mediaId on a listed series would update that entry
  (and could turn Completed into Reading); each add is recorded as a one-item run, so History shows it;
- a score is sent as `scoreRaw` (1-100), and one that is already AniList's score isn't sent;
- a status is one of the literals in EDIT_STATUSES (never Rereading), and one AniList already has isn't sent;
- notes are sent as `notes`, at most NOTES_MAX characters;
- scores go in batches; a batch AniList finds too complex is halved, down to one;
- every change is logged in list_edit, and the local copy of the entry follows what AniList answered;
- an AniList error stops the rest of the edit: what wasn't tried fails with the same reason.
SaveMediaListEntry with absolute values is idempotent, so the client's retry of a failure is safe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from mdal.clients.anilist import AniListClient, AniListComplexityError, AniListError, AniListGraphQLError
from mdal.db.repo import Repo, now_iso
from mdal.fetch.anilist_list import fetch_list
from mdal.recommend.ranking import to_anilist
from mdal.sync.rules import STATUS_LABELS

if TYPE_CHECKING:
    from mdal.lists import Series, SiteResult

SELECTION = "{ id mediaId status progress score(format: POINT_100) }"
NOT_LISTED = "not on your AniList list"


EDIT_STATUSES = ("CURRENT", "COMPLETED", "PAUSED", "DROPPED", "PLANNING")
# Offered for an add, in dropdown order. The value is inserted as an enum literal only after this whitelist check.
ADD_STATUSES = ("CURRENT", "PLANNING", "PAUSED", "COMPLETED", "DROPPED")
NO_MATCH = "This series has no confirmed AniList match."


def add_document(status: str) -> str:
    if status not in ADD_STATUSES:
        raise ValueError(f"refusing to add with status {status!r}")
    return (
        f"mutation ($m: Int, $p: Int) {{ SaveMediaListEntry(mediaId: $m, status: {status}, progress: $p) "
        "{ id mediaId progress status } }"
    )


NOTES_MAX = 5000
NOTES_DOCUMENT = f"mutation ($e: Int, $n: String) {{ SaveMediaListEntry(id: $e, notes: $n) {SELECTION} }}"
READ_NOTES = "query ($e: Int) { MediaList(id: $e) { id notes } }"


def status_document(status: str) -> str:
    if status not in EDIT_STATUSES:
        raise ValueError(f"refusing to set status {status!r}")
    return f"mutation ($e: Int) {{ SaveMediaListEntry(id: $e, status: {status}) {SELECTION} }}"


def score_document(n: int) -> str:
    params = ", ".join(f"$e{i}: Int, $s{i}: Int" for i in range(n))
    fields = " ".join(f"m{i}: SaveMediaListEntry(id: $e{i}, scoreRaw: $s{i}) {SELECTION}" for i in range(n))
    return f"mutation ({params}) {{ {fields} }}"


class AniListSite:
    name = "AniList"

    def __init__(self, repo: Repo, client: AniListClient, batch: Callable[[], int] = lambda: 10,
                 user_id: Callable[[], Awaitable[int]] | None = None) -> None:
        self.repo, self.client, self.batch, self.user_id = repo, client, batch, user_id

    def entry(self, series: Series) -> Any:
        """Your entry for the series, as last read, or None."""
        return self.repo.al_entries().get(series.media_id) if series.media_id is not None else None

    async def set_scores(self, scores: dict[Series, float]) -> SiteResult:
        from mdal.lists import SiteResult

        result = SiteResult(self.name)
        entries = self.repo.al_entries()
        todo: list[tuple[Series, int]] = []
        for series, score in scores.items():
            entry = entries.get(series.media_id) if series.media_id is not None else None
            if entry is None:
                result.failed[series] = NOT_LISTED
            elif (entry["score"] or 0) == to_anilist(score):
                result.unchanged.append(series)
            else:
                todo.append((series, to_anilist(score)))
        batch, start = max(1, int(self.batch())), 0
        while start < len(todo):
            chunk = todo[start:start + batch]
            variables: dict[str, Any] = {}
            for i, (series, value) in enumerate(chunk):
                variables[f"e{i}"], variables[f"s{i}"] = entries[series.media_id]["entry_id"], value
            try:
                data, errors = await self.client.graphql(score_document(len(chunk)), variables), []
            except AniListComplexityError as exc:
                if len(chunk) > 1:
                    batch = max(1, len(chunk) // 2)       # half of what was sent, not of the setting
                    continue
                for rest, _ in todo[start:]:
                    result.failed[rest] = str(exc)
                break
            except AniListGraphQLError as exc:
                data, errors = exc.data or {}, exc.errors
            except AniListError as exc:
                for rest, _ in todo[start:]:
                    result.failed[rest] = str(exc)
                break
            problems = {str((e.get("path") or [""])[0]): str(e.get("message") or "error") for e in errors}
            for i, (series, value) in enumerate(chunk):
                entry = entries[series.media_id]
                answer = (data or {}).get(f"m{i}")
                if answer and f"m{i}" not in problems:
                    self.repo.update_al_entry(series.media_id, score=answer.get("score") or value)
                    self.repo.log_edit(series.media_id, entry["entry_id"], "score", entry["score"], value, "done")
                    result.saved.append(series)
                else:
                    result.failed[series] = problems.get(f"m{i}") or "AniList returned no result"
                    self.repo.log_edit(series.media_id, entry["entry_id"], "score", entry["score"], value, "failed",
                                       result.failed[series])
            start += len(chunk)
        return result

    async def set_status(self, series: Series, status: str) -> SiteResult:
        """Set the entry's status. AniList may fill in progress itself (on Completed); `stored` says so."""
        from mdal.lists import SiteResult

        result = SiteResult(self.name)
        query = status_document(status)
        entry = self.entry(series)
        if entry is None:
            result.failed[series] = NOT_LISTED
            return result
        if entry["status"] == status:
            result.unchanged.append(series)
            return result
        try:
            data = await self.client.graphql(query, {"e": entry["entry_id"]})
        except AniListError as exc:
            self.repo.log_edit(series.media_id, entry["entry_id"], "status", entry["status"], status, "failed", str(exc))
            result.failed[series] = str(exc)
            return result
        answer = data.get("SaveMediaListEntry") or {}
        saved: dict[str, Any] = {"status": answer.get("status") or status}
        if answer.get("progress") is not None:
            saved["progress"] = answer["progress"]
            if answer["progress"] != entry["progress"]:
                result.stored[series] = {"progress": answer["progress"]}
        self.repo.update_al_entry(series.media_id, **saved)
        self.repo.log_edit(series.media_id, entry["entry_id"], "status", entry["status"], status, "done")
        result.saved.append(series)
        return result

    async def read_notes(self, series: Series) -> str:
        """Your notes on the entry, from AniList (1 request), kept locally. Raises AniListError, or
        LookupError when the series isn't on your list."""
        entry = self.entry(series)
        if entry is None:
            raise LookupError(NOT_LISTED)
        data = await self.client.graphql(READ_NOTES, {"e": entry["entry_id"]})
        notes = ((data.get("MediaList") or {}).get("notes")) or ""
        self.repo.update_al_entry(series.media_id, notes=notes)
        return notes

    async def set_notes(self, series: Series, notes: str) -> SiteResult:
        from mdal.lists import SiteResult

        result = SiteResult(self.name)
        entry = self.entry(series)
        if entry is None:
            result.failed[series] = NOT_LISTED
            return result
        try:
            await self.client.graphql(NOTES_DOCUMENT, {"e": entry["entry_id"], "n": notes})
        except AniListError as exc:
            self.repo.log_edit(series.media_id, entry["entry_id"], "notes", entry["notes"], notes, "failed", str(exc))
            result.failed[series] = f"AniList refused the notes: {exc}"
            return result
        self.repo.update_al_entry(series.media_id, notes=notes)
        self.repo.log_edit(series.media_id, entry["entry_id"], "notes", entry["notes"], notes, "done")
        result.saved.append(series)
        return result

    async def add(self, series: Series, status: str, progress: int) -> SiteResult:
        """Add a matched series that isn't on your list. Already on it (as last read) is `unchanged`, with no
        request; `stored` holds what AniList saved (it may fill in its total)."""
        from mdal.lists import SiteResult

        result = SiteResult(self.name)
        if series.media_id is None or not series.md_ids:
            result.failed[series] = NO_MATCH
            return result
        if self.entry(series) is not None:
            result.unchanged.append(series)
            return result
        media = self.repo.media([series.media_id]).get(series.media_id)
        if status not in ADD_STATUSES:
            problem = f"Unsupported status {status!r}."
        elif progress < 0:
            problem = "Progress cannot be negative."
        elif media is not None and media["chapters"] is not None and progress > media["chapters"]:
            problem = f"Progress {progress} exceeds AniList's total of {media['chapters']} chapters."
        else:
            problem = None
        if problem:
            result.failed[series] = problem
            return result

        md_id = series.key[3:] if series.key.startswith("md:") else series.md_ids[0]
        started, requests = now_iso(), [0]

        def record(state: str, **fields: Any) -> int:
            """History's record of this add, written once it's over: an add is never an in-progress Run."""
            run_id = self.repo.create_run(state)
            self.repo.update_run(run_id, started_at=started, approved_at=started, finished_at=now_iso(),
                                 req_anilist=requests[0], **fields)
            return run_id

        def count() -> None:
            requests[0] += 1

        previous = self.client.request_counter
        self.client.request_counter = count
        try:
            await fetch_list(self.client, self.repo, await self.user_id())
            existing = self.entry(series)
            if existing is not None:
                problem = (f"Already on your list now ({existing['status']}, progress {existing['progress']}); "
                           "nothing was changed.")
                record("cancelled", error=problem)
                result.failed[series] = problem
                return result
            data = await self.client.graphql(add_document(status), {"m": series.media_id, "p": progress})
        except AniListGraphQLError as exc:
            problem = f"AniList refused the add: {exc}"
        except AniListError as exc:
            problem = f"AniList error: {exc}"
        except Exception as exc:
            record("failed", error=f"{exc.__class__.__name__}: {exc}")
            raise
        finally:
            self.client.request_counter = previous
        if problem:
            record("failed", error=problem)
            result.failed[series] = problem
            return result

        entry = data["SaveMediaListEntry"]
        written_at = now_iso()
        run_id = record("done", phase_detail=f"added 1 entry: {STATUS_LABELS[status]}, progress {progress}")
        self.repo.add_al_entry({"entry_id": entry["id"], "media_id": entry["mediaId"], "status": entry["status"],
                                "progress": entry["progress"] or 0, "fetched_at": written_at})
        self.repo.replace_items(run_id, [{
            "run_id": run_id, "md_id": md_id, "al_media_id": series.media_id, "site_entry_id": entry["id"],
            "site_progress": None, "md_progress": progress, "action": "write",
            "set_status": "COMPLETED" if status == "COMPLETED" else None, "status_approved": int(status == "COMPLETED"),
            "reason": f"added to AniList as {STATUS_LABELS[status]}", "approved": 1,
            "write_state": "done", "written_at": written_at,
            "verify_note": None if entry["status"] == status and entry["progress"] == progress
            else f"AniList stored {entry['status']} / {entry['progress']}",
        }])
        if not self.repo.get_setting("first_write_done"):
            self.repo.set_setting("first_write_done", True)
        result.saved.append(series)
        result.stored[series] = {"status": entry["status"], "progress": entry["progress"] or 0}
        return result
