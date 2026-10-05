"""Ranking candidate series against your profile. Pure: no I/O.

Four signals, each scaled so the best candidate scores 1:
- genre: the candidate's best-liked genres (a genre you mostly drop counts against it);
- tag: its tags, weighted by how central each tag is to the series (AniList's tag rank);
- staff: whether its writer or artist made series you liked;
- community: AniList users recommend it from series you liked, weighted by their votes.
AniList's average score nudges every ranking a little. "Overall" blends all four.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from mdal.recommend.profile import Profile

KINDS = ("genre", "tag", "staff", "community")
OVERALL_WEIGHTS = {"genre": 0.2, "tag": 0.35, "staff": 0.15, "community": 0.3}
TOP_TAGS = 5


@dataclass
class Rec:
    media_id: int
    title: str
    url: str | None
    cover: str | None
    year: int | None
    format: str | None
    country: str | None
    chapters: int | None
    status: str | None
    genres: list[str]
    tags: list[dict[str, Any]]
    staff: list[dict[str, Any]]
    mean_score: int | None
    popularity: int | None
    is_adult: bool
    sources: list[dict[str, Any]]
    raw: dict[str, float] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)   # normalised per kind, plus "overall"
    reasons: dict[str, list[str]] = field(default_factory=dict)
    matched: dict[str, list[str]] = field(default_factory=dict)  # kind -> feature keys it shares with you

    @property
    def quality(self) -> float:
        return (self.mean_score or 60) / 100


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def make_rec(row: dict[str, Any]) -> Rec:
    return Rec(
        media_id=row["media_id"],
        title=row.get("romaji") or row.get("english") or row.get("native") or f"#{row['media_id']}",
        url=row.get("site_url"), cover=row.get("cover_url"), year=row.get("start_year"),
        format=row.get("format"), country=row.get("country"), chapters=row.get("chapters"), status=row.get("status"),
        genres=_json(row.get("genres"), []), tags=_json(row.get("tags"), []), staff=_json(row.get("staff_roles"), []),
        mean_score=row.get("mean_score"), popularity=row.get("popularity"), is_adult=bool(row.get("is_adult")),
        sources=_json(row.get("sources"), []),
    )


def _stats(f: Any) -> str:
    mean = f.mean_score
    return f"{f.count} on your list" + (f", avg score {mean:.0f}" if mean else "")


def score_recs(profile: Profile, rows: list[dict[str, Any]], weights: dict[int, float]) -> list[Rec]:
    """Score every candidate. `weights` maps your list's media ids to their entry weight."""
    recs = [make_rec(r) for r in rows]
    for r in recs:
        # genre
        found = sorted(((profile.genres[g].affinity, g) for g in r.genres if g in profile.genres), reverse=True)
        top = [a for a, _ in found[:3]]
        worst = min([a for a, _ in found] + [0.0])
        r.raw["genre"] = sum(top) / 3 + 0.5 * worst
        r.matched["genre"] = [g for a, g in found if a > 0][:4]
        r.reasons["genre"] = [f"{g}: {_stats(profile.genres[g])}" for g in r.matched["genre"][:3]]

        # tag
        contributions = sorted((((t.get("rank") or 50) / 100 * profile.tags[t["name"]].affinity, t["name"])
                                for t in r.tags if t["name"] in profile.tags), reverse=True)
        positive = [c for c in contributions if c[0] > 0][:TOP_TAGS]
        negative = sum(c for c, _ in contributions if c < 0)
        r.raw["tag"] = sum(c for c, _ in positive) / TOP_TAGS + 0.5 * negative
        r.matched["tag"] = [name for _, name in positive]
        r.reasons["tag"] = [f"{name}: {_stats(profile.tags[name])}" for name in r.matched["tag"][:3]]

        # staff
        people = sorted(((profile.staff[str(p["id"])].affinity, p) for p in r.staff if str(p["id"]) in profile.staff),
                        key=lambda x: -x[0])
        liked = [(a, p) for a, p in people if a > 0]
        r.raw["staff"] = (liked[0][0] + 0.25 * sum(a for a, _ in liked[1:])) if liked else 0.0
        r.matched["staff"] = [str(p["id"]) for _, p in liked]
        r.reasons["staff"] = [
            f"{p['name']} ({p.get('role') or 'creator'}), who made {', '.join(profile.staff[str(p['id'])].top_titles(2))}"
            for _, p in liked[:2]]

        # community
        community = [s for s in r.sources if s.get("kind") == "community"]
        total = 0.0
        votes: list[tuple[float, dict[str, Any]]] = []
        for s in community:
            w = weights.get(int(s["via"]), 0.0)
            value = math.log1p(max(s.get("rating") or 0, 0)) * max(w, 0.1)
            total += value
            votes.append((value, s))
        r.raw["community"] = total
        votes.sort(key=lambda x: -x[0])
        r.matched["community"] = [str(s["via"]) for _, s in votes[:3]]
        r.reasons["community"] = [f"recommended by {s.get('site') or 'AniList'} readers of {s.get('label')} ({s.get('rating') or 0:+d})"
                                  for _, s in votes[:2]]

    for kind in KINDS:
        top = max((r.raw[kind] for r in recs), default=0.0)
        for r in recs:
            r.scores[kind] = max(-1.0, r.raw[kind] / top) if top > 0 else 0.0
    for r in recs:
        blend = sum(OVERALL_WEIGHTS[k] * r.scores[k] for k in KINDS)
        r.scores["overall"] = blend * (0.6 + 0.4 * r.quality)
        for kind in KINDS:
            r.scores[f"{kind}_ranked"] = r.scores[kind] * (0.7 + 0.3 * r.quality)
    return recs


def ranked(recs: list[Rec], kind: str, n: int | None = None) -> list[Rec]:
    """Best first. A per-kind list only includes candidates that actually share something of that kind."""
    key = "overall" if kind == "overall" else f"{kind}_ranked"
    pool = recs if kind == "overall" else [r for r in recs if r.matched.get(kind)]
    out = sorted(pool, key=lambda r: -r.scores[key])
    return out[:n] if n else out
