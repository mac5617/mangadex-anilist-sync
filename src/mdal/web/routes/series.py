"""Series pages (/series/al/<id>, /series/md/<uuid>) and your verdicts on series (Discover → Ratings)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from mdal.clients.anilist import AniListError
from mdal.clients.myanimelist import MalError
from mdal.recommend.feedback import VERDICTS
from mdal.recommend.ratings import rate
from mdal.recommend.series import series_url
from mdal.sync import add_entry
from mdal.sync.list_edit import EDIT_STATUSES, ListEditError, read_notes, save_notes, set_status
from mdal.sync.list_edit import save_scores
from mdal.sync.mal_list_edit import mirror_scores, mirror_status, on_mal
from mdal.sync.orchestrator import refresh_item
from mdal.sync.rules import STATUS_LABELS
from mdal.sync.mal_writer import MalAddError, MalCommentsError, add_by_hand, save_comments
from mdal.recommend import ranking
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
    return verdict_panel(request, key)


def panel_context(svc: Any, s: dict[str, Any]) -> dict[str, Any]:
    """What the side panel needs besides the series: statuses to pick, or an add-to-AniList proposal."""
    add = add_entry.proposal(svc.repo, s["md_id"]) if not s["entry"] and s["md_id"] else None
    return {"verdicts": VERDICTS, "anilist": bool(svc.anilist_token()), "score_words": SCORE_WORDS,
            "list_statuses": [(code, STATUS_LABELS[code]) for code in EDIT_STATUSES], "add": add,
            "add_mal": bool(add and mal_add_id(svc, add.media_id)),
            "mal_missing": bool(s["entry"] and (m := mal_add_id(svc, s["al_id"])) and m not in svc.repo.mal_entries()),
            "add_statuses": [(code, STATUS_LABELS[code]) for code in add_entry.ADD_STATUSES]}


def mal_add_id(svc: Any, media_id: int) -> int | None:
    """The MyAnimeList id an add also goes to: when Sync covers MyAnimeList and the series has a link there."""
    if "mal" not in svc.orchestrator.sync_targets():
        return None
    media = svc.repo.media([media_id]).get(media_id)
    return media["id_mal"] if media and media["id_mal"] else None


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

    if status == s["entry"]["status_code"]:
        return answer(f"Already {STATUS_LABELS[status]}.", True)
    if svc.orchestrator.lock.locked():
        return answer("A sync is running; change the status after it finishes.", False)
    media_id = s["al_id"]
    try:
        saved = await set_status(svc.anilist, svc.repo, media_id, status)
    except (ListEditError, AniListError) as exc:
        return answer(f"AniList didn't save it: {exc}", False)
    note = f"Marked {STATUS_LABELS[status]} on AniList"
    if saved.get("progress") is not None and saved["progress"] != s["entry"]["progress"]:
        note += f" (it set progress to {saved['progress']})"
    if svc.mirror_to_mal() and media_id in on_mal(svc.repo, [media_id]):
        try:
            problem = await mirror_status(svc.mal, svc.repo, media_id, status)
        except MalError as exc:
            problem = str(exc)
        if problem:
            return answer(f"{note}; MyAnimeList: {problem}.", False)
        note += " and MyAnimeList"
    return answer(note + ".", True)


@router.post("/series/{kind}/{ident}/add", response_class=HTMLResponse)
async def series_add(request: Request, kind: str, ident: str, status: str = Form(...),
                     progress: int = Form(...)) -> HTMLResponse:
    """Add a matched series to your AniList list (the same add as Matches → Unlisted), and to your
    MyAnimeList list when Sync covers MyAnimeList."""
    key = series_key(kind, ident)
    svc = get_services(request)
    s = svc.series.view(key)
    if s is None or s["entry"] or not s["md_id"]:
        raise HTTPException(404, "This series can't be added from here.")

    def answer(note: str, ok: bool) -> HTMLResponse:
        return verdict_panel(request, key, status_note=note, status_ok=ok, oob_yours=ok)

    if svc.orchestrator.lock.locked():
        return answer("A sync is running; add it after it finishes.", False)
    try:
        entry = await add_entry.add(svc.repo, svc.anilist, s["md_id"], status, progress, svc.orchestrator.user_id)
    except add_entry.AddEntryError as exc:
        return answer(str(exc), False)
    except AniListError as exc:
        return answer(f"AniList error: {exc}", False)
    refresh_item(svc.repo, s["md_id"])
    label = STATUS_LABELS.get(entry["status"], entry["status"])
    note = f"Added to AniList as {label}, progress {entry['progress']}"
    if entry["progress"] != progress:
        note += f" (AniList filled in its total; you entered {progress})"
    mal_id = mal_add_id(svc, entry["mediaId"])
    if mal_id is None:
        return answer(note + ".", True)
    try:
        saved = await add_by_hand(svc.mal, svc.repo, entry["mediaId"], mal_id, status, progress)
    except (MalAddError, MalError) as exc:
        return answer(f"{note}. MyAnimeList: {exc}.", False)
    svc.ranker.start_save()   # a score you set in Shiori follows it to MyAnimeList
    return answer(f"{note}. Added to MyAnimeList too, progress {saved.get('num_chapters_read', progress)}.", True)


@router.post("/series/{kind}/{ident}/add-mal", response_class=HTMLResponse)
async def series_add_mal(request: Request, kind: str, ident: str) -> HTMLResponse:
    """Add a series on your AniList list to MyAnimeList too, with AniList's status and chapters read."""
    key = series_key(kind, ident)
    svc = get_services(request)
    s = svc.series.view(key)
    mal_id = mal_add_id(svc, s["al_id"]) if s and s["entry"] else None
    if mal_id is None:
        raise HTTPException(404, "This series can't be added to MyAnimeList from here.")

    def answer(note: str, ok: bool) -> HTMLResponse:
        return verdict_panel(request, key, status_note=note, status_ok=ok)

    status = s["entry"]["status_code"] if s["entry"]["status_code"] in EDIT_STATUSES else "CURRENT"
    if svc.orchestrator.lock.locked():
        return answer("A sync is running; add it after it finishes.", False)
    try:
        saved = await add_by_hand(svc.mal, svc.repo, s["al_id"], mal_id, status, s["entry"]["progress"] or 0)
    except (MalAddError, MalError) as exc:
        return answer(f"MyAnimeList: {exc}.", False)
    svc.ranker.start_save()
    return answer(f"Added to MyAnimeList as {STATUS_LABELS[status]}, "
                  f"progress {saved.get('num_chapters_read', s['entry']['progress'] or 0)}.", True)


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
    if not 0 < value <= 10:
        return verdict_panel(request, key, score_note="A score is a number from 0.1 to 10.", score_ok=False)
    try:
        saved, failed = await save_scores(svc.anilist, svc.repo, {media_id: ranking.to_anilist(value)})
    except AniListError as exc:
        return verdict_panel(request, key, score_note=f"AniList didn't save it: {exc}", score_ok=False)
    if failed:
        return verdict_panel(request, key, score_note=f"AniList didn't save it: {failed[media_id]}", score_ok=False)
    svc.ranker.remove(media_id)
    where = "AniList"
    if svc.mirror_to_mal():
        try:
            mal_saved, mal_failed = await mirror_scores(svc.mal, svc.repo, {media_id: ranking.to_anilist(value)})
            where = "AniList and MyAnimeList" if mal_saved else where
            if mal_failed:
                return verdict_panel(request, key, score_note=f"Saved to AniList; MyAnimeList: {mal_failed[media_id]}.",
                                     score_ok=False)
        except MalError as exc:
            return verdict_panel(request, key, score_note=f"Saved to AniList; MyAnimeList: {exc}.", score_ok=False)
    return verdict_panel(request, key, score_note=f"Saved {value:g}/10 to {where}.", score_ok=True)


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
    try:
        await read_notes(svc.anilist, svc.repo, media_id)
    except (AniListError, ListEditError) as exc:
        svc.repo.update_al_entry(media_id, notes="")   # shown empty rather than asking again on every visit
        return notes_panel(request, key, notes_error=f"Couldn't read your notes from AniList: {exc}")
    return notes_panel(request, key)


@router.post("/series/{kind}/{ident}/notes", response_class=HTMLResponse)
async def series_save_notes(request: Request, kind: str, ident: str, notes: str = Form(""), mal: str = Form("")) -> HTMLResponse:
    key = series_key(kind, ident)
    svc = get_services(request)
    if not _known(request, key)["entry"]:
        svc.repo.set_series_note(key, notes.strip()[:5000])
        return notes_panel(request, key, notes_saved="Saved in Shiori.")
    media_id = _on_list(request, key)
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
