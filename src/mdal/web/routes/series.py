"""Series pages (/series/al/<id>, /series/md/<uuid>) and your verdicts on series (Discover → Ratings)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from mdal.recommend.feedback import VERDICTS
from mdal.recommend.ratings import rate
from mdal.recommend.series import series_url
from mdal.sync import add_entry
from mdal.lists import EDIT_STATUSES
from mdal.sync.rules import STATUS_LABELS
from mdal.web.app import get_services, render, templates

router = APIRouter()

# MyAnimeList's words for each score, so a number reads as a judgement.
SCORE_WORDS = {1: "Appalling", 2: "Horrible", 3: "Very bad", 4: "Bad", 5: "Average", 6: "Fine", 7: "Good", 8: "Very good",
               9: "Great", 10: "Masterpiece"}
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
    svc = get_services(request)
    return render(request, "series.html", {"s": s, "mal_default": svc.mirror_to_mal(), **panel_context(svc, s)})


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
def series_rate(request: Request, kind: str, ident: str, verdict: str = Form(""), source: str = Form("")) -> Response:
    """Save or clear your verdict. Answers with the verdict panel (series page) or a row (Ratings page).

    Changing a rating from its Ratings row keeps where it came from (Ask, Not interested, a series page); `source`
    lets the row's Undo put a removed rating back the way it was."""
    key = series_key(kind, ident)
    svc = get_services(request)
    in_row = request.headers.get("HX-Target", "").startswith("rating-")
    before = svc.repo.feedback().get(key)
    if verdict == "clear":
        svc.repo.clear_feedback(key)
    else:
        kept = source if source in ("ask", "hide") else before["source"] if before and in_row else "page"
        if verdict not in VERDICTS or not rate(svc.repo, key, verdict, kept):
            raise HTTPException(400, "That rating can't be saved for this series.")
    if in_row:
        row = svc.repo.feedback().get(key)
        return templates.TemplateResponse(request, "_rating_row.html", {
            "r": row, "was": before, "key": key, "verdicts": VERDICTS, "href": series_url(key), "cover": _cover(svc, key)})
    return verdict_panel(request, key)


def panel_context(svc: Any, s: dict[str, Any]) -> dict[str, Any]:
    """What the side panel needs besides the series: statuses to pick, or an add-to-AniList proposal."""
    add = add_entry.proposal(svc.repo, s["md_id"]) if not s["entry"] and s["md_id"] else None
    return {"verdicts": VERDICTS, "anilist": bool(svc.anilist_token()), "score_words": SCORE_WORDS,
            "list_statuses": [(code, STATUS_LABELS[code]) for code in EDIT_STATUSES], "add": add,
            "add_mal": bool(add and svc.your_list.mirror_can_add(f"md:{s['md_id']}")),
            "mal_missing": bool(s["entry"] and svc.your_list.mirror_can_add(f"al:{s['al_id']}")),
            "add_statuses": [(code, STATUS_LABELS[code]) for code in add_entry.ADD_STATUSES]}


def verdict_panel(request: Request, key: str, **extra) -> HTMLResponse:
    svc = get_services(request)
    s = svc.series.view(key)
    return templates.TemplateResponse(request, "_series_verdict.html", {"s": s, **panel_context(svc, s), **extra})


# async: awaits the AniList and MyAnimeList clients.
@router.post("/series/{kind}/{ident}/status", response_class=HTMLResponse)
async def series_status(request: Request, kind: str, ident: str, status: str = Form(...)) -> HTMLResponse:
    """Set the status of a series on your list (and on MyAnimeList when list edits are mirrored there)."""
    key = series_key(kind, ident)
    svc = get_services(request)
    s = svc.series.view(key)
    if s is None or not s["entry"]:
        raise HTTPException(404, "This series isn't on your AniList list.")
    if status not in EDIT_STATUSES:
        raise HTTPException(400, "That status can't be set here.")

    def answer(note: str, ok: bool) -> HTMLResponse:
        return verdict_panel(request, key, status_note=note, status_ok=ok, oob_yours=True)

    edit = await svc.your_list.set_status(f"al:{s['al_id']}", status)
    return answer(edit.note, edit.ok)


