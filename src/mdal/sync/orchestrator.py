"""Sync state machine (architecture §9). This story: fetching → resolving → diffing → diffed.

There is no path to writing from here; approval and writing are story 16.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mdal.clients.anilist import AniListAuthError, AniListRateLimited, AniListUnavailable
from mdal.clients.mangadex import MangaDexAuthError, MangaDexBlocked, MangaDexRateLimited
from mdal.db.repo import Repo, now_iso
from mdal.fetch.anilist_list import fetch_list, viewer
from mdal.fetch.mangadex_library import fetch_library
from mdal.matching.pipeline import AniListFetch, resolve_all
from mdal.sync.estimate import estimate
from mdal.sync.rules import AlEntry, AlMediaInfo, MdInfo, evaluate

if TYPE_CHECKING:
    from mdal.services import Services

log = logging.getLogger(__name__)

ACTIVE_STATES = ("fetching", "resolving", "diffing", "writing", "verifying")
INTERRUPTIBLE = ("fetching", "resolving", "diffing")  # cheap to redo: a restart marks them failed
HALTING = (MangaDexBlocked, MangaDexRateLimited, AniListUnavailable, AniListRateLimited)

MAPPING_SKIPS = {
    None: "awaiting match review",
    "review": "awaiting match review",
    "unmatched": "no match",
    "not_on_anilist": "marked not on AniList",
}


class SyncAlreadyRunning(Exception):
    pass


class SyncStateError(Exception):
    pass


@dataclass(frozen=True)
class RunStatus:
    run_id: int
    state: str
    phase_detail: str | None
    error: str | None
    req_anilist: int
    req_mangadex: int


def halt_message(exc: Exception) -> str:
    if isinstance(exc, MangaDexBlocked):
        return f"Halted: {exc} (temporary IP ban)."
    if isinstance(exc, MangaDexRateLimited):
        return f"Halted: {exc}. Wait a few minutes before syncing again."
    if isinstance(exc, AniListUnavailable):
        return f"Halted: AniList refused the request or is disabled ({exc}). Try again later."
    if isinstance(exc, AniListRateLimited):
        return f"Halted: AniList kept rate-limiting ({exc}). Try again in a few minutes."
    return f"Halted: {exc}"


def failure_message(exc: Exception) -> str:
    if isinstance(exc, AniListAuthError):
        return f"AniList rejected the token: reconnect AniList in Settings. ({exc})"
    if isinstance(exc, MangaDexAuthError):
        return f"MangaDex login failed: check credentials in .env. ({exc})"
    return f"{exc.__class__.__name__}: {exc}"


def build_items(repo: Repo, run_id: int, md_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """One sync_item per library series (§8), or only `md_ids`. Pure DB reads; no API calls."""
    mappings = repo.mappings()
    entries = repo.al_entries()
    jump_limit = repo.get_setting("jump_limit")
    rows = [r for r in repo.md_manga() if md_ids is None or r["md_id"] in md_ids]
    mapped_ids = [m["al_media_id"] for m in mappings.values() if m["al_media_id"] is not None]
    media = repo.media(mapped_ids)
    items: list[dict[str, Any]] = []
    for row in rows:
        md_id = row["md_id"]
        m = mappings.get(md_id)
        base: dict[str, Any] = {"run_id": run_id, "md_id": md_id}
        state = m["state"] if m else None
        if state not in ("auto", "confirmed") or m["al_media_id"] is None:
            items.append({**base, "action": "skip", "reason": MAPPING_SKIPS.get(state, "no match")})
            continue
        al_id = m["al_media_id"]
        entry_row = entries.get(al_id)
        media_row = media.get(al_id)
        reads = repo.read_chapters(md_id)
        chapters = [r["chapter"] for r in reads if r["missing"] in (0, None) and r["chapter"] is not None]
        unresolved = len(reads) - len(chapters)
        d = evaluate(
            chapters, unresolved,
            AlEntry(entry_row["entry_id"], entry_row["status"], entry_row["progress"]) if entry_row else None,
            AlMediaInfo(media_row["chapters"] if media_row else None, media_row["status"] if media_row else None),
            MdInfo(row["pub_status"], row["last_chapter"], bool(row["chapter_numbers_reset"])),
            jump_limit,
        )
        action = "skip" if d.action == "not_on_list" else d.action
        items.append({
            **base,
            "al_media_id": al_id,
            "al_entry_id": entry_row["entry_id"] if entry_row else None,
            "al_progress": d.al_progress,
            "md_progress": d.md_progress,
            "set_status": d.set_status,
            "status_source": d.status_source,
            "status_approved": 1 if d.set_status else 0,
            "action": action,
            "flag_kind": d.flag_kind,
            "reason": d.reason,
            "hint": d.hint,
            "unresolved_reads": d.unresolved,
        })
    return items


def refresh_item(repo: Repo, md_id: str) -> bool:
    """After a match decision: recompute this series' row in the open `diffed` run, if any. No requests."""
    run = repo.latest_run()
    if run is None or run["state"] != "diffed":
        return False
    if not repo.conn.execute("SELECT 1 FROM sync_item WHERE run_id=? AND md_id=?", (run["run_id"], md_id)).fetchone():
        return False
    for item in build_items(repo, run["run_id"], {md_id}):
        with repo.conn:
            repo.conn.execute("DELETE FROM sync_item WHERE run_id=? AND md_id=?", (run["run_id"], md_id))
        repo.upsert_item(item)
    return True


