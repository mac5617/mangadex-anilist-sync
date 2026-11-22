"""Matched series that are not on your list (story 17, FR-30): the rows and proposals the Unlisted page and
series pages offer to add. The add itself is YourList.add (mdal/lists), on an explicit per-item click.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mdal import titles
from mdal.db.repo import Repo
from mdal.lists.anilist import ADD_STATUSES  # noqa: F401  (the statuses these rows offer)
from mdal.matching.matches import is_matched, not_on_list
from mdal.sync.rules import AlMediaInfo, completion_default, evaluate_series, library_info


@dataclass(frozen=True)
class NotListedRow:
    md_id: str
    md_title: str
    md_url: str
    md_cover_file: str | None
    media_id: int
    al_title: str
    al_url: str
    cover: str | None
    chapters: int | None
    proposed: int | None          # floor(max read chapter), None if nothing read
    progress: int                 # pre-filled value (capped at the AniList total)
    over_total: bool
    default_status: str
    unresolved: int


def not_listed_rows(repo: Repo) -> list[NotListedRow]:
    """Matched series whose media is not on the list, with a proposed progress from the latest read data."""
    mapped = not_on_list(repo)
    media = repo.media([m["al_media_id"] for m in mapped])
    md_rows = {r["md_id"]: r for r in repo.md_manga()}
    return [_row(repo, m, md_rows[m["md_id"]], media.get(m["al_media_id"])) for m in mapped]


def proposal(repo: Repo, md_id: str) -> NotListedRow | None:
    """The add proposal for one series (series page), or None when it can't be added from here."""
    mapping = repo.get_mapping(md_id)
    if not is_matched(mapping):
        return None
    if mapping["al_media_id"] in repo.al_entries():
        return None
    md = next((r for r in repo.md_manga() if r["md_id"] == md_id), None)
    if md is None:
        return None
    m = {"md_id": md_id, "al_media_id": mapping["al_media_id"]}
    return _row(repo, m, md, repo.media([mapping["al_media_id"]]).get(mapping["al_media_id"]))


def _row(repo: Repo, m: Any, md: Any, al: Any) -> NotListedRow:
    """One matched series not on the list: progress proposed from your reads, capped at AniList's total."""
    al_info = AlMediaInfo(al["chapters"] if al else None, al["status"] if al else None)
    d = evaluate_series(md, repo.read_chapters(m["md_id"]), None, al_info, repo.get_setting("jump_limit"))
    proposed = d.md_progress
    total = al_info.chapters
    over = proposed is not None and total is not None and proposed > total
    progress = (total if over else proposed) or 0
    title = titles.pick(al["romaji"], al["english"], al["native"]) if al else None
    return NotListedRow(
        md_id=m["md_id"],
        md_title=md["title"],
        md_url=f"https://mangadex.org/title/{m['md_id']}",
        md_cover_file=md["cover_file"],
        media_id=m["al_media_id"],
        al_title=title or f"AniList {m['al_media_id']}",
        al_url=(al["site_url"] if al else None) or f"https://anilist.co/manga/{m['al_media_id']}",
        cover=al["cover_url"] if al else None,
        chapters=total,
        proposed=proposed,
        progress=progress,
        over_total=over,
        default_status="COMPLETED" if completion_default(al_info, library_info(md), progress) else "CURRENT",
        unresolved=d.unresolved,
    )
