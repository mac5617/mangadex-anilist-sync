"""Settings screen (stories 06 and 18, FR-32): tunables, DB path, auth panels. Secrets are never rendered."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from mdal import titles
from mdal.services import Services
from mdal.web.app import get_services, render
from mdal.web.routes.sync import cooldown_info

router = APIRouter()


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
            "0 turns the background scan off. About 25 MangaDex requests per scan."),
    Tunable("match_auto", "Auto-accept score", float, 0, 1),
    Tunable("match_review", "Review score", float, 0, 1, "Below this, a series is unmatched."),
    Tunable("match_margin", "Auto-accept margin over the runner-up", float, 0, 0.5),
    Tunable("jump_limit", "Largest believable jump in chapters", int, 1, 10000),
)
ENV_KEYS = (
    "MANGADEX_USERNAME", "MANGADEX_PASSWORD", "MANGADEX_CLIENT_ID", "MANGADEX_CLIENT_SECRET",
    "ANILIST_CLIENT_ID", "ANILIST_CLIENT_SECRET", "ANILIST_REDIRECT_URI", "ANILIST_ACCESS_TOKEN",
    "MAL_CLIENT_ID", "MAL_CLIENT_SECRET", "MAL_REDIRECT_URI",
)


def validate_tunables(form: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
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


def apply_rates(svc: Services) -> None:
    """Live PacedQueues follow the stored budgets immediately."""
    svc.anilist_queue.set_interval(60.0 / svc.repo.get_setting("anilist_rpm"))
    svc.mangadex_queue.set_interval(1.0 / svc.repo.get_setting("mangadex_rps"))
    svc.mal_queue.set_interval(60.0 / svc.repo.get_setting("mal_rpm"))
    svc.mangaupdates_queue.set_interval(1.0 / svc.repo.get_setting("mangaupdates_rps"))


def _env_status(svc: Services) -> list[tuple[str, bool]]:
    s = svc.settings
    present = {
        "MANGADEX_USERNAME": s.mangadex_username, "MANGADEX_PASSWORD": s.mangadex_password.get_secret_value(),
        "MANGADEX_CLIENT_ID": s.mangadex_client_id, "MANGADEX_CLIENT_SECRET": s.mangadex_client_secret.get_secret_value(),
        "ANILIST_CLIENT_ID": s.anilist_client_id, "ANILIST_CLIENT_SECRET": s.anilist_client_secret.get_secret_value(),
        "ANILIST_REDIRECT_URI": s.anilist_redirect_uri, "ANILIST_ACCESS_TOKEN": s.anilist_access_token.get_secret_value(),
        "MAL_CLIENT_ID": s.mal_client_id, "MAL_CLIENT_SECRET": s.mal_client_secret.get_secret_value(),
        "MAL_REDIRECT_URI": s.mal_redirect_uri,
    }
    return [(k, bool(present[k])) for k in ENV_KEYS]


def render_settings(
    request: Request, *, message: str | None = None, error: str | None = None, status_code: int = 200,
    form_values: dict[str, Any] | None = None,
) -> HTMLResponse:
    svc = get_services(request)
    s = svc.settings
    stored = svc.repo.all_settings()
    context = {
        "message": message,
        "error": error,
        # Only booleans and non-secret identifiers reach the template.
        "anilist": {
            "client_configured": bool(s.anilist_client_id and s.anilist_client_secret.get_secret_value()),
            "connected": bool(svc.anilist_token()),
            "user_name": svc.repo.get_setting("anilist_user_name"),
            "uses_pin": svc.oauth.uses_pin,
            "pin_url": svc.oauth.pin_url(),
        },
        "mangadex": {
            "configured": all(
                [s.mangadex_username, s.mangadex_password.get_secret_value(), s.mangadex_client_id,
                 s.mangadex_client_secret.get_secret_value()]
            ),
            "username": s.mangadex_username,
        },
        "mal": {
            "client_configured": bool(s.mal_client_id),
            "connected": svc.mal.connected,
            "user_name": svc.repo.get_setting("mal_user_name"),
            "redirect_uri": s.mal_redirect_uri,
        },
        "tunables": [(t, (form_values or {}).get(t.key, stored[t.key])) for t in TUNABLES],
        "db_path": str(s.db_path),
        "title_language": titles.language(), "title_languages": titles.LANGUAGES,
        "mal_mirror": svc.repo.get_setting("mal_mirror"),
        "cooldown": cooldown_info(svc),
        "env_status": _env_status(svc),
    }
    return render(request, "settings.html", context, status_code=status_code)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return render_settings(request)


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(request: Request) -> HTMLResponse:
    svc = get_services(request)
    form = dict(await request.form())
    values, errors = validate_tunables(form)
    if errors:
        return render_settings(request, error=" ".join(errors), status_code=400,
                               form_values={t.key: form.get(t.key, "") for t in TUNABLES})
    for key, value in values.items():
        svc.repo.set_setting(key, value)
    apply_rates(svc)
    return render_settings(request, message="Settings saved.")


@router.post("/settings/titles", response_class=HTMLResponse)
def save_titles(request: Request, language: str = Form("")) -> HTMLResponse:
    """Which AniList title pages show: English (romaji when a series has none) or romaji."""
    if language not in titles.LANGUAGES:
        return render_settings(request, error="Choose English or Romaji.", status_code=400)
    get_services(request).repo.set_setting("title_language", language)
    titles.set_language(language)
    return render_settings(request, message=f"Titles now show in {titles.LANGUAGES[language]}.")


@router.post("/settings/mal-mirror", response_class=HTMLResponse)
def save_mal_mirror(request: Request, on: str = Form("")) -> HTMLResponse:
    get_services(request).repo.set_setting("mal_mirror", on == "1")
    return render_settings(request, message="List edits now also go to MyAnimeList." if on == "1"
                           else "List edits now only go to AniList.")


@router.post("/settings/clear-cooldown", response_class=HTMLResponse)
def clear_cooldown(request: Request) -> HTMLResponse:
    get_services(request).mangadex_guard.clear_cooldown()
    return render_settings(request, message="MangaDex cooldown cleared. Only do this if you're sure MangaDex is reachable again.")


@router.post("/settings/reset-batch", response_class=HTMLResponse)
def reset_batch(request: Request) -> HTMLResponse:
    get_services(request).repo.set_setting("anilist_write_batch", 10)
    return render_settings(request, message="Write batch size reset to 10.")
