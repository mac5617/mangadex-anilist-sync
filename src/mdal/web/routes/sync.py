"""Sync start/status and the diff screen (story 14, FR-3/4/26/28).

Rendering never calls an API: everything comes from the DB.
"""

from __future__ import annotations

import time
from datetime import datetime
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from mdal import titles
from mdal.db.repo import Repo
from mdal.services import Services
from mdal.sync.estimate import estimate
from mdal.sync.orchestrator import (
    ACTIVE_STATES,
    DISMISSED_PREFIX,
    SITE_NAMES,
    TARGETS,
    ApprovalError,
    SyncAlreadyRunning,
    SyncCoolingDown,
    SyncStateError,
    open_diff,
)
from mdal.sync.rules import AlMediaInfo, MdInfo, completion_info, completion_label
from mdal.web.app import clock, get_services, md_cover_url, render, templates

router = APIRouter()

MANGADEX_TITLE_URL = "https://mangadex.org/title/{}"
MAL_MANGA_URL = "https://myanimelist.net/manga/{}"
FILTERS = ("write", "add", "flag", "skip", "all")


def cooldown_info(svc: Services) -> dict[str, Any] | None:
    active = svc.mangadex_guard.cooldown()
    if not active:
        return None
    until, reason = active
    left = until - time.time()
    if left <= 0:
        return None
    return {"until": clock(datetime.fromtimestamp(until)), "minutes": max(1, round(left / 60)), "reason": reason}


def status_context(svc: Services, message: str | None = None) -> dict[str, Any]:
    run = svc.repo.latest_run()
    return {
        "cooldown": cooldown_info(svc),
        "run": run,
        "site": SITE_NAMES[run["target"]] if run else None,
        "active": bool(run and run["state"] in ACTIVE_STATES),
        "status_message": message,
        "resumable": svc.orchestrator.resumable_run(),
        "open_diffs": [(SITE_NAMES[t], d["run_id"]) for t in TARGETS if (d := open_diff(svc.repo, t))],
        "mal_connected": svc.mal.connected,
        "md_fresh": svc.orchestrator.md_snapshot_fresh(),
        "sync_sites": [SITE_NAMES[t] for t in svc.orchestrator.sync_targets()],
        "queued": [SITE_NAMES[t] for t in svc.orchestrator.queued],
    }


def _status_fragment(request: Request, message: str | None = None, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_sync_status.html", status_context(get_services(request), message), status_code=status_code
    )


@router.post("/sync", response_class=HTMLResponse)
async def start_sync(request: Request) -> HTMLResponse:
    """Sync every site turned on in Settings (target "all", the default), or just one."""
    target = str((await request.form()).get("target") or "all")
    orchestrator = get_services(request).orchestrator
    try:
        if target == "all":
            await orchestrator.start_sync()
        else:
            await orchestrator.start_run(target)
    except SyncAlreadyRunning:
        return _status_fragment(request, "A sync is already running.", status_code=409)
    except (SyncCoolingDown, SyncStateError) as exc:
        return _status_fragment(request, str(exc), status_code=409)
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
    cover: str | None
    al_title: str | None   # the series on the run's site (AniList or MyAnimeList)
    al_url: str | None
    site: str
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
    """Writes and adds are pre-selected; flagged rows wait for an explicit tick (and override)."""
    rows = []
    for r in repo.diff_rows(run_id):
        site = SITE_NAMES[r["target"]]
        if r["target"] == "mal":
            title = r["mal_title"] or titles.pick(r["romaji"], r["english"], r["native"])
            url = MAL_MANGA_URL.format(r["mal_id"]) if r["mal_id"] else None
        else:
            title, url = titles.pick(r["romaji"], r["english"], r["native"]), r["site_url"]
        status_label = None
        is_new = r["al_entry_id"] is None and r["action"] in ("add", "flag")
        if r["set_status"] == "COMPLETED":
            total = completion_info(
                AlMediaInfo(r["al_chapters"], r["al_media_status"]), MdInfo(r["pub_status"], r["last_chapter"])
            ).total
            status_label = completion_label(r["al_status"], r["status_source"], total, site)
            if is_new:
                status_label = "New entry → " + status_label.split(" → ", 1)[1] + "; untick to add as Reading"
        elif is_new:
            status_label = "New entry: Reading"
        selectable = r["action"] in ("write", "add", "flag") and r["flag_kind"] != "exceeds_total"
        rows.append(DiffRow(
            md_id=r["md_id"],
            md_title=r["md_title"] or r["md_id"],
            md_url=MANGADEX_TITLE_URL.format(r["md_id"]),
            cover=md_cover_url(r["md_id"], r["cover_file"]),
            al_title=title,
            al_url=url,
            site=site,
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
            checked=bool(r["approved"]) if r["write_state"] != "none" else r["action"] in ("write", "add"),
            complete_checked=bool(r["status_approved"]),
        ))
    return rows


@dataclass(frozen=True)
class Estimate:
    selected: int
    to_complete: int
    requests: int
    seconds: int
    site: str = "AniList"


def compute_estimate(repo: Repo, rows: list[DiffRow], selected: Iterable[str], completing: Iterable[str],
                     target: str = "anilist") -> Estimate:
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
    if target == "mal":  # one request per entry
        req, sec = estimate(n, 1, repo.get_setting("mal_rpm"))
    else:
        req, sec = estimate(n, repo.get_setting("anilist_write_batch"), repo.get_setting("anilist_rpm"))
    return Estimate(n, to_complete, req, sec, SITE_NAMES[target])


