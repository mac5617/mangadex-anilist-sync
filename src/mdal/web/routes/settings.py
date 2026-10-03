from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from mdal.web.app import get_services, templates

router = APIRouter()


def render_settings(request: Request, *, message: str | None = None, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    svc = get_services(request)
    s = svc.settings
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
    }
    return templates.TemplateResponse(request, "settings.html", context, status_code=status_code)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return render_settings(request)
