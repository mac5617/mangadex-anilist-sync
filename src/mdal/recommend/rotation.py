"""Which titles For you offers the model next, so each ask moves on to ones you haven't been shown. Pure: no I/O.

A title counts as seen once it was one of the model's picks. It stays out until the good matches run out; then the
oldest picks come back first.
"""

from __future__ import annotations

from typing import Any

from mdal.recommend.fresh import NewRec, chapter_number, ranked_new
from mdal.recommend.llm import PICKS
from mdal.recommend.score import Rec, ranked

GOOD_MATCH = 0.35         # overall score; about the best 135 of 233 candidates on a real library
RETURNING = PICKS * 2     # how many to offer once earlier picks are coming back, so the newest stay out
GOOD_NEW_MATCH = 0.5      # New releases' scores are built differently; about the best 135 of 459 on a real library
NEW_HISTORY = 2000


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


# ---- New releases: a pick stays out until it gets a higher English chapter -------------------------


def is_new_history(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(p, dict) and isinstance(p.get("id"), str)
        and (p.get("chapter") is None or isinstance(p["chapter"], str)) for p in value)


def stored_new_picks(llm: Any) -> list[dict[str, Any]]:
    """The well-formed picks in a saved New releases answer; a restored backup can hold anything."""
    picks = llm.get("picks") if isinstance(llm, dict) else None
    return [p for p in picks if isinstance(p, dict) and isinstance(p.get("md_id"), str)] if isinstance(picks, list) else []


def new_remember(past: list[dict[str, Any]], picks: list[tuple[str, str | None]]) -> list[dict[str, Any]]:
    """The history after these picks, each now the newest and remembered at its current chapter."""
    chosen = {md_id for md_id, _ in picks}
    kept = [p for p in past if p["id"] not in chosen]
    return (kept + [{"id": md_id, "chapter": chapter} for md_id, chapter in picks])[-NEW_HISTORY:]


def _waiting(r: NewRec, picked: dict[str, dict[str, Any]]) -> bool:
    """Picked before, with no higher English chapter since."""
    if r.md_id not in picked:
        return False
    now, then = chapter_number(r.en_chapter), chapter_number(picked[r.md_id].get("chapter"))
    return now is None or (then is not None and now <= then)


def came_back(recs: list[NewRec], past: list[dict[str, Any]], md_ids: list[str]) -> bool:
    """Whether any of these is an earlier pick returning without a new chapter."""
    picked = {p["id"]: p for p in past}
    wanted = set(md_ids)
    return any(r.md_id in wanted and _waiting(r, picked) for r in recs)


def _new_unseen(order: list[NewRec], past: list[dict[str, Any]]) -> list[NewRec]:
    picked = {p["id"]: p for p in past}
    taken = {r.title.casefold() for r in order if _waiting(r, picked)}
    out = []
    for r in order:
        if r.title.casefold() not in taken:
            taken.add(r.title.casefold())
            out.append(r)
    return out


def also_new(recs: list[NewRec], past: list[dict[str, Any]], shown: set[str]) -> list[NewRec]:
    """The next best good series for the list under the picks."""
    return [r for r in _new_unseen(ranked_new(recs), past)
            if r.scores["overall"] >= GOOD_NEW_MATCH and r.md_id not in shown]


def new_shortlist(recs: list[NewRec], past: list[dict[str, Any]], size: int) -> list[NewRec]:
    """The best good series not waiting; once fewer than a full set remain, topped up with the oldest picks."""
    order = ranked_new(recs)
    good = {r.md_id: r for r in order if r.scores["overall"] >= GOOD_NEW_MATCH}
    fresh = [r for r in _new_unseen(order, past) if r.md_id in good]
    if len(fresh) >= PICKS:
        return fresh[:size]
    taken = {r.md_id for r in fresh}
    oldest = [good[i] for i in dict.fromkeys(p["id"] for p in past) if i in good and i not in taken]
    chosen = (fresh + oldest)[:RETURNING]
    if len(chosen) < PICKS:          # a small pool: offer what there is, unseen first
        picked = {p["id"] for p in past}
        have = {r.md_id for r in chosen}
        chosen += sorted((r for r in order if r.md_id not in have), key=lambda r: r.md_id in picked)
    return chosen[:size]
