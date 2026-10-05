"""Your verdicts on recommended series, and how they count towards your taste. Pure: no I/O.

A verdict is a series you rated outside your lists: on its series page, from Ask ("I just read X and
loved it"), or by marking it Not interested. Each becomes a pseudo-entry next to your AniList list,
weighted like a score, so the genres, tags, creators and descriptions it carries shift your profile:

- loved / liked / disliked: like a score of 95 / 75 / 30 (a real list score counts the same);
- not interested: a mild 45, since it can mean "not now" as much as "not for me";
- read: no taste signal; it only stops the series being recommended.
"""

from __future__ import annotations

import json
import re
from typing import Any

from mdal.recommend.fresh import ALIASES, term_key

VERDICTS = {"loved": "Loved it", "liked": "Liked it", "disliked": "Didn't like it",
            "not_interested": "Not interested", "read": "Read it"}
SCORES = {"loved": 95, "liked": 75, "disliked": 30, "not_interested": 45}
READ = ("loved", "liked", "disliked", "read")       # verdicts that mean you've read it
ANILIST_GENRES = {"action", "adventure", "comedy", "drama", "ecchi", "fantasy", "horror", "mahou shoujo", "mecha",
                  "music", "mystery", "psychological", "romance", "sci-fi", "slice of life", "sports",
                  "supernatural", "thriller"}


def split_tags(names: list[str]) -> tuple[list[str], list[str]]:
    """MangaDex mixes genres and themes in one tag list; AniList keeps them apart. (genres, tags)."""
    genres, tags = [], []
    for name in names:
        key = ALIASES.get(term_key(name), term_key(name))
        (genres if key in ANILIST_GENRES else tags).append(name)
    return genres, tags


def pseudo_entry(row: Any) -> dict[str, Any] | None:
    """A verdict as an entry_rows-shaped row for build_profile; None when it carries no taste signal."""
    score = SCORES.get(row["verdict"])
    if score is None:
        return None
    key = row["key"]
    return {
        "media_id": int(key[3:]) if key.startswith("al:") else None, "key": key, "title": row["title"],
        "status": "FEEDBACK", "score": score, "progress": 0, "format": "MANGA", "verdict": row["verdict"],
        "genres": json.loads(row["genres"] or "[]"),
        "tag_ranks": [{"name": t, "rank": 80} for t in json.loads(row["tags"] or "[]")],
        "tags": json.loads(row["tags"] or "[]"),
        "staff": json.loads(row["staff"] or "[]"), "description": row["description"],
    }


# ---- what Ask can learn from a message -----------------------------------------------------------

LOVED = re.compile(r"\b(lov(e|ed|ing)|adored?|amazing|masterpiece|favou?rite|incredible|fantastic|awesome|"
                   r"10/10|one of the best)\b", re.I)
LIKED = re.compile(r"\b(lik(e|ed)\s+it|enjoy(ed)?|good|great|fun|solid|not bad|pretty good|nice)\b", re.I)
DISLIKED = re.compile(r"\b(hat(e|ed)|dislik(e|ed)|didn'?t (like|enjoy)|did not (like|enjoy)|boring|bad|awful|"
                      r"terrible|dropped|disappointing|disappointed|not for me|meh|worst)\b", re.I)
READ_WORDS = re.compile(r"\b(read|finished|completed|caught up|just read|already read|binged)\b", re.I)
WANT_MORE = re.compile(r"\b(another|more like|like (this|it|that)|similar)\b", re.I)


def detect_verdict(text: str) -> str | None:
    """What a message says about a series it names: loved / liked / disliked / read, or None if nothing.

    Asking for more like a series you just read counts as liking it."""
    if DISLIKED.search(text):
        return "disliked"
    if LOVED.search(text) and not re.search(r"\bwould love\b|\bi'?d love\b", text, re.I):
        return "loved"
    if LIKED.search(text):
        return "liked"
    if READ_WORDS.search(text):
        return "liked" if WANT_MORE.search(text) else "read"
    return None


def stronger(new: str, old: str | None) -> bool:
    """Whether a verdict from Ask should replace one already saved: "read" never overwrites a real opinion."""
    return old is None or new != "read" or old == "not_interested"
