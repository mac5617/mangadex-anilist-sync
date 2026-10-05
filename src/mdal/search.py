"""Finding a series by any of its titles. Pure: no I/O.

Every title a series is known by (romaji, English, native, synonyms, MangaDex titles) is normalised once;
a query matches whole-title, then title start, then words anywhere, in that order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mdal.matching.normalize import normalize


@dataclass
class Hit:
    key: str                  # 'al:<id>' or 'md:<uuid>'
    title: str
    cover: str | None
    meta: str
    group: str                # where it was found: "On your list", "MangaDex library", ...
    rank: int = 9
    names: list[str] = field(default_factory=list)


GROUP_ORDER = ("On your list", "MangaDex library", "New on MangaDex", "Recommended", "Seen elsewhere")


def match_rank(query: str, names: list[str]) -> int | None:
    """0 exact title, 1 a title starts with it, 2 every query word starts a word of one title; None no match."""
    q = normalize(query)
    if not q:
        return None
    words = q.split()
    best: int | None = None
    for name in names:
        n = normalize(name)
        if not n:
            continue
        if n == q:
            return 0
        if n.startswith(q):
            best = 1 if best is None else min(best, 1)
        elif best is None or best > 2:
            title_words = n.split()
            if all(any(t.startswith(w) for t in title_words) for w in words):
                best = 2
    return best


def search(query: str, candidates: list[Hit], limit: int = 40) -> list[Hit]:
    hits = []
    seen: set[str] = set()
    for c in candidates:
        if c.key in seen:
            continue
        rank = match_rank(query, c.names or [c.title])
        if rank is not None:
            c.rank = rank
            hits.append(c)
            seen.add(c.key)
    group = {g: i for i, g in enumerate(GROUP_ORDER)}
    return sorted(hits, key=lambda h: (h.rank, group.get(h.group, 9), h.title.casefold()))[:limit]


def anilist_hit(media: dict[str, Any], listed: set[int]) -> dict[str, Any]:
    """A result of AniList's own search, for the page."""
    title = media.get("title") or {}
    name = title.get("romaji") or title.get("english") or title.get("native") or f"#{media['id']}"
    meta = [str((media.get("startDate") or {}).get("year") or "") or None, media.get("format"), media.get("status")]
    return {"key": f"al:{media['id']}", "title": name, "english": title.get("english"),
            "cover": (media.get("coverImage") or {}).get("medium"),
            "meta": " · ".join(str(m).replace("_", " ").capitalize() for m in meta if m),
            "listed": media["id"] in listed}
