"""Tunables: the settings you can adjust on the Settings page, with their allowed ranges (architecture §3).

Owns what a valid value is, what resetting one means, and putting saved rates to work on the live request queues.
Values stay in the `settings` table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mdal import titles
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
SYNC_SITES = ("anilist", "mal")
_BY_KEY = {t.key: t for t in TUNABLES}
_NAMES = {"title_language": "title language", "sync_targets": "sites Sync covers"}

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


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_valid(key: str, value: Any) -> bool:
    """Whether a stored or restored value is one this setting can hold."""
    if key in _BY_KEY:
        t = _BY_KEY[key]
        return _is_number(value) and t.low <= value <= t.high and (t.kind is not int or float(value).is_integer())
    if key == "title_language":
        return isinstance(value, str) and value in titles.LANGUAGES
    if key == "sync_targets":
        return isinstance(value, list) and all(isinstance(t, str) and t in SYNC_SITES for t in value)
    default = SETTING_DEFAULTS[key]
    if default is None:          # saved job data
        return value is None or isinstance(value, (list, dict, str)) or _is_number(value)
    if isinstance(default, bool):
        return isinstance(value, bool)
    return _is_number(value) if _is_number(default) else isinstance(value, type(default))


def restorable(repo: Repo, incoming: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The settings from a backup that can be put back, and the names of those that can't (yours stay)."""
    good = {k: int(v) if k in _BY_KEY and _BY_KEY[k].kind is int and is_valid(k, v) else v
            for k, v in incoming.items() if is_valid(k, v)}
    bad = [k for k in incoming if k not in good]
    review = good.get("match_review", repo.get_setting("match_review"))
    auto = good.get("match_auto", repo.get_setting("match_auto"))
    if not (_is_number(review) and _is_number(auto) and review < auto):
        for key in ("match_review", "match_auto"):
            if key in good:
                del good[key]
                bad.append(key)
    return good, [name(k) for k in bad]


def name(key: str) -> str:
    return _BY_KEY[key].label if key in _BY_KEY else _NAMES.get(key, key.replace("_", " "))


def apply(services: Services) -> None:
    """Live request queues and the title language follow what's stored immediately."""
    for key, (queue, seconds) in RATES.items():
        getattr(services, queue).set_interval(seconds / services.repo.get_setting(key))
    titles.set_language(services.repo.get_setting("title_language"))


def reset(repo: Repo, key: str) -> Any:
    """Back to the default. Returns it."""
    repo.set_setting(key, SETTING_DEFAULTS[key])
    return SETTING_DEFAULTS[key]
