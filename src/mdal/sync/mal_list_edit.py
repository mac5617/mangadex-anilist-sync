"""List edits mirrored to MyAnimeList: scores (List → Rank) and Paused/Dropped (List → Stalled).

The AniList edit (sync/list_edit.py) comes first; this repeats it on MyAnimeList for series on your
MAL list. Rules:
- only an entry already on your MyAnimeList list is edited: each edit reads the entry first
  (MyAnimeList's save would otherwise create it), so an edit costs 2 requests;
- it sends only a score (MyAnimeList scores are whole numbers, 1-10) or the status on_hold or dropped;
- a score whose whole number didn't change isn't sent;
- every change is logged in list_edit with site 'mal'.
"""

from __future__ import annotations

import logging
from typing import Any

from mdal.clients.myanimelist import MalClient, MalError
from mdal.db.repo import Repo

log = logging.getLogger(__name__)

MAL_STATUS = {"PAUSED": "on_hold", "DROPPED": "dropped"}


def mal_score(score_100: int) -> int:
    """AniList POINT_100 -> MyAnimeList's 1-10."""
    return max(1, min(10, round(score_100 / 10)))


def on_mal(repo: Repo, media_ids: list[int]) -> dict[int, int]:
    """AniList id -> MAL id, for the series that are on your MyAnimeList list (as last read)."""
    listed = repo.mal_entries()
    media = repo.media(media_ids)
    return {m: media[m]["id_mal"] for m in media_ids
            if m in media and media[m]["id_mal"] and media[m]["id_mal"] in listed}


async def _edit(mal: MalClient, repo: Repo, media_id: int, mal_id: int, field: str, new: Any, **send: Any) -> str | None:
    """One checked edit. Returns None when done, else why it wasn't."""
    entry = await mal.my_list_status(mal_id)
    if entry is None:
        repo.log_edit(media_id, mal_id, field, None, new, "failed", "not on your MyAnimeList list", site="mal")
        return "not on your MyAnimeList list"
    try:
        await mal.edit_entry(mal_id, **send)
    except MalError as exc:
        repo.log_edit(media_id, mal_id, field, entry.get(field), new, "failed", str(exc), site="mal")
        return str(exc)
    repo.log_edit(media_id, mal_id, field, entry.get(field), new, "done", site="mal")
    return None


async def mirror_scores(mal: MalClient, repo: Repo, scores: dict[int, int]) -> tuple[list[int], dict[int, str]]:
    """Send AniList scores (0-100) on to MyAnimeList. Returns (saved, {media id: why not})."""
    ids = on_mal(repo, list(scores))
    listed = repo.mal_entries()
    saved: list[int] = []
    failed: dict[int, str] = {}
    for media_id, mal_id in ids.items():
        value = mal_score(scores[media_id])
        if (listed[mal_id]["score"] or 0) == value:
            continue                                  # same whole number: nothing to send
        problem = await _edit(mal, repo, media_id, mal_id, "score", value, score=value)
        if problem:
            failed[media_id] = problem
        else:
            repo.update_mal_entry(mal_id, score=value)
            saved.append(media_id)
    return saved, failed


async def mirror_status(mal: MalClient, repo: Repo, media_id: int, status: str) -> str | None:
    """Repeat Paused/Dropped on MyAnimeList. Returns None when done or not applicable, else why it failed."""
    if status not in MAL_STATUS:
        raise ValueError(f"refusing to mirror status {status!r}")
    ids = on_mal(repo, [media_id])
    if media_id not in ids:
        return None
    problem = await _edit(mal, repo, media_id, ids[media_id], "status", MAL_STATUS[status], status=MAL_STATUS[status])
    if problem is None:
        repo.update_mal_entry(ids[media_id], status=status)   # mal_entry keeps AniList's status words
    return problem
