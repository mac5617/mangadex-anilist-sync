"""Not on my list + "add to AniList" (story 17, FR-30)."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from mdal.sync import add_entry
from mdal.web.routes.series import _after_add
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
        "with_mal": svc.your_list.mirroring(),
    })


@router.post("/not-listed/{md_id}/add", response_class=HTMLResponse)
async def add(request: Request, md_id: str, status: str = Form(...), progress: int = Form(...)) -> HTMLResponse:
    svc = get_services(request)
    edit = await svc.your_list.add(f"md:{md_id}", status, progress)
    if edit.refused:
        return _row_response(request, md_id, error=edit.note, status_code=409)
    if not edit.sites[0].saved:
        return _row_response(request, md_id, error=edit.note, status_code=400)
    _after_add(svc, edit)
    return _row_response(request, md_id, done=edit.note)
