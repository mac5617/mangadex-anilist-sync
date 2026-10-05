"""New releases on MangaDex: the most recently updated series, the newest series, and your follows.

Read-only and small: about 6 pages of /manga plus one page of follows per 100 series followed,
all through the paced MangaDex client.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from mdal.clients.mangadex import ALL_CONTENT_RATINGS, MangaDexClient
from mdal.fetch.mangadex_library import _primary_title, _strings
from mdal.db.repo import Repo, now_iso

PAGE = 100
UPDATED_PAGES = 5        # the 500 series with the most recent chapters
ADDED_PAGES = 1          # plus the 100 newest series
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


def new_row(manga: dict[str, Any], source: str, language: str, fetched_at: str) -> dict[str, Any]:
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
        "source": source, "similarity": None, "similar_to": None, "fetched_at": fetched_at,
    }


async def fetch_new_releases(
    client: MangaDexClient,
    repo: Repo,
    language: str,
    progress: Callable[[str], None] = lambda _msg: None,
) -> tuple[int, int]:
    """Replace the stored new releases and follows. Returns (series stored, MangaDex requests)."""
    fetched_at = now_iso()
    requests = 0
    base = {"limit": PAGE, "includes[]": ["author", "artist", "cover_art"], "contentRating[]": ALL_CONTENT_RATINGS,
            "hasAvailableChapters": "true", "availableTranslatedLanguage[]": [language]}
    rows: dict[str, dict[str, Any]] = {}
    for source, order, pages in (("updated", "latestUploadedChapter", UPDATED_PAGES), ("added", "createdAt", ADDED_PAGES)):
        for page in range(pages):
            progress(f"reading MangaDex ({'latest chapters' if source == 'updated' else 'newest series'}, page {page + 1})")
            body = await client.get("/manga", {**base, f"order[{order}]": "desc", "offset": page * PAGE})
            requests += 1
            for manga in body.get("data") or []:
                if manga.get("id") and manga["id"] not in rows:
                    rows[manga["id"]] = new_row(manga, source, language, fetched_at)
            if (page + 1) * PAGE >= (body.get("total") or 0):
                break

    follows: list[str] = []
    for page in range(FOLLOW_PAGES_MAX):
        progress(f"reading your MangaDex follows (page {page + 1})")
        body = await client.get("/user/follows/manga", {"limit": PAGE, "offset": page * PAGE})
        requests += 1
        follows += [m["id"] for m in body.get("data") or [] if m.get("id")]
        if (page + 1) * PAGE >= (body.get("total") or 0):
            break

    repo.replace_new_releases(list(rows.values()), follows)
    return len(rows), requests