class SyncOrchestrator:
    """One per process (held by Services). Only one run is active at a time."""

    def __init__(self, services: Services) -> None:
        self.services = services
        self.lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None

    @property
    def repo(self) -> Repo:
        return self.services.repo

    # ---- lifecycle ------------------------------------------------------
    def recover_interrupted(self) -> list[int]:
        """On startup: runs that died in a cheap phase are failed; start a new sync to recover."""
        ids = [r["run_id"] for r in self.repo.conn.execute(
            f"SELECT run_id FROM sync_run WHERE state IN ({','.join('?' * len(INTERRUPTIBLE))})", INTERRUPTIBLE
        )]
        for run_id in ids:
            self.repo.update_run(run_id, state="failed", error="interrupted; start a new sync", finished_at=now_iso())
        return ids

    def active_run(self) -> Any:
        return self.repo.conn.execute(
            f"SELECT * FROM sync_run WHERE state IN ({','.join('?' * len(ACTIVE_STATES))}) ORDER BY run_id DESC LIMIT 1",
            ACTIVE_STATES,
        ).fetchone()

    async def start_run(self) -> int:
        if self.lock.locked() or (self.task and not self.task.done()):
            raise SyncAlreadyRunning("a sync is already running")
        await self.lock.acquire()
        try:
            run_id = self.repo.create_run("fetching")
        except BaseException:
            self.lock.release()
            raise
        self.task = asyncio.create_task(self._guarded(run_id, self._dry_run))
        return run_id

    async def wait(self) -> None:
        if self.task:
            await asyncio.shield(self.task)

    def status(self, run_id: int) -> RunStatus | None:
        r = self.repo.get_run(run_id)
        if r is None:
            return None
        return RunStatus(r["run_id"], r["state"], r["phase_detail"], r["error"], r["req_anilist"], r["req_mangadex"])

    def discard(self, run_id: int) -> None:
        r = self.repo.get_run(run_id)
        if r is None or r["state"] != "diffed":
            raise SyncStateError("only a diffed run can be discarded")
        self.repo.update_run(run_id, state="cancelled", finished_at=now_iso())

    # ---- running --------------------------------------------------------
    @contextmanager
    def _counting(self, run_id: int):
        s = self.services
        s.anilist.request_counter = lambda: self.repo.add_request(run_id, "anilist")
        s.mangadex.request_counter = lambda: self.repo.add_request(run_id, "mangadex")
        try:
            yield
        finally:
            s.anilist.request_counter = None
            s.mangadex.request_counter = None

    async def _guarded(self, run_id: int, body: Callable[[int], Awaitable[None]]) -> None:
        try:
            with self._counting(run_id):
                await body(run_id)
        except HALTING as exc:
            log.warning("sync %s halted: %s", run_id, exc)
            self.repo.update_run(run_id, state="halted", error=halt_message(exc), finished_at=now_iso())
        except Exception as exc:
            log.exception("sync %s failed", run_id)
            self.repo.update_run(run_id, state="failed", error=failure_message(exc), finished_at=now_iso())
        finally:
            self.lock.release()

    def _phase(self, run_id: int, state: str | None = None, detail: str | None = None) -> None:
        fields: dict[str, Any] = {"phase_detail": detail}
        if state:
            fields["state"] = state
        self.repo.update_run(run_id, **fields)

    async def _user_id(self) -> int:
        user_id = self.repo.get_setting("anilist_user_id")
        if user_id is None:
            v = await viewer(self.services.anilist)
            self.repo.set_setting("anilist_user_id", v["id"])
            self.repo.set_setting("anilist_user_name", v.get("name"))
            user_id = v["id"]
        return int(user_id)

    async def _dry_run(self, run_id: int) -> None:
        s, repo = self.services, self.repo

        self._phase(run_id, "fetching", "reading MangaDex library")
        md = await fetch_library(s.mangadex, repo, lambda msg: self._phase(run_id, detail=msg))
        self._phase(run_id, detail="reading your AniList list")
        al = await fetch_list(s.anilist, repo, await self._user_id())

        self._phase(run_id, "resolving", f"matching {md.series} series")
        match = await resolve_all(repo, AniListFetch(s.anilist, repo))

        self._phase(run_id, "diffing", "comparing progress")
        items = build_items(repo, run_id)
        repo.replace_items(run_id, items)

        n_write = sum(1 for i in items if i["action"] == "write")
        req, sec = estimate(n_write, repo.get_setting("anilist_write_batch"), repo.get_setting("anilist_rpm"))
        detail = (
            f"{md.series} series, {al.entries} AniList entries; "
            f"matched {match.auto} auto, {match.review} to review, {match.unmatched} unmatched; "
            f"{n_write} to write"
        )
        repo.update_run(run_id, state="diffed", phase_detail=detail, est_requests=req, est_seconds=sec)
