"""Sync start/status and the diff screen (story 14, FR-3/4/26/28).

Rendering never calls an API: everything comes from the DB.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from mdal import titles
from mdal.db.repo import Repo
from mdal.recommend.series import series_url
from mdal.services import Services
from mdal.sync.changes import (
    SITE_NAMES,
    TARGETS,
    ApprovalError,
    DismissalError,
    Estimate,
    Selection,
    dismiss,
    is_dismissed,
    is_preselected,
    is_selectable,
    is_status_only,
    open_diff,
    plan_estimate,
    shown_reason,
    undismiss,
    write_plan,
)
from mdal.sync.orchestrator import (
    SyncAlreadyRunning,
    SyncCoolingDown,
)
from mdal.sync.rules import completion_label
from mdal.sync.runs import ACTIVE_STATES, SyncStateError
from mdal.sync.sites import md_snapshot_fresh
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
        "md_fresh": md_snapshot_fresh(svc.repo),
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
    run_id: int
    md_id: str
    md_title: str
    md_url: str
    page: str              # the series page in Shiori
    cover: str | None
    al_title: str | None   # the series on the run's site (AniList or MyAnimeList)
    al_url: str | None
    site: str
    al_progress: int | None
    md_progress: int | None
    action: str
    flag_kind: str | None
    reason: str | None
    dismissed: bool
    hint: str | None
    unresolved: int
    set_status: str | None
    status_label: str | None
    status_only: bool
    selectable: bool
    checked: bool
    complete_checked: bool
    set_score: int | None
    score_checked: bool


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
            status_label = completion_label(r["al_status"], r["status_source"], r["total"], site)
            if is_new:
                status_label = "New entry → " + status_label.split(" → ", 1)[1] + "; untick to add as Reading"
        elif is_new:
            status_label = "New entry: Reading"
        rows.append(DiffRow(
            run_id=run_id,
            md_id=r["md_id"],
            md_title=r["md_title"] or r["md_id"],
            md_url=MANGADEX_TITLE_URL.format(r["md_id"]),
            page=series_url(f"al:{r['al_media_id']}" if r["al_media_id"] else f"md:{r['md_id']}"),
            cover=md_cover_url(r["md_id"], r["cover_file"]),
            al_title=title,
            al_url=url,
            site=site,
            al_progress=r["al_progress"],
            md_progress=r["md_progress"],
            action=r["action"],
            flag_kind=r["flag_kind"],
            reason=shown_reason(r),
            dismissed=is_dismissed(r),
            hint=r["hint"],
            unresolved=r["unresolved_reads"],
            set_status=r["set_status"],
            status_label=status_label,
            status_only=bool(r["set_status"] or r["set_score"]) and is_status_only(r),
            selectable=is_selectable(r),
            checked=is_preselected(r),
            complete_checked=bool(r["status_approved"]),
            set_score=r["set_score"],
            score_checked=bool(r["score_approved"]),
        ))
    return rows


def combine(parts: list[Estimate]) -> Estimate:
    """Several sites' estimates as one line ("≈ 3 AniList and 5 MyAnimeList requests" reads as the sum)."""
    if len(parts) == 1:
        return parts[0]
    return Estimate(sum(p.selected for p in parts), sum(p.to_complete for p in parts),
                    sum(p.requests for p in parts), sum(p.seconds for p in parts),
                    " and ".join(p.site for p in parts), sum(p.to_score for p in parts))


def group(repo: Repo, run: Any) -> list[Any]:
    """The runs shown on one changes page: every site waiting for approval, or just this run once it's past that."""
    if run["state"] != "diffed":
        return [run]
    return [d for t in TARGETS if (d := open_diff(repo, t))] or [run]


def choices(form: Any, runs: list[Any], default_run: int) -> dict[int, Selection]:
    """Form ticks ("run:md_id", or a bare md_id meaning this page's run) per run, for the runs the page showed
    (its `runs` fields). A site whose changes arrived after the page loaded is left waiting, never closed unseen."""
    shown = {int(v) for v in form.getlist("runs") if str(v).isdigit()} or {default_run}
    ids = {r["run_id"] for r in runs} & shown
    ticks: dict[int, tuple[list[str], list[str], list[str], list[str]]] = {i: ([], [], [], []) for i in ids}
    for n, key in enumerate(("sel", "mc", "ov", "sc")):
        for value in form.getlist(key):
            run_s, sep, md_id = str(value).partition(":")
            run_id = int(run_s) if sep and run_s.isdigit() else default_run
            if not sep:
                md_id = str(value)
            if run_id in ticks:
                ticks[run_id][n].append(md_id)
    return {run_id: Selection.of(*t) for run_id, t in ticks.items()}


def still_reading(svc: Any, runs: list[Any]) -> list[str]:
    """Sites whose changes aren't ready yet (a Sync covering both reads them one after the other)."""
    shown = {r["target"] for r in runs}
    sites = [t for t in svc.orchestrator.queued if t not in shown]
    active = svc.orchestrator.runs.active()
    if active and active["state"] in ("fetching", "resolving", "diffing") and active["target"] not in shown:
        sites.insert(0, active["target"])
    return [SITE_NAMES[t] for t in dict.fromkeys(sites)]


