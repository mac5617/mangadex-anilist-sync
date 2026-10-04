"""Stats pages: your list (AniList-style), the entries drill-down, and sync activity. DB reads only."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from mdal.stats import (
    COUNTRY_LABELS,
    FILTER_LABELS,
    FORMAT_LABELS,
    GLOBAL_FILTERS,
    MD_STATUS_LABELS,
    PUB_LABELS,
    STATUS_ORDER,
    Bar,
    chart,
    entries,
    filter_label,
    list_stats,
    runs_with_writes,
    sync_stats,
)
from mdal.sync.rules import STATUS_LABELS
from mdal.web.app import get_services, md_cover_url, render

router = APIRouter()

PAGE_SIZE = 100
GENRE_SORTS = {"count": "Entries", "score": "Mean score", "chapters": "Chapters read"}


def _filters(request: Request, keys: tuple[str, ...] = tuple(FILTER_LABELS)) -> dict[str, str]:
    return {k: v for k in keys if (v := request.query_params.get(k, "").strip())}


def entries_url(filters: dict[str, str], **extra: str | None) -> str:
    merged = {**filters, **{k: v for k, v in extra.items() if v is not None}}
    return "/stats/entries?" + urlencode(merged)


def _linked(c: dict[str, Any], filters: dict[str, str], dim: str, **fixed: str) -> dict[str, Any]:
    """Point every bar at the entries list for that bar (filters so far + this chart's dimension)."""
    for b in c["bars"]:
        b["href"] = entries_url(filters, **fixed, **{dim: b["key"]}) if b["key"] is not None else None
    return c


@router.get("/stats", response_class=HTMLResponse)
def stats_list(request: Request, gsort: str = "count") -> HTMLResponse:
    filters = _filters(request, GLOBAL_FILTERS)
    gsort = gsort if gsort in GENRE_SORTS else "count"
    s = list_stats(get_services(request).repo, filters, gsort)
    timeline = chart(s.timeline)
    for b in timeline["bars"]:
        b["href"] = entries_url(filters, started=b["key"])
        b["href2"] = entries_url(filters, completed=b["key"])
    for row in s.heat.get("rows", []):
        for cell in row["cells"]:
            cell["href"] = entries_url(filters, md_status=row["key"], status=cell["key"])
    return render(request, "stats_list.html", {
        "s": s, "filters": filters, "gsort": gsort, "genre_sorts": GENRE_SORTS,
        "options": {
            "status": [(k, STATUS_LABELS[k]) for k in STATUS_ORDER],
            "format": list(FORMAT_LABELS.items()),
            "country": list(COUNTRY_LABELS.items()),
        },
        "c": {
            "status": _linked(chart(s.status), filters, "status"),
            "formats": _linked(chart(s.formats), filters, "format"),
            "countries": _linked(chart(s.countries), filters, "country"),
            "years": _linked(chart(s.years), filters, "year"),
            "scores": _linked(chart(s.scores), filters, "score"),
            "timeline": timeline,
            "length": _linked(chart(s.length), filters, "length"),
            "through": _linked(chart(s.through), filters, "through", status="CURRENT"),
            "pub": _linked(chart(s.pub), filters, "pub", status="CURRENT"),
            "genres": _linked(chart(_genre_bars(s.genres, gsort)), filters, "genre"),
        },
        "mismatch_url": entries_url(filters, mismatch="1"),
        "nearly_url": entries_url(filters, status="CURRENT", through="90–99%", sort="through"),
    })


def _genre_bars(genres: list[dict], gsort: str) -> list[Bar]:
    top = [g for g in genres if gsort != "score" or g["scored"] >= 3][:10]
    def detail(g: dict) -> str:
        score = f" · mean score {g['mean_score']:.0f} ({g['scored']} scored)" if g["mean_score"] is not None else ""
        return f"{g['count']:,} entries · {g['chapters']:,} chapters read{score}"
    value = {"count": lambda g: g["count"], "chapters": lambda g: g["chapters"],
             "score": lambda g: round(g["mean_score"] or 0, 1)}[gsort]
    return [Bar(g["name"], value(g), detail(g), key=g["name"]) for g in top]


@router.get("/stats/entries", response_class=HTMLResponse)
def stats_entries(request: Request, sort: str = "title", page: int = 1) -> HTMLResponse:
    filters = _filters(request)
    repo = get_services(request).repo
    rows = entries(repo, filters, sort)
    page = max(1, page)
    shown = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    for r in shown:
        r["cover"] = md_cover_url(r["md_id"], r["md_cover_file"]) if r["md_id"] else r["cover_url"]
        r["md_url"] = f"https://mangadex.org/title/{r['md_id']}" if r["md_id"] else None
    chips = [{"label": f"{FILTER_LABELS[k]}: {filter_label(k, v)}",
              "remove": entries_url({x: y for x, y in filters.items() if x != k}, sort=sort)} for k, v in filters.items()]
    return render(request, "stats_entries.html", {
        "rows": shown, "total": len(rows), "page": page, "pages": max(1, -(-len(rows) // PAGE_SIZE)),
        "filters": filters, "chips": chips, "sort": sort,
        "sorts": {"title": "Title", "progress": "Chapters read", "score": "Score", "updated": "Last updated",
                  "year": "Release year", "through": "Read so far"},
        "url": lambda **kw: entries_url(filters, **{"sort": sort, **kw}),
        "status_labels": STATUS_LABELS, "md_labels": MD_STATUS_LABELS, "pub_labels": PUB_LABELS,
    })


@router.get("/stats/syncs", response_class=HTMLResponse)
def stats_syncs(request: Request, run: int | None = None) -> HTMLResponse:
    repo = get_services(request).repo
    runs = runs_with_writes(repo)
    if run is not None and run not in runs:
        run = None
    s = sync_stats(repo, run)
    for row in s.biggest + s.problems:
        row["cover"] = md_cover_url(row["md_id"], row["cover_file"])
        row["md_url"] = f"https://mangadex.org/title/{row['md_id']}"
    return render(request, "stats_syncs.html", {
        "s": s, "runs": runs, "selected": run,
        "c": {"per_run": chart(s.per_run), "jumps": chart(s.jumps), "transitions": chart(s.transitions)},
    })
