"""Sync state machine (architecture §9). This story: fetching → resolving → diffing → diffed.

There is no path to writing from here; approval and writing are story 16.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
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
from mdal.sync.writer import Writer

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


class ApprovalError(Exception):
    """Shown to the user as-is."""


FIRST_WRITE_MESSAGE = "First live write is limited to one entry. Pick one."
RESUMABLE = ("writing", "verifying", "failed", "halted")


def validate_approval(
    repo: Repo, run_id: int, selected: Iterable[str], completing: Iterable[str], overrides: Iterable[str]
) -> list[tuple[str, int]]:
    """Returns [(md_id, status_approved)] to approve, or raises ApprovalError."""
    run = repo.get_run(run_id)
    if run is None or run["state"] != "diffed":
        raise ApprovalError("Only a sync that is waiting for approval can be approved.")
    items = {i["md_id"]: i for i in repo.items(run_id)}
    chosen, marks, ok = set(selected), set(completing), set(overrides)
    approved: list[tuple[str, int]] = []
    for md_id in sorted(chosen):
        item = items.get(md_id)
        if item is None or item["action"] == "skip":
            raise ApprovalError("The selection contains a row that cannot be written.")
        if item["flag_kind"] == "exceeds_total":
            raise ApprovalError("Rows that exceed AniList's total chapter count cannot be written.")
        if item["flag_kind"] == "implausible" and md_id not in ok:
            raise ApprovalError("Tick “override” on flagged rows you want to write anyway.")
        completes = int(item["set_status"] == "COMPLETED" and md_id in marks)
        status_only = item["md_progress"] is not None and item["al_progress"] is not None and item["md_progress"] <= item["al_progress"]
        if status_only and not completes:
            continue  # nothing to send
        approved.append((md_id, completes))
    if not approved:
        raise ApprovalError("Nothing selected to write.")
    if not repo.get_setting("first_write_done") and len(approved) != 1:
        raise ApprovalError(FIRST_WRITE_MESSAGE)
    return approved


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

    async def approve(
        self, run_id: int, selected: Iterable[str], completing: Iterable[str], overrides: Iterable[str]
    ) -> int:
        """Validate, mark items pending and start writing. Returns the number of approved items."""
        if self.lock.locked() or (self.task and not self.task.done()):
            raise SyncAlreadyRunning("a sync is already running")
        approved = validate_approval(self.repo, run_id, selected, completing, overrides)
        await self.lock.acquire()
        try:
            self.repo.update_items(run_id, [
                (md_id, {"approved": 1, "write_state": "pending", "status_approved": completes})
                for md_id, completes in approved
            ])
            self.repo.update_run(run_id, state="writing", approved_at=now_iso(), phase_detail="starting")
        except BaseException:
            self.lock.release()
            raise
        self.task = asyncio.create_task(self._guarded(run_id, self._write_phase))
        return len(approved)

    def can_resume(self, run_id: int) -> bool:
        r = self.repo.get_run(run_id)
        if r is None or r["state"] not in RESUMABLE or r["approved_at"] is None:
            return False
        if r["state"] in ("writing", "verifying") and self.task and not self.task.done():
            return False
        return bool(self.repo.items_in_state(run_id, "pending")) or bool(self.repo.items_in_state(run_id, "done"))

    async def resume(self, run_id: int) -> None:
        if self.lock.locked() or (self.task and not self.task.done()):
            raise SyncAlreadyRunning("a sync is already running")
        if not self.can_resume(run_id):
            raise SyncStateError("this sync cannot be resumed")
        await self.lock.acquire()
        self.repo.update_run(run_id, state="writing", error=None, finished_at=None, phase_detail="resuming")
        self.task = asyncio.create_task(self._guarded(run_id, self._write_phase))

    def resumable_run(self) -> Any:
        r = self.repo.conn.execute(
            f"SELECT run_id FROM sync_run WHERE state IN ({','.join('?' * len(RESUMABLE))}) "
            "AND approved_at IS NOT NULL ORDER BY run_id DESC LIMIT 1", RESUMABLE,
        ).fetchone()
        return r["run_id"] if r and self.can_resume(r["run_id"]) else None

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

    async def _write_phase(self, run_id: int) -> None:
        repo = self.repo
        writer = Writer(repo, self.services.anilist, self._user_id, lambda msg: self._phase(run_id, detail=msg))
        self._phase(run_id, "writing", "re-reading AniList")
        await writer.write(run_id)
        self._phase(run_id, "verifying", "verifying on AniList")
        notes = await writer.verify(run_id)
        counts = {s: len(repo.items_in_state(run_id, s)) for s in ("done", "failed", "dropped", "pending")}
        if counts["done"] and not repo.get_setting("first_write_done"):
            repo.set_setting("first_write_done", True)
        detail = f"{counts['done']} written, {counts['failed']} failed, {counts['dropped']} dropped"
        if notes:
            detail += f"; {len(notes)} need a look: " + " | ".join(notes[:5])
        repo.update_run(run_id, state="done", phase_detail=detail, finished_at=now_iso())

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
