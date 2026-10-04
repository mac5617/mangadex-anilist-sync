from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mdal import __version__
from mdal.services import Services

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")

MD_COVER = "https://uploads.mangadex.org/covers/{md_id}/{file}.256.jpg"


def md_cover_url(md_id: str, cover_file: str | None) -> str | None:
    """MangaDex's smallest thumbnail. Loaded by the browser, lazily, with no referrer (api-notes risk 6)."""
    return MD_COVER.format(md_id=md_id, file=cover_file) if cover_file else None


def when(iso: str | None) -> str:
    """'2026-10-03T20:43:45+00:00' -> '2026-10-03 20:43 UTC' (stored times are UTC)."""
    if not iso:
        return "—"
    text = str(iso).replace("T", " ")
    return text[:16] + " UTC" if text.endswith("+00:00") and len(text) >= 16 else text


templates.env.globals["md_cover"] = md_cover_url
templates.env.filters["when"] = when
templates.env.globals["change_labels"] = {
    "write": "update", "add": "new entry", "skip": "skip", "flag": "flagged",
    "implausible": "unusual", "exceeds_total": "over total",
}


def num(value: float | int | None) -> str:
    """1234 -> '1,234'; whole floats lose their '.0'."""
    if value is None:
        return "—"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


templates.env.filters["num"] = num


def date_from_unix(value: int | None) -> str:
    """AniList updatedAt (Unix seconds) -> '2026-10-03'."""
    if not value:
        return "—"
    return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d")


templates.env.filters["date_from_unix"] = date_from_unix


def get_services(request: Request) -> Services:
    return request.app.state.services


def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200) -> HTMLResponse:
    """TemplateResponse plus the nav counts every full page shows."""
    repo = get_services(request).repo
    ctx = {"nav": {"review": repo.review_count(), "not_on_list": len(repo.not_on_list())}, **(context or {})}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services.orchestrator.recover_interrupted()
        yield
        await services.aclose()

    app = FastAPI(title="MangaDex → AniList sync", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.services = services
    app.state.oauth_states = {}
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    from mdal.web.routes import auth, dashboard, history, notlisted, review, settings, stats, sync

    app.include_router(auth.router)
    app.include_router(settings.router)
    app.include_router(dashboard.router)
    app.include_router(sync.router)
    app.include_router(review.router)
    app.include_router(notlisted.router)
    app.include_router(history.router)
    app.include_router(stats.router)

    return app
