"""Matches: the AniList series a MangaDex series is linked to, found by Shiori or confirmed by you.

The one definition of "matched": an automatic or confirmed mapping that names an AniList series.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mdal.db.repo import Repo

_STATES = ("auto", "confirmed")
_WHERE = f"p.state IN ({','.join(repr(s) for s in _STATES)}) AND p.al_media_id IS NOT NULL"


def is_matched(mapping: Any) -> bool:
    return mapping is not None and mapping["state"] in _STATES and mapping["al_media_id"] is not None


def matched(repo: Repo) -> dict[str, int]:
    """md_id -> AniList media id, for every Match."""
    return {r[0]: r[1] for r in repo.conn.execute(f"SELECT p.md_id, p.al_media_id FROM mapping p WHERE {_WHERE}")}


def library_series(repo: Repo, media_id: int | None = None) -> list[Any]:
    """Library series with a Match (to this AniList series, if given), with their `al_media_id`, by md_id."""
    only, args = (" AND p.al_media_id=?", (media_id,)) if media_id is not None else ("", ())
    return repo.conn.execute(
        f"SELECT d.*, p.al_media_id FROM mapping p JOIN md_manga d ON d.md_id = p.md_id WHERE {_WHERE}{only} "
        "ORDER BY d.md_id", args,
    ).fetchall()


def not_on_list(repo: Repo) -> list[Any]:
    """Library series whose Match is not on your AniList list (story 17), by title."""
    return repo.conn.execute(
        "SELECT p.md_id, p.al_media_id, p.state FROM mapping p "
        "JOIN md_manga m ON m.md_id = p.md_id "
        f"WHERE {_WHERE} "
        "AND p.al_media_id NOT IN (SELECT media_id FROM al_entry) ORDER BY m.title COLLATE NOCASE"
    ).fetchall()
