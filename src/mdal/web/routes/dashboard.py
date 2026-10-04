"""Dashboard (story 14, FR-27)."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from mdal.stats import mismatch_count
from mdal.web.app import get_services, render
from mdal.web.routes.sync import status_context

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    svc = get_services(request)
    s = svc.settings
    context = {
        "auth": {
            "anilist": bool(svc.anilist_token()),
            "anilist_user": svc.repo.get_setting("anilist_user_name"),
            "mangadex": all([s.mangadex_username, s.mangadex_password.get_secret_value(), s.mangadex_client_id,
                             s.mangadex_client_secret.get_secret_value()]),
            "mangadex_user": s.mangadex_username,
        },
        **status_context(svc),
        "mismatches": mismatch_count(svc.repo),
    }
    return render(request, "dashboard.html", context)
