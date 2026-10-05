"""Search (the box in the header): everything stored locally as you type, and AniList itself on request."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from mdal import titles
from mdal.clients.anilist import AniListError
from mdal.db.repo import now_iso
from mdal.fetch.anilist_list import DESCRIPTION_FIELD, MEDIA_FIELDS, media_row
from mdal.recommend.fresh import MD_COVER
from mdal.recommend.series import series_url
from mdal.search import Hit, anilist_hit, search
from mdal.stats import FORMAT_LABELS
from mdal.web.app import get_services, render, templates

router = APIRouter()

ANILIST_SEARCH = ("query ($q: String) { Page(page: 1, perPage: 15) { media(search: $q, type: MANGA, sort: [SEARCH_MATCH]) "
                  f"{{ {MEDIA_FIELDS} {DESCRIPTION_FIELD} }} }} }}")
STATUS = {"CURRENT": "Reading", "COMPLETED": "Completed", "PAUSED": "Paused", "DROPPED": "Dropped",
          "PLANNING": "Planning", "REPEATING": "Rereading"}


def md_cover(md_id: str, file: str | None) -> str | None:
    return MD_COVER.format(md_id=md_id, file=file) if file else None


def candidates(request: Request) -> list[Hit]:
    repo = get_services(request).repo
    entries = repo.al_entries()
    recommended = {r[0] for r in repo.conn.execute("SELECT media_id FROM rec_candidate")}
    hits: list[Hit] = []
    for m in repo.conn.execute("SELECT media_id, romaji, english, native, synonyms, cover_url, start_year, format FROM al_media"):
        names = [n for n in (m["romaji"], m["english"], m["native"], *json.loads(m["synonyms"] or "[]")) if n]
        e = entries.get(m["media_id"])
        group = "On your list" if e else "Recommended" if m["media_id"] in recommended else "Seen elsewhere"
        meta = " · ".join(x for x in (FORMAT_LABELS.get(m["format"] or "", m["format"]),   # same-named series differ by format
                                      str(m["start_year"]) if m["start_year"] else None,
                                      STATUS.get(e["status"]) if e else None) if x)
        hits.append(Hit(f"al:{m['media_id']}", titles.pick(m["romaji"], m["english"], m["native"], f"#{m['media_id']}"),
                        m["cover_url"], meta, group,
                        names=names))
    mapped = {r[0] for r in repo.conn.execute("SELECT md_id FROM mapping WHERE state IN ('auto','confirmed')")}
    for table, group in (("md_manga", "MangaDex library"), ("md_new", "New on MangaDex")):
        for r in repo.conn.execute(f"SELECT md_id, title, alt_titles, cover_file, year FROM {table}"):
            if table == "md_manga" and r["md_id"] in mapped:
                continue          # it's on your list: found by its AniList titles
            hits.append(Hit(f"md:{r['md_id']}", r["title"], md_cover(r["md_id"], r["cover_file"]), str(r["year"] or ""),
                            group, names=[r["title"], *json.loads(r["alt_titles"] or "[]")]))
    return hits


def results(request: Request, q: str) -> list[dict]:
    if not q.strip():
        return []
    return [{"href": series_url(h.key), "title": h.title, "cover": h.cover, "meta": h.meta, "group": h.group}
            for h in search(q, candidates(request))]


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "") -> HTMLResponse:
    return render(request, "search.html", {"q": q, "hits": results(request, q),
                                           "anilist": bool(get_services(request).anilist_token())})


@router.get("/search/results", response_class=HTMLResponse)
def search_results(request: Request, q: str = "") -> HTMLResponse:
    """The local results, as you type."""
    return templates.TemplateResponse(request, "_search_results.html", {
        "q": q, "hits": results(request, q), "anilist": bool(get_services(request).anilist_token())})


# async: awaits the AniList client.
@router.get("/search/anilist", response_class=HTMLResponse)
async def search_anilist(request: Request, q: str = "") -> HTMLResponse:
    """AniList's own search (1 request). Found series are kept, so their pages open straight away."""
    svc = get_services(request)
    q = q.strip()[:100]
    error, found = None, []
    if q:
        try:
            media = ((await svc.anilist.graphql(ANILIST_SEARCH, {"q": q})).get("Page") or {}).get("media") or []
            svc.repo.upsert_media([media_row(m, now_iso()) for m in media])
            listed = set(svc.repo.al_entries())
            found = [{**anilist_hit(m, listed), "href": series_url(f"al:{m['id']}")} for m in media]
        except AniListError as exc:
            error = f"AniList: {exc}"
    return templates.TemplateResponse(request, "_search_anilist.html", {"q": q, "found": found, "error": error})