def default_estimate(repo: Repo, rows: list[DiffRow], target: str = "anilist") -> Estimate:
    return compute_estimate(
        repo, rows, [r.md_id for r in rows if r.checked], [r.md_id for r in rows if r.complete_checked], target
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


def _counts(rows: list[DiffRow]) -> dict[str, int]:
    counts = {a: sum(r.action == a for r in rows) for a in ("write", "add", "flag", "skip")}
    counts["all"] = len(rows)
    counts["complete"] = sum(1 for r in rows if r.set_status and r.selectable)
    return counts


def _diff_page(request: Request, run_id: int, f: str | None = None, error: str | None = None,
               status_code: int = 200) -> HTMLResponse:
    svc = get_services(request)
    repo = svc.repo
    run = _run_or_404(repo, run_id)
    rows = diff_rows(repo, run_id)
    counts = _counts(rows)
    if f not in FILTERS:  # no tab asked for: open on the first one with something in it
        f = next((k for k in ("write", "add", "flag", "skip") if counts[k]), "write")
    return render(request, "diff.html", {
        "run": run, "rows": rows, "counts": counts, "est": default_estimate(repo, rows, run["target"]),
        "site": SITE_NAMES[run["target"]],
        "filter": f,
        "approvable": run["state"] == "diffed",
        "restorable": svc.orchestrator.can_restore(run_id),
        "error": error,
    }, status_code=status_code)


def _row_update(request: Request, run_id: int, md_id: str, status_code: int = 200) -> HTMLResponse:
    """The changed row, plus the summary tiles and tab counts swapped out-of-band."""
    repo = get_services(request).repo
    rows = diff_rows(repo, run_id)
    row = next((r for r in rows if r.md_id == md_id), None)
    return templates.TemplateResponse(request, "_diff_row_update.html", {
        "r": row, "run": repo.get_run(run_id), "counts": _counts(rows), "approvable": True,
    }, status_code=status_code)


def _open_item(repo: Repo, run_id: int, md_id: str) -> Any:
    run = _run_or_404(repo, run_id)
    if run["state"] != "diffed":
        raise HTTPException(409, "only a sync waiting for approval can be changed")
    item = repo.conn.execute("SELECT * FROM sync_item WHERE run_id=? AND md_id=?", (run_id, md_id)).fetchone()
    if item is None:
        raise HTTPException(404, "no such row")
    return item


@router.post("/sync/{run_id}/dismiss/{md_id}", response_class=HTMLResponse)
def dismiss_flag(request: Request, run_id: int, md_id: str) -> HTMLResponse:
    repo = get_services(request).repo
    item = _open_item(repo, run_id, md_id)
    if item["action"] != "flag" or item["md_progress"] is None:
        raise HTTPException(409, "only flagged rows can be dismissed")
    repo.dismiss_flag(md_id, item["md_progress"], item["flag_kind"], item["reason"])
    # Flip this row only; the next sync applies the stored dismissal itself (orchestrator.build_items).
    repo.update_items(run_id, [(md_id, {"action": "skip", "reason": f"{DISMISSED_PREFIX}{item['reason']}"})])
    return _row_update(request, run_id, md_id)


@router.post("/sync/{run_id}/undismiss/{md_id}", response_class=HTMLResponse)
def undismiss_flag(request: Request, run_id: int, md_id: str) -> HTMLResponse:
    repo = get_services(request).repo
    item = _open_item(repo, run_id, md_id)
    if item["action"] != "skip" or not item["flag_kind"] or not (item["reason"] or "").startswith(DISMISSED_PREFIX):
        raise HTTPException(409, "this row was not dismissed")
    repo.undismiss_flag(md_id)
    repo.update_items(run_id, [(md_id, {"action": "flag", "reason": item["reason"][len(DISMISSED_PREFIX):]})])
    return _row_update(request, run_id, md_id)


@router.post("/sync/{run_id}/restore")
def restore(request: Request, run_id: int) -> Response:
    svc = get_services(request)
    _run_or_404(svc.repo, run_id)
    try:
        svc.orchestrator.restore(run_id)
    except (SyncStateError, SyncAlreadyRunning) as exc:
        return render(request, "message.html", {"error": str(exc), "back": f"/sync/{run_id}"}, status_code=409)
    return RedirectResponse(f"/sync/{run_id}", status_code=303)


@router.get("/sync/{run_id}", response_class=HTMLResponse)
def diff_page(request: Request, run_id: int, f: str | None = None) -> HTMLResponse:
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
    run = _run_or_404(repo, run_id)
    form = await request.form()
    est = compute_estimate(repo, diff_rows(repo, run_id), form.getlist("sel"), form.getlist("mc"), run["target"])
    return templates.TemplateResponse(request, "_estimate.html", {"est": est})


@router.post("/sync/{run_id}/discard", response_class=HTMLResponse)
def diff_discard(request: Request, run_id: int) -> HTMLResponse:
    svc = get_services(request)
    try:
        svc.orchestrator.discard(run_id)
    except SyncStateError as exc:
        return render(request, "message.html", {"error": str(exc), "back": f"/sync/{run_id}"}, status_code=409)
    return render(request, "message.html", {"message": f"Sync #{run_id} discarded. Nothing was written.", "back": "/",
                                            "restore": run_id})
