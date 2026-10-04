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
    tip: str = ""            # tooltip / table detail beyond the value
    key: str | None = None   # drill-down filter value for this chart's dimension
    value2: float | None = None
    tip2: str = ""


# ---- entries: one row per list entry, with everything the charts and the drill-down need ----------------

MD_STATUS_LABELS = {"reading": "Reading", "completed": "Completed", "on_hold": "On hold", "plan_to_read": "Plan to read",
                    "dropped": "Dropped", "re_reading": "Re-reading", "none": "Not in library"}
MD_ORDER = ("reading", "completed", "on_hold", "plan_to_read", "dropped", "re_reading", "none")
AL_FOR_MD = {"reading": "CURRENT", "completed": "COMPLETED", "on_hold": "PAUSED", "plan_to_read": "PLANNING",
             "dropped": "DROPPED", "re_reading": "REPEATING"}
PUB_LABELS = {"RELEASING": "Releasing", "FINISHED": "Finished", "HIATUS": "On hiatus", "CANCELLED": "Cancelled",
              "NOT_YET_RELEASED": "Not yet released"}
PUB_ORDER = ("RELEASING", "FINISHED", "HIATUS", "CANCELLED", "NOT_YET_RELEASED")
LENGTH_BUCKETS = (("0", 0, 0), ("1", 1, 1), ("2–10", 2, 10), ("11–25", 11, 25), ("26–50", 26, 50),
                  ("51–100", 51, 100), ("101–200", 101, 200), ("201+", 201, None))
THROUGH_BUCKETS = (("Under 25%", 0, 25), ("25–49%", 25, 50), ("50–74%", 50, 75), ("75–89%", 75, 90),
                   ("90–99%", 90, 100), ("Caught up", 100, None))

FILTER_LABELS = {
    "status": "Status", "format": "Format", "country": "Country", "genre": "Genre", "year": "Released",
    "score": "Score", "started": "Started", "completed": "Completed", "length": "Chapters read",
    "through": "Read so far", "pub": "Publication", "md_status": "MangaDex status", "mismatch": "Status differs",
    "tag": "Tag", "staff": "Staff", "q": "Title",
}
GLOBAL_FILTERS = ("status", "format", "country")


def length_bucket(progress: int | None) -> str:
    p = progress or 0
    for label, lo, hi in LENGTH_BUCKETS:
        if p >= lo and (hi is None or p <= hi):
            return label
    return "201+"


def through_bucket(progress: int | None, chapters: int | None) -> str | None:
    """How far through a series with a known total; None when the total is unknown."""
    if not chapters:
        return None
    pct = 100 * (progress or 0) / chapters
    for label, lo, hi in THROUGH_BUCKETS:
        if pct >= lo and (hi is None or pct < hi):
            return label
    return "Caught up"


def score_bucket(score: float | None) -> str | None:
    return str(min(100, max(10, math.ceil(score / 10) * 10))) if score else None


def entry_rows(repo: Repo) -> list[dict[str, Any]]:
    rows = repo.conn.execute(
        "SELECT e.entry_id, e.media_id, e.status, e.progress, e.progress_volumes, e.score, e.started_at, "
        "e.completed_at, e.updated_at, m.format, m.country, m.start_year, m.genres, m.chapters, m.status AS pub, "
        "m.romaji, m.english, m.native, m.site_url, m.cover_url, m.tags, m.staff_roles "
        "FROM al_entry e LEFT JOIN al_media m ON m.media_id = e.media_id"
    ).fetchall()
    md: dict[int, Any] = {}
    for r in repo.conn.execute(
        "SELECT p.al_media_id, d.md_id, d.reading_status, d.cover_file, d.title FROM mapping p "
        "JOIN md_manga d ON d.md_id = p.md_id WHERE p.state IN ('auto','confirmed') ORDER BY d.md_id"
    ):
        md.setdefault(r["al_media_id"], r)
    out = []
    for r in rows:
        d = md.get(r["media_id"])
        row = dict(r)
        row["genres"] = json.loads(r["genres"] or "[]")
        row["tag_ranks"] = json.loads(r["tags"] or "[]")
        row["tags"] = [t["name"] for t in row["tag_ranks"]]
        row["tags_known"] = r["tags"] is not None
        row["staff"] = json.loads(r["staff_roles"] or "[]")
        row["staff_known"] = r["staff_roles"] is not None
        row["md_id"] = d["md_id"] if d else None
        row["md_status"] = (d["reading_status"] or "none") if d else "none"
        row["md_cover_file"] = d["cover_file"] if d else None
        row["title"] = r["romaji"] or r["english"] or r["native"] or (d["title"] if d else None) or f"#{r['media_id']}"
        row["through"] = through_bucket(r["progress"], r["chapters"])
        out.append(row)
    return out


