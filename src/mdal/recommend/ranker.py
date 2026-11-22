"""Your ranking and the scores it gives (List → Rank), saved to AniList in the background.

Placing a series recomputes every score in its tier; whatever now differs from your AniList score is
saved through Your list (mdal/lists), AniList first and then MyAnimeList.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from mdal.db.repo import now_iso
from mdal.recommend import ranking
from mdal.scores import to_anilist, to_mal

if TYPE_CHECKING:
    from mdal.services import Services

log = logging.getLogger(__name__)


class Ranker:
    def __init__(self, services: Services) -> None:
        self.services = services
        self.task: asyncio.Task[None] | None = None
        self._again = False

    @property
    def repo(self):
        return self.services.repo

    @property
    def saving(self) -> bool:
        return bool(self.task and not self.task.done())

    # ---- the ranking -------------------------------------------------------------------
    def tier(self, tier: str) -> list[Any]:
        """Ranked series in a tier, best first."""
        return [r for r in self.repo.ranking() if r["tier"] == tier]

    def ranked_scores(self) -> dict[int, float]:
        return ranking.scores([(r["media_id"], r["tier"]) for r in self.repo.ranking()])

    def place(self, media_id: int, tier: str, index: int) -> float:
        """Put a series at `index` in its tier (0 = best). Returns its new score out of 10."""
        if tier not in ranking.TIERS:
            raise ValueError(f"unknown tier {tier!r}")
        self.repo.unrank(media_id)
        positions = [r["position"] for r in self.tier(tier)]
        self.repo.set_rank(media_id, tier, ranking.position_at(positions, index))
        self.repo.renumber_tier(tier)
        skipped = [m for m in self.repo.get_setting("rank_skipped") or [] if m != media_id]
        self.repo.set_setting("rank_skipped", skipped)
        self.start_save()
        return self.ranked_scores()[media_id]

    def remove(self, media_id: int) -> None:
        """Take a series out of the ranking. Its AniList score stays as it is; its tier's others move up."""
        row = next((r for r in self.repo.ranking() if r["media_id"] == media_id), None)
        self.repo.unrank(media_id)
        if row:
            self.repo.renumber_tier(row["tier"])
            self.start_save()

    def skip(self, media_id: int) -> None:
        skipped = [m for m in self.repo.get_setting("rank_skipped") or [] if m != media_id] + [media_id]
        self.repo.set_setting("rank_skipped", skipped[-500:])

    def queue(self) -> list[dict[str, Any]]:
        from mdal.stats import entry_rows
        ranked = {r["media_id"] for r in self.repo.ranking()}
        return ranking.queue(entry_rows(self.repo), ranked, self.repo.get_setting("rank_skipped") or [])

    # ---- saving scores -----------------------------------------------------------------
    def pending(self) -> dict[int, int]:
        """{media_id: AniList score} for ranked series whose AniList score differs from their place."""
        entries = self.repo.al_entries()
        return {m: to_anilist(s) for m, s in self.ranked_scores().items()
                if m in entries and (entries[m]["score"] or 0) != to_anilist(s)}

    def pending_mal(self) -> dict[int, int]:
        """{media_id: AniList score} for scores Shiori set (ranked or quick-scored) whose whole number isn't on
        MyAnimeList yet. Scores you set yourself on MyAnimeList are never touched."""
        if not self.services.mirror_to_mal():
            return {}
        entries = self.repo.al_entries()
        ids = {r["media_id"] for r in self.repo.ranking()} | self.repo.scored_by_shiori()
        scores = {m: entries[m]["score"] for m in ids if m in entries and entries[m]["score"]}
        listed = self.repo.mal_entries()
        series = self.services.your_list.series([f"al:{m}" for m in scores])
        return {m: scores[m] for m in scores if (mal_id := series[f"al:{m}"].mal_id) in listed
                and (listed[mal_id]["score"] or 0) != to_mal(scores[m] / 10)}

    def status(self) -> dict[str, Any]:
        return self.repo.get_setting("rank_save") or {}

    def wanted(self) -> dict[str, float]:
        """The scores Shiori set, out of 10 by series key: each ranked series' place, and the quick scores
        already on AniList. Scores you set yourself are never in it."""
        entries = self.repo.al_entries()
        out = {f"al:{m}": entries[m]["score"] / 10 for m in self.repo.scored_by_shiori()
               if m in entries and entries[m]["score"]}
        out.update({f"al:{m}": s for m, s in self.ranked_scores().items() if m in entries})
        return out

    def start_save(self) -> None:
        """Save pending scores in the background (AniList, then MyAnimeList); a save already running picks up
        new ones after it. While Shiori is syncing it waits: the sync starts it when it finishes."""
        if self.saving:
            self._again = True
            return
        if self.services.orchestrator.busy():
            return
        if not (self.services.anilist_token() and self.pending()) and not self.pending_mal():
            return
        self.task = asyncio.create_task(self._save())

    async def _save(self) -> None:
        while True:
            self._again = False
            self.repo.set_setting("rank_save", {"state": "running", "detail": "saving scores", "started_at": now_iso()})
            edit = await self.services.your_list.set_scores(self.wanted())
            if edit.refused:
                self.repo.set_setting("rank_save", {"state": "done", "finished_at": now_iso(), "detail": edit.note,
                                                    "error": None})
                break
            problems = edit.problems()
            self.repo.set_setting("rank_save", {
                "state": "failed" if problems else "done", "finished_at": now_iso(), "detail": edit.note,
                "error": "; ".join(problems)[:300] if problems else None})
            if not self._again:
                break
