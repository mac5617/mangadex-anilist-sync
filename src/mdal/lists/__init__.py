"""Your list: list edits made in Shiori, on AniList (the main site) and mirrored on MyAnimeList.

Callers name series by series key (`al:<id>` / `md:<uuid>`) and scores out of 10; each site's adapter
(lists/anilist.py, lists/myanimelist.py) keeps its own scale and safety rules. This module owns the policy:
- nothing is written while Shiori is busy syncing;
- AniList comes first; MyAnimeList is tried only for series AniList took, and only while mirroring is on;
- a series' MyAnimeList entry is its MyAnimeList link, by the same rule Sync uses;
- open sync diffs are refreshed once the writes are done;
- the note you see is written here, once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from mdal.db.repo import Repo
from mdal.clients.anilist import AniListError
from mdal.lists.anilist import ADD_STATUSES, EDIT_STATUSES, NOTES_MAX
from mdal.sync.orchestrator import mal_target, refresh_item
from mdal.sync.rules import STATUS_LABELS

BUSY = "Shiori is syncing; try again when it finishes."
BAD_SCORE = "A score is a number from 0.1 to 10."
BAD_STATUS = "That status can't be set here."
MATCHED = ("auto", "confirmed")


@dataclass(frozen=True)
class Series:
    key: str
    media_id: int | None                 # AniList media
    md_ids: tuple[str, ...] = ()         # MangaDex series matched to it
    mal_id: int | None = None            # its MyAnimeList link
    mal_problem: str | None = None       # why there is no link


@dataclass
class SiteResult:
    """One site's part of a list edit."""
    site: str
    saved: list[Series] = field(default_factory=list)
    unchanged: list[Series] = field(default_factory=list)
    skipped: dict[Series, str] = field(default_factory=dict)    # not for this site, and why
    failed: dict[Series, str] = field(default_factory=dict)
    stored: dict[Series, dict[str, Any]] = field(default_factory=dict)  # what the site filled in itself

    def took(self) -> list[Series]:
        return self.saved + self.unchanged


