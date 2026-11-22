"""Your MyAnimeList list, for list edits made in Shiori (the mirror site).

Rules:
- only an entry already on your MyAnimeList list is edited, and each edit reads the entry first: MyAnimeList's
  save would otherwise create it, so an edit costs 2 requests;
- a score is a whole number 1-10, and one whose whole number didn't change isn't sent;
- a status is one of MAL_STATUS's words (never rereading), and one the entry already has isn't sent;
- every change is logged in list_edit with site 'mal', and the local copy follows what was saved;
- a MyAnimeList error stops the rest of the edit: what wasn't tried fails with the same reason.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mdal.clients.myanimelist import MalClient, MalError
from mdal.db.repo import Repo
from mdal.recommend.ranking import to_anilist

if TYPE_CHECKING:
    from mdal.lists import Series, SiteResult

NOT_LISTED = "not on your MyAnimeList list"
# AniList's words -> MyAnimeList's. The local copy (mal_entry) keeps AniList's words.
MAL_STATUS = {"CURRENT": "reading", "COMPLETED": "completed", "PAUSED": "on_hold", "DROPPED": "dropped",
              "PLANNING": "plan_to_read"}


def mal_score(score: float) -> int:
    """A score out of 10 -> MyAnimeList's whole number, by way of AniList's 1-100 (84.5 rounds half to even: 8)."""
    return max(1, min(10, round(to_anilist(score) / 10)))


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
            elif (listed[series.mal_id]["score"] or 0) == mal_score(score):
                result.unchanged.append(series)
            else:
                todo.append((series, mal_score(score)))
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
