"""Your list next to a friend's. Pure: no I/O.

- in common: series on both lists (Planning aside);
- score agreement: on series you both scored, how far apart your scores are, and whether you rank them
  alike (a correlation, once there are at least 5);
- genre match: how alike the mix of genres you each read is (cosine of the two genre shares);
- picks: what they loved that you haven't read, and the other way round;
- disagreements: the series you scored furthest apart.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any

from mdal import titles

LOVED = 80
READ = ("CURRENT", "COMPLETED", "PAUSED", "DROPPED", "REPEATING")


def _genres(media: Any) -> list[str]:
    return json.loads(media["genres"] or "[]") if media is not None else []


def _title(media: Any) -> str:
    return titles.pick(media["romaji"], media["english"], media["native"], "?") if media is not None else "?"


def pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 5:
        return None
    xs, ys = zip(*pairs)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / (sx * sy)


def cosine(a: Counter[str], b: Counter[str]) -> float:
    keys = set(a) | set(b)
    dot = sum(a[k] * b[k] for k in keys)
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def compare(mine: list[dict[str, Any]], theirs: list[dict[str, Any]], media: dict[int, Any]) -> dict[str, Any]:
    """`mine`: entry_rows; `theirs`: [{media_id, status, score, progress}]; `media`: al_media rows for theirs."""
    my = {e["media_id"]: e for e in mine}
    their = {e["media_id"]: e for e in theirs}
    my_read = {m for m, e in my.items() if e["status"] in READ}
    their_read = {m for m, e in their.items() if e["status"] in READ}
    shared = my_read & their_read
    union = my_read | their_read

    both_scored = [(my[m]["score"], their[m]["score"]) for m in shared if my[m].get("score") and their[m].get("score")]
    gap = sum(abs(a - b) for a, b in both_scored) / len(both_scored) if both_scored else None

    def genre_mix(ids: set[int], rows: dict[int, Any]) -> Counter[str]:
        c: Counter[str] = Counter()
        for m in ids:
            c.update(rows[m].get("genres") or [] if rows is my else _genres(media.get(m)))
        return c
    mine_mix, their_mix = genre_mix(my_read, my), genre_mix(their_read, their)

    def they_loved(e: dict[str, Any]) -> bool:
        return (e.get("score") or 0) >= LOVED

    their_picks = sorted((e for m, e in their.items() if m not in my_read and they_loved(e)),
                         key=lambda e: -(e.get("score") or 0))
    my_picks = sorted((e for m, e in my.items() if m not in their_read and (e.get("score") or 0) >= LOVED),
                      key=lambda e: -(e.get("score") or 0))
    both_loved = sorted((m for m in shared if (my[m].get("score") or 0) >= LOVED and (their[m].get("score") or 0) >= LOVED),
                        key=lambda m: -(my[m]["score"] + their[m]["score"]))
    apart = sorted(((m, my[m]["score"] - their[m]["score"]) for m in shared
                    if my[m].get("score") and their[m].get("score")), key=lambda x: -abs(x[1]))
    genre_rows = []
    top = [g for g, _ in (mine_mix + their_mix).most_common(10)]
    my_total, their_total = max(sum(mine_mix.values()), 1), max(sum(their_mix.values()), 1)
    for g in top:
        genre_rows.append({"genre": g, "mine": 100 * mine_mix[g] / my_total, "theirs": 100 * their_mix[g] / their_total})
    return {
        "mine": len(my_read), "theirs": len(their_read), "shared": len(shared),
        "overlap": 100 * len(shared) / len(union) if union else 0.0,
        "scored_both": len(both_scored), "gap": gap / 10 if gap is not None else None,
        "agreement": pearson(both_scored),
        "genre_match": 100 * cosine(mine_mix, their_mix),
        "genres": genre_rows,
        "their_picks": [{"media_id": e["media_id"], "title": _title(media.get(e["media_id"])),
                         "cover": media[e["media_id"]]["cover_url"] if e["media_id"] in media else None,
                         "score": e["score"], "planning": e["media_id"] in my} for e in their_picks[:18]],
        "my_picks": [{"media_id": e["media_id"], "title": e["title"], "cover": e.get("cover_url"), "score": e["score"]}
                     for e in my_picks[:12]],
        "both_loved": [{"media_id": m, "title": my[m]["title"], "cover": my[m].get("cover_url"),
                        "mine": my[m]["score"], "theirs": their[m]["score"]} for m in both_loved[:12]],
        "apart": [{"media_id": m, "title": my[m]["title"], "cover": my[m].get("cover_url"),
                   "mine": my[m]["score"], "theirs": their[m]["score"]} for m, _ in apart[:8]],
    }
