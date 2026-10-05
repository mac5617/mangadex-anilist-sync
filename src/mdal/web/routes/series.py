"""Series pages (/series/al/<id>, /series/md/<uuid>) and your verdicts on series (Discover → Ratings)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from mdal.clients.anilist import AniListError
from mdal.clients.myanimelist import MalError
from mdal.recommend.feedback import VERDICTS
from mdal.recommend.ratings import rate
from mdal.recommend.series import series_url
from mdal.sync.list_edit import ListEditError, read_notes, save_notes
from mdal.sync.mal_writer import MalCommentsError, save_comments
from mdal.web.app import get_services, render, templates

router = APIRouter()

MD_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def series_key(kind: str, ident: str) -> str:
    if kind == "al" and ident.isdigit():
        return f"al:{int(ident)}"
    if kind == "md" and MD_ID.match(ident):
        return f"md:{ident}"
    raise HTTPException(404, "no such series")


@router.get("/series/{kind}/{ident}", response_class=HTMLResponse)
def series_page(request: Request, kind: str, ident: str) -> HTMLResponse:
    key = series_key(kind, ident)
    s = get_services(request).series.view(key)
    if s is None:
        raise HTTPException(404, "Shiori doesn't know this series yet.")
    return render(request, "series.html", {"s": s, "verdicts": VERDICTS, "anilist": bool(get_services(request).anilist_token())})


# async: the lookups await the API clients on the event loop.
@router.post("/series/{kind}/{ident}/lookup", response_class=HTMLResponse)
async def series_lookup(request: Request, kind: str, ident: str, force: str = Form("")) -> HTMLResponse:
    key = series_key(kind, ident)
    pages = get_services(request).series
    if not pages.exists(key):
        raise HTTPException(404, "no such series")
    await pages.look_up(key, force=force == "1")
    return templates.TemplateResponse(request, "_series_details.html", {"s": pages.view(key)})


@router.post("/series/{kind}/{ident}/rate", response_class=HTMLResponse)
def series_rate(request: Request, kind: str, ident: str, verdict: str = Form("")) -> Response:
    """Save or clear your verdict. Answers with the verdict panel (series page) or a row (Ratings page)."""
    key = series_key(kind, ident)
    svc = get_services(request)
    if verdict == "clear":
        svc.repo.clear_feedback(key)
    elif verdict not in VERDICTS or not rate(svc.repo, key, verdict, "page"):
        raise HTTPException(400, "That rating can't be saved for this series.")
    if request.headers.get("HX-Target", "").startswith("rating-"):
        row = svc.repo.feedback().get(key)
        return templates.TemplateResponse(request, "_rating_row.html", {"r": row, "key": key, "verdicts": VERDICTS,
                                                                        "href": series_url(key)})
    return templates.TemplateResponse(request, "_series_verdict.html", {"s": svc.series.view(key), "verdicts": VERDICTS})


def notes_panel(request: Request, key: str, **extra) -> HTMLResponse:
    svc = get_services(request)
    return templates.TemplateResponse(request, "_series_notes.html", {
        "s": svc.series.view(key), "anilist": bool(svc.anilist_token()), **extra})


def _on_list(request: Request, key: str) -> int:
    s = get_services(request).series.view(key)
    if not s or not s["entry"]:
        raise HTTPException(404, "Only series on your AniList list have notes.")
    return s["al_id"]


# async: these await the AniList and MyAnimeList clients.
@router.get("/series/{kind}/{ident}/notes", response_class=HTMLResponse)
async def series_notes(request: Request, kind: str, ident: str) -> HTMLResponse:
    """Your notes, read from AniList the first time (later list reads keep them up to date)."""
    key = series_key(kind, ident)
    media_id = _on_list(request, key)
    svc = get_services(request)
    try:
        await read_notes(svc.anilist, svc.repo, media_id)
    except (AniListError, ListEditError) as exc:
        svc.repo.update_al_entry(media_id, notes="")   # shown empty rather than asking again on every visit
        return notes_panel(request, key, notes_error=f"Couldn't read your notes from AniList: {exc}")
    return notes_panel(request, key)


@router.post("/series/{kind}/{ident}/notes", response_class=HTMLResponse)
async def series_save_notes(request: Request, kind: str, ident: str, notes: str = Form(""), mal: str = Form("")) -> HTMLResponse:
    key = series_key(kind, ident)
    media_id = _on_list(request, key)
    svc = get_services(request)
    try:
        await save_notes(svc.anilist, svc.repo, media_id, notes)
    except (AniListError, ListEditError) as exc:
        return notes_panel(request, key, notes_error=str(exc))
    saved = "Saved to AniList."
    view = svc.series.view(key)
    if mal == "1" and view["mal_mirror"]:
        mal_id = svc.repo.media([media_id])[media_id]["id_mal"]
        try:
            await save_comments(svc.mal, svc.repo, media_id, mal_id, notes.strip()[:5000])
            saved = "Saved to AniList and MyAnimeList."
        except (MalCommentsError, MalError) as exc:
            return notes_panel(request, key, notes_saved=saved, notes_error=f"MyAnimeList: {exc}")
    return notes_panel(request, key, notes_saved=saved)


@router.get("/discover/ratings", response_class=HTMLResponse)
def ratings_page(request: Request) -> HTMLResponse:
    rows = list(get_services(request).repo.feedback().values())
    return render(request, "discover_ratings.html", {
        "rows": [{"r": r, "key": r["key"], "href": series_url(r["key"])} for r in rows], "verdicts": VERDICTS,
        "counts": {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}})
