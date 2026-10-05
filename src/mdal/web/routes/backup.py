"""Settings → Backup: download a backup or CSV exports, and restore a backup."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from mdal.backup import BackupError, make_backup, restore, to_csv
from mdal.recommend import ranking
from mdal.stats import entry_rows
from mdal.web.app import get_services
from mdal.web.routes.settings import render_settings

router = APIRouter()


def download(body: str, name: str, media_type: str) -> Response:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return Response(body, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="shiori-{name}-{stamp}.{media_type.split("/")[-1]}"'})


@router.get("/backup/shiori.json")
def backup_json(request: Request) -> Response:
    return download(json.dumps(make_backup(get_services(request).repo), ensure_ascii=False, indent=1), "backup", "application/json")


@router.get("/backup/list.csv")
def list_csv(request: Request) -> Response:
    rows = [[e["title"], e["status"], e["progress"], (e["score"] or 0) / 10 or "", e.get("started_at") or "",
             e.get("completed_at") or "", ", ".join(e["genres"]), e.get("site_url") or "", e["md_status"]]
            for e in entry_rows(get_services(request).repo)]
    header = ["Title", "Status", "Chapters read", "Score (out of 10)", "Started", "Completed", "Genres", "AniList", "MangaDex status"]
    return download(to_csv(header, rows), "list", "text/csv")


@router.get("/backup/ratings.csv")
def ratings_csv(request: Request) -> Response:
    svc = get_services(request)
    titles = {e["media_id"]: e["title"] for e in entry_rows(svc.repo)}
    scores = svc.ranker.ranked_scores()
    rows = [[titles.get(r["media_id"], f"#{r['media_id']}"), ranking.TIERS[r["tier"]][2], i + 1, scores.get(r["media_id"], "")]
            for tier in ranking.TIER_ORDER for i, r in enumerate(svc.ranker.tier(tier))]
    rows += [[r["title"], r["verdict"].replace("_", " "), "", ""] for r in svc.repo.feedback().values()]
    return download(to_csv(["Title", "Rating", "Place in tier", "Score (out of 10)"], rows), "ratings", "text/csv")


@router.post("/backup/restore", response_class=HTMLResponse)
async def backup_restore(request: Request, file: UploadFile = File(...)) -> HTMLResponse:
    raw = await file.read()
    try:
        counts = restore(get_services(request).repo, json.loads(raw.decode("utf-8-sig")))
    except (BackupError, ValueError, UnicodeDecodeError) as exc:
        message = str(exc) if isinstance(exc, BackupError) else "That file isn't a readable Shiori backup."
        return render_settings(request, error=message, status_code=400)
    total = sum(counts.values())
    detail = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in counts.items() if n)
    return render_settings(request, message=f"Restored {total} rows ({detail or 'nothing new'}) and your settings.")
