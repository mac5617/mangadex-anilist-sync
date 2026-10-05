"""Discover: recommendations from your list (For you, Genres, Tags, Creators, Map).

Pages read stored candidates and the model's stored picks; only Refresh (AniList) and Ask again
(the local model) start work, in the background.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from mdal import titles
from mdal.clients.ollama import OllamaError
from mdal.recommend.chat import Item
from mdal.recommend.fresh import NewRec, english_meta, ranked_new
from mdal.recommend.graph import build_graph
from mdal.recommend.ratings import rate
from mdal.recommend.series import series_url
from mdal.recommend.releases import ScanBusy
from mdal.recommend.score import Rec, ranked
from mdal.recommend.service import RecsBusy
from mdal.stats import COUNTRY_LABELS, FORMAT_LABELS, Bar, chart
from mdal.web.routes.stats import entries_url
from mdal.web.app import get_services, graph_json, render, templates

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


def larger_cover(url: str | None) -> str | None:
    """AniList's 230px cover instead of the ~100px one the list stores (cards show covers large)."""
    return url.replace("/cover/small/", "/cover/medium/") if url and "anilist" in url else url


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
        "id": r.media_id, "dom_id": f"rec-{r.media_id}", "title": r.title, "page": series_url(f"al:{r.media_id}"),
        "cover": larger_cover(r.cover),
        "links": [("AniList", r.url), ("Find on MangaDex", f"https://mangadex.org/search?q={quote(r.title)}")],
        "hide_url": f"/discover-hide/{r.media_id}",
        "meta": " · ".join(m for m in meta if m), "genres": r.genres[:4], "reasons": reasons[:3],
        "llm": bool(reason), "adult": r.is_adult,
    }


def new_card(r: NewRec, reason: str | None = None, mu: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": r.md_id, "dom_id": f"new-{r.md_id}", "title": r.title, "page": series_url(f"md:{r.md_id}"), "cover": r.cover,
        "links": [("MangaDex", r.url)] + ([("AniList", f"https://anilist.co/manga/{r.al_id}")] if r.al_id else []),
        "hide_url": f"/discover-new-hide/{r.md_id}",
        "meta": " · ".join(r.meta() + english_meta(mu)), "genres": r.tags[:5], "reasons": [reason] if reason else r.reasons[:3],
        "llm": bool(reason), "adult": r.adult, "description": r.description,
    }


