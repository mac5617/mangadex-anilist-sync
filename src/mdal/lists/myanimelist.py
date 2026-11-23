"""Your MyAnimeList list, for list edits made in Shiori (the mirror site).

Rules:
- only an entry already on your MyAnimeList list is edited, and each edit reads the entry first: MyAnimeList's
  save would otherwise create it, so an edit costs 2 requests;
- a score is a whole number 1-10, and one whose whole number didn't change isn't sent;
- a status is one of MAL_STATUS's words (never rereading), and one the entry already has isn't sent;
- notes go to the entry's comments;
- an add is the reverse: one read first, and it goes ahead only when the series is not on your list,
  so an existing entry is never overwritten;
- every change is logged in list_edit with site 'mal', and the local copy follows what was saved;
- a MyAnimeList error stops the rest of the edit: what wasn't tried fails with the same reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mdal.clients.myanimelist import MalClient, MalError, not_accepted_yet
from mdal.db.repo import Repo, now_iso
from mdal.scores import to_mal

if TYPE_CHECKING:
    from mdal.lists import Series, SiteResult

NOT_LISTED = "not on your MyAnimeList list"
# AniList's words -> MyAnimeList's. The local copy (mal_entry) keeps AniList's words.
MAL_STATUS = {"CURRENT": "reading", "COMPLETED": "completed", "PAUSED": "on_hold", "DROPPED": "dropped",
              "PLANNING": "plan_to_read"}


class MalSite:
    name = "MyAnimeList"

    def __init__(self, repo: Repo, client: MalClient) -> None:
        self.repo, self.client = repo, client

    def entry(self, series: Series) -> Any:
        """Your entry for the series, as last read, or None."""
        return self.repo.mal_entries().get(series.mal_id) if series.mal_id else None

    async def set_scores(self, scores: dict[Series, float]) -> SiteResult:
        from mdal.lists import SiteResult

        result = SiteResult(self.name)
        listed = self.repo.mal_entries()
        todo: list[tuple[Series, int]] = []
        for series, score in scores.items():
            if series.mal_id is None:
                result.skipped[series] = series.mal_problem or "no MyAnimeList link"
            elif series.mal_id not in listed:
                result.skipped[series] = NOT_LISTED
            elif (listed[series.mal_id]["score"] or 0) == to_mal(score):
                result.unchanged.append(series)
            else:
                todo.append((series, to_mal(score)))
        for i, (series, value) in enumerate(todo):
            try:
                problem = await self._checked_edit(series, "score", value, score=value)
            except MalError as exc:
                for rest, _ in todo[i:]:
                    result.failed[rest] = str(exc)
                break
            if problem:
                result.failed[series] = problem
            else:
                self.repo.update_mal_entry(series.mal_id, score=value)
                result.saved.append(series)
        return result

    async def set_status(self, series: Series, status: str) -> SiteResult:
        from mdal.lists import SiteResult

        if status not in MAL_STATUS:
            raise ValueError(f"refusing to set status {status!r}")
        result = SiteResult(self.name)
        entry = self.entry(series)
        if series.mal_id is None:
            result.skipped[series] = series.mal_problem or "no MyAnimeList link"
        elif entry is None:
            result.skipped[series] = NOT_LISTED
        elif entry["status"] == status:
            result.unchanged.append(series)
        else:
            word = MAL_STATUS[status]
            try:
                problem = await self._checked_edit(series, "status", word, status=word)
            except MalError as exc:
                problem = str(exc)
            if problem:
                result.failed[series] = problem
            else:
                self.repo.update_mal_entry(series.mal_id, status=status)
                result.saved.append(series)
        return result

    async def set_notes(self, series: Series, notes: str) -> SiteResult:
        from mdal.lists import SiteResult

        result = SiteResult(self.name)
        if series.mal_id is None:
            result.skipped[series] = series.mal_problem or "no MyAnimeList link"
            return result
        if self.entry(series) is None:
            result.skipped[series] = NOT_LISTED
            return result
        mal_id, problem = series.mal_id, None
        try:
            entry = await self.client.my_list_status(mal_id)
            if entry is None:
                problem = NOT_LISTED
                self.repo.log_edit(series.media_id, mal_id, "notes", None, notes, "failed", problem, site="mal")
            else:
                await self.client.update_comments(mal_id, notes)
                self.repo.log_edit(series.media_id, mal_id, "notes", entry.get("comments"), notes, "done", site="mal")
        except MalError as exc:
            problem = f"MyAnimeList refused the notes: {exc}"
            self.repo.log_edit(series.media_id, mal_id, "notes", None, notes, "failed", str(exc), site="mal")
        if problem:
            result.failed[series] = problem
        else:
            result.saved.append(series)
        return result

    async def add(self, series: Series, status: str, progress: int) -> SiteResult:
        """Add the series with this status and chapters read. On your list (as last read) is `unchanged`."""
        from mdal.lists import SiteResult

        if status not in MAL_STATUS:
            raise ValueError(f"refusing to add with status {status!r}")
        result = SiteResult(self.name)
        if series.mal_id is None:
            result.skipped[series] = series.mal_problem or "no MyAnimeList link"
            return result
        if self.entry(series) is not None:
            result.unchanged.append(series)
            return result
        mal_id, word = series.mal_id, MAL_STATUS[status]
        try:
            entry = await self.client.my_list_status(mal_id)
            if entry is not None:
                result.failed[series] = (f"already on your MyAnimeList list ({entry.get('status')}, "
                                         f"{entry.get('num_chapters_read', 0)} ch), so nothing was changed there")
                return result
            saved = await self.client.add_to_list(mal_id, chapters=progress, status=word)
        except MalError as exc:
            self.repo.log_edit(series.media_id, mal_id, "status", None, word, "failed", str(exc), site="mal")
            waiting = not_accepted_yet(exc)
            result.failed[series] = f"{waiting}; try again later." if waiting else f"MyAnimeList refused the add: {exc}"
            return result
        stored = next((code for code, w in MAL_STATUS.items() if w == saved.get("status")), status)
        chapters = saved.get("num_chapters_read", progress)
        self.repo.add_mal_entry({"mal_id": mal_id, "status": stored, "progress": chapters,
                                 "score": saved.get("score") or None, "fetched_at": now_iso()})
        self.repo.log_edit(series.media_id, mal_id, "status", None, word, "done", site="mal")
        result.saved.append(series)
        result.stored[series] = {"progress": chapters}
        return result

    async def _checked_edit(self, series: Series, field: str, new: Any, **send: Any) -> str | None:
        """Read the entry, then edit it. Returns None when done, else why it wasn't (MalError on reading)."""
        mal_id = series.mal_id
        entry = await self.client.my_list_status(mal_id)
        if entry is None:
            self.repo.log_edit(series.media_id, mal_id, field, None, new, "failed", NOT_LISTED, site="mal")
            return NOT_LISTED
        try:
            await self.client.edit_entry(mal_id, **send)
        except MalError as exc:
            self.repo.log_edit(series.media_id, mal_id, field, entry.get(field), new, "failed", str(exc), site="mal")
            return str(exc)
        self.repo.log_edit(series.media_id, mal_id, field, entry.get(field), new, "done", site="mal")
        return None