@router.post("/series/{kind}/{ident}/add", response_class=HTMLResponse)
async def series_add(request: Request, kind: str, ident: str, status: str = Form(...),
                     progress: int = Form(...)) -> HTMLResponse:
    """Add a matched series to your AniList list (the same add as Matches → Unlisted), and to your
    MyAnimeList list while list edits are mirrored there."""
    key = series_key(kind, ident)
    svc = get_services(request)
    s = svc.series.view(key)
    if s is None or s["entry"] or not s["md_id"]:
        raise HTTPException(404, "This series can't be added from here.")

    edit = await svc.your_list.add(f"md:{s['md_id']}", status, progress)
    _after_add(svc, edit)
    added = bool((main := edit.site("AniList")) and main.saved)
    return verdict_panel(request, key, status_note=edit.note, status_ok=edit.ok, oob_yours=added)


@router.post("/series/{kind}/{ident}/add-mal", response_class=HTMLResponse)
async def series_add_mal(request: Request, kind: str, ident: str) -> HTMLResponse:
    """Add a series on your AniList list to MyAnimeList too, with AniList's status and chapters read."""
    key = series_key(kind, ident)
    svc = get_services(request)
    s = svc.series.view(key)
    if not (s and s["entry"] and svc.your_list.mirror_can_add(key)):
        raise HTTPException(404, "This series can't be added to MyAnimeList from here.")
    status = s["entry"]["status_code"] if s["entry"]["status_code"] in EDIT_STATUSES else "CURRENT"
    edit = await svc.your_list.add(f"al:{s['al_id']}", status, s["entry"]["progress"] or 0)
    _after_add(svc, edit)
    return verdict_panel(request, key, status_note=edit.note, status_ok=edit.ok)


def _after_add(svc: Any, edit: Any) -> None:
    """A series just added to MyAnimeList gets the scores you set in Shiori."""
    if (mirror := edit.site("MyAnimeList")) and mirror.saved:
        svc.ranker.start_save()


@router.post("/series/{kind}/{ident}/score", response_class=HTMLResponse)
async def series_score(request: Request, kind: str, ident: str, score: str = Form("")) -> HTMLResponse:
    """A score for a series on your list, saved to AniList (and MyAnimeList when mirroring). A ranked series
    leaves the ranking, which would otherwise put its ranked score back."""
    key = series_key(kind, ident)
    media_id = _on_list(request, key)
    svc = get_services(request)
    try:
        value = float(score.replace(",", "."))
    except ValueError:
        value = -1
    edit = await svc.your_list.set_score(f"al:{media_id}", value)
    if (main := edit.site("AniList")) and main.took():
        svc.ranker.remove(media_id)
    return verdict_panel(request, key, score_note=edit.note, score_ok=edit.ok)


def notes_panel(request: Request, key: str, **extra) -> HTMLResponse:
    svc = get_services(request)
    return templates.TemplateResponse(request, "_series_notes.html", {
        "s": svc.series.view(key), "anilist": bool(svc.anilist_token()), "mal_default": svc.mirror_to_mal(), **extra})


def _known(request: Request, key: str) -> dict:
    s = get_services(request).series.view(key)
    if s is None:
        raise HTTPException(404, "Shiori doesn't know this series yet.")
    return s


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
    problem = await svc.your_list.read_notes(f"al:{media_id}")
    return notes_panel(request, key, notes_error=problem) if problem else notes_panel(request, key)


@router.post("/series/{kind}/{ident}/notes", response_class=HTMLResponse)
async def series_save_notes(request: Request, kind: str, ident: str, notes: str = Form(""), mal: str = Form("")) -> HTMLResponse:
    key = series_key(kind, ident)
    svc = get_services(request)
    if not _known(request, key)["entry"]:
        svc.repo.set_series_note(key, notes.strip()[:5000])
        return notes_panel(request, key, notes_saved="Saved in Shiori.")
    media_id = _on_list(request, key)
    edit = await svc.your_list.set_notes(f"al:{media_id}", notes, mirror=mal == "1")
    if edit.ok:
        return notes_panel(request, key, notes_saved=edit.note)
    return notes_panel(request, key, notes_error=edit.note)


def _cover(svc: Any, key: str) -> str | None:
    """The cover a series page would show, for a rating row."""
    view = svc.series.view(key) if svc.series.exists(key) else None
    return view["cover"] if view else None


@router.get("/discover/ratings", response_class=HTMLResponse)
def ratings_page(request: Request) -> HTMLResponse:
    svc = get_services(request)
    rows = list(svc.repo.feedback().values())
    return render(request, "discover_ratings.html", {
        "rows": [{"r": r, "key": r["key"], "href": series_url(r["key"]), "cover": _cover(svc, r["key"])} for r in rows],
        "verdicts": VERDICTS,
        "counts": {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}})
