"""Match review queue (story 15, FR-29, FR-20).

Decisions here are tier 1: never re-matched. Only a pasted, uncached AniList id costs a request.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from mdal.db.repo import Repo
from mdal.matching.pipeline import AniListFetch, confirm, confirm_manual, mark_not_on_anilist, retry_matching
from mdal.sync.orchestrator import refresh_item
from mdal.web.app import get_services, render, templates

router = APIRouter()

MD_COVER = "https://uploads.mangadex.org/covers/{md_id}/{file}.256.jpg"


def _card(repo: Repo, row: Any) -> dict[str, Any]:
    md_id = row["md_id"]
    return {
        "md_id": md_id,
        "state": row["state"],
        "title": row["title"],
        "alt_titles": json.loads(row["alt_titles"] or "[]"),
        "year": row["year"],
        "language": row["original_language"],
        "authors": json.loads(row["authors"] or "[]"),
        "cover": MD_COVER.format(md_id=md_id, file=row["cover_file"]) if row["cover_file"] else None,
        "md_url": f"https://mangadex.org/title/{md_id}",
        "reasons": json.loads(row["reasons"] or "[]"),
        "candidates": [
            {
                "media_id": c["media_id"],
                "title": c["romaji"] or c["english"] or c["native"] or f"#{c['media_id']}",
                "english": c["english"] if c["english"] != c["romaji"] else None,
                "year": c["start_year"],
                "format": c["format"],
                "country": c["country"],
                "score": c["score"],
                "reasons": json.loads(c["candidate_reasons"] or "[]"),
                "cover": c["cover_url"],
                "url": c["site_url"] or f"https://anilist.co/manga/{c['media_id']}",
            }
            for c in repo.candidate_media(md_id)
        ],
    }


def _card_for(repo: Repo, md_id: str) -> dict[str, Any] | None:
    row = repo.conn.execute(
        "SELECT p.*, m.title, m.alt_titles, m.year, m.original_language, m.authors, m.cover_file "
        "FROM mapping p JOIN md_manga m ON m.md_id = p.md_id WHERE p.md_id=?",
        (md_id,),
    ).fetchone()
    return _card(repo, row) if row else None


def _done(request: Request, md_id: str, text: str, undo: bool = False) -> HTMLResponse:
    return templates.TemplateResponse(request, "_review_done.html", {"md_id": md_id, "text": text, "undo": undo})


def _error(request: Request, md_id: str, error: str) -> HTMLResponse:
    card = _card_for(get_services(request).repo, md_id)
    if card is None:
        return _done(request, md_id, error)
    return templates.TemplateResponse(request, "_review_card.html", {"card": card, "card_error": error}, status_code=400)


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request) -> HTMLResponse:
    repo = get_services(request).repo
    return render(request, "review.html", {
        "review": [_card(repo, r) for r in repo.mapped_series("review")],
        "unmatched": [_card(repo, r) for r in repo.mapped_series("unmatched")],
        "not_on_anilist": [_card(repo, r) for r in repo.mapped_series("not_on_anilist")],
    })


@router.post("/review/{md_id}/accept", response_class=HTMLResponse)
def accept(request: Request, md_id: str, al_media_id: int = Form(...)) -> HTMLResponse:
    repo = get_services(request).repo
    if al_media_id not in {c["al_media_id"] for c in repo.candidates(md_id)}:
        return _error(request, md_id, "That candidate is no longer offered for this series.")
    confirm(repo, md_id, al_media_id)
    refresh_item(repo, md_id)
    return _done(request, md_id, f"Confirmed: AniList {al_media_id}.")


@router.post("/review/{md_id}/manual", response_class=HTMLResponse)
async def manual(request: Request, md_id: str, text: str = Form("")) -> HTMLResponse:
    svc = get_services(request)
    if _card_for(svc.repo, md_id) is None:
        return _done(request, md_id, "This series is no longer in the queue.")
    error = await confirm_manual(svc.repo, AniListFetch(svc.anilist, svc.repo), md_id, text)
    if error:
        return _error(request, md_id, error)
    refresh_item(svc.repo, md_id)
    return _done(request, md_id, f"Confirmed: AniList {svc.repo.get_mapping(md_id)['al_media_id']}.")


@router.post("/review/{md_id}/not-on-anilist", response_class=HTMLResponse)
def not_on_anilist(request: Request, md_id: str) -> HTMLResponse:
    repo = get_services(request).repo
    mark_not_on_anilist(repo, md_id)
    refresh_item(repo, md_id)
    return _done(request, md_id, "Marked as not on AniList. It will not be matched again.", undo=True)


@router.post("/review/{md_id}/retry", response_class=HTMLResponse)
def retry(request: Request, md_id: str) -> HTMLResponse:
    repo = get_services(request).repo
    retry_matching(repo, md_id)
    refresh_item(repo, md_id)
    return _done(request, md_id, "Will be matched again on the next sync.")