def item_card(item: Item, reason: str, dom_id: str) -> dict[str, Any]:
    """A series the Ask chat suggested."""
    if item.key.startswith("md:"):
        links, hide = [("MangaDex", item.url)], f"/discover-new-hide/{item.key[3:]}"
    else:
        links = [("AniList", item.url), ("Find on MangaDex", f"https://mangadex.org/search?q={quote(item.title)}")]
        hide = f"/discover-hide/{item.key[3:]}"
    return {
        "id": item.key, "dom_id": dom_id, "title": item.title, "page": series_url(item.key), "cover": larger_cover(item.cover),
        "links": links, "hide_url": hide, "meta": " · ".join([item.source, *item.meta]), "genres": item.tags[:5],
        "reasons": [reason] if reason else [], "llm": True, "adult": item.adult, "description": item.description,
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
    series = {r["media_id"]: (titles.of(r), r["cover_url"])
              for r in svc.repo.conn.execute(
                  "SELECT m.media_id, m.romaji, m.english, m.native, m.cover_url FROM al_entry e JOIN al_media m USING (media_id)")}
    graph = build_graph(profile, top, series)
    return _page(request, "discover_map.html", {"graph": graph, "graph_json": graph_json(graph)})


@router.get("/discover/new", response_class=HTMLResponse)
def new_releases(request: Request) -> HTMLResponse:
    svc = get_services(request)
    profile, recs = svc.releases.recs(_adult(request))
    by_id = {r.md_id: r for r in recs}
    llm = svc.repo.get_setting("new_llm") or {}
    mu = svc.repo.cached_all("mu")
    picks = [new_card(by_id[p["md_id"]], p["reason"], mu.get(f"md:{p['md_id']}")) for p in llm.get("picks") or []
             if p["md_id"] in by_id]
    shown = {c["id"] for c in picks}
    more = [new_card(r, mu=mu.get(f"md:{r.md_id}")) for r in ranked_new(recs, LIST_SIZE + len(shown)) if r.md_id not in shown]
    if not picks:
        picks, more = more[:12], more[12:]
    svc.releases.mark_seen([c["id"] for c in picks])   # Home's "new since you last looked" starts again from here
    return _page(request, "discover_new.html", {
        "picks": picks, "more": more[:LIST_SIZE - len(picks) if shown else LIST_SIZE], "from_model": bool(shown),
        "total": len(recs), "entries": profile.entries, "status_template": "_new_status.html",
        "hidden": len(svc.repo.hidden_new()), "unhide_url": "/discover-new-unhide-all", **new_status_context(request),
    })


@router.get("/discover/ask", response_class=HTMLResponse)
def ask_page(request: Request) -> HTMLResponse:
    svc = get_services(request)
    profile, items = svc.releases.pool(_adult(request))
    by_key = svc.releases.cards_pool(_adult(request), items)
    return _page(request, "discover_ask.html", {
        "messages": [chat_message(m, by_key) for m in svc.repo.chat_messages()],
        "pool": len(items), "new_count": sum(1 for i in items if i.key.startswith("md:")),
        "prompts": example_prompts(profile), **new_status_context(request),
    })


def example_prompts(profile: Any) -> list[str]:
    """Starting questions, made from your own list where it can."""
    prompts = ["What's the best new release for me right now?"]
    favourite = next((f["title"] for f in profile.favourites if f.get("title") and len(f["title"]) <= 40), None)
    if favourite:
        prompts.append(f"Something like {favourite} that's finished")
    if genres := profile.top("genres", 1):
        prompts.append(f"A short {genres[0].label.lower()} series I can finish in a weekend")
    prompts.append("Surprise me with something outside my usual genres")
    return prompts


def chat_message(m: Any, by_key: dict[str, Item]) -> dict[str, Any]:
    picks = json.loads(m["picks"]) if m["picks"] else []
    cards = [item_card(by_key[p["key"]], p["reason"], f"chat-{m['id']}-{n}")
             for n, p in enumerate(picks) if p["key"] in by_key]
    return {"id": m["id"], "role": m["role"], "content": m["content"], "cards": cards, "gone": len(picks) - len(cards),
            "notes": json.loads(m["notes"]) if m["notes"] else []}


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


def reload_when_done(response: HTMLResponse, watch: bool, busy: bool, status: dict[str, Any]) -> HTMLResponse:
    """The panel polls while work runs; once it has finished, reload the page so its results appear."""
    if watch and not busy and status.get("state") == "done":
        response.headers["HX-Refresh"] = "true"
    return response


def _status_fragment(request: Request, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, "_discover_status.html", {**status_context(request), "status_error": error},
                                      status_code=status_code)


@router.get("/discover-status", response_class=HTMLResponse)
def discover_status(request: Request, watch: bool = False) -> HTMLResponse:
    response = _status_fragment(request)
    svc = get_services(request)
    return reload_when_done(response, watch, svc.recommender.busy, svc.recommender.status())


# async: the refresh is an asyncio task, so it must be started on the event loop, not in a worker thread.
@router.post("/discover-refresh", response_class=HTMLResponse)
async def refresh(request: Request) -> HTMLResponse:
    svc = get_services(request)
    if not svc.anilist_token():
        return _status_fragment(request, "Connect AniList in Settings first.", 409)
    try:
        svc.recommender.start_refresh()
    except RecsBusy as exc:
        return _status_fragment(request, str(exc), 409)
    return _status_fragment(request)


@router.post("/discover-ask", response_class=HTMLResponse)
async def ask(request: Request) -> HTMLResponse:
    try:
        get_services(request).recommender.start_llm()
    except RecsBusy as exc:
        return _status_fragment(request, str(exc), 409)
    return _status_fragment(request)


def is_embedding(m: dict[str, Any]) -> bool:
    return "embedding" in (m.get("capabilities") or [])


@router.post("/discover-model", response_class=HTMLResponse)
async def choose_model(request: Request, model: str = Form(""), panel: str = Form("")) -> HTMLResponse:
    svc = get_services(request)
    names = {m["name"] for m in await svc.recommender.models() if not is_embedding(m)}
    if model not in names:
        return _panel(request, panel, "That model is not installed in Ollama.", 400)
    svc.repo.set_setting("ollama_model", model)
    return _panel(request, panel)


@router.get("/discover-models", response_class=HTMLResponse)
async def model_options(request: Request, panel: str = "") -> HTMLResponse:
    """The model pickers, loaded after the page so a stopped Ollama never slows the page down."""
    svc = get_services(request)
    models = await svc.recommender.models()
    return templates.TemplateResponse(request, "_discover_models.html", {
        "models": [m for m in models if not is_embedding(m)], "model": svc.repo.get_setting("ollama_model"),
        "embedders": [m for m in models if is_embedding(m)], "embed_model": svc.repo.get_setting("embed_model"),
        "panel": panel, "target": PANEL_IDS.get(panel, "#discover-status")})


@router.post("/discover-hide/{media_id}", response_class=HTMLResponse)
def hide(request: Request, media_id: int) -> Response:
    repo = get_services(request).repo
    repo.hide_rec(media_id)
    note_not_interested(repo, f"al:{media_id}")
    return HTMLResponse("")


def note_not_interested(repo: Any, key: str) -> None:
    """Not interested also counts, a little, against what the series is about (unless you rated it already)."""
    if key not in repo.feedback():
        rate(repo, key, "not_interested", "hide")


def forget_not_interested(repo: Any, prefix: str) -> None:
    for key, row in repo.feedback().items():
        if key.startswith(prefix) and row["verdict"] == "not_interested":
            repo.clear_feedback(key)


@router.post("/discover-unhide-all", response_class=HTMLResponse)
def unhide_all(request: Request) -> Response:
    repo = get_services(request).repo
    for media_id in repo.hidden_recs():
        repo.unhide_rec(media_id)
    forget_not_interested(repo, "al:")
    return Response(status_code=204, headers={"HX-Refresh": "true"})


# ---- new releases and Ask ------------------------------------------------------------

MD_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PANEL_IDS = {"new": "#new-status", "ask": "#ask-status"}


def new_status_context(request: Request) -> dict[str, Any]:
    svc = get_services(request)
    status = svc.releases.status()
    return {"ns": status, "new_running": status.get("state") == "running" and svc.releases.busy,
            "new_llm": svc.repo.get_setting("new_llm") or {}, "model": svc.repo.get_setting("ollama_model"),
            "embed_model": svc.repo.get_setting("embed_model"),
            "has_new": bool(svc.repo.conn.execute("SELECT 1 FROM md_new LIMIT 1").fetchone()),
            "mangadex": bool(svc.settings.mangadex_username)}


def _panel(request: Request, panel: str, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    """The status panel the request came from (For you, New releases or Ask)."""
    if panel not in PANEL_IDS:
        return _status_fragment(request, error, status_code)
    context = {**new_status_context(request), "status_error": error}
    if panel == "ask":
        context["pool"] = len(get_services(request).releases.pool()[1])
    return templates.TemplateResponse(request, f"_{panel}_status.html", context, status_code=status_code)


@router.get("/discover-new-status", response_class=HTMLResponse)
def new_status(request: Request, watch: bool = False) -> HTMLResponse:
    svc = get_services(request)
    return reload_when_done(_panel(request, "new"), watch, svc.releases.busy, svc.releases.status())


# async: the scan is an asyncio task, so it must be started on the event loop.
@router.post("/discover-new-scan", response_class=HTMLResponse)
async def new_scan(request: Request) -> HTMLResponse:
    svc = get_services(request)
    if not svc.settings.mangadex_username:
        return _panel(request, "new", "Add your MangaDex login to .env first.", 409)
    try:
        svc.releases.start_scan()
    except ScanBusy as exc:
        return _panel(request, "new", str(exc), 409)
    return _panel(request, "new")


@router.post("/discover-new-ask", response_class=HTMLResponse)
async def new_ask(request: Request) -> HTMLResponse:
    try:
        get_services(request).releases.start_llm()
    except ScanBusy as exc:
        return _panel(request, "new", str(exc), 409)
    return _panel(request, "new")


@router.post("/discover-new-hide/{md_id}", response_class=HTMLResponse)
def new_hide(request: Request, md_id: str) -> Response:
    if not MD_ID.match(md_id):
        raise HTTPException(404, "no such series")
    repo = get_services(request).repo
    repo.hide_new(md_id)
    note_not_interested(repo, f"md:{md_id}")
    return HTMLResponse("")


@router.post("/discover-new-unhide-all", response_class=HTMLResponse)
def new_unhide_all(request: Request) -> Response:
    repo = get_services(request).repo
    repo.unhide_all_new()
    forget_not_interested(repo, "md:")
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.post("/discover-embed-model", response_class=HTMLResponse)
async def choose_embed_model(request: Request, model: str = Form(""), panel: str = Form("new")) -> HTMLResponse:
    svc = get_services(request)
    if model not in {m["name"] for m in await svc.recommender.models() if is_embedding(m)}:
        return _panel(request, panel, "That embedding model is not installed in Ollama.", 400)
    svc.repo.set_setting("embed_model", model)
    return _panel(request, panel)


@router.post("/discover/ask", response_class=HTMLResponse)
async def ask_message(request: Request, message: str = Form(""), adult: str = Form("")) -> HTMLResponse:
    """One question and its answer, appended to the conversation on the page."""
    question = " ".join(message.split())[:1000]
    if not question:
        return HTMLResponse("", status_code=204)
    svc = get_services(request)
    try:
        await svc.releases.ask(question, adult == "1")
    except OllamaError as exc:
        return templates.TemplateResponse(request, "_chat_messages.html", {
            "messages": [{"role": "user", "content": question, "cards": [], "gone": 0}], "error": str(exc)})
    by_key = svc.releases.cards_pool(adult == "1")
    return templates.TemplateResponse(request, "_chat_messages.html", {
        "messages": [chat_message(m, by_key) for m in svc.repo.chat_messages(2)]})


@router.post("/discover-chat-clear", response_class=HTMLResponse)
def chat_clear(request: Request) -> Response:
    get_services(request).repo.clear_chat()
    return Response(status_code=204, headers={"HX-Refresh": "true"})


def adult_url(path: str, adult: bool) -> str:
    return path + ("?" + urlencode({"adult": "1"}) if adult else "")


templates.env.globals["adult_url"] = adult_url
