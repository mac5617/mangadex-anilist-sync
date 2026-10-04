"""The recommendation map: recommendations linked to what they share with your list. Pure: no I/O.

Node kinds: "rec" (a recommendation), "series" (a series on your list that AniList readers recommend
it from), "theme" (a genre or tag) and "creator". Only connections that explain at least one shown
recommendation are kept, and hubs are limited so the map stays readable.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from mdal.recommend.profile import Profile
from mdal.recommend.score import Rec

MAX_THEMES, MAX_CREATORS, MAX_SERIES = 12, 10, 14


def build_graph(profile: Profile, recs: list[Rec], titles: dict[int, str]) -> dict[str, Any]:
    """`recs` best first; `titles` names your list's series (for community links)."""
    links: list[tuple[str, str, str]] = []
    for r in recs:
        rid = f"r{r.media_id}"
        for g in r.matched.get("genre", [])[:2]:
            links.append((rid, f"t:{g}", "theme"))
        for t in r.matched.get("tag", [])[:3]:
            links.append((rid, f"t:{t}", "theme"))
        for s in r.matched.get("staff", [])[:2]:
            links.append((rid, f"c:{s}", "creator"))
        for v in r.matched.get("community", [])[:2]:
            links.append((rid, f"s:{v}", "series"))

    degree = Counter(target for _, target, _ in links)
    keep: set[str] = set()
    for kind, prefix, limit, minimum in (("theme", "t:", MAX_THEMES, 2), ("creator", "c:", MAX_CREATORS, 1),
                                         ("series", "s:", MAX_SERIES, 1)):
        hubs = sorted((n for n in degree if n.startswith(prefix) and degree[n] >= minimum), key=lambda n: -degree[n])
        keep.update(hubs[:limit])
    links = [link for link in links if link[1] in keep]

    nodes: list[dict[str, Any]] = []
    top = max((r.scores.get("overall", 0) for r in recs), default=1) or 1
    for rank, r in enumerate(recs, start=1):
        nodes.append({"id": f"r{r.media_id}", "kind": "rec", "label": r.title, "url": r.url, "rank": rank,
                      "size": 0.45 + 0.55 * max(0.0, r.scores.get("overall", 0)) / top})
    for hub in sorted(keep, key=lambda n: -degree[n]):
        key = hub[2:]
        if hub.startswith("t:"):
            f = profile.tags.get(key) or profile.genres.get(key)
            label, kind, url = key, "theme", None
        elif hub.startswith("c:"):
            f = profile.staff.get(key)
            label, kind, url = (f.label if f else key), "creator", f"https://anilist.co/staff/{key}"
        else:
            f = None
            label, kind, url = titles.get(int(key), f"#{key}"), "series", f"https://anilist.co/manga/{key}"
        nodes.append({"id": hub, "kind": kind, "label": label, "url": url, "degree": degree[hub],
                      "count": f.count if f else None, "size": min(1.0, 0.35 + 0.1 * degree[hub])})
    return {"nodes": nodes, "links": [{"source": s, "target": t, "kind": k} for s, t, k in links]}
