"""Stats pages: library (AniList-style), the entries drill-down, and sync activity. DB reads only."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from mdal.stats import (
    COUNTRY_LABELS,
    ENTRY_SORTS,
    FILTER_LABELS,
    FORMAT_LABELS,
    GLOBAL_FILTERS,
    MD_STATUS_LABELS,
    PUB_LABELS,
    STATUS_ORDER,
    Bar,
    chart,
    entries,
    entry_rows,
    filter_label,
    list_stats,
    matches,
    runs_with_writes,
    sync_stats,
)
from mdal.stats_graph import focus_graph, theme_graph
from mdal.sync.orchestrator import SITE_NAMES
from mdal.sync.rules import STATUS_LABELS
from mdal.web.app import get_services, graph_json, md_cover_url, render

router = APIRouter()

PAGE_SIZE = 100
RANK_SORTS = {"count": "Entries", "score": "Mean score", "chapters": "Chapters read"}
ENTRY_COLUMNS = (  # (sort key or None, header)
    ("title", "Series"), ("status", "AniList"), ("progress", "Progress"), ("through", "Read"),
    ("score", "Score"), (None, "Genres"), ("md", "MangaDex"), ("updated", "Updated"),
)


def _filters(request: Request, keys: tuple[str, ...] = tuple(FILTER_LABELS)) -> dict[str, str]:
    return {k: v for k in keys if (v := request.query_params.get(k, "").strip())}


def entries_url(filters: dict[str, str], **extra: Any) -> str:
    merged = {**filters, **{k: v for k, v in extra.items() if v is not None}}
    return "/stats/entries?" + urlencode(merged)


def _linked(c: dict[str, Any], filters: dict[str, str], dim: str, **fixed: str) -> dict[str, Any]:
    """Point every bar at the entries list for that bar (filters so far + this chart's dimension)."""
    for b in c["bars"]:
        b["href"] = entries_url(filters, **fixed, **{dim: b["key"]}) if b["key"] is not None else None
    return c


def _ranked_bars(items: list[dict], sort: str, unit: str = "entries") -> list[Bar]:
    top = [g for g in items if sort != "score" or g["scored"] >= 3][:10]

    def detail(g: dict) -> str:
        score = f" · mean score {g['mean_score']:.0f} ({g['scored']} scored)" if g["mean_score"] is not None else ""
        roles = f" · {', '.join(g['roles'])}" if g.get("roles") else ""
        return f"{g['count']:,} {unit} · {g['chapters']:,} chapters read{score}{roles}"

    value = {"count": lambda g: g["count"], "chapters": lambda g: g["chapters"],
             "score": lambda g: round(g["mean_score"] or 0, 1)}[sort]
    return [Bar(g["name"], value(g), detail(g), key=str(g.get("key", g["name"]))) for g in top]


@router.get("/stats", response_class=HTMLResponse)
def stats_list(request: Request, gsort: str = "count") -> HTMLResponse:
    filters = _filters(request, GLOBAL_FILTERS)
    gsort = gsort if gsort in RANK_SORTS else "count"
    s = list_stats(get_services(request).repo, filters, gsort)
    timeline = chart(s.timeline)
    for b in timeline["bars"]:
        b["href"] = entries_url(filters, started=b["key"])
        b["href2"] = entries_url(filters, completed=b["key"])
    for row in s.heat.get("rows", []):
        for cell in row["cells"]:
            cell["href"] = entries_url(filters, md_status=row["key"], status=cell["key"])
    return render(request, "stats_list.html", {
        "s": s, "filters": filters, "gsort": gsort, "rank_sorts": RANK_SORTS,
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
            "genres": _linked(chart(_ranked_bars(s.genres, gsort)), filters, "genre"),
            "tags": _linked(chart(_ranked_bars(s.tags, gsort)), filters, "tag"),
            "staff": _linked(chart(_ranked_bars(s.staff, gsort, "series")), filters, "staff"),
        },
        "entries_url": entries_url(filters),
        "mismatch_url": entries_url(filters, mismatch="1"),
        "nearly_url": entries_url(filters, status="CURRENT", through="90–99%", sort="through"),
        "rank_url": lambda key: "/stats?" + urlencode({**filters, "gsort": key}),
        "genre_url": lambda name: entries_url(filters, genre=name),
        "tag_url": lambda name: entries_url(filters, tag=name),
        "staff_url": lambda key: entries_url(filters, staff=key),
    })


