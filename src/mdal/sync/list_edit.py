"""Edits to your AniList list outside syncs: Paused/Dropped (List → Stalled), the status you pick on a series
page, and notes. Scores have moved to mdal/lists/anilist.py.

Like writer.py and add_entry.py, this module may contain a mutation. Its rules:
- it only edits an entry that exists, addressed by its entry `id` (never `mediaId`), so nothing can be added;
- it sends only `notes`, or one of the literal statuses in EDIT_STATUSES (never Rereading);
- every change is logged in list_edit, and the local copy of the entry follows what AniList answered.
SaveMediaListEntry with absolute values is idempotent, so the client's retry of a failure is safe.
"""

from __future__ import annotations

import logging
from typing import Any

from mdal.clients.anilist import AniListClient, AniListGraphQLError
from mdal.db.repo import Repo

log = logging.getLogger(__name__)

SELECTION = "{ id mediaId status progress score(format: POINT_100) }"
EDIT_STATUSES = ("CURRENT", "COMPLETED", "PAUSED", "DROPPED", "PLANNING")


class ListEditError(Exception):
    pass


def status_document(status: str) -> str:
    if status not in EDIT_STATUSES:
        raise ListEditError(f"refusing to set status {status!r}")
    return f"mutation ($e: Int) {{ SaveMediaListEntry(id: $e, status: {status}) {SELECTION} }}"


NOTES_MAX = 5000
NOTES_DOCUMENT = f"mutation ($e: Int, $n: String) {{ SaveMediaListEntry(id: $e, notes: $n) {SELECTION} }}"
READ_NOTES = "query ($e: Int) { MediaList(id: $e) { id notes } }"


def _entry(repo: Repo, media_id: int) -> Any:
    entry = repo.al_entries().get(media_id)
    if entry is None:
        raise ListEditError("This series isn't on your AniList list.")
    return entry


async def read_notes(client: AniListClient, repo: Repo, media_id: int) -> str:
    """Your notes on one entry, from AniList (1 request), kept locally."""
    entry = _entry(repo, media_id)
    data = await client.graphql(READ_NOTES, {"e": entry["entry_id"]})
    notes = ((data.get("MediaList") or {}).get("notes")) or ""
    repo.update_al_entry(media_id, notes=notes)
    return notes


async def save_notes(client: AniListClient, repo: Repo, media_id: int, notes: str) -> None:
    """Replace your notes on one entry. Raises ListEditError when AniList refuses."""
    entry = _entry(repo, media_id)
    notes = notes.strip()[:NOTES_MAX]
    try:
        await client.graphql(NOTES_DOCUMENT, {"e": entry["entry_id"], "n": notes})
    except AniListGraphQLError as exc:
        repo.log_edit(media_id, entry["entry_id"], "notes", entry["notes"], notes, "failed", str(exc))
        raise ListEditError(f"AniList refused the notes: {exc}") from exc
    repo.update_al_entry(media_id, notes=notes)
    repo.log_edit(media_id, entry["entry_id"], "notes", entry["notes"], notes, "done")


async def set_status(client: AniListClient, repo: Repo, media_id: int, status: str) -> dict[str, Any]:
    """Set one entry's status. Returns the entry as AniList saved it (it may fill in progress on Completed).
    Raises ListEditError when it can't."""
    query = status_document(status)
    entry = _entry(repo, media_id)
    try:
        data = await client.graphql(query, {"e": entry["entry_id"]})
    except AniListGraphQLError as exc:
        repo.log_edit(media_id, entry["entry_id"], "status", entry["status"], status, "failed", str(exc))
        raise ListEditError(f"AniList refused the change: {exc}") from exc
    result = data.get("SaveMediaListEntry") or {}
    saved = {"status": result.get("status") or status}
    if result.get("progress") is not None:
        saved["progress"] = result["progress"]
    repo.update_al_entry(media_id, **saved)
    repo.log_edit(media_id, entry["entry_id"], "status", entry["status"], status, "done")
    return saved
