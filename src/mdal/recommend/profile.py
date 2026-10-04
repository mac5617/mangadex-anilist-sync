"""Your taste, from your AniList list. Pure: no I/O.

Each list entry gets a weight for how much you liked it: your score when there is one, otherwise what
its status and chapters read suggest (most of a long library is unscored, so a real score counts three
times as much). A genre, tag or creator then gets an affinity from two parts:
- preference: how its entries' average weight compares with your library's average, pulled towards
  that average when only a few entries have it (so one 100 doesn't make a favourite);
- prevalence: how much of your reading it covers.
Checked on a 2,300-entry list: rarely-read but top-scored genres (Horror, Psychological) rank first,
the genres read most (Fantasy, Action) stay near the top, and a mostly-dropped genre goes negative.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

PRIOR = 6.0          # pseudo-entries at the library average added to every feature (shrinks rare ones)
SCORED_BOOST = 3.0   # an explicit score counts this many times as much as a weight implied by status
PREFERENCE, PREVALENCE = 1.5, 0.6
NEUTRAL_SCORE = 55   # an AniList score (0-100) at this value counts as indifferent
SCORE_SPAN = 30      # 30 points above neutral = weight 1.0
UNSCORED_BASE = {"COMPLETED": 0.6, "REPEATING": 0.9, "CURRENT": 0.25, "PAUSED": 0.05, "DROPPED": -0.6}


def entry_weight(status: str | None, score: float | None, progress: int | None) -> float | None:
    """How much you liked an entry, roughly -1..1.5. None for Planning (no signal yet)."""
    if status == "PLANNING":
        return None
    if score:
        return max(-1.0, min(1.5, (score - NEUTRAL_SCORE) / SCORE_SPAN))
    base = UNSCORED_BASE.get(status or "", 0.2)
    if status in ("CURRENT", "PAUSED"):
        base += 0.35 * min(1.0, math.log10(1 + (progress or 0)) / 2)  # 100 chapters read = full bonus
    return base


@dataclass
class Feature:
    """One genre, tag or creator in your list."""
    key: str                  # genre/tag name, or staff id as text
    label: str
    count: int = 0            # entries that have it
    weight_sum: float = 0.0   # sum of weight x relevance
    relevance_sum: float = 0.0
    scores: list[float] = field(default_factory=list)
    titles: list[tuple[float, str]] = field(default_factory=list)  # (weight, title) for the reasons text
    affinity: float = 0.0

    @property
    def mean_score(self) -> float | None:
        return sum(self.scores) / len(self.scores) if self.scores else None

    def top_titles(self, n: int = 3) -> list[str]:
        return [t for _, t in sorted(self.titles, key=lambda x: -x[0])[:n]]


@dataclass
class Profile:
    entries: int
    genres: dict[str, Feature]
    tags: dict[str, Feature]
    staff: dict[str, Feature]
    favourites: list[dict[str, Any]]   # highest-weight entries
    disliked: list[dict[str, Any]]     # dropped or low-scored
    formats: set[str]

    def top(self, kind: str, n: int, positive: bool = True) -> list[Feature]:
        items = getattr(self, kind).values()
        ranked = sorted(items, key=lambda f: -f.affinity)
        return [f for f in ranked if f.affinity > 0][:n] if positive else ranked[:n]


def _add(table: dict[str, Feature], key: str, label: str, w: float, relevance: float, score: float | None,
         title: str) -> None:
    """`relevance` is how much the entry counts (tag rank, times SCORED_BOOST for scored entries)."""
    f = table.get(key)
    if f is None:
        f = table[key] = Feature(key, label)
    f.count += 1
    f.weight_sum += w * relevance
    f.relevance_sum += relevance
    if score:
        f.scores.append(score)
    f.titles.append((w, title))


def _finish(table: dict[str, Feature], total: int, mean: float) -> None:
    for f in table.values():
        preference = (f.weight_sum + PRIOR * mean) / (f.relevance_sum + PRIOR) - mean
        prevalence = math.sqrt(f.count / max(total, 1))
        f.affinity = PREVALENCE * prevalence + PREFERENCE * preference


def build_profile(entries: Iterable[dict[str, Any]]) -> Profile:
    """`entries` are stats.entry_rows rows (genres, tags as names with ranks, staff as dicts)."""
    genres: dict[str, Feature] = {}
    tags: dict[str, Feature] = {}
    staff: dict[str, Feature] = {}
    weighted: list[tuple[float, dict[str, Any]]] = []
    format_counts: dict[str, int] = {}
    n = 0
    weight_total = boost_total = 0.0
    for e in entries:
        w = entry_weight(e.get("status"), e.get("score"), e.get("progress"))
        if w is None:
            continue
        n += 1
        boost = SCORED_BOOST if e.get("score") else 1.0
        weight_total += w * boost
        boost_total += boost
        title = e.get("title") or f"#{e.get('media_id')}"
        weighted.append((w, e))
        format_counts[e.get("format") or ""] = format_counts.get(e.get("format") or "", 0) + 1
        for g in e.get("genres") or []:
            _add(genres, g, g, w, boost, e.get("score"), title)
        for t in e.get("tag_ranks") or []:
            _add(tags, t["name"], t["name"], w, boost * (t.get("rank") or 50) / 100, e.get("score"), title)
        seen: set[int] = set()
        for p in e.get("staff") or []:
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            _add(staff, str(p["id"]), p["name"], w, boost, e.get("score"), title)
    mean = weight_total / boost_total if boost_total else 0.0
    for table in (genres, tags, staff):
        _finish(table, n, mean)
    weighted.sort(key=lambda x: -x[0])

    def brief(w: float, e: dict[str, Any]) -> dict[str, Any]:
        return {"media_id": e.get("media_id"), "title": e.get("title"), "score": e.get("score"),
                "status": e.get("status"), "progress": e.get("progress"), "weight": w}

    favourites = [brief(w, e) for w, e in weighted if w > 0][:40]
    disliked = [brief(w, e) for w, e in reversed(weighted) if w < 0][:15]
    # Formats you actually read (a handful of light novels shouldn't bring in more light novels).
    formats = {f for f, c in format_counts.items() if f and c >= max(3, 0.02 * n)}
    return Profile(n, genres, tags, staff, favourites, disliked, formats or {"MANGA"})
