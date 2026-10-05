"""Which of a series' titles to show. Pure: no I/O.

AniList gives each series a romaji, an English and a native title. Pages show the English one
(Settings → Titles can switch to romaji), falling back to the others when it's missing. Matching
never uses this: it compares every title a series has.
"""

from __future__ import annotations

from typing import Any

LANGUAGES = {"english": "English", "romaji": "Romaji"}
_preferred = {"language": "english"}


def set_language(language: str) -> None:
    if language not in LANGUAGES:
        raise ValueError(f"unknown title language {language!r}")
    _preferred["language"] = language


def language() -> str:
    return _preferred["language"]


def pick(romaji: str | None, english: str | None, native: str | None = None, fallback: str = "") -> str:
    first, second = (english, romaji) if _preferred["language"] == "english" else (romaji, english)
    return first or second or native or fallback


def other(romaji: str | None, english: str | None) -> str | None:
    """The title not shown, when it differs (shown alongside, e.g. on match candidates)."""
    shown = pick(romaji, english)
    rest = romaji if shown == english else english
    return rest if rest and rest != shown else None


def of(row: Any) -> str:
    """For an al_media row (or any mapping with romaji/english/native and media_id)."""
    return pick(row["romaji"], row["english"], row["native"], f"#{row['media_id']}")