def matches(row: dict[str, Any], f: dict[str, str]) -> bool:
    for key, want in f.items():
        if not want:
            continue
        if key in ("status", "format", "country", "pub", "md_status") and (row.get(key) or "") != want:
            return False
        if key == "genre" and want not in row["genres"]:
            return False
        if key == "tag" and want not in row["tags"]:
            return False
        if key == "staff" and want not in {str(p["id"]) for p in row["staff"]}:
            return False
        if key == "year" and str(row["start_year"] or "") != want:
            return False
        if key == "score" and score_bucket(row["score"]) != want:
            return False
        if key in ("started", "completed") and (row[f"{key}_at"] or "")[:4] != want:
            return False
        if key == "length" and length_bucket(row["progress"]) != want:
            return False
        if key == "through" and row["through"] != want:
            return False
        if key == "mismatch" and want == "1" and not is_mismatch(row):
            return False
        if key == "q" and want.casefold() not in " ".join(
                str(row.get(k) or "") for k in ("romaji", "english", "native")).casefold():
            return False
    return True


def mismatch_count(repo: Repo) -> int:
    return sum(1 for r in entry_rows(repo) if is_mismatch(r))


def is_mismatch(row: dict[str, Any]) -> bool:
    expected = AL_FOR_MD.get(row["md_status"])
    return expected is not None and row["status"] != expected


def filter_label(key: str, value: str) -> str:
    labels = {"status": STATUS_LABELS, "format": FORMAT_LABELS, "country": COUNTRY_LABELS, "pub": PUB_LABELS,
              "md_status": MD_STATUS_LABELS}.get(key, {})
    if key == "score":
        return f"{int(value) - 9}–{value}"
    if key == "mismatch":
        return "MangaDex vs AniList"
    return labels.get(value, value)


# ---- the list overview ------------------------------------------------------------------------


@dataclass
class ListStats:
    total: int = 0
    chapters: int = 0
    volumes: int = 0
    mean_score: float | None = None
    score_sd: float | None = None
    scored: int = 0
    completed: int = 0
    status: list[Bar] = field(default_factory=list)
    formats: list[Bar] = field(default_factory=list)
    countries: list[Bar] = field(default_factory=list)
    years: list[Bar] = field(default_factory=list)
    scores: list[Bar] = field(default_factory=list)
    timeline: list[Bar] = field(default_factory=list)
    length: list[Bar] = field(default_factory=list)
    through: list[Bar] = field(default_factory=list)
    nearly: int = 0
    pub: list[Bar] = field(default_factory=list)
    genres: list[dict[str, Any]] = field(default_factory=list)
    has_genres: bool = False
    tags: list[dict[str, Any]] = field(default_factory=list)
    tags_known: int = 0
    staff: list[dict[str, Any]] = field(default_factory=list)
    staff_known: int = 0
    heat: dict[str, Any] = field(default_factory=dict)
    md: dict[str, int] = field(default_factory=dict)


