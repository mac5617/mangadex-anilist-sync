"""AniList reads: viewer, the user's manga list, bulk id lookups, batched title search.

Everything here is read-only. Query text is constant; user input only travels as variables.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mdal.clients.anilist import AniListClient, AniListComplexityError
from mdal.db.repo import Repo, now_iso

log = logging.getLogger(__name__)

MEDIA_FIELDS = (
    "id idMal type format status chapters countryOfOrigin startDate { year } "
    "title { romaji english native } synonyms coverImage { medium } siteUrl genres meanScore popularity isAdult"
)
STAFF_FIELDS = "staff(perPage: 3) { nodes { name { full native } } }"

DESCRIPTION_FIELD = "description(asHtml: false)"  # list request only: it feeds description matching
TAG_FIELDS = "tags { name rank category isMediaSpoiler isGeneralSpoiler }"
TAG_MIN_RANK = 50  # AniList's own stats count a tag only at 50%+ relevance
STAFF_ROLE_FIELDS = "staff(perPage: 6, sort: [RELEVANCE, ID]) { edges { role node { id name { full } } } }"
STAFF_PAGE = 25
CREATOR_ROLES = ("story", "art", "original creator", "original story")

VIEWER_QUERY = "query { Viewer { id name } }"


def list_query(with_tags: bool) -> str:
    tags = f" {TAG_FIELDS}" if with_tags else ""
    return (
        "query ($userId: Int) { MediaListCollection(userId: $userId, type: MANGA) { "
        "lists { name isCustomList entries { id status progress progressVolumes score(format: POINT_100) updatedAt notes "
        "startedAt { year month day } completedAt { year month day } "
        f"media {{ {MEDIA_FIELDS} {DESCRIPTION_FIELD}{tags} }} }} }} }} }}"
    )


LIST_QUERY = list_query(with_tags=True)
STAFF_QUERY = (
    "query ($ids: [Int], $perPage: Int) { Page(page: 1, perPage: $perPage) { "
    f"media(id_in: $ids, type: MANGA) {{ id {STAFF_ROLE_FIELDS} }} }} }}"
)
BY_IDS_QUERY = (
    "query ($ids: [Int], $perPage: Int) { Page(page: 1, perPage: $perPage) { "
    f"pageInfo {{ hasNextPage }} media(id_in: $ids, type: MANGA) {{ {MEDIA_FIELDS} }} }} }}"
)
BY_MAL_QUERY = (
    "query ($ids: [Int], $perPage: Int) { Page(page: 1, perPage: $perPage) { "
    f"pageInfo {{ hasNextPage }} media(idMal_in: $ids, type: MANGA) {{ {MEDIA_FIELDS} }} }} }}"
)


@dataclass
class ListSummary:
    entries: int
    custom_only: int


def fuzzy_date(d: dict[str, Any] | None) -> str | None:
    """AniList FuzzyDate -> 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'; None without a year."""
    if not d or not d.get("year"):
        return None
    parts = [f"{d['year']:04d}"]
    for key in ("month", "day"):
        if not d.get(key):
            break
        parts.append(f"{d[key]:02d}")
    return "-".join(parts)


