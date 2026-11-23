"""New releases on MangaDex: series with a new English chapter in the last week, and your follows.

Read-only and small: about a dozen pages of chapters, then their series 100 at a time, plus one page of follows per
100 series followed, all through the paced MangaDex client. The hourly check (`fetch_latest`) reads only the newest
pages, and stops at the first page with nothing new: usually one or two requests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from mdal.clients.mangadex import ALL_CONTENT_RATINGS, IDS_PER_REQUEST, MangaDexClient
from mdal.fetch.mangadex_library import _primary_title, _strings
from mdal.db.repo import Repo, now_iso
from mdal.recommend.fresh import chapter_number

PAGE = 100
WINDOW = timedelta(days=7)   # how far back a new English chapter counts
CHAPTER_PAGES = 20       # at most 2,000 chapters a scan; a week is usually about 1,200
FOLLOW_PAGES_MAX = 50    # 5,000 follows
DESCRIPTION_MAX = 2000
CREDIT_LINES = ("links:", "official", "raw:", "raws:", "alt.", "alt:", "english:", "translation:", "source:")

MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
MD_RULE = re.compile(r"^\s*(-{3,}|_{3,}|\*{3,})\s*$", re.MULTILINE)
MD_MARKS = re.compile(r"(\*\*|__|~~|\*|^#+\s*|^>\s*)", re.MULTILINE)
BBCODE = re.compile(r"\[/?(b|i|u|s|spoiler|url[^\]]*)\]", re.IGNORECASE)


def plain_description(localized: Any, language: str = "en") -> str:
    """MangaDex's Markdown description as plain text, without the links section most entries end with."""
    if not isinstance(localized, dict):
        return ""
    text = localized.get(language) or localized.get("en") or next(iter(_strings(localized)), "")
    text = MD_RULE.split(text)[0]                     # "---" usually starts the links and credits
    text = MD_LINK.sub(r"\1", BBCODE.sub("", text))
    text = MD_MARKS.sub("", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line and not line.lower().startswith(CREDIT_LINES)]
    return "\n".join(lines)[:DESCRIPTION_MAX]


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def new_row(manga: dict[str, Any], source: str, language: str, fetched_at: str, release: EnglishRelease) -> dict[str, Any]:
    attrs = manga.get("attributes") or {}
    title_map = attrs.get("title") or {}
    primary = _primary_title(title_map)
    alts: list[str] = []
    for value in _strings(title_map) + [s for alt in attrs.get("altTitles") or [] for s in _strings(alt)]:
        if value != primary and value not in alts:
            alts.append(value)
    links = attrs.get("links") if isinstance(attrs.get("links"), dict) else {}
    tags = [{"name": name, "group": (t.get("attributes") or {}).get("group")}
            for t in attrs.get("tags") or []
            if (name := ((t.get("attributes") or {}).get("name") or {}).get("en"))]
    rels = manga.get("relationships") or []
    authors: list[str] = []
    for rel in rels:
        name = (rel.get("attributes") or {}).get("name")
        if rel.get("type") in ("author", "artist") and name and name not in authors:
            authors.append(name)
    cover = next(((rel.get("attributes") or {}).get("fileName") for rel in rels if rel.get("type") == "cover_art"), None)
    return {
        "md_id": manga["id"], "title": primary, "alt_titles": json.dumps(alts, ensure_ascii=False),
        "description": plain_description(attrs.get("description"), language) or None,
        "tags": json.dumps(tags, ensure_ascii=False), "authors": json.dumps(authors, ensure_ascii=False),
        "original_language": attrs.get("originalLanguage"), "year": attrs.get("year"),
        "pub_status": attrs.get("status"), "demographic": attrs.get("publicationDemographic"),
        "content_rating": attrs.get("contentRating"),
        "al_id": _int(links.get("al")), "mal_id": _int(links.get("mal")), "mu_id": links.get("mu") or None,
        "cover_file": cover, "last_chapter": attrs.get("lastChapter") or None,
        "latest_chapter_id": release.chapter_id, "en_chapter": release.chapter, "en_chapter_at": release.at,
        "source": source, "similarity": None, "similar_to": None, "fetched_at": fetched_at,
    }


@dataclass(frozen=True)
class EnglishRelease:
    """A series' recent English chapters: the newest upload's id and date, and the highest chapter number."""
    chapter_id: str
    chapter: str | None
    at: str


