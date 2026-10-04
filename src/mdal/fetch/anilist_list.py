"""AniList reads: viewer, the user's manga list, bulk id lookups, batched title search.

Everything here is read-only. Query text is constant; user input only travels as variables.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mdal.clients.anilist import AniListClient
from mdal.db.repo import Repo, now_iso

MEDIA_FIELDS = (
    "id idMal type format status chapters countryOfOrigin startDate { year } "
    "title { romaji english native } synonyms coverImage { medium } siteUrl genres"
)
STAFF_FIELDS = "staff(perPage: 3) { nodes { name { full native } } }"

VIEWER_QUERY = "query { Viewer { id name } }"
LIST_QUERY = (
    "query ($userId: Int) { MediaListCollection(userId: $userId, type: MANGA) { "
    "lists { name isCustomList entries { id status progress progressVolumes score(format: POINT_100) updatedAt "
    "startedAt { year month day } completedAt { year month day } "
    f"media {{ {MEDIA_FIELDS} }} }} }} }} }}"
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
        "cover_url": (media.get("coverImage") or {}).get("medium"),
        "site_url": media.get("siteUrl"),
        "fetched_at": fetched_at,
    }


async def viewer(client: AniListClient) -> dict[str, Any]:
    return (await client.graphql(VIEWER_QUERY))["Viewer"]


async def fetch_list(client: AniListClient, repo: Repo, user_id: int) -> ListSummary:
    """One request. Replaces the al_entry snapshot; every list, custom ones included, de-duplicated by entry id."""
    fetched_at = now_iso()
    data = await client.graphql(LIST_QUERY, {"userId": user_id})
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
         "updated_at": e.get("updatedAt")}
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
