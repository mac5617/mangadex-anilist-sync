"""Numbers for the stats pages. DB reads only: rendering a stats page never calls an API.

- `list_stats`: an AniList-style overview of your manga list (from the last sync's list snapshot)
  plus the MangaDex side (library, read markers, matching).
- `sync_stats`: what syncs actually wrote: updates, adds, chapters, completions, problems.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from mdal.db.repo import Repo
from mdal.sync.rules import STATUS_LABELS

STATUS_ORDER = ("CURRENT", "COMPLETED", "PAUSED", "DROPPED", "PLANNING", "REPEATING")
FORMAT_LABELS = {"MANGA": "Manga", "ONE_SHOT": "One shot", "NOVEL": "Light novel"}
COUNTRY_LABELS = {"JP": "Japan", "KR": "South Korea", "CN": "China", "TW": "Taiwan", "HK": "Hong Kong"}
JUMP_BUCKETS = ((1, 1, "1"), (2, 5, "2–5"), (6, 10, "6–10"), (11, 25, "11–25"), (26, 50, "26–50"),
                (51, 100, "51–100"), (101, None, "100+"))


@dataclass(frozen=True)
class Bar:
    label: str
    value: float
    tip: str = ""          # tooltip / table detail beyond the value


@dataclass
class ListStats:
    total: int = 0
    chapters: int = 0
    volumes: int = 0
    mean_score: float | None = None
    score_sd: float | None = None
    scored: int = 0
    status: list[Bar] = field(default_factory=list)
    formats: list[Bar] = field(default_factory=list)
    countries: list[Bar] = field(default_factory=list)
    years: list[Bar] = field(default_factory=list)
    scores: list[Bar] = field(default_factory=list)
    completed_by_year: list[Bar] = field(default_factory=list)
    genres: list[dict[str, Any]] = field(default_factory=list)
    has_genres: bool = False
    md: dict[str, int] = field(default_factory=dict)


def _bars(counter: Counter, labels: dict[str, str] | None = None, order: tuple[str, ...] | None = None,
          unit: str = "entries", other_after: int = 7) -> list[Bar]:
    keys = [k for k in order if counter.get(k)] if order else [k for k, _ in counter.most_common()]
    if not order and len(keys) > other_after:  # fold the long tail: never more than ~8 bars
        rest = sum(counter[k] for k in keys[other_after:])
        keys = keys[:other_after]
        bars = [Bar((labels or {}).get(k, str(k)), counter[k], f"{counter[k]:,} {unit}") for k in keys]
        return bars + [Bar("Other", rest, f"{rest:,} {unit}")]
    return [Bar((labels or {}).get(k, str(k)), counter[k], f"{counter[k]:,} {unit}") for k in keys]


def _year_columns(counter: Counter, unit: str) -> list[Bar]:
    if not counter:
        return []
    lo, hi = min(counter), max(counter)
    return [Bar(str(y), counter.get(y, 0), f"{counter.get(y, 0):,} {unit} in {y}") for y in range(lo, hi + 1)]


def list_stats(repo: Repo) -> ListStats:
    rows = repo.conn.execute(
        "SELECT e.status, e.progress, e.progress_volumes, e.score, e.completed_at, "
        "m.format, m.country, m.start_year, m.genres FROM al_entry e LEFT JOIN al_media m ON m.media_id = e.media_id"
    ).fetchall()
    s = ListStats(total=len(rows))
    status, formats, countries, years, buckets, completed = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    genre_count: Counter = Counter()
    genre_scores: dict[str, list[float]] = defaultdict(list)
    genre_chapters: Counter = Counter()
    scores: list[float] = []
    for r in rows:
        s.chapters += r["progress"] or 0
        s.volumes += r["progress_volumes"] or 0
        status[r["status"]] += 1
        if r["format"]:
            formats[r["format"]] += 1
        if r["country"]:
            countries[r["country"]] += 1
        if r["start_year"]:
            years[r["start_year"]] += 1
        if r["score"]:
            scores.append(r["score"])
            buckets[min(100, max(10, math.ceil(r["score"] / 10) * 10))] += 1
        if r["completed_at"]:
            completed[int(r["completed_at"][:4])] += 1
        for g in json.loads(r["genres"] or "[]"):
            genre_count[g] += 1
            genre_chapters[g] += r["progress"] or 0
            if r["score"]:
                genre_scores[g].append(r["score"])
    s.scored = len(scores)
    if scores:
        s.mean_score = sum(scores) / len(scores)
        s.score_sd = math.sqrt(sum((x - s.mean_score) ** 2 for x in scores) / len(scores))
    s.status = _bars(status, STATUS_LABELS, STATUS_ORDER)
    s.formats = _bars(formats, FORMAT_LABELS)
    s.countries = _bars(countries, COUNTRY_LABELS)
    s.years = _year_columns(years, "series")
    s.completed_by_year = _year_columns(completed, "completed")
    s.scores = [Bar(str(b), buckets.get(b, 0), f"{buckets.get(b, 0):,} scored {b - 9}–{b}") for b in range(10, 101, 10)] if scores else []
    s.has_genres = bool(genre_count)
    s.genres = [
        {"name": g, "count": n, "chapters": genre_chapters[g],
         "mean_score": (sum(genre_scores[g]) / len(genre_scores[g])) if genre_scores[g] else None}
        for g, n in genre_count.most_common()
    ]
    md = repo.conn.execute(
        "SELECT (SELECT COUNT(*) FROM md_manga) AS library, (SELECT COUNT(*) FROM md_read) AS read_markers, "
        "(SELECT COUNT(*) FROM md_chapter WHERE missing > 0) AS unresolved, "
        "(SELECT COUNT(*) FROM mapping WHERE state IN ('auto','confirmed')) AS matched, "
        "(SELECT COUNT(*) FROM mapping WHERE state='review') AS review, "
        "(SELECT COUNT(*) FROM mapping WHERE state='unmatched') AS unmatched"
    ).fetchone()
    s.md = dict(md)
    s.md["not_on_list"] = len(repo.not_on_list())
    return s


# ---- sync activity ---------------------------------------------------------------


@dataclass
class SyncStats:
    run_id: int | None
    updated: int = 0
    added: int = 0
    chapters: int = 0
    completed: int = 0
    failed: int = 0
    dropped: int = 0
    needs_look: int = 0
    runs_with_writes: int = 0
    requests_anilist: int = 0
    requests_mangadex: int = 0
    per_run: list[Bar] = field(default_factory=list)
    jumps: list[Bar] = field(default_factory=list)
    transitions: list[Bar] = field(default_factory=list)
    biggest: list[dict[str, Any]] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)


def _is_problem(note: str | None) -> bool:
    return bool(note) and note != "verified" and not note.endswith("; verified")


def sync_stats(repo: Repo, run_id: int | None = None) -> SyncStats:
    where, args = ("AND i.run_id=?", (run_id,)) if run_id is not None else ("", ())
    items = repo.conn.execute(
        "SELECT i.*, m.title AS md_title, m.cover_file, a.romaji, a.english, a.site_url "
        "FROM sync_item i LEFT JOIN md_manga m ON m.md_id = i.md_id LEFT JOIN al_media a ON a.media_id = i.al_media_id "
        f"WHERE i.write_state != 'none' {where} ORDER BY i.run_id, i.md_id",
        args,
    ).fetchall()
    s = SyncStats(run_id)
    per_run: Counter = Counter()
    jumps: Counter = Counter()
    transitions: Counter = Counter()
    gains: list[dict[str, Any]] = []
    for i in items:
        state = i["write_state"]
        if state == "failed":
            s.failed += 1
        elif state == "dropped":
            s.dropped += 1
        if state == "done" and _is_problem(i["verify_note"]):
            s.needs_look += 1
        if state in ("failed",) or (state == "done" and _is_problem(i["verify_note"])):
            s.problems.append({"run_id": i["run_id"], "md_id": i["md_id"], "title": i["md_title"] or i["md_id"],
                               "cover_file": i["cover_file"], "note": i["verify_note"]})
        if state != "done":
            continue
        is_add = i["action"] == "add" or i["al_progress"] is None  # diff adds and single "Add" runs
        before = 0 if i["al_progress"] is None else i["al_progress"]
        gain = max(0, (i["md_progress"] or 0) - before)
        if is_add:
            s.added += 1
        else:
            s.updated += 1
        s.chapters += gain
        per_run[i["run_id"]] += gain
        if gain:
            for lo, hi, label in JUMP_BUCKETS:
                if gain >= lo and (hi is None or gain <= hi):
                    jumps[label] += 1
                    break
        if i["set_status"] == "COMPLETED" and i["status_approved"]:
            s.completed += 1
            before_label = "New entry" if is_add else STATUS_LABELS.get(i["al_status_before"] or "", "Unknown")
            transitions[before_label] += 1
        gains.append({"run_id": i["run_id"], "md_id": i["md_id"], "title": i["md_title"] or i["md_id"],
                      "cover_file": i["cover_file"], "al_title": i["romaji"] or i["english"], "al_url": i["site_url"],
                      "before": None if is_add else before, "after": i["md_progress"], "gain": gain,
                      "completed": bool(i["set_status"] == "COMPLETED" and i["status_approved"])})
    s.biggest = sorted(gains, key=lambda g: g["gain"], reverse=True)[:10]
    s.jumps = [Bar(label, jumps.get(label, 0), f"{jumps.get(label, 0):,} entries moved {label} chapters")
               for _, _, label in JUMP_BUCKETS] if jumps else []
    s.transitions = [Bar(k, v, f"{v:,} entries: {k} → Completed") for k, v in transitions.most_common()]
    runs = repo.conn.execute(
        "SELECT run_id, req_anilist, req_mangadex FROM sync_run" + (" WHERE run_id=?" if run_id is not None else ""),
        args,
    ).fetchall()
    s.requests_anilist = sum(r["req_anilist"] for r in runs)
    s.requests_mangadex = sum(r["req_mangadex"] for r in runs)
    written_runs = sorted({i["run_id"] for i in items if i["write_state"] == "done"})
    s.runs_with_writes = len(written_runs)
    s.per_run = [Bar(f"#{r}", per_run.get(r, 0), f"sync #{r}: {per_run.get(r, 0):,} chapters") for r in written_runs]
    return s


def runs_with_writes(repo: Repo) -> list[int]:
    return [r[0] for r in repo.conn.execute(
        "SELECT DISTINCT run_id FROM sync_item WHERE write_state != 'none' ORDER BY run_id DESC")]


def nice_max(value: float) -> float:
    """Smallest 1/2/5 x 10^k at or above value (axis top)."""
    if value <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if step * exp >= value:
            return step * exp
    return 10 * exp


def chart(bars: list[Bar], max_labels: int = 12) -> dict[str, Any]:
    """Bars scaled to a clean axis: pct per bar, three ticks, and which x labels to show."""
    biggest = max((b.value for b in bars), default=0)
    top = nice_max(biggest)
    every = max(1, math.ceil(len(bars) / max_labels))
    return {
        # pct: against the axis top (columns, which have an axis); rel: against the largest bar (labelled bars)
        "bars": [{"label": b.label, "value": b.value, "tip": b.tip, "pct": 100 * b.value / top,
                  "rel": 100 * b.value / biggest if biggest else 0,
                  "show_label": i % every == 0 or i == len(bars) - 1} for i, b in enumerate(bars)],
        "ticks": [top, top / 2, 0],
        "empty": not bars or all(b.value == 0 for b in bars),
    }