def _when(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def is_recent(value: Any, now: datetime) -> bool:
    """Whether an English chapter date is inside the week New releases covers."""
    moment = _when(value)
    return moment is not None and moment >= now - WINDOW


def is_reupload(entry: Any, chapter: str | None, now: datetime) -> bool:
    """Whether this chapter was checked and first went up in English before the week New releases covers."""
    return (isinstance(entry, dict) and entry.get("chapter") == chapter and entry.get("first_at") is not None
            and not is_recent(entry["first_at"], now))


async def first_english_upload(client: MangaDexClient, md_id: str, chapter: str, language: str) -> str | None:
    """When this chapter first went up in this language (one request); None when MangaDex lists no upload."""
    body = await client.get("/chapter", {
        "manga": md_id, "translatedLanguage[]": [language], "chapter[]": [chapter], "order[readableAt]": "asc", "limit": 1,
        "contentRating[]": ALL_CONTENT_RATINGS, "includeFuturePublishAt": 0, "includeEmptyPages": 0})
    data = body.get("data") or []
    return (data[0].get("attributes") or {}).get("readableAt") if data else None


def _rank(release: EnglishRelease) -> float:
    number = chapter_number(release.chapter)
    return -1.0 if number is None else number


def best_release(a: EnglishRelease | None, b: EnglishRelease) -> EnglishRelease:
    """Keep the highest chapter, so a re-upload of an old one doesn't move a series backwards."""
    if a is None:
        return b
    newest = b if (_when(b.at) or datetime.min.replace(tzinfo=UTC)) >= (_when(a.at) or datetime.min.replace(tzinfo=UTC)) else a
    return EnglishRelease(newest.chapter_id, (b if _rank(b) > _rank(a) else a).chapter, newest.at)


async def _english_feed(
    client: MangaDexClient, language: str, since: datetime, known: dict[str, datetime] | None = None,
) -> tuple[dict[str, EnglishRelease], int]:
    """Newest chapters first, until the week ends. With `known` (series -> its latest date), also stops at the first
    page with nothing newer. Returns (series -> release, requests)."""
    found: dict[str, EnglishRelease] = {}
    requests = 0
    for page in range(CHAPTER_PAGES):
        body = await client.get("/chapter", {
            "limit": PAGE, "offset": page * PAGE, "translatedLanguage[]": [language], "order[readableAt]": "desc",
            "contentRating[]": ALL_CONTENT_RATINGS, "includeFuturePublishAt": 0, "includeEmptyPages": 0})
        requests += 1
        data = body.get("data") or []
        ended, changed = False, False
        for chapter in data:
            attrs = chapter.get("attributes") or {}
            moment = _when(attrs.get("readableAt"))
            if moment is None:
                continue
            if moment < since:
                ended = True
                continue
            md_id = next((r.get("id") for r in chapter.get("relationships") or [] if r.get("type") == "manga"), None)
            if not md_id or (known is not None and md_id in known and moment <= known[md_id]):
                continue
            changed = True
            found[md_id] = best_release(found.get(md_id), EnglishRelease(chapter["id"], attrs.get("chapter"), attrs["readableAt"]))
        if ended or not data or (known is not None and not changed) or (page + 1) * PAGE >= (body.get("total") or 0):
            break
    return found, requests


async def _series_rows(
    client: MangaDexClient, releases: dict[str, EnglishRelease], language: str, since: datetime, fetched_at: str,
) -> tuple[list[dict[str, Any]], int]:
    """The details of these series. One marked "added" first appeared on MangaDex this week."""
    ids = list(releases)
    rows: list[dict[str, Any]] = []
    requests = 0
    for start in range(0, len(ids), IDS_PER_REQUEST):
        body = await client.get("/manga", {
            "ids[]": ids[start : start + IDS_PER_REQUEST], "limit": IDS_PER_REQUEST,
            "includes[]": ["author", "artist", "cover_art"], "contentRating[]": ALL_CONTENT_RATINGS})
        requests += 1
        for manga in body.get("data") or []:
            if manga.get("id") in releases:
                created = _when((manga.get("attributes") or {}).get("createdAt"))
                source = "added" if created and created >= since else "updated"
                rows.append(new_row(manga, source, language, fetched_at, releases[manga["id"]]))
    return rows, requests


async def fetch_latest(client: MangaDexClient, repo: Repo, language: str) -> tuple[list[str], int]:
    """The hourly check: newest English chapters first, stopping at the first page with nothing new. New series are
    added, known ones get their latest chapter. Returns (ids of the series added, MangaDex requests)."""
    fetched_at = now_iso()
    since = datetime.now(UTC) - WINDOW
    stored = {r["md_id"]: r for r in repo.new_releases() if _when(r["en_chapter_at"])}
    known = {md_id: _when(r["en_chapter_at"]) for md_id, r in stored.items()}
    found, requests = await _english_feed(client, language, since, known)
    for md_id, release in found.items():
        if r := stored.get(md_id):
            found[md_id] = best_release(EnglishRelease(r["latest_chapter_id"] or "", r["en_chapter"], r["en_chapter_at"]), release)
    rows, more = await _series_rows(client, found, language, since, fetched_at)
    return repo.merge_new_releases(rows), requests + more


async def fetch_new_releases(
    client: MangaDexClient,
    repo: Repo,
    language: str,
    progress: Callable[[str], None] = lambda _msg: None,
) -> tuple[int, int]:
    """Replace the stored new releases and follows. Returns (series stored, MangaDex requests)."""
    fetched_at = now_iso()
    since = datetime.now(UTC) - WINDOW
    progress("reading this week's English chapters on MangaDex")
    found, requests = await _english_feed(client, language, since)
    progress(f"reading {len(found)} series on MangaDex")
    rows, more = await _series_rows(client, found, language, since, fetched_at)
    requests += more

    follows: list[str] = []
    for page in range(FOLLOW_PAGES_MAX):
        progress(f"reading your MangaDex follows (page {page + 1})")
        body = await client.get("/user/follows/manga", {"limit": PAGE, "offset": page * PAGE})
        requests += 1
        follows += [m["id"] for m in body.get("data") or [] if m.get("id")]
        if (page + 1) * PAGE >= (body.get("total") or 0):
            break

    repo.replace_new_releases(rows, follows)
    return len(rows), requests