def _bars(counter: Counter, labels: dict[str, str] | None = None, order: tuple[str, ...] | None = None,
          unit: str = "entries", other_after: int = 7) -> list[Bar]:
    keys = [k for k in order if counter.get(k)] if order else [k for k, _ in counter.most_common()]
    def bar(k: str) -> Bar:
        return Bar((labels or {}).get(k, str(k)), counter[k], f"{counter[k]:,} {unit}", key=str(k))
    if not order and len(keys) > other_after:  # fold the long tail: never more than ~8 bars
        rest = sum(counter[k] for k in keys[other_after:])
        return [bar(k) for k in keys[:other_after]] + [Bar("Other", rest, f"{rest:,} {unit}")]
    return [bar(k) for k in keys]


def _year_columns(counter: Counter, unit: str) -> list[Bar]:
    if not counter:
        return []
    lo, hi = min(counter), max(counter)
    return [Bar(str(y), counter.get(y, 0), f"{counter.get(y, 0):,} {unit} in {y}", key=str(y)) for y in range(lo, hi + 1)]


class _Groups:
    """Entries grouped by genre, tag or person: count, chapters read, mean score."""

    def __init__(self) -> None:
        self.count: Counter = Counter()
        self.chapters: Counter = Counter()
        self.scores: dict[str, list[float]] = defaultdict(list)

    def add(self, key: str, row: dict[str, Any]) -> None:
        self.count[key] += 1
        self.chapters[key] += row["progress"] or 0
        if row["score"]:
            self.scores[key].append(row["score"])

    def ranked(self, sort: str = "count") -> list[dict[str, Any]]:
        out = [{"name": k, "count": n, "chapters": self.chapters[k], "scored": len(self.scores[k]),
                "mean_score": (sum(self.scores[k]) / len(self.scores[k])) if self.scores[k] else None}
               for k, n in self.count.most_common()]
        if sort == "score":  # needs a few scores to mean anything
            out.sort(key=lambda g: (g["scored"] >= 3, g["mean_score"] or 0), reverse=True)
        elif sort == "chapters":
            out.sort(key=lambda g: g["chapters"], reverse=True)
        return out


