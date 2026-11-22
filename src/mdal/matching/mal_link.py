"""The MyAnimeList link: which MyAnimeList entry a series is. Pure: callers fetch the rows.

AniList's link first, MangaDex's otherwise; when they disagree there is none, so nothing is written to a guess.
`series_link` answers for one MangaDex series (Sync), `media_link` for an AniList series and every MangaDex series
matched to it (list edits).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from mdal.matching.matches import is_matched


def md_mal_link(row: Any) -> int | None:
    """The MyAnimeList id from MangaDex's links, if it is a plain number."""
    try:
        value = json.loads(row["links"] or "{}").get("mal")
    except (ValueError, AttributeError):
        return None
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _disagree(al_mal: int, md_mal: int) -> str:
    return f"AniList and MangaDex link different MyAnimeList entries ({al_mal} and {md_mal})"


def series_link(row: Any, mapping: Any, media: dict[int, Any]) -> tuple[int | None, int | None, str | None]:
    """(mal_id, al_media_id, skip reason) for one MangaDex series.

    A series with no AniList match can still sync by MangaDex's link. One whose match awaits review waits too (its
    links are often the reason it needs review).
    """
    if mapping and mapping["state"] == "review":
        return None, None, "awaiting match review"
    matched = is_matched(mapping)
    al_id = mapping["al_media_id"] if matched else None
    al_mal = media[al_id]["id_mal"] if al_id is not None and al_id in media else None
    md_mal = md_mal_link(row)
    if al_mal and md_mal and al_mal != md_mal:
        return None, al_id, _disagree(al_mal, md_mal)
    mal_id = al_mal or md_mal
    if mal_id is None:
        why = "the AniList match has none" if matched else "no AniList match, and no MyAnimeList link on MangaDex"
        return None, al_id, f"no MyAnimeList id ({why})"
    return mal_id, al_id, None


def media_link(al_mal: int | None, matched_rows: Iterable[Any]) -> tuple[int | None, str | None]:
    """(mal_id, why there is none) for an AniList series, given its link and the library series matched to it."""
    links = [md_mal_link(row) for row in matched_rows]
    if al_mal:
        other = next((md for md in links if md and md != al_mal), None)
        return (None, _disagree(al_mal, other)) if other else (al_mal, None)
    ids = {md for md in links if md}
    if len(ids) > 1:
        return None, f"MangaDex links different MyAnimeList entries ({', '.join(map(str, sorted(ids)))})"
    return (ids.pop(), None) if ids else (None, "no MyAnimeList id (the AniList match has none)")