def media_row(media: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    title = media.get("title") or {}
    staff_names: list[str] | None = None  # None = not requested; the repo keeps cached staff
    if "staff" in media:
        staff_names = []
        for node in ((media.get("staff") or {}).get("nodes")) or []:
            for name in ((node or {}).get("name") or {}).values():
                if name and name not in staff_names:
                    staff_names.append(name)
    return {
        "media_id": media["id"],
        "id_mal": media.get("idMal"),
        "type": media.get("type"),
        "format": media.get("format"),
        "status": media.get("status"),
        "chapters": media.get("chapters"),
        "country": media.get("countryOfOrigin"),
        "start_year": (media.get("startDate") or {}).get("year"),
        "romaji": title.get("romaji"),
        "english": title.get("english"),
        "native": title.get("native"),
        "synonyms": json.dumps(media.get("synonyms") or [], ensure_ascii=False),
        "staff": None if staff_names is None else json.dumps(staff_names, ensure_ascii=False),
        "genres": json.dumps(media.get("genres") or [], ensure_ascii=False),
        # None = not part of this request: the repo keeps what it already has
        "tags": json.dumps(clean_tags(media["tags"]), ensure_ascii=False) if "tags" in media else None,
        "description": plain_text(media.get("description")) if "description" in media else None,
        "mean_score": media.get("meanScore"),
        "popularity": media.get("popularity"),
        "is_adult": None if media.get("isAdult") is None else int(bool(media["isAdult"])),
        "cover_url": (media.get("coverImage") or {}).get("medium"),
        "site_url": media.get("siteUrl"),
        "fetched_at": fetched_at,
    }


HTML_TAG = re.compile(r"<[^>]+>")
LINE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
# The publisher credit and any notes after it ("Note: Includes eight extra chapters.") aren't the story.
SOURCE_NOTE = re.compile(r"(\(\s*source\s*:|^\s*notes?\s*:).*", re.IGNORECASE | re.DOTALL | re.MULTILINE)


def plain_text(description: str | None) -> str:
    """AniList's description without HTML or a trailing "(Source: ...)". "" when there is none (fetched, empty)."""
    if not description:
        return ""
    text = HTML_TAG.sub("", LINE_BREAK.sub("\n", description))
    text = SOURCE_NOTE.sub("", html.unescape(text).strip())
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def clean_tags(tags: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Relevant, non-spoiler tags only."""
    return [{"name": t["name"], "rank": t.get("rank") or 0, "category": t.get("category")}
            for t in tags or []
            if (t.get("rank") or 0) >= TAG_MIN_RANK and not t.get("isMediaSpoiler") and not t.get("isGeneralSpoiler")]


def creator_roles(staff: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Story/art creators (translators, letterers and editors left out)."""
    out = []
    for edge in (staff or {}).get("edges") or []:
        role, node = edge.get("role") or "", edge.get("node") or {}
        if node.get("id") and any(r in role.lower() for r in CREATOR_ROLES):
            out.append({"id": node["id"], "name": (node.get("name") or {}).get("full") or f"#{node['id']}", "role": role})
    return out


async def viewer(client: AniListClient) -> dict[str, Any]:
    return (await client.graphql(VIEWER_QUERY))["Viewer"]


async def fetch_list(client: AniListClient, repo: Repo, user_id: int) -> ListSummary:
    """One request. Replaces the al_entry snapshot; every list, custom ones included, de-duplicated by entry id."""
    fetched_at = now_iso()
    try:
        data = await client.graphql(LIST_QUERY, {"userId": user_id})
    except AniListComplexityError:
        # Tags make the one list request much bigger; without them the sync still works.
        log.warning("AniList rejected the list request with tags as too complex; fetching without tags")
        data = await client.graphql(list_query(with_tags=False), {"userId": user_id})
    lists = (data.get("MediaListCollection") or {}).get("lists") or []
    entries: dict[int, dict[str, Any]] = {}
    media: dict[int, dict[str, Any]] = {}
    in_status_list: set[int] = set()
    for group in lists:
        for entry in group.get("entries") or []:
            entries[entry["id"]] = entry
            media[entry["media"]["id"]] = entry["media"]
            if not group.get("isCustomList"):
                in_status_list.add(entry["id"])
    repo.upsert_media([media_row(m, fetched_at) for m in media.values()])
    repo.replace_al_entries([
        {"entry_id": e["id"], "media_id": e["media"]["id"], "status": e["status"],
         "progress": e.get("progress") or 0, "fetched_at": fetched_at,
         "score": e.get("score") or None,  # AniList reports 0 for "no score"
         "progress_volumes": e.get("progressVolumes"),
         "started_at": fuzzy_date(e.get("startedAt")), "completed_at": fuzzy_date(e.get("completedAt")),
         "updated_at": e.get("updatedAt") or None, "notes": e.get("notes") or ""}
        for e in entries.values()
    ])
    return ListSummary(entries=len(entries), custom_only=len(set(entries) - in_status_list))


async def _lookup(client: AniListClient, repo: Repo, query: str, ids: list[int], page_size: int) -> list[dict[str, Any]]:
    fetched_at = now_iso()
    found: list[dict[str, Any]] = []
    for start in range(0, len(ids), page_size):
        chunk = ids[start : start + page_size]
        page = (await client.graphql(query, {"ids": chunk, "perPage": page_size}))["Page"]
        found += page.get("media") or []
    repo.upsert_media([media_row(m, fetched_at) for m in found])
    return found


async def media_by_ids(client: AniListClient, repo: Repo, ids: Iterable[int], page_size: int = 50) -> dict[int, Any]:
    """Validate AniList ids as existing MANGA media. Cached media cost nothing. Absent ids are invalid."""
    wanted = sorted(set(ids))
    cached = repo.media(wanted)
    missing = [i for i in wanted if i not in cached]
    if missing:
        await _lookup(client, repo, BY_IDS_QUERY, missing, page_size)
    rows = repo.media(wanted)
    return {i: r for i, r in rows.items() if r["type"] == "MANGA"}


async def media_by_mal_ids(client: AniListClient, repo: Repo, mal_ids: Iterable[int], page_size: int = 50) -> dict[int, list[Any]]:
    """MAL id -> AniList MANGA media (a list: several AniList entries can share one idMal)."""
    wanted = sorted(set(mal_ids))
    cached = repo.media_by_mal(wanted)
    missing = [i for i in wanted if i not in cached]
    if missing:
        await _lookup(client, repo, BY_MAL_QUERY, missing, page_size)
    rows = repo.media_by_mal(wanted)
    return {k: [r for r in v if r["type"] == "MANGA"] for k, v in rows.items() if any(r["type"] == "MANGA" for r in v)}


def search_query(n: int) -> str:
    params = ", ".join(f"$q{i}: String" for i in range(n))
    roots = " ".join(
        f"s{i}: Page(page: 1, perPage: 5) {{ media(search: $q{i}, type: MANGA) {{ {MEDIA_FIELDS} {STAFF_FIELDS} }} }}"
        for i in range(n)
    )
    return f"query ({params}) {{ {roots} }}"


async def search_batch(client: AniListClient, repo: Repo, titles: list[str], batch: int = 5) -> list[list[dict[str, Any]]]:
    """Title search, `batch` aliased Page roots per request. Results in input order; also cached in al_media."""
    fetched_at = now_iso()
    results: list[list[dict[str, Any]]] = []
    for start in range(0, len(titles), batch):
        chunk = titles[start : start + batch]
        data = await client.graphql(search_query(len(chunk)), {f"q{i}": t for i, t in enumerate(chunk)})
        for i in range(len(chunk)):
            media = (data.get(f"s{i}") or {}).get("media") or []
            repo.upsert_media([media_row(m, fetched_at) for m in media])
            results.append(media)
    return results


async def fetch_staff(client: AniListClient, repo: Repo, max_requests: int, progress=lambda _msg: None) -> int:
    """Backfill story/art staff for list media that have none yet, at most `max_requests` requests.

    Results are kept for good; later syncs only look up newly added series. Returns media left to fetch.
    """
    missing = [r[0] for r in repo.conn.execute(
        "SELECT m.media_id FROM al_media m JOIN al_entry e ON e.media_id = m.media_id "
        "WHERE m.staff_roles IS NULL ORDER BY m.media_id")]
    return await fetch_staff_for(client, repo, missing, max_requests, progress)


async def fetch_staff_for(client: AniListClient, repo: Repo, media_ids: list[int], max_requests: int,
                          progress=lambda _msg: None) -> int:
    """Staff for these media, in the given order, 25 per request, at most `max_requests`. Returns ids left."""
    batches = [media_ids[i:i + STAFF_PAGE] for i in range(0, len(media_ids), STAFF_PAGE)][:max_requests]
    for n, ids in enumerate(batches, start=1):
        progress(f"fetching staff ({n}/{len(batches)})")
        page = (await client.graphql(STAFF_QUERY, {"ids": ids, "perPage": STAFF_PAGE}))["Page"]
        found = {m["id"]: creator_roles(m.get("staff")) for m in page.get("media") or []}
        repo.set_staff_roles({i: found.get(i, []) for i in ids})
    return max(0, len(media_ids) - sum(len(b) for b in batches))
