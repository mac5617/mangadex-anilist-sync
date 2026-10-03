"""Matching pipeline, tiers 1–4 (architecture §7, story 11).

Request-minimising: links are validated in one bulk pass per tier (list media are free),
title searches are batched, and every result is cached in `mapping` until the series'
MangaDex links change or the user asks to retry.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Protocol

from mdal.clients.anilist import AniListClient
from mdal.db.repo import Repo
from mdal.fetch import anilist_list
from mdal.matching.normalize import normalize
from mdal.matching.score import AlMedia, MdSeries, Scored, Thresholds, classify, score

USER_STATES = ("confirmed", "not_on_anilist")   # tier 1: never re-matched (FR-20)
CACHED_STATES = ("auto", "review", "unmatched")  # reused until links change (FR-19)


class AniListLookup(Protocol):
    async def media_by_ids(self, ids: list[int]) -> dict[int, sqlite3.Row]: ...
    async def media_by_mal_ids(self, mal_ids: list[int]) -> dict[int, list[sqlite3.Row]]: ...
    async def search(self, titles: list[str]) -> list[list[int]]: ...


class AniListFetch:
    """The real lookup: fetch/anilist_list.py at the configured page and batch sizes."""

    def __init__(self, client: AniListClient, repo: Repo) -> None:
        self.client, self.repo = client, repo

    async def media_by_ids(self, ids: list[int]) -> dict[int, sqlite3.Row]:
        return await anilist_list.media_by_ids(self.client, self.repo, ids, self.repo.get_setting("anilist_page_size"))

    async def media_by_mal_ids(self, mal_ids: list[int]) -> dict[int, list[sqlite3.Row]]:
        return await anilist_list.media_by_mal_ids(
            self.client, self.repo, mal_ids, self.repo.get_setting("anilist_page_size")
        )

    async def search(self, titles: list[str]) -> list[list[int]]:
        results = await anilist_list.search_batch(
            self.client, self.repo, titles, self.repo.get_setting("anilist_search_batch")
        )
        return [[m["id"] for m in media] for media in results]


@dataclass
class MatchSummary:
    kept: int = 0           # tier 1 or cached, not looked at again
    auto: int = 0
    review: int = 0
    unmatched: int = 0
    searched: int = 0       # title searches sent (not requests: several go in one request)
    by_tier: dict[int, int] = field(default_factory=dict)


@dataclass
class _Pending:
    row: sqlite3.Row
    al_id: int | None
    mal_id: int | None
    scored: dict[int, Scored] = field(default_factory=dict)

    @property
    def md_id(self) -> str:
        return self.row["md_id"]


def parse_link_id(value: Any) -> int | None:
    """MangaDex stores links as strings; anything that is not a positive integer counts as absent."""
    text = str(value).strip() if value is not None else ""
    return int(text) if text.isdigit() and int(text) > 0 else None


def _links(row: sqlite3.Row) -> dict[str, Any]:
    links = json.loads(row["links"] or "{}")
    return links if isinstance(links, dict) else {}


def needs_matching(row: sqlite3.Row, mapping: sqlite3.Row | None) -> bool:
    if mapping is None:
        return True
    if mapping["state"] in USER_STATES:
        return False
    return mapping["links_hash"] != row["links_hash"]


def _label(media: sqlite3.Row) -> str:
    return media["romaji"] or media["english"] or media["native"] or f"#{media['media_id']}"


def _chapter_count(row: sqlite3.Row, read_counts: dict[str, int]) -> int:
    listed = 0
    try:
        listed = int(float(row["last_chapter"])) if row["last_chapter"] else 0
    except ValueError:
        pass
    return max(read_counts.get(row["md_id"], 0), listed)


def _latin_share(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    return sum(c.isascii() for c in letters) / len(letters) if letters else 0.0


def alt_search_title(row: sqlite3.Row) -> str | None:
    """The second search uses the first mostly-Latin alt title that differs from the primary.

    The stored alt titles carry no language, so this stands in for "English first, then ja-ro"
    (MangaDex lists those before native-script titles in practice).
    """
    primary = normalize(row["title"])
    alts = [a for a in json.loads(row["alt_titles"] or "[]") if normalize(a) and normalize(a) != primary]
    latin = [a for a in alts if _latin_share(a) >= 0.8]
    return (latin or alts or [None])[0]


def _link_candidate(media: sqlite3.Row, reason: str) -> dict[str, Any]:
    return {"al_media_id": media["media_id"], "score": 1.0, "reasons": [reason]}


class _Resolver:
    def __init__(self, repo: Repo, al: AniListLookup) -> None:
        self.repo, self.al = repo, al
        self.summary = MatchSummary()
        self.on_list = set(repo.al_entries())
        self.thresholds = Thresholds(
            repo.get_setting("match_auto"), repo.get_setting("match_review"), repo.get_setting("match_margin")
        )

    def save(self, p: _Pending, state: str, tier: int, al_media_id: int | None, confidence: float | None,
             reasons: list[str], candidates: list[dict[str, Any]]) -> None:
        self.repo.save_match(
            {"md_id": p.md_id, "al_media_id": al_media_id, "state": state, "tier": tier,
             "confidence": confidence, "reasons": reasons, "links_hash": p.row["links_hash"]},
            candidates,
        )
        setattr(self.summary, state, getattr(self.summary, state) + 1)
        self.summary.by_tier[tier] = self.summary.by_tier.get(tier, 0) + 1

    def where(self, media_id: int) -> str:
        return "on your list" if media_id in self.on_list else "not on your list"

    # ---- tier 2: links.al -------------------------------------------------
    async def tier2(self, pending: list[_Pending]) -> list[_Pending]:
        ids = [p.al_id for p in pending if p.al_id is not None]
        valid = await self.al.media_by_ids(ids) if ids else {}
        rest: list[_Pending] = []
        for p in pending:
            media = valid.get(p.al_id) if p.al_id is not None else None
            if media is None:
                rest.append(p)
                continue
            reason = f"links.al {p.al_id} → '{_label(media)}' ({self.where(p.al_id)})"
            problems: list[str] = []
            if media["format"] == "NOVEL":
                problems.append("linked media is a NOVEL")
            # FR-16 without an extra request: the linked media's own idMal must agree with links.mal.
            if p.mal_id is not None and media["id_mal"] is not None and media["id_mal"] != p.mal_id:
                problems.append(f"links.mal {p.mal_id} differs from the linked media's MAL id {media['id_mal']}")
            state = "review" if problems else "auto"
            self.save(p, state, 2, media["media_id"] if state == "auto" else None, 1.0,
                      [reason, *problems], [_link_candidate(media, reason)])
        return rest

    # ---- tier 3: links.mal ------------------------------------------------
    async def tier3(self, pending: list[_Pending]) -> list[_Pending]:
        ids = [p.mal_id for p in pending if p.mal_id is not None]
        found = await self.al.media_by_mal_ids(ids) if ids else {}
        rest: list[_Pending] = []
        for p in pending:
            media_list = found.get(p.mal_id, []) if p.mal_id is not None else []
            if not media_list:
                rest.append(p)
                continue
            candidates = [
                _link_candidate(m, f"links.mal {p.mal_id} → '{_label(m)}' ({self.where(m['media_id'])})")
                for m in media_list
            ]
            problems: list[str] = []
            if len(media_list) > 1:
                problems.append(f"{len(media_list)} AniList entries share MAL id {p.mal_id}")
            elif media_list[0]["format"] == "NOVEL":
                problems.append("linked media is a NOVEL")
            state = "review" if problems else "auto"
            self.save(p, state, 3, media_list[0]["media_id"] if state == "auto" else None, 1.0,
                      [*(c["reasons"][0] for c in candidates), *problems], candidates)
        return rest

    # ---- tier 4: title search ---------------------------------------------
    async def search_and_score(self, pending: list[_Pending], titles: list[str], read_counts: dict[str, int]) -> None:
        if not pending:
            return
        results = await self.al.search(titles)
        self.summary.searched += len(titles)
        media = self.repo.media({mid for ids in results for mid in ids})
        for p, ids in zip(pending, results):
            md = MdSeries.from_row(p.row)
            chapters = _chapter_count(p.row, read_counts)
            for mid in ids:
                if mid in media:
                    s = score(md, AlMedia.from_row(media[mid]), chapters)
                    if mid not in p.scored or s.confidence > p.scored[mid].confidence:
                        p.scored[mid] = s

    async def tier4(self, pending: list[_Pending]) -> None:
        read_counts = self.repo.read_counts()
        await self.search_and_score(pending, [p.row["title"] for p in pending], read_counts)
        second = [
            (p, t) for p in pending
            if max((s.confidence for s in p.scored.values()), default=0.0) < self.thresholds.review - 1e-9
            and (t := alt_search_title(p.row))
        ]
        await self.search_and_score([p for p, _ in second], [t for _, t in second], read_counts)

        for p in pending:
            state, top = classify(list(p.scored.values()), self.thresholds)
            candidates = [{"al_media_id": s.media_id, "score": s.confidence,
                           "reasons": [*s.reasons, *(f"disagrees: {d}" for d in s.disagreements)]} for s in top]
            if top:
                best = top[0]
                reasons = candidates[0]["reasons"]
                if state != "auto" and len(top) > 1 and best.confidence - top[1].confidence < self.thresholds.margin:
                    reasons = [*reasons, f"runner-up {top[1].confidence:.2f} is within {self.thresholds.margin:.2f}"]
                self.save(p, state, 4, best.media_id if state == "auto" else None, best.confidence, reasons, candidates)
            else:
                self.save(p, "unmatched", 4, None, None, ["no AniList search results"], [])


async def resolve_all(repo: Repo, al_fetch: AniListLookup) -> MatchSummary:
    """Match every library series that has no usable mapping. Results are written to the DB."""
    r = _Resolver(repo, al_fetch)
    mappings = repo.mappings()
    pending: list[_Pending] = []
    for row in repo.md_manga():
        if not needs_matching(row, mappings.get(row["md_id"])):
            r.summary.kept += 1
            continue
        links = _links(row)
        pending.append(_Pending(row, parse_link_id(links.get("al")), parse_link_id(links.get("mal"))))
    if pending:
        pending = await r.tier2(pending)
        pending = await r.tier3(pending)
        await r.tier4(pending)
    return r.summary


# ---- manual resolution (review screen, story 15) -------------------------

def confirm(repo: Repo, md_id: str, al_media_id: int) -> None:
    row = _md_row(repo, md_id)
    repo.save_match(
        {"md_id": md_id, "al_media_id": al_media_id, "state": "confirmed", "tier": 1, "confidence": None,
         "reasons": [f"confirmed by you: AniList {al_media_id}"], "links_hash": row["links_hash"]},
        [],
    )


def mark_not_on_anilist(repo: Repo, md_id: str) -> None:
    row = _md_row(repo, md_id)
    repo.save_match(
        {"md_id": md_id, "al_media_id": None, "state": "not_on_anilist", "tier": 1, "confidence": None,
         "reasons": ["marked by you: not on AniList"], "links_hash": row["links_hash"]},
        [],
    )


def retry_matching(repo: Repo, md_id: str) -> None:
    """Forget the mapping and candidates; the next sync matches the series again."""
    repo.delete_mapping(md_id)


def _md_row(repo: Repo, md_id: str) -> sqlite3.Row:
    row = repo.conn.execute("SELECT * FROM md_manga WHERE md_id=?", (md_id,)).fetchone()
    if row is None:
        raise KeyError(md_id)
    return row


ANILIST_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?anilist\.co/manga/(\d+)(?:[/?#].*)?$", re.IGNORECASE)


def parse_anilist_ref(text: str) -> int | None:
    """`123`, `https://anilist.co/manga/123/slug` or `anilist.co/manga/123` → 123. Anything else → None."""
    text = (text or "").strip()
    if text.isdigit():
        return int(text) if int(text) > 0 else None
    m = ANILIST_URL_RE.match(text)
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None

