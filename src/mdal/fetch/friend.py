"""A friend's public AniList manga list, for Stats → Compare. Read-only, one request.

Their series are cached in al_media (with tags and descriptions), so their pages open straight away;
their entries are kept apart from yours, in the friend table.
"""

from __future__ import annotations

from typing import Any

from mdal.clients.anilist import AniListClient, AniListGraphQLError
from mdal.db.repo import Repo, now_iso
from mdal.fetch.anilist_list import DESCRIPTION_FIELD, MEDIA_FIELDS, TAG_FIELDS, media_row

FRIEND_QUERY = (
    "query ($name: String) { User(name: $name) { id name } "
    "MediaListCollection(userName: $name, type: MANGA) { lists { entries { status progress score(format: POINT_100) "
    f"media {{ {MEDIA_FIELDS} {TAG_FIELDS} {DESCRIPTION_FIELD} }} }} }} }} }}"
)


class FriendNotFound(Exception):
    pass


async def fetch_friend(client: AniListClient, repo: Repo, name: str) -> list[dict[str, Any]]:
    try:
        data = await client.graphql(FRIEND_QUERY, {"name": name})
    except AniListGraphQLError as exc:
        text = str(exc).lower()
        if "private" in text:
            raise FriendNotFound(f"{name}'s list is private on AniList.") from exc
        if "not found" in text:
            raise FriendNotFound(f"AniList has no user called {name}.") from exc
        raise
    user = data.get("User") or {}
    if not user:
        raise FriendNotFound(f"AniList has no user called {name}.")
    entries: dict[int, dict[str, Any]] = {}
    media = {}
    for group in (data.get("MediaListCollection") or {}).get("lists") or []:
        for e in group.get("entries") or []:
            m = e.get("media") or {}
            if m.get("id"):
                media[m["id"]] = m
                entries[m["id"]] = {"media_id": m["id"], "status": e.get("status"), "score": e.get("score") or None,
                                    "progress": e.get("progress") or 0}
    fetched = now_iso()
    repo.upsert_media([media_row(m, fetched) for m in media.values()])
    repo.save_friend(user.get("name") or name, user.get("id"), list(entries.values()))
    return list(entries.values())
