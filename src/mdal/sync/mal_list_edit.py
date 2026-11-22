"""List edits mirrored to MyAnimeList: Paused/Dropped (List → Stalled) and the status picked on a series page.
Scores have moved to mdal/lists/myanimelist.py.

The AniList edit (sync/list_edit.py) comes first; this repeats it on MyAnimeList for series on your
MAL list. Rules:
- only an entry already on your MyAnimeList list is edited: each edit reads the entry first
  (MyAnimeList's save would otherwise create it), so an edit costs 2 requests;
- it sends only one of the statuses in MAL_STATUS;
- every change is logged in list_edit with site 'mal'.
"""

from __future__ import annotations

import logging
from typing import Any

from mdal.clients.myanimelist import MalClient, MalError
from mdal.db.repo import Repo

log = logging.getLogger(__name__)

MAL_STATUS = {"CURRENT": "reading", "COMPLETED": "completed", "PAUSED": "on_hold", "DROPPED": "dropped",
              "PLANNING": "plan_to_read"}


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


async def mirror_status(mal: MalClient, repo: Repo, media_id: int, status: str) -> str | None:
    """Repeat a status change on MyAnimeList. Returns None when done or not applicable, else why it failed."""
    if status not in MAL_STATUS:
        raise ValueError(f"refusing to mirror status {status!r}")
    ids = on_mal(repo, [media_id])
    if media_id not in ids:
        return None
    problem = await _edit(mal, repo, media_id, ids[media_id], "status", MAL_STATUS[status], status=MAL_STATUS[status])
    if problem is None:
        repo.update_mal_entry(ids[media_id], status=status)   # mal_entry keeps AniList's status words
    return problem
