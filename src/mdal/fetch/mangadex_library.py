"""MangaDex library, details, read markers and chapter numbers (architecture §6, story 07)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mdal.clients.mangadex import ALL_CONTENT_RATINGS, IDS_PER_REQUEST, MangaDexClient, MangaDexError
from mdal.db.repo import Repo, now_iso

TITLE_PREFERENCE = ("en", "ja-ro", "ko-ro", "zh-ro")
PERMANENTLY_MISSING = 2  # md_chapter.missing: 0 resolved, 1 missed once, 2 give up


@dataclass
class LibrarySummary:
    series: int = 0
    read_ids: int = 0
    chapters_resolved: int = 0
    chapters_missing: int = 0


def links_hash(links: dict[str, str]) -> str:
    return hashlib.sha1(json.dumps(links, sort_keys=True).encode()).hexdigest()


def _strings(localized: Any) -> list[str]:
    if isinstance(localized, dict):
        return [v for v in localized.values() if isinstance(v, str) and v.strip()]
    return []


def _primary_title(title: dict[str, str]) -> str:
    for lang in TITLE_PREFERENCE:
        if title.get(lang):
            return title[lang]
    values = _strings(title)
    return values[0] if values else "(untitled)"


def manga_row(manga: dict[str, Any], reading_status: str | None, fetched_at: str) -> dict[str, Any]:
    attrs = manga.get("attributes") or {}
    title_map = attrs.get("title") or {}
    primary = _primary_title(title_map)
    alts: list[str] = []
    for value in _strings(title_map) + [s for alt in attrs.get("altTitles") or [] for s in _strings(alt)]:
        if value != primary and value not in alts:
            alts.append(value)
    links = attrs.get("links") if isinstance(attrs.get("links"), dict) else {}  # MangaDex sends [] when empty
    rels = manga.get("relationships") or []
    authors: list[str] = []
    for rel in rels:
        name = (rel.get("attributes") or {}).get("name")
        if rel.get("type") in ("author", "artist") and name and name not in authors:
            authors.append(name)
    cover = next(
        ((rel.get("attributes") or {}).get("fileName") for rel in rels if rel.get("type") == "cover_art"), None
    )
    return {
        "md_id": manga["id"],
        "in_library": 1,
        "reading_status": reading_status,
        "title": primary,
        "alt_titles": json.dumps(alts, ensure_ascii=False),
        "original_language": attrs.get("originalLanguage"),
        "year": attrs.get("year"),
        "pub_status": attrs.get("status"),
        "last_chapter": attrs.get("lastChapter") or None,
        "links": json.dumps(links, ensure_ascii=False, sort_keys=True),
        "links_hash": links_hash(links),
        "authors": json.dumps(authors, ensure_ascii=False),
        "cover_file": cover,
        "chapter_numbers_reset": int(bool(attrs.get("chapterNumbersResetOnNewVolume"))),
        "fetched_at": fetched_at,
    }


async def _fetch_reads(client: MangaDexClient, ids: list[str]) -> dict[str, list[str]]:
    reads: dict[str, list[str]] = {}
    for start in range(0, len(ids), IDS_PER_REQUEST):
        chunk = ids[start : start + IDS_PER_REQUEST]
        data = (await client.get("/manga/read", {"ids[]": chunk, "grouped": "true"})).get("data")
        if isinstance(data, dict):
            reads.update({k: list(v) for k, v in data.items()})
        elif isinstance(data, list) and data:
            # The spec allows an ungrouped array; only unambiguous for a single manga.
            if len(chunk) != 1:
                raise MangaDexError("/manga/read returned ungrouped data for several manga")
            reads[chunk[0]] = list(data)
    return reads


async def fetch_library(
    client: MangaDexClient,
    repo: Repo,
    progress: Callable[[str], None] = lambda _msg: None,
) -> LibrarySummary:
    """Refresh the MangaDex snapshot. Completed steps stay in the DB if a later one fails."""
    fetched_at = now_iso()
    summary = LibrarySummary()

    progress("reading MangaDex library")
    statuses: dict[str, str] = (await client.get("/manga/status")).get("statuses") or {}
    ids = sorted(statuses)

    progress(f"fetching details for {len(ids)} series")
    manga = await client.get_by_ids(
        "/manga", ids, {"contentRating[]": ALL_CONTENT_RATINGS, "includes[]": ["author", "artist", "cover_art"]}
    )
    rows = [manga_row(m, statuses.get(m["id"]), fetched_at) for m in manga if m.get("id") in statuses]
    known = {r["md_id"] for r in rows}

    progress("reading read markers")
    reads = {md_id: chs for md_id, chs in (await _fetch_reads(client, ids)).items() if md_id in known}
    repo.replace_md_snapshot(rows, reads)
    summary.series = len(rows)
    summary.read_ids = sum(len(v) for v in reads.values())

    cache = repo.chapter_cache_state()
    owner = {ch: md_id for md_id, chs in reads.items() for ch in chs}
    # Unknown ids, plus ids missed exactly once (one retry, then they stay missing).
    wanted = sorted(ch for ch in owner if ch not in cache or cache[ch] == 1)
    if not wanted:
        return summary

    progress(f"resolving {len(wanted)} chapter numbers")
    chapters = await client.get_by_ids(
        "/chapter", wanted, {"contentRating[]": ALL_CONTENT_RATINGS, "includeUnavailable": "1"}
    )
    found = {c["id"]: c for c in chapters}
    cache_rows = []
    for ch in wanted:
        if ch in found:
            attrs = found[ch].get("attributes") or {}
            cache_rows.append({
                "chapter_id": ch, "md_id": owner[ch], "chapter": attrs.get("chapter"),
                "volume": attrs.get("volume"), "missing": 0, "fetched_at": fetched_at,
            })
            summary.chapters_resolved += 1
        else:
            missing = min(cache.get(ch, 0) + 1, PERMANENTLY_MISSING)
            cache_rows.append({
                "chapter_id": ch, "md_id": owner[ch], "chapter": None, "volume": None,
                "missing": missing, "fetched_at": fetched_at,
            })
            summary.chapters_missing += 1
    repo.upsert_chapters(cache_rows)
    return summary
