"""Not on my list + "add to AniList" (story 17, FR-30)."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from mdal.clients.anilist import AniListError
from mdal.sync import add_entry
from mdal.sync.orchestrator import refresh_item
from mdal.sync.rules import STATUS_LABELS
from mdal.web.app import get_services, render, templates

router = APIRouter()


def _row_response(request: Request, md_id: str, *, error: str | None = None, done: str | None = None,
                  status_code: int = 200) -> HTMLResponse:
    row = next((r for r in add_entry.not_listed_rows(get_services(request).repo) if r.md_id == md_id), None)
    return templates.TemplateResponse(request, "_notlisted_row.html", {
        "r": row, "md_id": md_id, "row_error": error, "done": done,
        "statuses": add_entry.ADD_STATUSES, "labels": STATUS_LABELS,
    }, status_code=status_code)


@router.get("/not-listed", response_class=HTMLResponse)
def not_listed(request: Request) -> HTMLResponse:
    svc = get_services(request)
    return render(request, "notlisted.html", {
        "rows": add_entry.not_listed_rows(svc.repo),
        "statuses": add_entry.ADD_STATUSES, "labels": STATUS_LABELS,
        "first_write_done": bool(svc.repo.get_setting("first_write_done")),
    })


@router.post("/not-listed/{md_id}/add", response_class=HTMLResponse)
async def add(request: Request, md_id: str, status: str = Form(...), progress: int = Form(...)) -> HTMLResponse:
    svc = get_services(request)
    if svc.orchestrator.lock.locked():
        return _row_response(request, md_id, error="A sync is running; add entries after it finishes.", status_code=409)
    try:
        entry = await add_entry.add(svc.repo, svc.anilist, md_id, status, progress, svc.orchestrator.user_id)
    except add_entry.AddEntryError as exc:
        return _row_response(request, md_id, error=str(exc), status_code=400)
    except AniListError as exc:
        return _row_response(request, md_id, error=f"AniList error: {exc}", status_code=502)
    refresh_item(svc.repo, md_id)
    label = STATUS_LABELS.get(entry["status"], entry["status"])
    return _row_response(request, md_id, done=f"Added to AniList as {label}, progress {entry['progress']}.")
