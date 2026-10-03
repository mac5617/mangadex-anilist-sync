from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from mdal import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="MangaDex → AniList sync", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        # Placeholder until the dashboard (story 14).
        return "<!doctype html><title>MangaDex → AniList sync</title><h1>MangaDex → AniList sync</h1>"

    return app