def list_stats(repo: Repo, filters: dict[str, str] | None = None, genre_sort: str = "count") -> ListStats:
    rows = [r for r in entry_rows(repo) if matches(r, filters or {})]
    s = ListStats(total=len(rows))
    status, formats, countries, years, buckets = Counter(), Counter(), Counter(), Counter(), Counter()
    started, completed, length, through, pub = Counter(), Counter(), Counter(), Counter(), Counter()
    heat: Counter = Counter()
    genre_groups = _Groups()
    tag_groups = _Groups()
    staff_groups = _Groups()
    staff_names: dict[str, str] = {}
    staff_roles: dict[str, Counter] = defaultdict(Counter)
    scores: list[float] = []
    for r in rows:
        s.chapters += r["progress"] or 0
        s.volumes += r["progress_volumes"] or 0
        status[r["status"]] += 1
        s.completed += r["status"] == "COMPLETED"
        if r["format"]:
            formats[r["format"]] += 1
        if r["country"]:
            countries[r["country"]] += 1
        if r["start_year"]:
            years[r["start_year"]] += 1
        if r["score"]:
            scores.append(r["score"])
            buckets[score_bucket(r["score"])] += 1
        if r["started_at"]:
            started[int(r["started_at"][:4])] += 1
        if r["completed_at"]:
            completed[int(r["completed_at"][:4])] += 1
        length[length_bucket(r["progress"])] += 1
        if r["status"] == "CURRENT":
            if r["through"]:
                through[r["through"]] += 1
            if r["pub"]:
                pub[r["pub"]] += 1
        heat[(r["md_status"], r["status"])] += 1
        for g in r["genres"]:
            genre_groups.add(g, r)
        for t in r["tags"]:
            tag_groups.add(t, r)
        s.tags_known += r["tags_known"]
        s.staff_known += r["staff_known"]
        for person in {str(p["id"]): p for p in r["staff"]}.values():
            key = str(person["id"])
            staff_groups.add(key, r)
            staff_names[key] = person["name"]
            staff_roles[key][person["role"]] += 1
    s.scored = len(scores)
    if scores:
        s.mean_score = sum(scores) / len(scores)
        s.score_sd = math.sqrt(sum((x - s.mean_score) ** 2 for x in scores) / len(scores))
    s.status = _bars(status, STATUS_LABELS, STATUS_ORDER)
    s.formats = _bars(formats, FORMAT_LABELS)
    s.countries = _bars(countries, COUNTRY_LABELS)
    s.years = _year_columns(years, "series")
    s.scores = [Bar(f"{b - 9}–{b}", buckets.get(str(b), 0), f"{buckets.get(str(b), 0):,} scored {b - 9}–{b}", key=str(b))
                for b in range(10, 101, 10)] if scores else []
    span = sorted(set(started) | set(completed))
    s.timeline = [Bar(str(y), started.get(y, 0), f"{started.get(y, 0):,} started in {y}", key=str(y),
                      value2=completed.get(y, 0), tip2=f"{completed.get(y, 0):,} completed in {y}")
                  for y in range(span[0], span[-1] + 1)] if span else []
    s.length = [Bar(label, length.get(label, 0), f"{length.get(label, 0):,} series with {label} chapters read", key=label)
                for label, _, _ in LENGTH_BUCKETS if length.get(label)]
    s.through = [Bar(label, through.get(label, 0), f"{through.get(label, 0):,} of the series you're reading", key=label)
                 for label, _, _ in THROUGH_BUCKETS if through.get(label)]
    s.nearly = through.get("90–99%", 0)
    s.pub = _bars(pub, PUB_LABELS, PUB_ORDER, unit="series you're reading")
    s.has_genres = bool(genre_groups.count)
    s.genres = genre_groups.ranked(genre_sort)
    s.tags = tag_groups.ranked(genre_sort)
    s.staff = staff_groups.ranked(genre_sort)
    for p in s.staff:
        p["key"] = p["name"]
        p["name"] = staff_names[p["key"]]
        p["roles"] = [role for role, _ in staff_roles[p["key"]].most_common(2)]
    md_rows = [m for m in MD_ORDER if any(heat.get((m, a)) for a in STATUS_ORDER)]
    al_cols = [a for a in STATUS_ORDER if any(heat.get((m, a)) for m in MD_ORDER)]
    s.heat = {
        "rows": [{"key": m, "label": MD_STATUS_LABELS[m],
                  "cells": [{"key": a, "value": heat.get((m, a), 0), "match": AL_FOR_MD.get(m) == a,
                             "tip": f"{heat.get((m, a), 0):,} · MangaDex {MD_STATUS_LABELS[m]}, AniList {STATUS_LABELS.get(a, a)}"}
                            for a in al_cols]} for m in md_rows],
        "cols": [{"key": a, "label": STATUS_LABELS.get(a, a)} for a in al_cols],
        "max": max(heat.values(), default=0),
        "mismatches": sum(1 for r in rows if is_mismatch(r)),
    }
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


STATUS_RANK = {k: i for i, k in enumerate(STATUS_ORDER)}
# column -> (sort key, newest/biggest first by default)
ENTRY_SORTS = {
    "title": (lambda r: r["title"].casefold(), False),
    "status": (lambda r: (STATUS_RANK.get(r["status"], 99), r["title"].casefold()), False),
    "progress": (lambda r: r["progress"] or 0, True),
    "through": (lambda r: (r["progress"] or 0) / r["chapters"] if r["chapters"] else -1, True),
    "score": (lambda r: r["score"] or 0, True),
    "year": (lambda r: r["start_year"] or 0, True),
    "updated": (lambda r: r["updated_at"] or 0, True),
    "md": (lambda r: (MD_ORDER.index(r["md_status"]) if r["md_status"] in MD_ORDER else 99, r["title"].casefold()), False),
}


