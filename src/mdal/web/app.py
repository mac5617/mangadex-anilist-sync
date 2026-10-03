from contextlib import asynccontextmanager
from pathlib import Path

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


def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await services.aclose()

    app = FastAPI(title="MangaDex → AniList sync", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.services = services
    app.state.oauth_states = {}
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    from mdal.web.routes import auth, settings

    app.include_router(auth.router)
    app.include_router(settings.router)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        # Placeholder until the dashboard (story 14).
        return '<!doctype html><title>MangaDex → AniList sync</title><h1>MangaDex → AniList sync</h1><a href="/settings">Settings</a>'

    return app
