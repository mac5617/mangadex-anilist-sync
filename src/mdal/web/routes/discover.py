"""Discover: recommendations from your list (For you, Genres, Tags, Creators, Map).

Pages read stored candidates and the model's stored picks; only Refresh (AniList) and Ask again
(the local model) start work, in the background.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from mdal.recommend.graph import build_graph
from mdal.recommend.score import Rec, ranked
from mdal.recommend.service import RecsBusy
from mdal.stats import COUNTRY_LABELS, FORMAT_LABELS, Bar, chart
from mdal.web.routes.stats import entries_url
from mdal.web.app import get_services, render, templates

router = APIRouter()

KIND_PAGES = {
    "genres": ("genre", "Genres", "genres", "genre"),
    "tags": ("tag", "Tags", "tags", "tag"),
    "creators": ("staff", "Creators", "staff", "staff"),
}
LIST_SIZE = 30
MAP_SIZE = 24


def _adult(request: Request) -> bool:
    return request.query_params.get("adult") == "1"


def card(r: Rec, kind: str, reason: str | None = None) -> dict[str, Any]:
    reasons = [reason] if reason else []
    if kind == "overall" and not reason:
        for k in ("community", "staff", "tag", "genre"):
            reasons += r.reasons.get(k, [])[:1]
    elif kind != "overall":
        reasons = r.reasons.get(kind, [])
    meta = [str(r.year) if r.year else None, FORMAT_LABELS.get(r.format or "", r.format),
            COUNTRY_LABELS.get(r.country or "", r.country),
            f"{r.chapters} ch" if r.chapters else None,
            f"AniList avg {r.mean_score}" if r.mean_score else None]
    return {
        "id": r.media_id, "title": r.title, "url": r.url, "cover": r.cover,
        "md_search": f"https://mangadex.org/search?q={quote(r.title)}",
        "meta": " · ".join(m for m in meta if m), "genres": r.genres[:4], "reasons": reasons[:3],
        "llm": bool(reason), "adult": r.is_adult,
    }


def status_context(request: Request) -> dict[str, Any]:
    svc = get_services(request)
    rec = svc.recommender
    status = rec.status()
    llm = svc.repo.get_setting("rec_llm") or {}
    return {"rs": status, "running": status.get("state") == "running" and rec.busy, "llm": llm,
            "model": svc.repo.get_setting("ollama_model"),
            "has_candidates": bool(svc.repo.conn.execute("SELECT 1 FROM rec_candidate LIMIT 1").fetchone()),
            "anilist": bool(svc.anilist_token())}


def _page(request: Request, name: str, context: dict[str, Any]) -> HTMLResponse:
    adult = _adult(request)
    return render(request, name, {**status_context(request), "adult": adult,
                                  "hidden": len(get_services(request).repo.hidden_recs()), **context})


@router.get("/discover", response_class=HTMLResponse)
def for_you(request: Request) -> HTMLResponse:
    svc = get_services(request)
    profile, recs = svc.recommender.recs(_adult(request))
    by_id = {r.media_id: r for r in recs}
    llm = svc.repo.get_setting("rec_llm") or {}
    picks = [card(by_id[p["id"]], "overall", p["reason"]) for p in llm.get("picks") or [] if p["id"] in by_id]
    shown = {p["id"] for p in picks}
    more = [card(r, "overall") for r in ranked(recs, "overall", LIST_SIZE + len(shown)) if r.media_id not in shown]
    if not picks:
        picks, more = more[:12], more[12:]
    return _page(request, "discover.html", {
        "picks": picks, "more": more[:LIST_SIZE - len(picks) if picks else LIST_SIZE], "summary": llm.get("summary") if shown else None,
        "from_model": bool(shown), "entries": profile.entries,
    })


@router.get("/discover/map", response_class=HTMLResponse)
def rec_map(request: Request) -> HTMLResponse:
    svc = get_services(request)
    profile, recs = svc.recommender.recs(_adult(request))
    top = ranked(recs, "overall", MAP_SIZE)
    titles = {f["media_id"]: f["title"] for f in profile.favourites}
    titles.update({r["media_id"]: r["romaji"] or r["english"] or r["native"] for r in svc.repo.conn.execute(
        "SELECT m.media_id, m.romaji, m.english, m.native FROM al_entry e JOIN al_media m USING (media_id)")})
    graph = build_graph(profile, top, titles)
    return _page(request, "discover_map.html", {
        "graph": graph,
        # "</" escaped: a title can never close the <script> element the JSON sits in.
        "graph_json": json.dumps(graph, ensure_ascii=False).replace("</", "<\\/"),
    })


@router.get("/discover/{page}", response_class=HTMLResponse)
def by_kind(request: Request, page: str) -> HTMLResponse:
    if page not in KIND_PAGES:
        raise HTTPException(404, "no such page")
    kind, title, attr, filter_key = KIND_PAGES[page]
    svc = get_services(request)
    profile, recs = svc.recommender.recs(_adult(request))
    features = profile.top(attr, 12)
    bars = [Bar(f.label, round(f.affinity * 100), f"{f.count} on your list" + (f", avg score {f.mean_score:.0f}" if f.mean_score else ""),
                key=f.key) for f in features]
    c = chart(bars)
    for b in c["bars"]:
        b["href"] = entries_url({filter_key: b["key"]})
    return _page(request, "discover_kind.html", {
        "kind": kind, "title": title, "page": page, "c": c,
        "cards": [card(r, kind) for r in ranked(recs, kind, LIST_SIZE)],
    })


# ---- actions ------------------------------------------------------------------------


def _status_fragment(request: Request, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, "_discover_status.html", {**status_context(request), "status_error": error},
                                      status_code=status_code)


@router.get("/discover-status", response_class=HTMLResponse)
def discover_status(request: Request) -> HTMLResponse:
    return _status_fragment(request)


@router.post("/discover-refresh", response_class=HTMLResponse)
def refresh(request: Request) -> HTMLResponse:
    svc = get_services(request)
    if not svc.anilist_token():
        return _status_fragment(request, "Connect AniList in Settings first.", 409)
    try:
        svc.recommender.start_refresh()
    except RecsBusy as exc:
        return _status_fragment(request, str(exc), 409)
    return _status_fragment(request)


@router.post("/discover-ask", response_class=HTMLResponse)
def ask(request: Request) -> HTMLResponse:
    try:
        get_services(request).recommender.start_llm()
    except RecsBusy as exc:
        return _status_fragment(request, str(exc), 409)
    return _status_fragment(request)


@router.post("/discover-model", response_class=HTMLResponse)
async def choose_model(request: Request, model: str = Form("")) -> HTMLResponse:
    svc = get_services(request)
    names = {m["name"] for m in await svc.recommender.models()}
    if model not in names:
        return _status_fragment(request, "That model is not installed in Ollama.", 400)
    svc.repo.set_setting("ollama_model", model)
    return _status_fragment(request)


@router.get("/discover-models", response_class=HTMLResponse)
async def model_options(request: Request) -> HTMLResponse:
    """The model picker, loaded after the page so a stopped Ollama never slows the page down."""
    svc = get_services(request)
    models = await svc.recommender.models()
    return templates.TemplateResponse(request, "_discover_models.html", {
        "models": models, "model": svc.repo.get_setting("ollama_model")})


@router.post("/discover-hide/{media_id}", response_class=HTMLResponse)
def hide(request: Request, media_id: int) -> Response:
    get_services(request).repo.hide_rec(media_id)
    return HTMLResponse("")


@router.post("/discover-unhide-all", response_class=HTMLResponse)
def unhide_all(request: Request) -> Response:
    repo = get_services(request).repo
    for media_id in repo.hidden_recs():
        repo.unhide_rec(media_id)
    return Response(status_code=204, headers={"HX-Refresh": "true"})


def adult_url(path: str, adult: bool) -> str:
    return path + ("?" + urlencode({"adult": "1"}) if adult else "")


templates.env.globals["adult_url"] = adult_url
