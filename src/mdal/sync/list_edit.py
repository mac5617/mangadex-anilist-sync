"""Your notes on AniList entries, outside syncs. Scores and statuses have moved to mdal/lists/anilist.py.

Like writer.py and add_entry.py, this module may contain a mutation. Its rules:
- it only edits an entry that exists, addressed by its entry `id` (never `mediaId`), so nothing can be added;
- it sends only `notes`;
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


class ListEditError(Exception):
    pass


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