def estimates(repo: Repo, runs: list[Any], picked: dict[int, Selection] | None) -> Estimate:
    """The cost of each site's Write plan: for what's ticked, or for what the page starts with."""
    return combine([
        plan_estimate(repo, run["target"], write_plan(repo, run["run_id"], picked[run["run_id"]] if picked else None))
        for run in runs
    ])


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
    counts["score"] = sum(1 for r in rows if r.set_score is not None and r.selectable)
    return counts


def _diff_page(request: Request, run_id: int, f: str | None = None, error: str | None = None,
               status_code: int = 200) -> HTMLResponse:
    svc = get_services(request)
    repo = svc.repo
    run = _run_or_404(repo, run_id)
    runs = group(repo, run)
    rows = [row for r in runs for row in diff_rows(repo, r["run_id"])]
    if len(runs) > 1:   # a series' AniList and MyAnimeList rows side by side
        rows.sort(key=lambda r: (r.md_title.casefold(), [x["run_id"] for x in runs].index(r.run_id)))
    counts = _counts(rows)
    if f not in FILTERS:  # no tab asked for: open on the first one with something in it
        f = next((k for k in ("write", "add", "flag", "skip") if counts[k]), "write")
    return render(request, "diff.html", {
        "run": run, "runs": runs, "rows": rows, "counts": counts, "est": estimates(repo, runs, None),
        "site": " and ".join(SITE_NAMES[r["target"]] for r in runs),
        "site_names": SITE_NAMES,
        "reading": still_reading(svc, runs) if run["state"] == "diffed" else [],
        "filter": f,
        "approvable": run["state"] == "diffed",
        "restorable": svc.orchestrator.runs.can_restore(run_id),
        "error": error,
    }, status_code=status_code)


def _row_update(request: Request, run_id: int, md_id: str, status_code: int = 200) -> HTMLResponse:
    """The changed row, plus the summary tiles and tab counts swapped out-of-band."""
    repo = get_services(request).repo
    run = repo.get_run(run_id)
    rows = [row for r in group(repo, run) for row in diff_rows(repo, r["run_id"])]
    row = next((r for r in rows if r.run_id == run_id and r.md_id == md_id), None)
    return templates.TemplateResponse(request, "_diff_row_update.html", {
        "r": row, "run": run, "counts": _counts(rows), "approvable": True,
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
    return _change_dismissal(request, run_id, md_id, dismiss)


@router.post("/sync/{run_id}/undismiss/{md_id}", response_class=HTMLResponse)
def undismiss_flag(request: Request, run_id: int, md_id: str) -> HTMLResponse:
    return _change_dismissal(request, run_id, md_id, undismiss)


def _change_dismissal(request: Request, run_id: int, md_id: str, change: Callable[[Repo, Any, Any], None]) -> HTMLResponse:
    repo = get_services(request).repo
    item = _open_item(repo, run_id, md_id)
    try:
        change(repo, repo.get_run(run_id), item)
    except DismissalError as exc:
        raise HTTPException(409, str(exc)) from exc
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


@router.get("/sync/{run_id}/others", response_class=HTMLResponse)
def diff_others(request: Request, run_id: int, shown: str = "") -> HTMLResponse:
    """The changes page's note about other sites: still being read, or ready to show here."""
    svc = get_services(request)
    run = _run_or_404(svc.repo, run_id)
    ids = {int(x) for x in shown.split(",") if x.isdigit()}
    runs = [r for r in group(svc.repo, run) if r["run_id"] in ids] or [run]
    ready = [SITE_NAMES[r["target"]] for r in group(svc.repo, run) if r["run_id"] not in ids]
    return templates.TemplateResponse(request, "_diff_others.html", {
        "run": run, "shown": shown, "reading": still_reading(svc, runs), "ready": ready})


@router.post("/sync/{run_id}/approve", response_class=HTMLResponse)
async def approve(request: Request, run_id: int) -> Response:
    svc = get_services(request)
    run = _run_or_404(svc.repo, run_id)
    form = await request.form()
    try:
        await svc.orchestrator.approve_many(choices(form, group(svc.repo, run), run_id))
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
    runs = group(repo, run)
    est = estimates(repo, runs, choices(form, runs, run_id))
    return templates.TemplateResponse(request, "_estimate.html", {"est": est})


@router.post("/sync/{run_id}/discard", response_class=HTMLResponse)
def diff_discard(request: Request, run_id: int) -> HTMLResponse:
    svc = get_services(request)
    run = _run_or_404(svc.repo, run_id)
    runs = group(svc.repo, run)
    try:
        for r in runs:
            svc.orchestrator.runs.discard(r["run_id"])
    except SyncStateError as exc:
        return render(request, "message.html", {"error": str(exc), "back": f"/sync/{run_id}"}, status_code=409)
    names = " and ".join(f"#{r['run_id']}" for r in runs)
    return render(request, "message.html", {"message": f"Sync {names} discarded. Nothing was written.", "back": "/",
                                            "restore": run_id})
