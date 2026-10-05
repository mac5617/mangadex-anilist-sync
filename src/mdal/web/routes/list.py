"""List: rank what you've read (compare two at a time), your ranking, stalled series, and ready to binge."""

from __future__ import annotations

import json
from html import escape
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from mdal.clients.anilist import AniListError
from mdal.clients.mangaupdates import MangaUpdatesError
from mdal.fetch.mangaupdates import lookup_mu
from mdal.listtools import binge, stalled
from mdal.recommend import ranking
from mdal.recommend.fresh import english_meta
from mdal.recommend.series import big_cover, series_url
from mdal.stats import entry_rows
from mdal.clients.myanimelist import MalError
from mdal.sync.list_edit import EDIT_STATUSES, ListEditError, save_scores, set_status
from mdal.sync.mal_list_edit import mirror_scores, mirror_status, on_mal
from mdal.web.app import get_services, render, templates

router = APIRouter()

RANKING_SORTS = {"rank": "By rank", "recent": "Recently ranked", "oldest": "Oldest ranked", "popular": "Most popular"}
STATUS_WORDS = {"CURRENT": "Reading", "COMPLETED": "Completed", "PAUSED": "Paused", "DROPPED": "Dropped",
                "PLANNING": "Planning", "REPEATING": "Rereading"}


def view(e: dict[str, Any]) -> dict[str, Any]:
    """What the ranking screens show of a list entry."""
    meta = [str(e["start_year"]) if e.get("start_year") else None, STATUS_WORDS.get(e["status"], e["status"]),
            f"{e['progress']} ch read" + (f" of {e['chapters']}" if e.get("chapters") else "") if e.get("progress") else None,
            f"AniList score {e['score'] / 10:g}" if e.get("score") else "no score yet"]
    return {"media_id": e["media_id"], "title": e["title"], "cover": big_cover(e.get("cover_url")),
            "cover_fallback": e.get("cover_url"),
            "meta": " · ".join(m for m in meta if m), "href": series_url(f"al:{e['media_id']}"),
            "genres": (e.get("genres") or [])[:4]}


def entries_by_id(request: Request) -> dict[int, dict[str, Any]]:
    return {e["media_id"]: e for e in entry_rows(get_services(request).repo)}


def save_context(request: Request) -> dict[str, Any]:
    r = get_services(request).ranker
    return {"save": r.status(), "saving": r.saving, "pending": len(r.pending()), "pending_mal": len(r.pending_mal()),
            "anilist": bool(get_services(request).anilist_token())}


@router.get("/list", include_in_schema=False)
def list_home() -> RedirectResponse:
    return RedirectResponse("/list/rank", status_code=303)


# ---- rank -------------------------------------------------------------------------------------


def tier_step(request: Request, media_id: int | None = None, note: str | None = None) -> dict[str, Any]:
    svc = get_services(request)
    entries = entries_by_id(request)
    queue = svc.ranker.queue()
    current = entries.get(media_id) if media_id else (queue[0] if queue else None)
    sizes = {t: len(svc.ranker.tier(t)) for t in ranking.TIERS}
    return {"step": "tier" if current else "empty", "s": view(current) if current else None, "note": note,
            "suggested": ranking.tier_for(current.get("score")) if current else None,
            "left": len(queue), "sizes": sizes, "tiers": ranking.TIERS, **save_context(request)}


def compare_step(request: Request, media_id: int, tier: str, search: ranking.Search) -> dict[str, Any]:
    svc = get_services(request)
    entries = entries_by_id(request)
    pivot = svc.ranker.tier(tier)[search.pivot]
    asked = ranking.questions_left(ranking.start(len(svc.ranker.tier(tier)))) - ranking.questions_left(search)
    return {"step": "compare", "s": view(entries[media_id]), "other": view(entries[pivot["media_id"]]),
            "tier": tier, "tier_label": ranking.TIERS[tier][2], "low": search.low, "high": search.high,
            "question": asked + 1, "questions": asked + ranking.questions_left(search), **save_context(request)}


def result_step(request: Request, media_id: int, tier: str) -> dict[str, Any]:
    svc = get_services(request)
    entries = entries_by_id(request)
    order = [r["media_id"] for r in svc.ranker.tier(tier)]
    score = svc.ranker.ranked_scores()[media_id]
    return {"step": "result", "s": view(entries[media_id]), "tier": tier, "tier_label": ranking.TIERS[tier][2],
            "place": order.index(media_id) + 1, "of": len(order), "score": score, **save_context(request)}


def step(request: Request, context: dict[str, Any]) -> HTMLResponse:
    return templates.TemplateResponse(request, "_rank_step.html", context)


# async: opening the page starts any waiting save (an asyncio task), e.g. scores not yet on MyAnimeList.
@router.get("/list/rank", response_class=HTMLResponse)
async def rank_page(request: Request, media_id: int | None = None) -> HTMLResponse:
    if media_id is not None and media_id not in get_services(request).repo.al_entries():
        raise HTTPException(404, "Only series on your AniList list can be ranked.")
    get_services(request).ranker.start_save()
    ranked = len(get_services(request).repo.ranking())
    return render(request, "list_rank.html", {**tier_step(request, media_id), "ranked": ranked})