@router.get("/stats/entries", response_class=HTMLResponse)
def stats_entries(request: Request, sort: str = "title", dir: str = "", page: int = 1) -> HTMLResponse:
    filters = _filters(request)
    repo = get_services(request).repo
    sort = sort if sort in ENTRY_SORTS else "title"
    desc = {"asc": False, "desc": True}.get(dir)  # None: the column's natural order
    rows = entries(repo, filters, sort, desc)
    current_desc = ENTRY_SORTS[sort][1] if desc is None else desc
    page = max(1, page)
    shown = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    for r in shown:
        r["cover"] = md_cover_url(r["md_id"], r["md_cover_file"]) if r["md_id"] else r["cover_url"]
        r["md_url"] = f"https://mangadex.org/title/{r['md_id']}" if r["md_id"] else None
    staff_names = {str(p["id"]): p["name"] for r in rows for p in r["staff"]}
    chips = [{"label": f"{FILTER_LABELS[k]}: {staff_names.get(v, v) if k == 'staff' else filter_label(k, v)}",
              "remove": entries_url({x: y for x, y in filters.items() if x != k}, sort=sort)} for k, v in filters.items()]
    columns = []
    for key, label in ENTRY_COLUMNS:
        if key is None:
            columns.append({"label": label})
            continue
        active = key == sort
        next_desc = (not current_desc) if active else ENTRY_SORTS[key][1]
        columns.append({"label": label, "active": active, "desc": current_desc if active else None,
                        "href": entries_url(filters, sort=key, dir="desc" if next_desc else "asc")})
    return render(request, "stats_entries.html", {
        "rows": shown, "total": len(rows), "page": page, "pages": max(1, -(-len(rows) // PAGE_SIZE)),
        "filters": filters, "chips": chips, "sort": sort, "columns": columns,
        "url": lambda **kw: entries_url(filters, **{"sort": sort, "dir": dir or None, **kw}),
        "library_url": "/stats?" + urlencode({k: v for k, v in filters.items() if k in GLOBAL_FILTERS}),
        "status_labels": STATUS_LABELS, "md_labels": MD_STATUS_LABELS, "pub_labels": PUB_LABELS,
    })


@router.get("/stats/syncs", response_class=HTMLResponse)
def stats_syncs(request: Request, run: str = "", site: str = "") -> HTMLResponse:
    """`run` and `site` arrive as "" when a filter is set to All."""
    repo = get_services(request).repo
    run = int(run) if run.strip().isdigit() else None
    target = site if site in SITE_NAMES else None
    runs = runs_with_writes(repo, target)
    if run is not None and run not in runs:
        run = None
    s = sync_stats(repo, run, target)
    for row in s.biggest + s.problems:
        row["cover"] = md_cover_url(row["md_id"], row["cover_file"])
        row["md_url"] = f"https://mangadex.org/title/{row['md_id']}"
    return render(request, "stats_syncs.html", {
        "s": s, "runs": runs, "selected": run, "site": target or "", "sites": SITE_NAMES,
        "has_mal": bool(runs_with_writes(repo, "mal")),
        "c": {"per_run": chart(s.per_run), "jumps": chart(s.jumps), "transitions": chart(s.transitions)},
    })


TRAIL_MAX = 6


def connections_url(filters: dict[str, str], show: str, focus: str | None = None, trail: list[str] | None = None) -> str:
    params: list[tuple[str, str]] = [*filters.items(), ("show", show)]
    params += [("trail", t) for t in (trail or [])[-TRAIL_MAX:]]
    if focus:
        params.append(("focus", focus))
    return "/stats/connections?" + urlencode(params)


@router.get("/stats/connections", response_class=HTMLResponse)
def stats_connections(request: Request, show: str = "tags", focus: str = "") -> HTMLResponse:
    """The overview network, or with `focus` one theme opened into the series that have it.
    `trail` holds the themes opened before this one, for the breadcrumb."""
    filters = _filters(request, GLOBAL_FILTERS)
    show = show if show in ("tags", "genres") else "tags"
    key = "tag" if show == "tags" else "genre"
    trail = [x for x in request.query_params.getlist("trail") if x and x != focus][-TRAIL_MAX:]
    rows = [r for r in entry_rows(get_services(request).repo) if matches(r, filters)]
    context: dict[str, Any] = {
        "show": show, "filters": filters, "focus": focus or None,
        "show_urls": {v: connections_url(filters, v) for v in ("tags", "genres")},
        "options": {
            "status": [(k, STATUS_LABELS[k]) for k in STATUS_ORDER],
            "format": list(FORMAT_LABELS.items()),
            "country": list(COUNTRY_LABELS.items()),
        },
        "tags_known": any(r["tags_known"] for r in rows),
        "overview_url": connections_url(filters, show),
    }
    if focus:
        graph = focus_graph(rows, show, focus)
        for n in graph["nodes"]:
            if n["kind"] == "focus":
                n["href"] = entries_url(filters, **{key: focus})
            elif n["kind"] == "theme":
                n["href"] = connections_url(filters, show, n["key"], [*trail, focus])
            elif n["kind"] == "creator":
                n["href"] = entries_url(filters, staff=n["key"])
        for r in graph["related"]:
            r["href"] = connections_url(filters, show, r["name"], [*trail, focus])
        context.update({
            "graph": graph, "graph_json": graph_json(graph), "entries_url": entries_url(filters, **{key: focus}),
            "breadcrumb": [(name, connections_url(filters, show, name, trail[:i])) for i, name in enumerate(trail)],
        })
        return render(request, "stats_connections.html", context)

    graph = theme_graph(rows, show)
    for n in graph["nodes"]:
        n["href"] = connections_url(filters, show, n["id"])
        n["tip"] = [*n["tip"], "Click to open it"]
    for p in graph["pairs"]:
        p["href_a"], p["href_b"] = connections_url(filters, show, p["a"]), connections_url(filters, show, p["b"])
    context.update({"graph": graph, "graph_json": graph_json(graph)})
    return render(request, "stats_connections.html", context)
