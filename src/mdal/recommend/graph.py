"""The recommendation map: recommendations linked to what they share with your list. Pure: no I/O.

Node kinds: "rec" (a recommendation, drawn as its cover), "series" (a series on your list that AniList
readers recommend it from, also a cover), "theme" (a genre or tag) and "creator" (both labelled pills).
Each recommendation keeps only its strongest few links, and a theme must explain at least two
recommendations, so the map shows structure rather than every possible edge.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from mdal.recommend.profile import Profile
from mdal.recommend.score import Rec

MAX_THEMES, MAX_CREATORS, MAX_SERIES = 10, 8, 10
THEMES_PER_REC, CREATORS_PER_REC, SERIES_PER_REC = 2, 2, 2


def build_graph(profile: Profile, recs: list[Rec], series: dict[int, tuple[str, str | None]]) -> dict[str, Any]:
    """`recs` best first; `series` maps your list's media ids to (title, cover) for community links."""
    links: list[tuple[str, str, str, float]] = []
    for r in recs:
        rid = f"r{r.media_id}"
        themes = (r.matched.get("tag", []) + r.matched.get("genre", []))[:THEMES_PER_REC]
        links += [(rid, f"t:{t}", "theme", 0.5) for t in themes]
        links += [(rid, f"c:{s}", "creator", 0.8) for s in r.matched.get("staff", [])[:CREATORS_PER_REC]]
        ratings = {str(s["via"]): s.get("rating") or 0 for s in r.sources if s.get("kind") == "community"}
        top_rating = max(ratings.values(), default=1) or 1
        links += [(rid, f"s:{v}", "series", 0.4 + 0.6 * max(0, ratings.get(v, 0)) / top_rating)
                  for v in r.matched.get("community", [])[:SERIES_PER_REC]]

    degree = Counter(target for _, target, _, _ in links)
    keep: set[str] = set()
    for prefix, limit, minimum in (("t:", MAX_THEMES, 2), ("c:", MAX_CREATORS, 1), ("s:", MAX_SERIES, 1)):
        hubs = sorted((n for n in degree if n.startswith(prefix) and degree[n] >= minimum), key=lambda n: (-degree[n], n))
        keep.update(hubs[:limit])
    links = [link for link in links if link[1] in keep]
    linked = Counter(source for source, _, _, _ in links)

    nodes: list[dict[str, Any]] = []
    top = max((r.scores.get("overall", 0) for r in recs), default=1) or 1
    for rank, r in enumerate(recs, start=1):
        why = next((reasons[0] for k in ("community", "staff", "tag", "genre") if (reasons := r.reasons.get(k))), None)
        nodes.append({
            "id": f"r{r.media_id}", "kind": "rec", "label": r.title, "rank": rank, "image": r.cover,
            "href": r.url, "external": True, "size": max(0.0, r.scores.get("overall", 0)) / top,
            "tip": [r.title, f"Recommendation #{rank}" + (f" · AniList avg {r.mean_score}" if r.mean_score else "")]
                   + ([why] if why else []) + ([] if linked[f"r{r.media_id}"] else ["(no shared hub on the map)"]),
        })
    for hub in sorted(keep, key=lambda n: (-degree[n], n)):
        key = hub[2:]
        shared = f"links {degree[hub]} recommendation{'s' if degree[hub] != 1 else ''}"
        if hub.startswith("t:"):
            f = profile.tags.get(key) or profile.genres.get(key)
            nodes.append({"id": hub, "kind": "theme", "label": key, "size": min(1.0, degree[hub] / 6),
                          "tip": [key, f"{f.count} on your list · {shared}" if f else shared]})
        elif hub.startswith("c:"):
            f = profile.staff.get(key)
            name = f.label if f else key
            nodes.append({"id": hub, "kind": "creator", "label": name, "href": f"https://anilist.co/staff/{key}",
                          "external": True, "size": min(1.0, degree[hub] / 4),
                          "tip": [name, (f"made {', '.join(f.top_titles(2))} · " if f else "") + shared]})
        else:
            title, cover = series.get(int(key), (f"#{key}", None))
            nodes.append({"id": hub, "kind": "series", "label": title, "image": cover,
                          "href": f"https://anilist.co/manga/{key}", "external": True, "size": 0.3,
                          "tip": [title, f"On your list · AniList readers recommend {degree[hub]} of these from it"]})
    return {"nodes": nodes, "links": [{"source": s, "target": t, "kind": k, "weight": w} for s, t, k, w in links]}
