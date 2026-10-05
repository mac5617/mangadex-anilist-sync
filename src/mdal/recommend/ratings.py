"""Saving your verdict on a series, with a snapshot of what the series is (see feedback.py)."""

from __future__ import annotations

import json
from typing import Any

from mdal.db.repo import Repo
from mdal.fetch.anilist_list import plain_text
from mdal.recommend.feedback import VERDICTS, split_tags


def title_of(m: Any) -> str:
    return m["romaji"] or m["english"] or m["native"] or f"#{m['media_id']}"


def snapshot(repo: Repo, key: str) -> dict[str, Any] | None:
    """{title, genres, tags, staff, description} for 'al:<id>' or 'md:<uuid>', from what is stored; None if unknown.

    A MangaDex series linked to AniList uses the AniList data (its genres, tags and creators are richer)."""
    if key.startswith("md:"):
        row = repo.conn.execute("SELECT * FROM md_new WHERE md_id=?", (key[3:],)).fetchone()
        if row is None:
            return None
        linked = snapshot(repo, f"al:{row['al_id']}") if row["al_id"] else None
        if linked:
            return {**linked, "title": row["title"], "description": row["description"] or linked["description"]}
        genres, tags = split_tags([t["name"] for t in json.loads(row["tags"] or "[]")])
        return {"title": row["title"], "genres": genres, "tags": tags, "staff": [], "description": row["description"]}
    media_id = int(key[3:])
    m = repo.media([media_id]).get(media_id)
    if m is None:
        return None
    staff = [{"id": p["id"], "name": p["name"]} for p in json.loads(m["staff_roles"] or "[]")]
    return {"title": title_of(m), "genres": json.loads(m["genres"] or "[]"),
            "tags": [t["name"] for t in json.loads(m["tags"] or "[]")], "staff": staff,
            "description": plain_text(m["description"]) or None}


def rate(repo: Repo, key: str, verdict: str, source: str, title: str | None = None) -> bool:
    """Save a verdict. False when the series isn't known here (nothing saved)."""
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}")
    snap = snapshot(repo, key)
    if snap is None:
        return False
    repo.set_feedback(key, verdict, title or snap["title"], source, genres=snap["genres"], tags=snap["tags"],
                      staff=snap["staff"], description=snap["description"])
    return True
