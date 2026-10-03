"""AniList connect/disconnect and the MangaDex login check (story 06).

The one web module allowed to call clients directly (architecture §2).
"""

import logging
import secrets
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from mdal.clients.anilist import AniListAuthError, AniListError
from mdal.clients.anilist_oauth import AniListOAuthError
from mdal.clients.mangadex import MangaDexAuthError, MangaDexError
from mdal.fetch.anilist_list import viewer
from mdal.web.app import get_services
from mdal.web.routes.settings import render_settings

log = logging.getLogger(__name__)
router = APIRouter()

STATE_TTL = 600.0


def _oauth_states(request: Request) -> dict[str, float]:
    states: dict[str, float] = request.app.state.oauth_states
    now = time.monotonic()
    for key in [k for k, expiry in states.items() if expiry < now]:
        del states[key]
    return states


@router.get("/auth/anilist/start")
def anilist_start(request: Request) -> Response:
    svc = get_services(request)
    if not svc.settings.anilist_client_id:
        return render_settings(request, error="ANILIST_CLIENT_ID is not set in .env.", status_code=400)
    state = secrets.token_urlsafe(24)
    _oauth_states(request)[state] = time.monotonic() + STATE_TTL
    return RedirectResponse(svc.oauth.authorize_url(state), status_code=303)


@router.get("/auth/anilist/callback", response_class=HTMLResponse)
async def anilist_callback(request: Request, code: str = "", state: str = "") -> HTMLResponse:
    if not state or _oauth_states(request).pop(state, None) is None or not code:
        return render_settings(request, error="The AniList sign-in link expired or was not started here. Try Connect again.", status_code=400)
    try:
        token = await get_services(request).oauth.exchange_code(code)
    except AniListOAuthError as exc:
        return render_settings(request, error=str(exc), status_code=502)
    return await _connect(request, token)


@router.post("/auth/anilist/token", response_class=HTMLResponse)
async def anilist_pin_token(request: Request, token: str = Form("")) -> HTMLResponse:
    token = token.strip()
    if not token:
        return render_settings(request, error="Paste the token AniList showed you.", status_code=400)
    return await _connect(request, token)


async def _connect(request: Request, token: str) -> HTMLResponse:
    svc = get_services(request)
    svc.store_anilist_token(token)
    try:
        user = await viewer(svc.anilist)
    except AniListAuthError:
        svc.clear_anilist_token()
        return render_settings(request, error="AniList rejected that token. Nothing was saved.", status_code=400)
    except AniListError as exc:
        log.warning("Saved AniList token but could not verify it: %s", exc)
        return render_settings(request, error=f"Token saved, but AniList could not be reached to verify it: {exc}", status_code=502)
    svc.repo.set_setting("anilist_user_id", user["id"])
    svc.repo.set_setting("anilist_user_name", user["name"])
    return render_settings(request, message=f"Connected to AniList as {user['name']}.")


@router.post("/auth/anilist/disconnect", response_class=HTMLResponse)
def anilist_disconnect(request: Request) -> HTMLResponse:
    get_services(request).clear_anilist_token()
    return render_settings(request, message="AniList disconnected. The token was removed from .env.")


@router.post("/auth/mangadex/check", response_class=HTMLResponse)
async def mangadex_check(request: Request) -> HTMLResponse:
    try:
        await get_services(request).mangadex.check_login()
    except MangaDexAuthError as exc:
        return render_settings(request, error=str(exc), status_code=400)
    except MangaDexError as exc:
        return render_settings(request, error=f"MangaDex check failed: {exc}", status_code=502)
    return render_settings(request, message="MangaDex login works.")
