"""Your ranking and the scores it gives (List → Rank), saved to AniList in the background.

Placing a series recomputes every score in its tier; whatever now differs from your AniList score is
saved (by entry id, through sync/list_edit.py), a batch of up to 10 per request.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from mdal.clients.anilist import AniListError
from mdal.db.repo import now_iso
from mdal.recommend import ranking
from mdal.sync.list_edit import save_scores

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
        return {m: ranking.to_anilist(s) for m, s in self.ranked_scores().items()
                if m in entries and (entries[m]["score"] or 0) != ranking.to_anilist(s)}

    def status(self) -> dict[str, Any]:
        return self.repo.get_setting("rank_save") or {}

    def start_save(self) -> None:
        """Save pending scores in the background; a save already running picks up the new ones after it."""
        if not self.services.anilist_token():
            return
        if self.saving:
            self._again = True
            return
        self.task = asyncio.create_task(self._save())

    async def _save(self) -> None:
        while True:
            self._again = False
            todo = self.pending()
            if not todo:
                break
            self.repo.set_setting("rank_save", {"state": "running", "detail": f"saving {len(todo)} scores to AniList",
                                                "started_at": now_iso()})
            try:
                saved, failed = await save_scores(self.services.anilist, self.repo, todo,
                                                  self.repo.get_setting("anilist_write_batch"))
                self.repo.set_setting("rank_save", {
                    "state": "failed" if failed else "done", "finished_at": now_iso(), "saved": len(saved),
                    "detail": f"{len(saved)} scores saved to AniList" + (f", {len(failed)} failed" if failed else ""),
                    "error": "; ".join(sorted(set(failed.values())))[:300] if failed else None})
            except AniListError as exc:
                log.warning("saving scores failed: %s", exc)
                self.repo.set_setting("rank_save", {"state": "failed", "finished_at": now_iso(), "error": str(exc),
                                                    "detail": "scores not saved"})
                break
            if not self._again:
                break
