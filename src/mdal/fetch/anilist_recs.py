"""Recommendation candidates from AniList. Read-only; about 10 requests per refresh.

- community: AniList users' recommendations for your favourite series (10 series per request);
- tag / genre: the highest-scored manga for your strongest tags and genres (4 per request);
- staff: the most popular manga by your favourite writers and artists (4 per request).
Every media found is cached in al_media with its tags, and with its creators when it has none yet.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from mdal.clients.anilist import AniListClient, AniListComplexityError, AniListGraphQLError
from mdal.db.repo import Repo, now_iso
from mdal.fetch.anilist_list import DESCRIPTION_FIELD, MEDIA_FIELDS, TAG_FIELDS, creator_roles, media_row
from mdal.recommend.profile import Profile

log = logging.getLogger(__name__)

CANDIDATE_FIELDS = f"{MEDIA_FIELDS} {TAG_FIELDS} staff(perPage: 4, sort: [RELEVANCE, ID]) {{ edges {{ role node {{ id name {{ full }} }} }} }}"
COMMUNITY_SERIES, COMMUNITY_PER_SERIES = 30, 6
TAG_COUNT, GENRE_COUNT, STAFF_COUNT = 8, 4, 8
PER_ROOT = 20


def community_query() -> str:
    return ("query ($ids: [Int]) { Page(page: 1, perPage: 10) { media(id_in: $ids, type: MANGA) { id "
            f"recommendations(sort: [RATING_DESC], perPage: {COMMUNITY_PER_SERIES}) {{ nodes {{ rating "
            f"mediaRecommendation {{ {CANDIDATE_FIELDS} }} }} }} }} }} }}")


def tag_query(n: int) -> str:
    params = ", ".join(f"$v{i}: String" for i in range(n))
    roots = " ".join(f"r{i}: Page(page: 1, perPage: {PER_ROOT}) {{ media(type: MANGA, tag_in: [$v{i}], minimumTagRank: 60, "
                     f"sort: [SCORE_DESC]) {{ {CANDIDATE_FIELDS} }} }}" for i in range(n))
    return f"query ({params}) {{ {roots} }}"


def genre_query(n: int) -> str:
    params = ", ".join(f"$v{i}: String" for i in range(n))
    roots = " ".join(f"r{i}: Page(page: 1, perPage: {PER_ROOT}) {{ media(type: MANGA, genre_in: [$v{i}], "
                     f"sort: [SCORE_DESC]) {{ {CANDIDATE_FIELDS} }} }}" for i in range(n))
    return f"query ({params}) {{ {roots} }}"


def staff_query(n: int) -> str:
    params = ", ".join(f"$v{i}: Int" for i in range(n))
    roots = " ".join(f"r{i}: Staff(id: $v{i}) {{ id staffMedia(type: MANGA, sort: [POPULARITY_DESC], perPage: 10) "
                     f"{{ nodes {{ {CANDIDATE_FIELDS} }} }} }}" for i in range(n))
    return f"query ({params}) {{ {roots} }}"


class Collector:
    """Gathers candidate media and why each was found."""

    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.fetched_at = now_iso()
        self.sources: dict[int, list[dict[str, Any]]] = {}
        self.media: dict[int, dict[str, Any]] = {}

    def add(self, media: dict[str, Any] | None, source: dict[str, Any]) -> None:
        if not media or media.get("type", "MANGA") != "MANGA" or not media.get("id"):
            return
        self.media[media["id"]] = media
        listed = self.sources.setdefault(media["id"], [])
        if source not in listed:
            listed.append(source)

    def save(self) -> None:
        self.repo.upsert_media([media_row(m, self.fetched_at) for m in self.media.values()])
        known = {r[0] for r in self.repo.conn.execute("SELECT media_id FROM al_media WHERE staff_roles IS NOT NULL")}
        self.repo.set_staff_roles({i: creator_roles(m.get("staff")) for i, m in self.media.items()
                                   if i not in known and "staff" in m})


async def _chunked(client: AniListClient, items: list[Any], size: int, run: Callable) -> int:
    """Run `run(client, chunk)` over chunks; halve the chunk on a complexity error. Returns requests sent."""
    sent, start = 0, 0
    while start < len(items):
        chunk = items[start:start + size]
        try:
            await run(client, chunk)
            sent += 1
            start += len(chunk)
        except AniListComplexityError:
            if size == 1:
                raise
            size = max(1, size // 2)
            log.warning("AniList rejected a recommendation query as too complex; retrying with %s per request", size)
    return sent


async def fetch_candidates(client: AniListClient, repo: Repo, profile: Profile,
                           progress: Callable[[str], None] = lambda _m: None) -> tuple[Collector, int]:
    c = Collector(repo)
    sent = 0

    favourites = [f for f in profile.favourites if f["weight"] > 0.3 and f["media_id"]][:COMMUNITY_SERIES]
    titles = {f["media_id"]: f["title"] for f in favourites}

    async def community(cl: AniListClient, ids: list[int]) -> None:
        page = (await cl.graphql(community_query(), {"ids": ids}))["Page"]
        for m in page.get("media") or []:
            for node in ((m.get("recommendations") or {}).get("nodes")) or []:
                c.add(node.get("mediaRecommendation"), {"kind": "community", "via": m["id"],
                                                        "label": titles.get(m["id"], f"#{m['id']}"),
                                                        "rating": node.get("rating") or 0})

    progress("asking AniList what readers of your favourites recommend")
    sent += await _chunked(client, [f["media_id"] for f in favourites], 10, community)

    def by_roots(query: Callable[[int], str], kind: str, labels: dict[str, str]):
        async def run(cl: AniListClient, keys: list[str]) -> None:
            variables = {f"v{i}": (int(k) if kind == "staff" else k) for i, k in enumerate(keys)}
            data = await cl.graphql(query(len(keys)), variables)
            for i, key in enumerate(keys):
                root = data.get(f"r{i}") or {}
                found = ((root.get("staffMedia") or {}).get("nodes") if kind == "staff" else root.get("media")) or []
                for media in found:
                    c.add(media, {"kind": kind, "via": key, "label": labels[key]})
        return run

    tags = [f for f in profile.top("tags", TAG_COUNT * 2) if f.count >= 3][:TAG_COUNT]
    genres = [f for f in profile.top("genres", GENRE_COUNT * 2) if f.count >= 3][:GENRE_COUNT]
    staff = [f for f in profile.top("staff", STAFF_COUNT * 2) if f.count >= 2][:STAFF_COUNT]
    progress("finding top series for your favourite tags and genres")
    sent += await _chunked(client, [f.key for f in tags], 4, by_roots(tag_query, "tag", {f.key: f.label for f in tags}))
    sent += await _chunked(client, [f.key for f in genres], 4, by_roots(genre_query, "genre", {f.key: f.label for f in genres}))
    progress("finding more by your favourite creators")
    sent += await _chunked(client, [f.key for f in staff], 4, by_roots(staff_query, "staff", {f.key: f.label for f in staff}))
    c.save()
    return c, sent


# ---- one series' neighbours, for the Ask chat ---------------------------------------------------

NEIGHBOURS = 15
FEW_NEIGHBOURS = 5      # below this many reader recommendations, also look up series sharing its main tags
SIMILAR_TAGS = 3


def neighbours_query(by_id: bool) -> str:
    """One series (by id, or the best title match) and what AniList readers recommend for it, with descriptions."""
    params, args = ("$id: Int", "id: $id") if by_id else ("$q: String", "search: $q")
    return (f"query ({params}) {{ Media({args}, type: MANGA) {{ {MEDIA_FIELDS} {TAG_FIELDS} {DESCRIPTION_FIELD} "
            f"recommendations(sort: [RATING_DESC], perPage: {NEIGHBOURS}) {{ nodes {{ rating "
            f"mediaRecommendation {{ {CANDIDATE_FIELDS} {DESCRIPTION_FIELD} }} }} }} }} }}")


SIMILAR_QUERY = (
    "query ($id: Int, $tags: [String], $genres: [String]) { Page(page: 1, perPage: %d) { "
    "media(type: MANGA, id_not: $id, tag_in: $tags, genre_in: $genres, minimumTagRank: 60, sort: [SCORE_DESC]) "
    "{ %s %s } } }" % (NEIGHBOURS, CANDIDATE_FIELDS, DESCRIPTION_FIELD)
)


async def fetch_neighbours(client: AniListClient, repo: Repo, *, media_id: int | None = None,
                           title: str | None = None) -> tuple[int | None, list[tuple[int, int]]]:
    """(the series' AniList id, [(media id, readers' rating)]). Everything found is cached.

    One request: the series and what its readers recommend. A new series often has few or none, so then
    a second request adds the top-scored manga sharing its main tags and a genre (rating 0)."""
    try:
        data = await client.graphql(neighbours_query(media_id is not None),
                                    {"id": media_id} if media_id is not None else {"q": title})
    except AniListGraphQLError as exc:
        if "not found" in str(exc).lower():      # no series by that id or title
            return None, []
        raise
    media = data.get("Media")
    if not media:
        return None, []
    c = Collector(repo)
    c.add(media, {})
    found: list[tuple[int, int]] = []
    for node in ((media.get("recommendations") or {}).get("nodes")) or []:
        rec = node.get("mediaRecommendation")
        if rec and rec.get("type", "MANGA") == "MANGA" and rec.get("id"):
            c.add(rec, {})
            found.append((rec["id"], node.get("rating") or 0))
    tags = [t["name"] for t in sorted((t for t in media.get("tags") or [] if not t.get("isMediaSpoiler")),
                                      key=lambda t: -(t.get("rank") or 0))][:SIMILAR_TAGS]
    if len(found) < FEW_NEIGHBOURS and (tags or media.get("genres")):
        variables = {"id": media["id"], "tags": tags or None, "genres": media.get("genres") or None}
        page = (await client.graphql(SIMILAR_QUERY, variables)).get("Page") or {}
        known = {m for m, _ in found}
        for rec in page.get("media") or []:
            if rec.get("id") and rec["id"] not in known:
                known.add(rec["id"])
                c.add(rec, {})
                found.append((rec["id"], 0))
    c.save()
    return media["id"], found
