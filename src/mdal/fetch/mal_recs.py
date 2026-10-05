"""What MyAnimeList readers recommend: for one series (series pages, Ask) and for your favourites (For you).

MyAnimeList answers with its own ids and titles only, so the recommended series are then read from
AniList by MAL id (50 per request), with the tags and creators scoring needs. Needs MyAnimeList connected.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from mdal.clients.anilist import AniListClient
from mdal.clients.myanimelist import MalClient
from mdal.db.repo import Repo
from mdal.fetch.anilist_list import DESCRIPTION_FIELD
from mdal.fetch.anilist_recs import CANDIDATE_FIELDS, Collector
from mdal.fetch.mangaupdates import fresh_enough

log = logging.getLogger(__name__)

FAVOURITES = 15          # For you asks MyAnimeList about this many of your favourites (one request each)
PER_SERIES = 8
BY_MAL_PAGE = 50
BY_MAL_QUERY = (
    "query ($ids: [Int]) { Page(page: 1, perPage: %d) { media(idMal_in: $ids, type: MANGA) { %s %s } } }"
    % (BY_MAL_PAGE, CANDIDATE_FIELDS, DESCRIPTION_FIELD)
)


async def mal_recommendations(mal: MalClient, repo: Repo, mal_id: int) -> list[dict[str, Any]]:
    """[{mal_id, title, votes}] for one MAL id, cached for a week."""
    key = f"mal:{mal_id}"
    row = repo.cached(key, "mal")
    if fresh_enough(row):
        return json.loads(row["data"]) if row["data"] else []
    recs = [{"mal_id": r["node"]["id"], "title": r["node"].get("title"), "votes": r.get("num_recommendations") or 0}
            for r in await mal.recommendations(mal_id) if (r.get("node") or {}).get("id")][:PER_SERIES]
    repo.cache(key, "mal", recs)
    return recs


async def anilist_by_mal(anilist: AniListClient, repo: Repo, mal_ids: list[int]) -> dict[int, int]:
    """MAL id -> AniList media id for MANGA, caching each with tags, creators and description."""
    found: dict[int, int] = {}
    c = Collector(repo)
    for start in range(0, len(mal_ids), BY_MAL_PAGE):
        page = (await anilist.graphql(BY_MAL_QUERY, {"ids": mal_ids[start:start + BY_MAL_PAGE]})).get("Page") or {}
        for m in page.get("media") or []:
            if m.get("idMal") and m["idMal"] not in found:
                found[m["idMal"]] = m["id"]
                c.add(m, {})
    c.save()
    return found


async def favourites_on_mal(mal: MalClient, anilist: AniListClient, repo: Repo, favourites: list[dict[str, Any]],
                            progress: Callable[[str], None] = lambda _m: None) -> tuple[dict[int, list[dict[str, Any]]], int]:
    """({AniList id: [sources]}, requests) for For you: MyAnimeList readers' recommendations from your favourites."""
    with_mal = []
    for f in favourites:
        m = repo.media([f["media_id"]]).get(f["media_id"]) if f.get("media_id") else None
        if m and m["id_mal"]:
            with_mal.append((f, m["id_mal"]))
    with_mal = with_mal[:FAVOURITES]
    if not with_mal:
        return {}, 0
    progress(f"asking MyAnimeList what readers of {len(with_mal)} favourites recommend")
    sent = 0
    per_favourite: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for f, mal_id in with_mal:
        cached = fresh_enough(repo.cached(f"mal:{mal_id}", "mal"))
        per_favourite.append((f, await mal_recommendations(mal, repo, mal_id)))
        sent += 0 if cached else 1
    wanted = sorted({r["mal_id"] for _, recs in per_favourite for r in recs})
    progress("reading MyAnimeList's picks from AniList")
    mapping = await anilist_by_mal(anilist, repo, wanted) if wanted else {}
    sent += -(-len(wanted) // BY_MAL_PAGE)
    sources: dict[int, list[dict[str, Any]]] = {}
    for f, recs in per_favourite:
        for r in recs:
            if (media_id := mapping.get(r["mal_id"])) and media_id != f["media_id"]:
                sources.setdefault(media_id, []).append(
                    {"kind": "community", "via": f["media_id"], "label": f["title"], "rating": r["votes"], "site": "MyAnimeList"})
    return sources, sent
