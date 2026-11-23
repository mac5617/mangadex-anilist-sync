"""Which titles For you offers the model next, so each ask moves on to ones you haven't been shown. Pure: no I/O.

A title counts as seen once it was one of the model's picks. It stays out until the good matches run out; then the
oldest picks come back first.
"""

from __future__ import annotations

from typing import Any

from mdal.recommend.llm import PICKS
from mdal.recommend.score import Rec, ranked

GOOD_MATCH = 0.35         # overall score; about the best 135 of 233 candidates on a real library
RETURNING = PICKS * 2     # how many to offer once earlier picks are coming back, so the newest stay out


def is_history(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in value)


def stored_picks(llm: Any) -> list[dict[str, Any]]:
    """The well-formed picks in a saved answer; a restored backup can hold anything."""
    picks = llm.get("picks") if isinstance(llm, dict) else None
    return [p for p in picks if isinstance(p, dict) and is_history([p.get("id")])] if isinstance(picks, list) else []


def past_picks(stored: Any, llm: Any) -> list[int]:
    """AniList ids already picked, oldest first. Before any history exists, the stored picks count as seen."""
    return list(stored) if is_history(stored) else [p["id"] for p in stored_picks(llm)]


def remember(past: list[int], picks: list[int]) -> list[int]:
    """The history after these picks: each is now the newest, wherever it was."""
    chosen = set(picks)
    return [i for i in past if i not in chosen] + picks


def _unseen(order: list[Rec], past: list[int]) -> list[Rec]:
    """Titles not picked yet, best first. Two AniList entries with one name (editions) count as one title."""
    ids = set(past)
    taken = {r.title.casefold() for r in order if r.media_id in ids}
    out = []
    for r in order:
        if r.title.casefold() not in taken:
            taken.add(r.title.casefold())
            out.append(r)
    return out


def also_worth(recs: list[Rec], past: list[int], shown: set[int]) -> list[Rec]:
    """The next best good matches for the list under the picks."""
    return [r for r in _unseen(ranked(recs, "overall"), past)
            if r.scores["overall"] >= GOOD_MATCH and r.media_id not in shown]


def shortlist(recs: list[Rec], past: list[int], size: int) -> list[Rec]:
    """The best unseen good matches; once fewer than a full set remain, topped up with the oldest picks."""
    order = ranked(recs, "overall")
    seen = set(past)
    good = {r.media_id: r for r in order if r.scores["overall"] >= GOOD_MATCH}
    fresh = [r for r in _unseen(order, past) if r.media_id in good]
    if len(fresh) >= PICKS:
        return fresh[:size]
    chosen = (fresh + [good[i] for i in dict.fromkeys(past) if i in good])[:RETURNING]
    if len(chosen) < PICKS:          # a small pool: offer what there is, unseen first
        taken = {r.media_id for r in chosen}
        chosen += sorted((r for r in order if r.media_id not in taken), key=lambda r: r.media_id in seen)
    return chosen[:size]