def entries(repo: Repo, filters: dict[str, str], sort: str = "title", desc: bool | None = None) -> list[dict[str, Any]]:
    key, default_desc = ENTRY_SORTS.get(sort, ENTRY_SORTS["title"])
    return sorted((r for r in entry_rows(repo) if matches(r, filters)), key=key,
                  reverse=default_desc if desc is None else desc)


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
    requests_mal: int = 0
    per_run: list[Bar] = field(default_factory=list)
    jumps: list[Bar] = field(default_factory=list)
    transitions: list[Bar] = field(default_factory=list)
    biggest: list[dict[str, Any]] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)


def _is_problem(note: str | None) -> bool:
    return bool(note) and note != "verified" and not note.endswith("; verified")


def sync_stats(repo: Repo, run_id: int | None = None, target: str | None = None) -> SyncStats:
    """Everything written by syncs; one run, or one site ("anilist" / "mal"), or all."""
    clauses, params = [], []
    if run_id is not None:
        clauses.append("r.run_id=?")
        params.append(run_id)
    if target is not None:
        clauses.append("r.target=?")
        params.append(target)
    run_where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    item_where = "".join(f" AND {c}" for c in clauses)
    args = tuple(params)
    items = repo.conn.execute(
        "SELECT i.*, m.title AS md_title, m.cover_file, a.romaji, a.english, a.site_url "
        "FROM sync_item i JOIN sync_run r ON r.run_id = i.run_id "
        "LEFT JOIN md_manga m ON m.md_id = i.md_id LEFT JOIN al_media a ON a.media_id = i.al_media_id "
        f"WHERE i.write_state != 'none'{item_where} ORDER BY i.run_id, i.md_id",
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
    runs = repo.conn.execute("SELECT run_id, req_anilist, req_mangadex, req_mal FROM sync_run r" + run_where, args).fetchall()
    s.requests_anilist = sum(r["req_anilist"] for r in runs)
    s.requests_mangadex = sum(r["req_mangadex"] for r in runs)
    s.requests_mal = sum(r["req_mal"] for r in runs)
    written_runs = sorted({i["run_id"] for i in items if i["write_state"] == "done"})
    s.runs_with_writes = len(written_runs)
    s.per_run = [Bar(f"#{r}", per_run.get(r, 0), f"sync #{r}: {per_run.get(r, 0):,} chapters") for r in written_runs]
    return s


def runs_with_writes(repo: Repo, target: str | None = None) -> list[int]:
    return [r[0] for r in repo.conn.execute(
        "SELECT DISTINCT i.run_id FROM sync_item i JOIN sync_run r ON r.run_id = i.run_id "
        "WHERE i.write_state != 'none'" + (" AND r.target=?" if target else "") + " ORDER BY i.run_id DESC",
        (target,) if target else ())]


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
    biggest = max([b.value for b in bars] + [b.value2 or 0 for b in bars], default=0)
    top = nice_max(biggest)
    every = max(1, math.ceil(len(bars) / max_labels))
    return {
        # pct: against the axis top (columns, which have an axis); rel: against the largest bar (labelled bars)
        "bars": [{"label": b.label, "value": b.value, "tip": b.tip, "pct": 100 * b.value / top,
                  "rel": 100 * b.value / biggest if biggest else 0, "key": b.key,
                  "value2": b.value2, "tip2": b.tip2, "pct2": 100 * (b.value2 or 0) / top,
                  "show_label": i % every == 0 or i == len(bars) - 1} for i, b in enumerate(bars)],
        "ticks": [top, top / 2, 0],
        "empty": not bars or all(b.value == 0 and not b.value2 for b in bars),
    }
