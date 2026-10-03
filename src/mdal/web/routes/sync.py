"""Sync start/status and the diff screen (story 14, FR-3/4/26/28).

Rendering never calls an API: everything comes from the DB.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from mdal.db.repo import Repo
from mdal.services import Services
from mdal.sync.estimate import estimate
from mdal.sync.orchestrator import ACTIVE_STATES, ApprovalError, SyncAlreadyRunning, SyncStateError
from mdal.sync.rules import AlMediaInfo, MdInfo, completion_info, completion_label
from mdal.web.app import get_services, render, templates

router = APIRouter()

MANGADEX_TITLE_URL = "https://mangadex.org/title/{}"
FILTERS = ("write", "flag", "skip", "all")


def status_context(svc: Services, message: str | None = None) -> dict[str, Any]:
    run = svc.repo.latest_run()
    return {
        "run": run,
        "active": bool(run and run["state"] in ACTIVE_STATES),
        "status_message": message,
        "resumable": svc.orchestrator.resumable_run(),
    }


def _status_fragment(request: Request, message: str | None = None, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_sync_status.html", status_context(get_services(request), message), status_code=status_code
    )


@router.post("/sync", response_class=HTMLResponse)
async def start_sync(request: Request) -> HTMLResponse:
    try:
        await get_services(request).orchestrator.start_run()
    except SyncAlreadyRunning:
        return _status_fragment(request, "A sync is already running.", status_code=409)
    return _status_fragment(request)


@router.get("/sync/status", response_class=HTMLResponse)
def sync_status(request: Request) -> HTMLResponse:
    return _status_fragment(request)


# ---- diff -------------------------------------------------------------------


@dataclass(frozen=True)
class DiffRow:
    md_id: str
    md_title: str
    md_url: str
    al_title: str | None
    al_url: str | None
    al_progress: int | None
    md_progress: int | None
    action: str
    flag_kind: str | None
    reason: str | None
    hint: str | None
    unresolved: int
    set_status: str | None
    status_label: str | None
    status_only: bool
    selectable: bool
    checked: bool
    complete_checked: bool


def diff_rows(repo: Repo, run_id: int) -> list[DiffRow]:
    """Before the first live write nothing is pre-selected: that write must be a single, deliberate pick."""
    preselect = bool(repo.get_setting("first_write_done"))
    rows = []
    for r in repo.diff_rows(run_id):
        status_label = None
        if r["set_status"] == "COMPLETED":
            total = completion_info(
                AlMediaInfo(r["al_chapters"], r["al_media_status"]), MdInfo(r["pub_status"], r["last_chapter"])
            ).total
            status_label = completion_label(r["al_status"], r["status_source"], total)
        selectable = r["action"] in ("write", "flag") and r["flag_kind"] != "exceeds_total"
        rows.append(DiffRow(
            md_id=r["md_id"],
            md_title=r["md_title"] or r["md_id"],
            md_url=MANGADEX_TITLE_URL.format(r["md_id"]),
            al_title=r["romaji"] or r["english"] or r["native"],
            al_url=r["site_url"],
            al_progress=r["al_progress"],
            md_progress=r["md_progress"],
            action=r["action"],
            flag_kind=r["flag_kind"],
            reason=r["reason"],
            hint=r["hint"],
            unresolved=r["unresolved_reads"],
            set_status=r["set_status"],
            status_label=status_label,
            status_only=bool(r["set_status"]) and r["md_progress"] is not None and r["md_progress"] == r["al_progress"],
            selectable=selectable,
            checked=bool(r["approved"]) if r["write_state"] != "none" else (preselect and r["action"] == "write"),
            complete_checked=bool(r["status_approved"]),
        ))
    return rows


@dataclass(frozen=True)
class Estimate:
    selected: int
    to_complete: int
    requests: int
    seconds: int


def compute_estimate(repo: Repo, rows: list[DiffRow], selected: Iterable[str], completing: Iterable[str]) -> Estimate:
    """Pure DB/maths: the write-phase cost of the current selection."""
    chosen, marks = set(selected), set(completing)
    n = to_complete = 0
    for row in rows:
        if not row.selectable or row.md_id not in chosen:
            continue
        completes = bool(row.set_status) and row.md_id in marks
        if row.status_only and not completes:
            continue  # nothing left to send
        n += 1
        to_complete += completes
    req, sec = estimate(n, repo.get_setting("anilist_write_batch"), repo.get_setting("anilist_rpm"))
    return Estimate(n, to_complete, req, sec)


def default_estimate(repo: Repo, rows: list[DiffRow]) -> Estimate:
    return compute_estimate(
        repo, rows, [r.md_id for r in rows if r.checked], [r.md_id for r in rows if r.complete_checked]
    )


def _run_or_404(repo: Repo, run_id: int) -> Any:
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "no such sync run")
    return run


@router.get("/sync/latest")
def latest_sync(request: Request) -> RedirectResponse:
    run = get_services(request).repo.latest_run()
    return RedirectResponse(f"/sync/{run['run_id']}" if run else "/", status_code=303)


def _diff_page(request: Request, run_id: int, f: str = "write", error: str | None = None, status_code: int = 200) -> HTMLResponse:
    repo = get_services(request).repo
    run = _run_or_404(repo, run_id)
    rows = diff_rows(repo, run_id)
    counts = {a: sum(r.action == a for r in rows) for a in ("write", "flag", "skip")}
    counts["all"] = len(rows)
    return render(request, "diff.html", {
        "run": run, "rows": rows, "counts": counts, "est": default_estimate(repo, rows),
        "filter": f if f in FILTERS else "write",
        "approvable": run["state"] == "diffed",
        "first_write_done": bool(repo.get_setting("first_write_done")),
        "error": error,
    }, status_code=status_code)


@router.get("/sync/{run_id}", response_class=HTMLResponse)
def diff_page(request: Request, run_id: int, f: str = "write") -> HTMLResponse:
    return _diff_page(request, run_id, f)


@router.post("/sync/{run_id}/approve", response_class=HTMLResponse)
async def approve(request: Request, run_id: int) -> Response:
    svc = get_services(request)
    _run_or_404(svc.repo, run_id)
    form = await request.form()
    try:
        await svc.orchestrator.approve(run_id, form.getlist("sel"), form.getlist("mc"), form.getlist("ov"))
    except (ApprovalError, SyncAlreadyRunning) as exc:
        return _diff_page(request, run_id, str(form.get("f") or "write"), error=str(exc), status_code=400)
    return RedirectResponse("/", status_code=303)


@router.post("/sync/{run_id}/resume", response_class=HTMLResponse)
async def resume(request: Request, run_id: int) -> HTMLResponse:
    svc = get_services(request)
    _run_or_404(svc.repo, run_id)
    try:
        await svc.orchestrator.resume(run_id)
    except (SyncStateError, SyncAlreadyRunning) as exc:
        return _status_fragment(request, str(exc), status_code=409)
    return _status_fragment(request)


@router.post("/sync/{run_id}/estimate", response_class=HTMLResponse)
async def diff_estimate(request: Request, run_id: int) -> HTMLResponse:
    repo = get_services(request).repo
    _run_or_404(repo, run_id)
    form = await request.form()
    est = compute_estimate(repo, diff_rows(repo, run_id), form.getlist("sel"), form.getlist("mc"))
    return templates.TemplateResponse(request, "_estimate.html", {"est": est})


@router.post("/sync/{run_id}/discard", response_class=HTMLResponse)
def diff_discard(request: Request, run_id: int) -> HTMLResponse:
    svc = get_services(request)
    try:
        svc.orchestrator.discard(run_id)
    except SyncStateError as exc:
        return render(request, "message.html", {"error": str(exc), "back": f"/sync/{run_id}"}, status_code=409)
    return render(request, "message.html", {"message": f"Sync #{run_id} discarded. Nothing was written.", "back": "/"})
