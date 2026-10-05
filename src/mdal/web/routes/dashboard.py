"""Home: a greeting, sync, what needs you, what to read next, new picks and shortcuts."""

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from mdal.listtools import binge, stalled
from mdal.recommend.series import big_cover, series_url
from mdal.stats import entry_rows, is_mismatch
from mdal.web.app import get_services, render
from mdal.web.routes.sync import status_context

router = APIRouter()

CONTINUE, BINGE, DIGEST = 6, 6, 6


def greeting(now: datetime) -> str:
    return "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 18 else "Good evening"


def tile(e: dict, note: str, progress: float | None = None) -> dict:
    return {"title": e["title"], "href": series_url(f"al:{e['media_id']}"), "cover": big_cover(e.get("cover_url")),
            "fallback": e.get("cover_url"), "note": note, "progress": progress}


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    svc = get_services(request)
    s, repo = svc.settings, svc.repo
    entries = entry_rows(repo)

    reading = sorted((e for e in entries if e["status"] in ("CURRENT", "REPEATING")),
                     key=lambda e: -(e.get("updated_at") or 0))[:CONTINUE]
    continue_reading = [tile(e, f"ch. {e['progress']}" + (f" of {e['chapters']}" if e.get("chapters") else ""),
                             min(1.0, e["progress"] / e["chapters"]) if e.get("chapters") else None) for e in reading]
    ready = [tile(e, f"{e['left']} ch left" if e["left"] is not None else "finished")
             for e in binge(entries, repo.get_setting("stalled_days"), repo.cached_all("mu"))[:BINGE]]

    review = repo.review_count()
    unlisted = len(repo.not_on_list())
    mismatches = sum(1 for e in entries if is_mismatch(e))
    stalled_count = len(stalled(entries, repo.get_setting("stalled_days")))
    mal_waiting = len(svc.ranker.pending_mal())
    needs = [(n, label, href) for n, label, href in (
        (review, "matches to review", "/review"),
        (unlisted, "matched series missing from your AniList list", "/not-listed"),
        (mismatches, "series with a different status on MangaDex and AniList", "/stats/entries?mismatch=1"),
        (stalled_count, "series stalled as Reading", "/list/stalled"),
        (mal_waiting, "scores waiting for MyAnimeList", "/list/ranking"),
    ) if n]

    ranked = len(repo.ranking())
    unranked = len(svc.ranker.queue())
    digest = [{"title": r.title, "cover": r.cover, "fallback": None, "href": series_url(f"md:{r.md_id}"),
               "note": " · ".join(r.meta()[:2]), "progress": None}
              for r in svc.releases.digest()]
    context = {
        "greeting": greeting(datetime.now()),
        "name": repo.get_setting("anilist_user_name"),
        "summary": {"entries": len(entries), "reading": sum(1 for e in entries if e["status"] == "CURRENT"),
                    "ranked": ranked, "new_picks": len(digest)},
        "auth": {
            "anilist": bool(svc.anilist_token()),
            "anilist_user": repo.get_setting("anilist_user_name"),
            "mangadex": all([s.mangadex_username, s.mangadex_password.get_secret_value(), s.mangadex_client_id,
                             s.mangadex_client_secret.get_secret_value()]),
            "mangadex_user": s.mangadex_username,
            "mal": svc.mal.connected,
            "mal_user": repo.get_setting("mal_user_name"),
        },
        **status_context(svc),
        "needs": needs,
        "continue_reading": continue_reading, "ready": ready,
        "digest": digest[:DIGEST], "digest_total": len(digest),
        "scan": svc.releases.status(), "next_scan": svc.releases.next_scan(), "scan_running": svc.releases.busy,
        "ranked": ranked, "unranked": unranked,
    }
    return render(request, "dashboard.html", context)
