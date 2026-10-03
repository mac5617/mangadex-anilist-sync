"""Sync history (story 18, FR-31, NFR-16). Read-only."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from mdal.web.app import get_services, render
from mdal.web.routes.sync import diff_rows

router = APIRouter()


def _duration(started: str | None, finished: str | None) -> str | None:
    if not started or not finished:
        return None
    try:
        seconds = int((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds())
    except ValueError:
        return None
    return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"


@router.get("/history", response_class=HTMLResponse)
def history(request: Request) -> HTMLResponse:
    repo = get_services(request).repo
    counts = repo.run_counts()
    runs: list[dict[str, Any]] = []
    for r in repo.runs(200):
        c = counts.get(r["run_id"], {})
        runs.append({**dict(r), "counts": c, "duration": _duration(r["started_at"], r["finished_at"])})
    return render(request, "history.html", {"runs": runs})


@router.get("/history/{run_id}", response_class=HTMLResponse)
def history_run(request: Request, run_id: int) -> HTMLResponse:
    repo = get_services(request).repo
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "no such sync run")
    states = {i["md_id"]: i for i in repo.items(run_id)}
    rows = [(row, states[row.md_id]) for row in diff_rows(repo, run_id)]
    return render(request, "history_run.html", {
        "run": run, "rows": rows, "duration": _duration(run["started_at"], run["finished_at"]),
    })
