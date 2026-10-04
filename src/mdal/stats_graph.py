"""Connections: how the tags (or genres) on your list go together. Pure: works on stats.entry_rows rows.

Two themes are linked when they share series more often than chance would give them: the lift,
together / (expected if unrelated), is at least LIFT_MIN, with at least MIN_SHARED series in common.
Each theme keeps only its PER_NODE strongest links, so the graph shows clusters instead of a hairball.
Size is how many series have it; shade is how much you like it (the same affinity Discover uses).
"""

from __future__ import annotations

import math
from collections import Counter
from itertools import combinations
from typing import Any

from mdal.recommend.profile import build_profile

MAX_NODES = {"tags": 48, "genres": 19}
MIN_COUNT = {"tags": 5, "genres": 3}
MIN_SHARED, LIFT_MIN, PER_NODE = 3, 1.25, 4
TONES = 5


def _features(row: dict[str, Any], kind: str) -> set[str]:
    return set(row["tags"] if kind == "tags" else row["genres"])


def theme_graph(rows: list[dict[str, Any]], kind: str = "tags") -> dict[str, Any]:
    """{nodes, links, pairs, total}. Node ids are the tag/genre names (also the drill-down filter value)."""
    rows = [r for r in rows if _features(r, kind)]
    total = len(rows)
    counts: Counter = Counter()
    for r in rows:
        counts.update(_features(r, kind))
    floor = max(MIN_COUNT[kind], math.ceil(0.01 * total))
    names = [n for n, c in counts.most_common() if c >= floor][:MAX_NODES[kind]]
    if len(names) < 2:
        return {"nodes": [], "links": [], "pairs": [], "total": total}
    chosen = set(names)
    together: Counter = Counter()
    for r in rows:
        present = sorted(_features(r, kind) & chosen)
        together.update(combinations(present, 2))

    def lift(a: str, b: str, shared: int) -> float:
        return shared * total / (counts[a] * counts[b])

    candidates = []
    for (a, b), shared in together.items():
        value = lift(a, b, shared)
        if shared >= MIN_SHARED and value >= LIFT_MIN:
            candidates.append((math.log(value) * math.sqrt(shared), a, b, shared, value))
    best: dict[str, list[tuple]] = {n: [] for n in names}
    for c in candidates:
        best[c[1]].append(c)
        best[c[2]].append(c)
    kept: dict[tuple[str, str], tuple] = {}
    for n in names:
        for c in sorted(best[n], reverse=True)[:PER_NODE]:
            kept[(c[1], c[2])] = c
    # No theme left floating: attach a lonely one to the series it shares most with.
    linked = {x for a, b in kept for x in (a, b)}
    for n in names:
        if n in linked:
            continue
        options = [((a, b), s) for (a, b), s in together.items() if n in (a, b)]
        if options:
            (a, b), shared = max(options, key=lambda o: o[1])
            kept[(a, b)] = (0.0, a, b, shared, lift(a, b, shared))

    profile = build_profile(rows)
    table = profile.tags if kind == "tags" else profile.genres
    affinity = {n: table[n].affinity if n in table else None for n in names}
    ranked = sorted((a, n) for n, a in affinity.items() if a is not None)
    tone = {n: 1 + min(TONES - 1, i * TONES // max(len(ranked), 1)) for i, (_, n) in enumerate(ranked)}

    neighbours: dict[str, list[tuple[float, str]]] = {n: [] for n in names}
    for (a, b), c in kept.items():
        neighbours[a].append((c[4], b))
        neighbours[b].append((c[4], a))
    biggest = counts[names[0]]
    nodes = []
    for n in names:
        scored = [r["score"] for r in rows if r.get("score") and n in _features(r, kind)]
        often = [m for _, m in sorted(neighbours[n], reverse=True)[:3]]
        tip = [n, f"{counts[n]:,} series ({100 * counts[n] / total:.0f}% of these)"]
        if len(scored) >= 3:
            tip.append(f"mean score {sum(scored) / len(scored):.0f} ({len(scored)} scored)")
        if often:
            tip.append("often with " + ", ".join(often))
        nodes.append({"id": n, "label": n, "kind": "tag", "size": counts[n] / biggest, "tone": tone.get(n, 0),
                      "count": counts[n], "tip": tip})
    top_score = max((c[0] for c in kept.values()), default=1) or 1
    links = [{"source": a, "target": b, "weight": round(0.15 + 0.85 * max(0.0, c[0]) / top_score, 3)}
             for (a, b), c in kept.items()]
    pairs = sorted(({"a": a, "b": b, "shared": c[3], "lift": c[4]} for (a, b), c in kept.items() if c[0] > 0),
                   key=lambda p: (-p["lift"], -p["shared"]))[:15]
    return {"nodes": nodes, "links": links, "pairs": pairs, "total": total}
