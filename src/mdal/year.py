"""Year in review, from your AniList list's start and completion dates. Pure: no I/O.

"Finished" means completed in that year; "started" means started in it. Genres, tags and creators are
counted over both, and compared with your whole list to show what you read more of than usual.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MIN_LIFT_COUNT = 3     # a genre or tag needs this many series that year to count as "more than usual"
MIN_LIFT_SHARE = 0.05  # ...and to be at least this share of the year, so a niche tag on 4 of 600 series is not news


def years(entries: list[dict[str, Any]]) -> list[int]:
    found = {int(d[:4]) for e in entries for d in (e.get("started_at"), e.get("completed_at")) if d and d[:4].isdigit()}
    return sorted(found, reverse=True)


def _in(date: str | None, year: int) -> bool:
    return bool(date) and date[:4] == str(year)


def review(entries: list[dict[str, Any]], year: int) -> dict[str, Any]:
    finished = [e for e in entries if _in(e.get("completed_at"), year) and e["status"] in ("COMPLETED", "REPEATING")]
    started = [e for e in entries if _in(e.get("started_at"), year)]
    touched = {e["media_id"]: e for e in finished + started}.values()

    months = Counter(int(e["completed_at"][5:7]) for e in finished if len(e["completed_at"] or "") >= 7)
    def tally(rows, key):
        c: Counter[str] = Counter()
        for e in rows:
            names = [p["name"] for p in e.get("staff") or []] if key == "staff" else e.get(key) or []
            c.update(set(names))
        return c

    year_genres, year_tags, year_staff = tally(touched, "genres"), tally(touched, "tags"), tally(touched, "staff")
    all_genres, all_tags = tally(entries, "genres"), tally(entries, "tags")
    n_year, n_all = max(len(touched), 1), max(len(entries), 1)

    def lift(year_counts: Counter[str], all_counts: Counter[str]) -> list[dict[str, Any]]:
        """Read more than usual: this year's share against your whole list's."""
        out = []
        for name, count in year_counts.items():
            if count < MIN_LIFT_COUNT:
                continue
            share, usual = count / n_year, all_counts[name] / n_all
            if usual and share >= MIN_LIFT_SHARE and share > usual * 1.25:
                out.append({"name": name, "count": count, "share": share, "usual": usual, "lift": share / usual})
        return sorted(out, key=lambda x: -x["lift"])[:8]

    scored = sorted((e for e in finished if e.get("score")), key=lambda e: -e["score"])
    return {
        "year": year, "finished": len(finished), "started": len(started),
        "chapters": sum(e.get("progress") or 0 for e in finished),
        "mean_score": sum(e["score"] for e in scored) / len(scored) if scored else None,
        "months": [(MONTHS[m - 1], months.get(m, 0)) for m in range(1, 13)],
        "genres": year_genres.most_common(10), "tags": year_tags.most_common(12), "staff": year_staff.most_common(8),
        "more_than_usual": lift(year_genres, all_genres) + lift(year_tags, all_tags),
        "best": scored[:6],
        "longest": sorted(finished, key=lambda e: -(e.get("progress") or 0))[:3],
        "first": min(finished, key=lambda e: e["completed_at"], default=None),
        "last": max(finished, key=lambda e: e["completed_at"], default=None),
        "dropped": [e for e in entries if e["status"] == "DROPPED" and _in(e.get("started_at"), year)],
    }
