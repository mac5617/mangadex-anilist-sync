import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mdal import __version__
from mdal.recommend.fresh import MD_COVER
from mdal.services import Services

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")

def md_cover_url(md_id: str, cover_file: str | None) -> str | None:
    """MangaDex's smallest thumbnail. Loaded by the browser, lazily, with no referrer (api-notes risk 6)."""
    return MD_COVER.format(md_id=md_id, file=cover_file) if cover_file else None


def when(iso: str | None) -> str:
    """'2026-10-04T02:36:36+00:00' -> '2026-10-03 10:36 PM' in this computer's time zone (times are stored in UTC).
    Anything that isn't a full timestamp (a plain date, say) is shown as it is."""
    if not iso:
        return "—"
    try:
        moment = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)
    if moment.tzinfo is None:
        return str(iso).replace("T", " ")[:16]
    return moment.astimezone().strftime("%Y-%m-%d ") + clock(moment.astimezone())


def clock(moment: datetime) -> str:
    """12-hour time without a leading zero: '9:05 PM' (Windows' strftime has no %-I)."""
    return moment.strftime("%I:%M %p").lstrip("0")


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


def model_label(name: str | None) -> str:
    """'hf.co/unsloth/Qwen3.5-35B-A3B-GGUF:UD-Q6_K_XL' -> 'Qwen3.5-35B-A3B'; Ollama library names stay as they are."""
    if not name or not name.startswith("hf.co/"):
        return name or ""
    base = name.rsplit("/", 1)[-1].split(":", 1)[0]
    return base[:-5] if base.upper().endswith("-GGUF") else base


templates.env.filters["model_label"] = model_label


def date_from_unix(value: int | None) -> str:
    """AniList updatedAt (Unix seconds) -> '2026-10-03', in this computer's time zone."""
    if not value:
        return "—"
    return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d")


templates.env.filters["date_from_unix"] = date_from_unix


def graph_json(graph: dict[str, Any]) -> str:
    """JSON for a <script type="application/json"> block. "</" is escaped so no title can close the element."""
    return json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")


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
        schedule = asyncio.create_task(services.releases.run_schedule())
        yield
        schedule.cancel()
        await services.aclose()

    app = FastAPI(title="Shiori", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.services = services
    app.state.oauth_states = {}
    app.state.mal_states = {}
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        """Browsers ask for this path on their own, whatever the page links."""
        return FileResponse(WEB_DIR / "static" / "favicon.ico", media_type="image/x-icon")

    from mdal.web.routes import auth, backup, dashboard, discover, history, insights, list as list_routes, notlisted, review, search, series, settings, stats, sync

    app.include_router(auth.router)
    app.include_router(settings.router)
    app.include_router(dashboard.router)
    app.include_router(sync.router)
    app.include_router(review.router)
    app.include_router(notlisted.router)
    app.include_router(history.router)
    app.include_router(stats.router)
    app.include_router(list_routes.router)
    app.include_router(search.router)
    app.include_router(insights.router)
    app.include_router(backup.router)
    app.include_router(series.router)   # before discover: /discover/ratings must win over /discover/{page}
    app.include_router(discover.router)

    return app
