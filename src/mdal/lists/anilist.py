"""Your AniList list, for list edits made in Shiori (the main site).

Like sync/writer.py, this module may contain a mutation. Its rules:
- it only edits an entry that exists, addressed by its entry `id` (never `mediaId`), so nothing can be added;
- a score is sent as `scoreRaw` (1-100), and one that is already AniList's score isn't sent;
- a status is one of the literals in EDIT_STATUSES (never Rereading), and one AniList already has isn't sent;
- scores go in batches; a batch AniList finds too complex is halved, down to one;
- every change is logged in list_edit, and the local copy of the entry follows what AniList answered;
- an AniList error stops the rest of the edit: what wasn't tried fails with the same reason.
SaveMediaListEntry with absolute values is idempotent, so the client's retry of a failure is safe.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mdal.clients.anilist import AniListClient, AniListComplexityError, AniListError, AniListGraphQLError
from mdal.db.repo import Repo
from mdal.recommend.ranking import to_anilist

if TYPE_CHECKING:
    from mdal.lists import Series, SiteResult

SELECTION = "{ id mediaId status progress score(format: POINT_100) }"
NOT_LISTED = "not on your AniList list"


EDIT_STATUSES = ("CURRENT", "COMPLETED", "PAUSED", "DROPPED", "PLANNING")


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

    def __init__(self, repo: Repo, client: AniListClient, batch: Callable[[], int] = lambda: 10) -> None:
        self.repo, self.client, self.batch = repo, client, batch

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
