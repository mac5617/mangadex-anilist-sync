"""Settings screen (stories 06 and 18, FR-32): tunables, DB path, auth panels. Secrets are never rendered."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from mdal import titles, tunables
from mdal.services import Services
from mdal.tunables import TUNABLES
from mdal.web.app import get_services, render
from mdal.web.routes.sync import cooldown_info

router = APIRouter()


ENV_KEYS = (
    "MANGADEX_USERNAME", "MANGADEX_PASSWORD", "MANGADEX_CLIENT_ID", "MANGADEX_CLIENT_SECRET",
    "ANILIST_CLIENT_ID", "ANILIST_CLIENT_SECRET", "ANILIST_REDIRECT_URI", "ANILIST_ACCESS_TOKEN",
    "MAL_CLIENT_ID", "MAL_CLIENT_SECRET", "MAL_REDIRECT_URI",
)


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
        "llm_auto": svc.repo.get_setting("llm_auto"),
        "sync_targets": svc.repo.get_setting("sync_targets") or [],
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
    values, errors = tunables.validate(form)
    if errors:
        return render_settings(request, error=" ".join(errors), status_code=400,
                               form_values={t.key: form.get(t.key, "") for t in TUNABLES})
    for key, value in values.items():
        svc.repo.set_setting(key, value)
    tunables.apply(svc)
    return render_settings(request, message="Settings saved.")


@router.post("/settings/titles", response_class=HTMLResponse)
def save_titles(request: Request, language: str = Form("")) -> HTMLResponse:
    """Which AniList title pages show: English (romaji when a series has none) or romaji."""
    if language not in titles.LANGUAGES:
        return render_settings(request, error="Choose English or Romaji.", status_code=400)
    get_services(request).repo.set_setting("title_language", language)
    titles.set_language(language)
    return render_settings(request, message=f"Titles now show in {titles.LANGUAGES[language]}.")


@router.post("/settings/sync-targets", response_class=HTMLResponse)
def save_sync_targets(request: Request, anilist: str = Form(""), mal: str = Form("")) -> HTMLResponse:
    """Which sites Sync covers. MyAnimeList's box is disabled until it's connected, so keep it if it was on."""
    svc = get_services(request)
    keep_mal = "mal" in (svc.repo.get_setting("sync_targets") or []) and not svc.mal.connected
    targets = [t for t, on in (("anilist", anilist == "1"), ("mal", mal == "1" or keep_mal)) if on]
    svc.repo.set_setting("sync_targets", targets)
    names = " and ".join({"anilist": "AniList", "mal": "MyAnimeList"}[t] for t in targets)
    return render_settings(request, message=f"Sync now covers {names}." if targets else "Sync is off for both sites.")


@router.post("/settings/mal-mirror", response_class=HTMLResponse)
def save_mal_mirror(request: Request, on: str = Form("")) -> HTMLResponse:
    get_services(request).repo.set_setting("mal_mirror", on == "1")
    return render_settings(request, message="List edits now also go to MyAnimeList." if on == "1"
                           else "List edits now only go to AniList.")


@router.post("/settings/llm-auto", response_class=HTMLResponse)
def save_llm_auto(request: Request, on: str = Form("")) -> HTMLResponse:
    get_services(request).repo.set_setting("llm_auto", on == "1")
    return render_settings(request, message="Refresh and scans now ask the local model." if on == "1"
                           else "The local model now only runs when you ask it to.")


@router.post("/settings/clear-cooldown", response_class=HTMLResponse)
def clear_cooldown(request: Request) -> HTMLResponse:
    get_services(request).mangadex_guard.clear_cooldown()
    return render_settings(request, message="MangaDex cooldown cleared. Only do this if you're sure MangaDex is reachable again.")


@router.post("/settings/reset-batch", response_class=HTMLResponse)
def reset_batch(request: Request) -> HTMLResponse:
    size = tunables.reset(get_services(request).repo, "anilist_write_batch")
    return render_settings(request, message=f"Write batch size reset to {size}.")
