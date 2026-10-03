from contextlib import asynccontextmanager
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

    from mdal.web.routes import auth, dashboard, review, settings, sync

    app.include_router(auth.router)
    app.include_router(settings.router)
    app.include_router(dashboard.router)
    app.include_router(sync.router)
    app.include_router(review.router)

    return app