def _known(request: Request, media_id: int) -> None:
    if media_id not in get_services(request).repo.al_entries():
        raise HTTPException(404, "That series isn't on your AniList list.")


# async: placing a series may start the background save of scores (an asyncio task).
@router.post("/list/rank/tier", response_class=HTMLResponse)
async def rank_tier(request: Request, media_id: int = Form(...), tier: str = Form(...)) -> HTMLResponse:
    _known(request, media_id)
    if tier not in ranking.TIERS:
        raise HTTPException(400, "Choose liked, fine or didn't like.")
    ranker = get_services(request).ranker
    ranker.repo.unrank(media_id)                       # re-ranking starts from scratch
    size = len(ranker.tier(tier))
    if size == 0:
        ranker.place(media_id, tier, 0)
        return step(request, result_step(request, media_id, tier))
    return step(request, compare_step(request, media_id, tier, ranking.start(size)))


@router.post("/list/rank/compare", response_class=HTMLResponse)
async def rank_compare(request: Request, media_id: int = Form(...), tier: str = Form(...), low: int = Form(...),
                       high: int = Form(...), answer: str = Form(...)) -> HTMLResponse:
    _known(request, media_id)
    ranker = get_services(request).ranker
    size = len(ranker.tier(tier)) if tier in ranking.TIERS else -1
    if size < 0 or not 0 <= low <= high <= size or answer not in ("better", "worse", "tie"):
        raise HTTPException(400, "That comparison is out of date; start this series again.")
    search = ranking.Search(low, high).answer({"better": True, "worse": False, "tie": None}[answer])
    if search.done:
        ranker.place(media_id, tier, search.low)
        return step(request, result_step(request, media_id, tier))
    return step(request, compare_step(request, media_id, tier, search))


@router.post("/list/rank/score", response_class=HTMLResponse)
async def rank_score(request: Request, media_id: int = Form(...), score: str = Form("")) -> HTMLResponse:
    """Just give it a score (out of 10), saved to AniList now; it isn't placed in the ranking."""
    _known(request, media_id)
    svc = get_services(request)
    try:
        value = float(score.replace(",", "."))
    except ValueError:
        value = -1
    if not 0 < value <= 10:
        return step(request, tier_step(request, media_id, note="A score is a number from 0.1 to 10."))
    try:
        saved, failed = await save_scores(svc.anilist, svc.repo, {media_id: ranking.to_anilist(value)})
    except AniListError as exc:
        return step(request, tier_step(request, media_id, note=f"AniList didn't save it: {exc}"))
    if failed:
        return step(request, tier_step(request, media_id, note=f"AniList didn't save it: {failed[media_id]}"))
    svc.ranker.skip(media_id)        # scored now: it goes to the back of the queue
    title = entries_by_id(request)[media_id]["title"]
    where = "AniList"
    if svc.mirror_to_mal():
        try:
            mal_saved, mal_failed = await mirror_scores(svc.mal, svc.repo, {media_id: ranking.to_anilist(value)})
            where = "AniList and MyAnimeList" if mal_saved else where
            if mal_failed:
                return step(request, tier_step(request, note=f"Saved {value:g}/10 for {title} to AniList; "
                                                             f"MyAnimeList: {mal_failed[media_id]}."))
        except MalError as exc:
            return step(request, tier_step(request, note=f"Saved {value:g}/10 for {title} to AniList; MyAnimeList: {exc}."))
    return step(request, tier_step(request, note=f"Saved {value:g}/10 for {title} to {where}."))


@router.post("/list/rank/skip", response_class=HTMLResponse)
def rank_skip(request: Request, media_id: int = Form(...)) -> HTMLResponse:
    get_services(request).ranker.skip(media_id)
    return step(request, tier_step(request))


@router.post("/list/rank/next", response_class=HTMLResponse)
def rank_next(request: Request) -> HTMLResponse:
    return step(request, tier_step(request))


@router.get("/list/rank-status", response_class=HTMLResponse)
def rank_status(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "_rank_save.html", save_context(request))


@router.post("/list/rank-save", response_class=HTMLResponse)
async def rank_save(request: Request) -> HTMLResponse:
    get_services(request).ranker.start_save()
    return templates.TemplateResponse(request, "_rank_save.html", save_context(request))


# ---- your ranking -------------------------------------------------------------------------------


