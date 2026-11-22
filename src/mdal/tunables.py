"""Tunables: the settings you can adjust on the Settings page, with their allowed ranges (architecture §3).

Owns what a valid value is, what resetting one means, and putting saved rates to work on the live request queues.
Values stay in the `settings` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mdal.db.repo import SETTING_DEFAULTS, Repo

if TYPE_CHECKING:
    from mdal.services import Services


@dataclass(frozen=True)
class Tunable:
    key: str
    label: str
    kind: type
    low: float
    high: float
    help: str = ""


# Architecture §3. anilist_rpm and mangadex_rps ranges are from §3; the others are sanity bounds.
TUNABLES = (
    Tunable("anilist_rpm", "AniList requests per minute", int, 1, 30, "AniList's current limit is 30/min."),
    Tunable("anilist_write_batch", "AniList entries per write request", int, 1, 25, "Lowered automatically on a complexity error."),
    Tunable("anilist_search_batch", "Title searches per AniList request", int, 1, 10),
    Tunable("anilist_page_size", "AniList ids per lookup page", int, 1, 50, "50 was confirmed live."),
    Tunable("mangadex_rps", "MangaDex requests per second", float, 0.2, 4, "MangaDex allows about 5/s; stay below."),
    Tunable("mal_rpm", "MyAnimeList requests per minute", int, 1, 60,
            "MyAnimeList publishes no limit; one request per entry written."),
    Tunable("mangaupdates_rps", "MangaUpdates requests per second", float, 0.2, 2,
            "MangaUpdates publishes no limit; series pages and scans use a few requests each."),
    Tunable("new_scan_hours", "Scan MangaDex for new releases every (hours)", int, 0, 168,
            "About 25 MangaDex requests per scan. In between, the newest releases are checked every hour (usually "
            "1–2 requests). 0 turns both off."),
    Tunable("match_auto", "Auto-accept score", float, 0, 1),
    Tunable("match_review", "Review score", float, 0, 1, "Below this, a series is unmatched."),
    Tunable("match_margin", "Auto-accept margin over the runner-up", float, 0, 0.5),
    Tunable("jump_limit", "Largest believable jump in chapters", int, 1, 10000),
)
# Rate setting -> (the Services queue it paces, seconds it is counted over).
RATES = {
    "anilist_rpm": ("anilist_queue", 60.0),
    "mangadex_rps": ("mangadex_queue", 1.0),
    "mal_rpm": ("mal_queue", 60.0),
    "mangaupdates_rps": ("mangaupdates_queue", 1.0),
}


def validate(form: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The Settings form's values, parsed, and what's wrong with them."""
    values: dict[str, Any] = {}
    errors: list[str] = []
    for t in TUNABLES:
        raw = str(form.get(t.key, "")).strip()
        try:
            value: Any = t.kind(float(raw)) if t.kind is int and float(raw).is_integer() else t.kind(raw)
        except ValueError:
            errors.append(f"{t.label}: “{raw}” is not a {'whole number' if t.kind is int else 'number'}.")
            continue
        if not t.low <= value <= t.high:
            errors.append(f"{t.label} must be between {t.low:g} and {t.high:g}.")
            continue
        values[t.key] = value
    if "match_review" in values and "match_auto" in values and not values["match_review"] < values["match_auto"]:
        errors.append("The review score must be lower than the auto-accept score.")
    return values, errors


def apply(services: Services) -> None:
    """Live request queues follow the stored rates immediately."""
    for key, (queue, seconds) in RATES.items():
        getattr(services, queue).set_interval(seconds / services.repo.get_setting(key))


def reset(repo: Repo, key: str) -> Any:
    """Back to the default. Returns it."""
    repo.set_setting(key, SETTING_DEFAULTS[key])
    return SETTING_DEFAULTS[key]
