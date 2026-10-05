"""Stats → Year (your year in review) and Stats → Compare (your list next to a friend's)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from mdal.clients.anilist import AniListError
from mdal.compare import compare
from mdal.fetch.friend import FriendNotFound, fetch_friend
from mdal.recommend.series import big_cover, series_url
from mdal.stats import Bar, chart, entry_rows
from mdal.web.app import get_services, render
from mdal.year import review, years

router = APIRouter()


def card(e: dict[str, Any], note: str = "") -> dict[str, Any]:
    return {"title": e["title"], "href": series_url(f"al:{e['media_id']}"), "cover": big_cover(e.get("cover_url")),
            "note": note}


@router.get("/stats/year", response_class=HTMLResponse)
def year_page(request: Request, year: int | None = None) -> HTMLResponse:
    entries = entry_rows(get_services(request).repo)
    available = years(entries)
    if not available:
        return render(request, "stats_year.html", {"r": None, "years": []})
    year = year if year in available else available[0]
    r = review(entries, year)
    charts = {
        "months": chart([Bar(m, n, f"{n} finished") for m, n in r["months"]]),
        "genres": chart([Bar(g, n, f"{n} series") for g, n in r["genres"]]),
        "tags": chart([Bar(t, n, f"{n} series") for t, n in r["tags"]]),
    }
    picks = {
        "best": [card(e, f"scored {e['score'] / 10:g}") for e in r["best"]],
        "longest": [card(e, f"{e['progress']} chapters") for e in r["longest"]],
        "first": card(r["first"], r["first"]["completed_at"]) if r["first"] else None,
        "last": card(r["last"], r["last"]["completed_at"]) if r["last"] else None,
    }
    return render(request, "stats_year.html", {"r": r, "years": available, "c": charts, "p": picks})


# ---- compare with a friend -------------------------------------------------------------------------


@router.get("/stats/compare", response_class=HTMLResponse)
def compare_page(request: Request, user: str = "") -> HTMLResponse:
    svc = get_services(request)
    friend = svc.repo.friend(user) if user else None
    result = None
    if friend:
        mine = entry_rows(svc.repo)
        theirs = json.loads(friend["entries"])
        media = svc.repo.media({e["media_id"] for e in theirs})
        result = compare(mine, theirs, media)
    return render(request, "stats_compare.html", {
        "user": friend["name"] if friend else user, "friend": friend, "r": result,
        "friends": svc.repo.friends(), "anilist": bool(svc.anilist_token()),
        "missing": bool(user and not friend)})


# async: awaits the AniList client.
@router.post("/stats/compare", response_class=HTMLResponse)
async def compare_fetch(request: Request, user: str = Form("")) -> HTMLResponse:
    """Read a friend's public AniList list (1 request) and show the comparison."""
    svc = get_services(request)
    name = user.strip()[:40]
    if not name:
        return RedirectResponse("/stats/compare", status_code=303)
    try:
        await fetch_friend(svc.anilist, svc.repo, name)
    except FriendNotFound as exc:
        return render(request, "stats_compare.html", {"user": name, "friend": None, "r": None, "error": str(exc),
                                                      "friends": svc.repo.friends(), "anilist": bool(svc.anilist_token())})
    except AniListError as exc:
        return render(request, "stats_compare.html", {"user": name, "friend": None, "r": None, "error": f"AniList: {exc}",
                                                      "friends": svc.repo.friends(), "anilist": bool(svc.anilist_token())})
    return RedirectResponse(f"/stats/compare?user={name}", status_code=303)
