"""MyAnimeList reads: the user's manga list. Read-only.

Statuses are stored in AniList's words so the progress rules, labels and verify step are shared.
"""

from __future__ import annotations

from typing import Any

from mdal.clients.myanimelist import MalClient
from mdal.db.repo import Repo, now_iso

LIST_STATUS = {
    "reading": "CURRENT", "completed": "COMPLETED", "on_hold": "PAUSED",
    "dropped": "DROPPED", "plan_to_read": "PLANNING",
}
PUBLISHING_STATUS = {
    "finished": "FINISHED", "currently_publishing": "RELEASING", "not_yet_published": "NOT_YET_RELEASED",
    "on_hiatus": "HIATUS", "discontinued": "CANCELLED",
}


def entry_row(item: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    node, ls = item.get("node") or {}, item.get("list_status") or {}
    status = LIST_STATUS.get(ls.get("status") or "", "CURRENT")
    if ls.get("is_rereading"):
        status = "REPEATING"
    return {
        "mal_id": node["id"],
        "status": status,
        "progress": ls.get("num_chapters_read") or 0,
        "volumes": ls.get("num_volumes_read"),
        "score": ls.get("score") or None,     # MAL reports 0 for "no score"
        "title": node.get("title"),
        "chapters": node.get("num_chapters") or None,  # 0 = unknown
        "media_status": PUBLISHING_STATUS.get(node.get("status") or ""),
        "picture": (node.get("main_picture") or {}).get("medium"),
        "updated_at": ls.get("updated_at"),
        "fetched_at": fetched_at,
    }


async def fetch_mal_list(client: MalClient, repo: Repo) -> int:
    """Replace the mal_entry snapshot. One request per 1,000 entries. Returns the entry count."""
    fetched_at = now_iso()
    rows = [entry_row(item, fetched_at) for item in await client.manga_list() if (item.get("node") or {}).get("id")]
    repo.replace_mal_entries(rows)
    return len(rows)
