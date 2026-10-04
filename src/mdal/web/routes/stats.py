"""Stats pages: your list (AniList-style) and sync activity. DB reads only."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from mdal.stats import Bar, chart, list_stats, runs_with_writes, sync_stats
from mdal.web.app import get_services, md_cover_url, render

router = APIRouter()


@router.get("/stats", response_class=HTMLResponse)
def stats_list(request: Request) -> HTMLResponse:
    s = list_stats(get_services(request).repo)
    return render(request, "stats_list.html", {
        "s": s,
        "c": {
            "status": chart(s.status), "formats": chart(s.formats), "countries": chart(s.countries),
            "years": chart(s.years), "scores": chart(s.scores), "completed": chart(s.completed_by_year),
            "genres": chart(_genre_bars(s.genres)),
        },
    })


def _genre_bars(genres: list[dict]) -> list[Bar]:
    top = genres[:10]
    return [Bar(g["name"], g["count"], f"{g['count']:,} entries · {g['chapters']:,} chapters read"
                + (f" · mean score {g['mean_score']:.0f}" if g["mean_score"] is not None else "")) for g in top]


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
