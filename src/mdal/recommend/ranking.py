"""Ranking series by comparing two at a time (like Beli). Pure: no I/O.

You first say how a series felt (liked it / it was fine / didn't like it), then compare it with series
already in that tier until its place is found: a binary search, so about log2(n) questions. A series'
score follows from its place: each tier spans a band of the 10-point scale, best at the top, spread
evenly. Adding one moves its neighbours a little, so their scores are recomputed too.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

TIERS: dict[str, tuple[float, float, str]] = {          # tier -> (lowest, highest score, how it felt)
    "liked": (6.8, 10.0, "I liked it"),
    "fine": (4.0, 6.7, "It was fine"),
    "disliked": (0.0, 3.9, "I didn't like it"),
}
TIER_ORDER = ("liked", "fine", "disliked")
RANKABLE = ("COMPLETED", "REPEATING", "DROPPED", "PAUSED", "CURRENT")   # read at least some of it


def tier_for(score: float | None) -> str | None:
    """The tier an existing AniList score (0-100) suggests."""
    if not score:
        return None
    s = score / 10
    return "liked" if s >= 6.8 else "fine" if s >= 4.0 else "disliked"


def scores(ranked: list[tuple[int, str]]) -> dict[int, float]:
    """{media_id: score out of 10, one decimal} for [(media_id, tier)] in rank order (best first per tier)."""
    out: dict[int, float] = {}
    for tier in TIER_ORDER:
        ids = [m for m, t in ranked if t == tier]
        low, high, _ = TIERS[tier]
        for i, m in enumerate(ids):
            value = high if len(ids) == 1 else high - (high - low) * i / (len(ids) - 1)
            out[m] = round(value, 1)
    return out


def to_anilist(score: float) -> int:
    """10-point score -> AniList's POINT_100 (1-100; 0 would mean "no score")."""
    return max(1, min(100, round(score * 10)))


@dataclass(frozen=True)
class Search:
    """Where a series being ranked can still go in its tier: any index in [low, high]."""
    low: int
    high: int

    @property
    def done(self) -> bool:
        return self.low >= self.high

    @property
    def pivot(self) -> int:
        """The ranked series to compare with next."""
        return (self.low + self.high) // 2

    def answer(self, better: bool | None) -> Search:
        """better: True if the new series beats the pivot, False if not, None for "too close to call"."""
        if better is None:
            return Search(self.pivot, self.pivot)
        return Search(self.low, self.pivot) if better else Search(self.pivot + 1, self.high)


def start(tier_size: int) -> Search:
    return Search(0, tier_size)


def questions_left(search: Search) -> int:
    span = search.high - search.low
    return 0 if span <= 0 else math.ceil(math.log2(span + 1))


def position_at(positions: list[float], index: int) -> float:
    """A position that sorts at `index` among existing positions (sorted ascending)."""
    if not positions:
        return 0.0
    if index <= 0:
        return positions[0] - 1
    if index >= len(positions):
        return positions[-1] + 1
    return (positions[index - 1] + positions[index]) / 2


def queue(entries: list[dict[str, Any]], ranked: set[int], skipped: list[int]) -> list[dict[str, Any]]:
    """Series to rank next: read but not ranked; unscored first (they help your taste most), most read first;
    ones you skipped go last."""
    todo = [e for e in entries if e["status"] in RANKABLE and e["media_id"] not in ranked]
    skip = {m: i for i, m in enumerate(skipped)}
    return sorted(todo, key=lambda e: (e["media_id"] in skip, skip.get(e["media_id"], 0), bool(e.get("score")),
                                        -(e.get("progress") or 0)))