@dataclass
class Edit:
    """What a list edit did, and the note to show for it."""
    note: str
    refused: bool = False
    sites: list[SiteResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refused and not any(s.failed for s in self.sites)

    def site(self, name: str) -> SiteResult | None:
        return next((s for s in self.sites if s.site == name), None)

    def problems(self) -> list[str]:
        """Each distinct failure reason, the mirror's prefixed with its site."""
        out: list[str] = []
        for i, s in enumerate(self.sites):
            prefix = "" if i == 0 else f"{s.site}: "
            out += [prefix + reason for reason in sorted(set(s.failed.values()))]
        return out


class Site(Protocol):
    name: str

    def entry(self, series: Series) -> Any: ...

    async def set_scores(self, scores: dict[Series, float]) -> SiteResult: ...

    async def set_status(self, series: Series, status: str) -> SiteResult: ...

    async def set_notes(self, series: Series, notes: str) -> SiteResult: ...

    async def add(self, series: Series, status: str, progress: int) -> SiteResult: ...


class YourList:
    def __init__(self, repo: Repo, main: Site, mirror: Site, busy: Callable[[], bool],
                 mirroring: Callable[[], bool], refresh: Callable[[Repo, str], Any] = refresh_item) -> None:
        self.repo, self.main, self.mirror = repo, main, mirror
        self.busy, self.mirroring, self.refresh = busy, mirroring, refresh

    # ---- naming series -------------------------------------------------------------------
    def series(self, keys: list[str]) -> dict[str, Series]:
        """The series behind each key: its AniList media, the MangaDex series matched to it, its MyAnimeList link."""
        mappings = self.repo.mappings()
        matched: dict[int, list[str]] = {}
        for md_id, m in mappings.items():
            if m["state"] in MATCHED and m["al_media_id"] is not None:
                matched.setdefault(m["al_media_id"], []).append(md_id)
        media_ids: dict[str, int | None] = {}
        for key in keys:
            kind, _, ident = key.partition(":")
            if kind == "al" and ident.isdigit():
                media_ids[key] = int(ident)
            elif kind == "md" and (m := mappings.get(ident)) and m["state"] in MATCHED:
                media_ids[key] = m["al_media_id"]
            else:
                media_ids[key] = None
        media = self.repo.media([m for m in media_ids.values() if m is not None])
        library = {r["md_id"]: r for r in self.repo.md_manga()}
        out = {}
        for key, media_id in media_ids.items():
            md_ids = tuple(matched.get(media_id, ())) if media_id is not None else ()
            mal_id, problem = _mal_link(media_id, media, [(library[d], mappings[d]) for d in md_ids if d in library])
            out[key] = Series(key, media_id, md_ids, mal_id, problem)
        return out

    # ---- edits ---------------------------------------------------------------------------
    async def set_score(self, key: str, score: float) -> Edit:
        """One series' score, out of 10, with a note about that series ("Saved 8.5/10 to AniList.")."""
        edit = await self.set_scores({key: score})
        if not edit.refused:
            edit.note = _score_note(edit, score)
        return edit

    async def set_scores(self, scores: dict[str, float]) -> Edit:
        """Scores out of 10, by series key, with a note counting them. A score a site already has isn't sent there."""
        if self.busy():
            return Edit(BUSY, refused=True)
        if not all(0 < s <= 10 for s in scores.values()):
            return Edit(BAD_SCORE, refused=True)
        series = self.series(list(scores))
        wanted = {series[k]: s for k, s in scores.items()}
        main = await self.main.set_scores(wanted)
        sites = [main]
        if self.mirroring():
            sites.append(await self.mirror.set_scores({s: wanted[s] for s in main.took()}))
        self._refresh(main.saved)
        edit = Edit("", sites=sites)
        edit.note = _scores_note(edit)
        return edit

    async def set_status(self, key: str, status: str) -> Edit:
        """One series' status, in AniList's words (EDIT_STATUSES)."""
        if self.busy():
            return Edit(BUSY, refused=True)
        if status not in EDIT_STATUSES:
            return Edit(BAD_STATUS, refused=True)
        series = self.series([key])[key]
        main = await self.main.set_status(series, status)
        sites = [main]
        if self.mirroring() and main.took():
            sites.append(await self.mirror.set_status(series, status))
        self._refresh(main.saved)
        edit = Edit("", sites=sites)
        edit.note = _status_note(edit, series, status)
        return edit

    def mirror_can_add(self, key: str) -> bool:
        """Whether an add would also go to MyAnimeList: mirroring is on, the series has a MyAnimeList link,
        and it isn't on your MyAnimeList list yet (as last read)."""
        series = self.series([key])[key]
        return bool(self.mirroring() and series.mal_id and self.mirror.entry(series) is None)

    async def add(self, key: str, status: str, progress: int) -> Edit:
        """Add a series to your list with this status (ADD_STATUSES) and chapters read: to AniList, and to
        MyAnimeList while mirroring. A series already on AniList goes on to MyAnimeList only."""
        if self.busy():
            return Edit(BUSY, refused=True)
        if status not in ADD_STATUSES:
            return Edit(f"Unsupported status {status!r}.", refused=True)
        series = self.series([key])[key]
        main = await self.main.add(series, status, progress)
        sites = [main]
        if self.mirroring() and main.took():
            sites.append(await self.mirror.add(series, status, progress))
        edit = Edit("", sites=sites)
        self._refresh([series] if any(s.saved for s in sites) else [])
        edit.note = _add_note(edit, series, status, progress)
        return edit

    async def read_notes(self, key: str) -> str | None:
        """Read your notes from AniList into the local copy. Returns why it couldn't, or None. Notes that
        can't be read are kept empty, so the page doesn't ask again on every visit."""
        series = self.series([key])[key]
        try:
            await self.main.read_notes(series)
        except (AniListError, LookupError) as exc:
            if series.media_id is not None:
                self.repo.update_al_entry(series.media_id, notes="")
            return f"Couldn't read your notes from AniList: {exc}"
        return None

    async def set_notes(self, key: str, notes: str, mirror: bool = True) -> Edit:
        """Replace your notes (trimmed, at most NOTES_MAX characters). `mirror=False` keeps this save off
        MyAnimeList even while mirroring is on."""
        if self.busy():
            return Edit(BUSY, refused=True)
        notes = notes.strip()[:NOTES_MAX]
        series = self.series([key])[key]
        main = await self.main.set_notes(series, notes)
        sites = [main]
        if mirror and self.mirroring() and main.took():
            sites.append(await self.mirror.set_notes(series, notes))
        edit = Edit("", sites=sites)
        mirrored = edit.site("MyAnimeList")
        if main.failed:
            edit.note = main.failed[series]
        elif mirrored and mirrored.failed:
            edit.note = f"Saved to AniList; MyAnimeList: {mirrored.failed[series]}."
        else:
            edit.note = "Saved to AniList and MyAnimeList." if mirrored and mirrored.saved else "Saved to AniList."
        return edit

    def _refresh(self, saved: list[Series]) -> None:
        for md_id in {d for s in saved for d in s.md_ids}:
            self.refresh(self.repo, md_id)


def _mal_link(media_id: int | None, media: dict[int, Any], library: list[tuple[Any, Any]]) -> tuple[int | None, str | None]:
    """Sync's rule (orchestrator.mal_target) for each MangaDex series matched to the media: AniList's link, else
    MangaDex's; links that disagree mean there is none."""
    row = media.get(media_id) if media_id is not None else None
    al_mal = row["id_mal"] if row is not None else None
    if not library:
        return (al_mal, None) if al_mal else (None, "no MyAnimeList id (the AniList match has none)")
    ids, why = set(), None
    for md_row, mapping in library:
        mal_id, _, reason = mal_target(md_row, mapping, media)
        if mal_id is None and al_mal:
            return None, reason                       # AniList and MangaDex disagree
        if mal_id is None:
            why = why or reason
        else:
            ids.add(mal_id)
    if len(ids) > 1:
        return None, f"MangaDex links different MyAnimeList entries ({', '.join(map(str, sorted(ids)))})"
    return (ids.pop(), None) if ids else (None, why)


def _score_note(edit: Edit, score: float) -> str:
    main = edit.sites[0]
    if main.failed:
        return f"AniList didn't save it: {next(iter(main.failed.values()))}"
    saved = f"Saved {score:g}/10 to AniList"
    mirror = edit.site("MyAnimeList")
    if mirror and mirror.failed:
        return f"{saved}; MyAnimeList: {next(iter(mirror.failed.values()))}."
    if mirror and mirror.saved:
        return f"{saved} and MyAnimeList."
    return f"{saved}."


def _scores_note(edit: Edit) -> str:
    parts = []
    for i, s in enumerate(edit.sites):
        tried = len(s.saved) + len(s.failed)
        if not tried:
            continue
        text = f"{len(s.saved)} scores saved to AniList" if i == 0 else f"{len(s.saved)} on {s.site}"
        parts.append(text + (f", {len(s.failed)} failed" if s.failed else ""))
    return "; ".join(parts) or "nothing to save"


def _status_note(edit: Edit, series: Series, status: str) -> str:
    label = STATUS_LABELS[status]
    main, mirror = edit.sites[0], edit.site("MyAnimeList")
    if main.failed:
        return f"AniList didn't save it: {main.failed[series]}"
    mal_saved = bool(mirror and mirror.saved)
    if not main.saved:
        return f"Marked {label} on MyAnimeList (AniList already had it)." if mal_saved else f"Already {label}."
    note = f"Marked {label} on AniList"
    if (progress := main.stored.get(series, {}).get("progress")) is not None:
        note += f" (it set progress to {progress})"
    if mirror and mirror.failed:
        return f"{note}; MyAnimeList: {mirror.failed[series]}."
    return f"{note} and MyAnimeList." if mal_saved else f"{note}."


def _add_note(edit: Edit, series: Series, status: str, progress: int) -> str:
    label = STATUS_LABELS[status]
    main, mirror = edit.sites[0], edit.site("MyAnimeList")
    if main.failed:
        return main.failed[series]
    mal_progress = (mirror.stored.get(series) or {}).get("progress", progress) if mirror else progress
    if main.saved:
        stored = main.stored[series]
        note = f"Added to AniList as {STATUS_LABELS.get(stored['status'], stored['status'])}, progress {stored['progress']}"
        if stored["progress"] != progress:
            note += f" (AniList filled in its total; you entered {progress})"
        if mirror and mirror.failed:
            return f"{note}. MyAnimeList: {mirror.failed[series]}."
        if mirror and mirror.saved:
            return f"{note}. Added to MyAnimeList too, progress {mal_progress}."
        return f"{note}."
    if mirror and mirror.saved:
        return f"Added to MyAnimeList as {label}, progress {mal_progress}."
    if mirror and mirror.failed:
        return f"MyAnimeList: {mirror.failed[series]}."
    return "Already on your AniList list."
