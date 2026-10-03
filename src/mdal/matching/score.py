"""Candidate scoring and classification (architecture §7). Pure: no I/O, no DB.

A wrong match is worse than a missing one: every adjustment is written to `reasons`,
and any disagreeing signal blocks auto-accept.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from rapidfuzz import fuzz

from mdal.matching.normalize import normalize

# MangaDex originalLanguage → acceptable AniList countryOfOrigin values.
COUNTRIES: dict[str, frozenset[str]] = {
    "ja": frozenset({"JP"}),
    "ko": frozenset({"KR"}),
    "zh": frozenset({"CN"}),
    "zh-hk": frozenset({"TW", "HK", "CN"}),
}

YEAR_AGREE, YEAR_DISAGREE = 0.03, -0.15
COUNTRY_AGREE, COUNTRY_DISAGREE = 0.02, -0.15
NOVEL_PENALTY, ONE_SHOT_PENALTY = -0.30, -0.10
ONE_SHOT_MAX_CHAPTERS = 3
AUTHOR_AGREE, AUTHOR_DISAGREE = 0.05, -0.10
TOP_N = 5
EPS = 1e-9  # values exactly at a threshold go to the higher class, despite float error

Classification = Literal["auto", "review", "unmatched"]


def _json_list(value: str | None) -> tuple[str, ...]:
    return tuple(x for x in json.loads(value or "[]") if x)


@dataclass(frozen=True)
class MdSeries:
    md_id: str
    title: str
    alt_titles: tuple[str, ...] = ()
    original_language: str | None = None
    year: int | None = None
    authors: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> MdSeries:
        return cls(
            md_id=row["md_id"],
            title=row["title"],
            alt_titles=_json_list(row["alt_titles"]),
            original_language=row["original_language"],
            year=row["year"],
            authors=_json_list(row["authors"]),
        )


@dataclass(frozen=True)
class AlMedia:
    media_id: int
    romaji: str | None = None
    english: str | None = None
    native: str | None = None
    synonyms: tuple[str, ...] = ()
    format: str | None = None
    country: str | None = None
    start_year: int | None = None
    staff: tuple[str, ...] = ()

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> AlMedia:
        return cls(
            media_id=row["media_id"],
            romaji=row["romaji"],
            english=row["english"],
            native=row["native"],
            synonyms=_json_list(row["synonyms"]),
            format=row["format"],
            country=row["country"],
            start_year=row["start_year"],
            staff=_json_list(row["staff"]),
        )


@dataclass(frozen=True)
class Scored:
    media_id: int
    confidence: float
    reasons: tuple[str, ...]
    disagreements: tuple[str, ...]


@dataclass(frozen=True)
class Thresholds:
    auto: float = 0.92
    review: float = 0.60
    margin: float = 0.05


def title_sim(md: MdSeries, al: AlMedia) -> tuple[float, str]:
    """Best pair over (MD title ∪ alt titles) × (romaji, english, native, synonyms)."""
    md_titles = [("title", md.title)] + [("alt", t) for t in md.alt_titles]
    al_titles = [("romaji", al.romaji), ("english", al.english), ("native", al.native)]
    al_titles += [("synonym", s) for s in al.synonyms]
    best, reason = 0.0, "title 0.00 (no comparable titles)"
    for md_label, md_raw in md_titles:
        a = normalize(md_raw)
        if not a:
            continue
        for al_label, al_raw in al_titles:
            b = normalize(al_raw)
            if not b:
                continue
            sim = 1.0 if a == b else fuzz.token_sort_ratio(a, b) / 100
            if sim > best:
                op = "=" if sim == 1.0 else "~"
                best, reason = sim, f"title {sim:.2f} ({md_label} '{md_raw}' {op} {al_label} '{al_raw}')"
    return best, reason


def _name_tokens(names: tuple[str, ...]) -> set[str]:
    # Word overlap, not whole-name equality: "Oda Eiichirou" vs "Eiichiro Oda" must agree.
    return {tok for n in names for tok in normalize(n).split() if len(tok) >= 2}


def score(md: MdSeries, al: AlMedia, md_chapter_count: int) -> Scored:
    """`md_chapter_count` = read or listed MangaDex chapters (for the ONE_SHOT check)."""
    sim, title_reason = title_sim(md, al)
    total = sim
    reasons = [title_reason]
    disagreements: list[str] = []

    def add(delta: float, reason: str, disagree: bool = False) -> None:
        nonlocal total
        total += delta
        reasons.append(f"{reason} {delta:+.2f}")
        if disagree:
            disagreements.append(reason)

    # Start year
    if md.year is None or al.start_year is None:
        reasons.append("year unknown")
    else:
        delta_years = abs(md.year - al.start_year)
        label = f"year {md.year} vs {al.start_year}"
        if delta_years <= 1:
            add(YEAR_AGREE, label)
        elif delta_years >= 3:
            add(YEAR_DISAGREE, label, disagree=True)
        else:
            add(0.0, label)

    # Country vs original language
    expected = COUNTRIES.get((md.original_language or "").lower())
    if expected is None or not al.country:
        reasons.append("country unknown")
    elif al.country in expected:
        add(COUNTRY_AGREE, f"country {md.original_language} vs {al.country}")
    else:
        add(COUNTRY_DISAGREE, f"country {md.original_language} vs {al.country}", disagree=True)

    # Format
    if al.format == "NOVEL":
        add(NOVEL_PENALTY, "format NOVEL", disagree=True)
    elif al.format == "ONE_SHOT" and md_chapter_count > ONE_SHOT_MAX_CHAPTERS:
        add(ONE_SHOT_PENALTY, f"format ONE_SHOT vs {md_chapter_count} chapters", disagree=True)
    elif al.format:
        reasons.append(f"format {al.format}")
    else:
        reasons.append("format unknown")

    # Author
    md_names, al_names = _name_tokens(md.authors), _name_tokens(al.staff)
    if not md_names or not al_names:
        reasons.append("author unknown")
    elif md_names & al_names:
        add(AUTHOR_AGREE, f"author {', '.join(md.authors)} ~ {', '.join(al.staff)}")
    else:
        add(AUTHOR_DISAGREE, f"author {', '.join(md.authors)} vs {', '.join(al.staff)}", disagree=True)

    return Scored(al.media_id, min(max(total, 0.0), 1.0), tuple(reasons), tuple(disagreements))


def classify(scored: list[Scored], thresholds: Thresholds) -> tuple[Classification, list[Scored]]:
    """Returns the class and the top candidates (best first) to store for review."""
    ranked = sorted(scored, key=lambda s: s.confidence, reverse=True)
    top_n = ranked[:TOP_N]
    if not ranked:
        return "unmatched", top_n
    top = ranked[0]
    margin_ok = len(ranked) == 1 or top.confidence - ranked[1].confidence >= thresholds.margin - EPS
    if top.confidence >= thresholds.auto - EPS and margin_ok and not top.disagreements:
        return "auto", top_n
    if top.confidence >= thresholds.review - EPS:
        return "review", top_n
    return "unmatched", top_n