@router.get("/list/ranking", response_class=HTMLResponse)
async def ranking_page(request: Request, sort: str = "rank") -> HTMLResponse:
    """Your ranking, by tier and place (the default), when you ranked them, or by popularity on AniList."""
    svc = get_services(request)
    svc.ranker.start_save()
    sort = sort if sort in RANKING_SORTS else "rank"
    entries = entries_by_id(request)
    scores = svc.ranker.ranked_scores()
    ranked = svc.repo.ranking()
    popularity = {m: r["popularity"] or 0 for m, r in svc.repo.media([r["media_id"] for r in ranked]).items()}
    tiers, rows = [], []
    for tier in ranking.TIER_ORDER:
        tier_rows = [{**view(entries[r["media_id"]]), "score": scores[r["media_id"]], "place": i + 1, "tier": tier,
                      "tier_label": ranking.TIERS[tier][2], "ranked_at": r["ranked_at"],
                      "popularity": popularity.get(r["media_id"], 0),
                      "saved": (entries[r["media_id"]]["score"] or 0) == ranking.to_anilist(scores[r["media_id"]])}
                     for i, r in enumerate(svc.ranker.tier(tier)) if r["media_id"] in entries]
        low, high, label = ranking.TIERS[tier]
        tiers.append({"tier": tier, "label": label, "range": f"{low:g}–{high:g}", "rows": tier_rows})
        rows += tier_rows
    if sort == "recent":
        rows.sort(key=lambda r: r["ranked_at"], reverse=True)
    elif sort == "oldest":
        rows.sort(key=lambda r: r["ranked_at"])
    elif sort == "popular":
        rows.sort(key=lambda r: -r["popularity"])
    return render(request, "list_ranking.html", {"tiers": tiers, "rows": rows, "total": len(rows), "sort": sort,
                                                 "sorts": RANKING_SORTS, **save_context(request)})


@router.post("/list/ranking/{media_id}/remove", response_class=HTMLResponse)
async def ranking_remove(request: Request, media_id: int) -> Response:
    get_services(request).ranker.remove(media_id)
    return Response(status_code=204, headers={"HX-Refresh": "true"})


# ---- stalled and ready to binge ---------------------------------------------------------------------


@router.get("/list/stalled", response_class=HTMLResponse)
def stalled_page(request: Request) -> HTMLResponse:
    svc = get_services(request)
    days = svc.repo.get_setting("stalled_days")
    rows = [{**view(e), "idle_days": e["idle_days"], "status": e["status"]}
            for e in stalled(entry_rows(svc.repo), days)]
    return render(request, "list_stalled.html", {"rows": rows, "days": days, "anilist": bool(svc.anilist_token())})


@router.post("/list/status/{media_id}", response_class=HTMLResponse)
async def change_status(request: Request, media_id: int, status: str = Form(...)) -> HTMLResponse:
    """Mark a stalled series Paused or Dropped on AniList."""
    svc = get_services(request)
    if status not in EDIT_STATUSES:
        raise HTTPException(400, "Only Paused or Dropped can be set here.")
    try:
        await set_status(svc.anilist, svc.repo, media_id, status)
        note, ok = f"Marked {STATUS_WORDS[status]} on AniList.", True
    except (ListEditError, AniListError) as exc:
        return templates.TemplateResponse(request, "_status_done.html", {"note": str(exc), "ok": False, "media_id": media_id})
    if svc.mirror_to_mal():
        try:
            problem = await mirror_status(svc.mal, svc.repo, media_id, status)
        except MalError as exc:
            problem = str(exc)
        if problem:
            note, ok = f"Marked {STATUS_WORDS[status]} on AniList; MyAnimeList: {problem}.", False
        elif media_id in on_mal(svc.repo, [media_id]):
            note = f"Marked {STATUS_WORDS[status]} on AniList and MyAnimeList."
    return templates.TemplateResponse(request, "_status_done.html", {"note": note, "ok": ok, "media_id": media_id})


@router.get("/list/binge", response_class=HTMLResponse)
def binge_page(request: Request) -> HTMLResponse:
    svc = get_services(request)
    mu = svc.repo.cached_all("mu")
    rows = [{**view(e), "left": e["left"], "status": STATUS_WORDS.get(e["status"], e["status"]),
             "idle_days": e["idle_days"], "english": english_meta(mu.get(f"al:{e['media_id']}")),
             "checked": svc.repo.cached(f"al:{e['media_id']}", "mu") is not None}
            for e in binge(entry_rows(svc.repo), svc.repo.get_setting("stalled_days"), mu)]
    return render(request, "list_binge.html", {"rows": rows})


@router.get("/list/binge/english/{media_id}", response_class=HTMLResponse)
async def binge_english(request: Request, media_id: int) -> HTMLResponse:
    """MangaUpdates for one row, loaded as it scrolls into view (1-2 requests, cached a week)."""
    svc = get_services(request)
    m = svc.repo.media([media_id]).get(media_id)
    if m is None:
        raise HTTPException(404, "no such series")
    titles = [t for t in (m["romaji"], m["english"], m["native"], *json.loads(m["synonyms"] or "[]")) if t]
    try:
        info = await lookup_mu(svc.mangaupdates, svc.repo, f"al:{media_id}", titles, m["start_year"])
        text = " · ".join(english_meta(info)) or ("MangaUpdates: no English release listed" if info else "Not on MangaUpdates")
    except MangaUpdatesError:
        text = "MangaUpdates didn't answer"
    return HTMLResponse(f'<span class="muted small">{escape(text)}</span>')
