"""MangaUpdates details for one series: English releases, licensing, categories and recommendations.

The series is found by the MangaUpdates link MangaDex keeps (no search needed), else by one title
search checked against every known title. The useful parts are kept, compactly, in lookup_cache.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from mdal.clients.mangaupdates import MangaUpdatesClient
from mdal.db.repo import Repo
from mdal.matching.normalize import normalize

KEEP_FOR = timedelta(days=7)
CATEGORIES, RECOMMENDATIONS = 10, 10


def mu_id_from_link(value: str | None) -> int | None:
    """MangaDex keeps the id from the MangaUpdates URL: base 36 since 2022 ('671g9vp'). Old numeric ids belong
    to retired URLs and can't be converted, so those are looked up by title instead."""
    if not value or not re.fullmatch(r"[0-9a-z]+", value) or value.isdigit():
        return None
    return int(value, 36)


def pick_match(results: list[dict[str, Any]], titles: list[str], year: int | None) -> int | None:
    """The search result whose title (or the title it matched on) is one of ours; the right year breaks ties."""
    wanted = {normalize(t) for t in titles if t}
    best: tuple[int, int] | None = None
    for r in results:
        record = r.get("record") or {}
        if not ({normalize(record.get("title")), normalize(r.get("hit_title"))} & wanted):
            continue
        rank = 0 if year and str(year) == str(record.get("year")) else 1
        if best is None or rank < best[0]:
            best = (rank, record["series_id"])
    return best[1] if best else None


def summarize(s: dict[str, Any]) -> dict[str, Any]:
    publishers = s.get("publishers") or []
    english = [{"name": p.get("publisher_name"), "notes": p.get("notes") or ""}
               for p in publishers if (p.get("type") or "").lower() == "english"]
    categories = sorted(s.get("categories") or [], key=lambda c: -(c.get("votes") or 0))[:CATEGORIES]
    recs = [{"id": r.get("series_id"), "name": r.get("series_name"), "url": r.get("series_url"),
             "cover": ((r.get("series_image") or {}).get("url") or {}).get("thumb")}
            for r in (s.get("recommendations") or []) + (s.get("category_recommendations") or [])
            if r.get("series_name")]
    seen: set[Any] = set()
    recs = [r for r in recs if not (r["id"] in seen or seen.add(r["id"]))][:RECOMMENDATIONS]
    return {
        "id": s.get("series_id"), "url": s.get("url"), "title": s.get("title"), "type": s.get("type"),
        "year": s.get("year"), "status": s.get("status"), "latest_chapter": s.get("latest_chapter"),
        "completed": bool(s.get("completed")), "licensed": bool(s.get("licensed")),
        "rating": s.get("bayesian_rating"), "votes": s.get("rating_votes"), "english": english,
        "categories": [c.get("category") for c in categories if c.get("category")],
        "recommendations": recs,
    }


def fresh_enough(row: Any) -> bool:
    if row is None:
        return False
    fetched = datetime.fromisoformat(row["fetched_at"])
    return datetime.now(UTC) - fetched < KEEP_FOR


async def lookup_mu(client: MangaUpdatesClient, repo: Repo, key: str, titles: list[str], year: int | None,
                    link: str | None = None, *, force: bool = False) -> dict[str, Any] | None:
    """The cached MangaUpdates summary for a series, fetched when missing or a week old (1 or 2 requests)."""
    row = repo.cached(key, "mu")
    if fresh_enough(row) and not force:
        data = json.loads(row["data"]) if row["data"] else None
        if not data or all("cover" in r for r in data.get("recommendations") or []):
            return data          # (records saved before covers were kept are fetched again)
    series_id = mu_id_from_link(link)
    if series_id is None and titles:
        series_id = pick_match(await client.search(titles[0]), titles, year)
    data = await client.series(series_id) if series_id else None
    summary = summarize(data) if data else None
    repo.cache(key, "mu", summary)
    return summary
