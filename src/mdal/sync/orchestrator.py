"""Sync state machine (architecture §9): fetching → resolving → diffing → diffed → writing → verifying → done.

A run syncs MangaDex to one target: AniList or MyAnimeList (`sync_run.target`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mdal.clients.anilist import AniListAuthError, AniListRateLimited, AniListUnavailable
from mdal.clients.mangadex import (
    MangaDexAuthError,
    MangaDexBlocked,
    MangaDexCoolingDown,
    MangaDexRateLimited,
    MangaDexUnreachable,
)
from mdal.clients.myanimelist import MalAuthError, MalRateLimited, MalUnavailable
from mdal.db.repo import Repo, now_iso
from mdal.fetch.anilist_list import viewer
from mdal.sync.changes import (
    TARGETS,
    ApprovalError,
    Selection,
    WritePlan,
    approved_plan,
)
from mdal.sync.runs import Runs, SyncStateError
from mdal.sync.sites import sync_sites

if TYPE_CHECKING:
    from mdal.services import Services

log = logging.getLogger(__name__)

HALTING = (MangaDexBlocked, MangaDexRateLimited, MangaDexUnreachable, MangaDexCoolingDown,
           AniListUnavailable, AniListRateLimited, MalRateLimited, MalUnavailable)


class SyncAlreadyRunning(Exception):
    pass


class SyncCoolingDown(Exception):
    """MangaDex cooldown active: a new sync would only knock again."""


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
        return f"Halted: {exc}. MangaDex requests are paused for an hour."
    if isinstance(exc, (MangaDexUnreachable, MangaDexCoolingDown)):
        return f"Halted: {exc}"
    if isinstance(exc, AniListUnavailable):
        return f"Halted: AniList refused the request or is disabled ({exc}). Try again later."
    if isinstance(exc, AniListRateLimited):
        return f"Halted: AniList kept rate-limiting ({exc}). Try again in a few minutes."
    if isinstance(exc, MalRateLimited):
        return f"Halted: {exc}. Wait a few minutes, or lower MyAnimeList requests per minute in Settings."
    if isinstance(exc, MalUnavailable):
        return f"Halted: {exc}. MyAnimeList may be down for maintenance; try again later."
    return f"Halted: {exc}"


def failure_message(exc: Exception) -> str:
    if isinstance(exc, AniListAuthError):
        return f"AniList rejected the token: reconnect AniList in Settings. ({exc})"
    if isinstance(exc, MalAuthError):
        return f"MyAnimeList sign-in failed: reconnect MyAnimeList in Settings. ({exc})"
    if isinstance(exc, MangaDexAuthError):
        return f"MangaDex login failed: check credentials in .env. ({exc})"
    return f"{exc.__class__.__name__}: {exc}"


class SyncOrchestrator:
    """One per process (held by Services). Only one run is active at a time."""

    def __init__(self, services: Services) -> None:
        self.services = services
        self.runs = Runs(services.repo)
        self.sites = sync_sites(services, self.runs, self.user_id)
        self.lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self.queued: list[str] = []      # sites still to sync after the current one (Sync covers both)

    @property
    def repo(self) -> Repo:
        return self.services.repo

    def busy(self) -> bool:
        """Whether a Sync is in progress for any site, including the gap before a queued site starts.
        A sync waiting for your approval (`diffed`) is not busy. No list edit is made while busy."""
        return self.lock.locked() or bool(self.task and not self.task.done()) or bool(self.queued)

    def _watch(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        task.add_done_callback(lambda _task: self._after())
        return task

    def _after(self) -> None:
        """Once nothing is syncing, scores held back meanwhile (List → Rank) go out."""
        if not self.busy():
            self.services.ranker.start_save()

    async def start_run(self, target: str = "anilist") -> int:
        if target not in TARGETS:
            raise SyncStateError(f"unknown sync target {target!r}")
        if self.lock.locked() or (self.task and not self.task.done()):
            raise SyncAlreadyRunning("a sync is already running")
        if target == "mal" and not self.services.mal.connected:
            raise SyncStateError("Connect MyAnimeList in Settings first.")
        if self.sites[target].reads_mangadex():
            try:
                self.services.mangadex.check_cooldown()
            except MangaDexCoolingDown as exc:
                raise SyncCoolingDown(str(exc)) from exc
        await self.lock.acquire()
        try:
            run_id = self.runs.create(target)
        except BaseException:
            self.lock.release()
            raise
        self.task = self._watch(asyncio.create_task(self._guarded(run_id, self._read)))
        return run_id

    def sync_targets(self) -> list[str]:
        """The sites Sync covers: those turned on in Settings (MyAnimeList only once connected; AniList as before,
        so a first sync explains a missing AniList connection instead of offering nothing)."""
        wanted = self.repo.get_setting("sync_targets")
        return [t for t in TARGETS if t in wanted and (t != "mal" or self.services.mal.connected)]

    async def start_sync(self, targets: list[str] | None = None) -> int:
        """Sync each site in turn: AniList, then MyAnimeList, which reuses the MangaDex read the first one did.
        Their changes are reviewed together and approved at once (approve_many)."""
        targets = [t for t in (targets if targets is not None else self.sync_targets()) if t in TARGETS]
        if not targets:
            raise SyncStateError("Turn on at least one connected site under Settings → Sync.")
        run_id = await self.start_run(targets[0])
        self.queued = targets[1:]
        return run_id

    async def _start_queued(self, previous: asyncio.Task[Any]) -> None:
        """Start the next site once the run before it has fully finished."""
        await asyncio.wait([previous])
        if not self.queued:
            return
        target = self.queued.pop(0)
        try:
            await self.start_run(target)
        except (SyncAlreadyRunning, SyncCoolingDown, SyncStateError) as exc:
            log.warning("queued %s sync not started: %s", target, exc)
            self.queued = []
            self._after()

    async def wait(self) -> None:
        if self.task:
            await asyncio.shield(self.task)

    def status(self, run_id: int) -> RunStatus | None:
        r = self.repo.get_run(run_id)
        if r is None:
            return None
        return RunStatus(r["run_id"], r["state"], r["phase_detail"], r["error"], r["req_anilist"], r["req_mangadex"])

    async def approve(
        self, run_id: int, selected: Iterable[str], completing: Iterable[str], overrides: Iterable[str],
        scoring: Iterable[str] = (),
    ) -> int:
        """Validate, mark items pending and start writing. Returns the number of approved items."""
        return await self.approve_many({run_id: Selection.of(selected, completing, overrides, scoring)})

    async def approve_many(self, choices: dict[int, Selection]) -> int:
        """Approve the changes of several sites at once ({run_id: Selection}), then write
        them one site after another (AniList first), each re-checked and verified as on its own. A site with
        nothing ticked is closed without writing. Returns the number of approved items."""
        if self.lock.locked() or (self.task and not self.task.done()):
            raise SyncAlreadyRunning("a sync is already running")
        several = len(choices) > 1
        plan: dict[int, WritePlan] = {run_id: approved_plan(self.repo, run_id, chosen, allow_empty=several)
                                      for run_id, chosen in choices.items()}
        if not any(p.writes for p in plan.values()):
            raise ApprovalError("Nothing selected to write.")
        order = sorted(plan, key=lambda r: (TARGETS.index(self.repo.get_run(r)["target"]), r))
        await self.lock.acquire()
        try:
            for run_id in order:
                if not plan[run_id].writes:
                    self.repo.update_run(run_id, state="done", approved_at=now_iso(), finished_at=now_iso(),
                                         phase_detail="nothing selected to write")
                    continue
                self.repo.update_items(run_id, [
                    (w.md_id, {"approved": 1, "write_state": "pending", "status_approved": int(w.completes),
                               "score_approved": int(w.scored)})
                    for w in plan[run_id].writes
                ])
                self.repo.update_run(run_id, state="writing", approved_at=now_iso(),
                                     phase_detail="starting" if run_id == order[0] else "waiting for the site before")
        except BaseException:
            self.lock.release()
            raise
        to_write = [r for r in order if plan[r].writes]
        self.task = self._watch(asyncio.create_task(self._write_all(to_write)))
        return sum(len(p.writes) for p in plan.values())

    async def _write_all(self, run_ids: list[int]) -> None:
        """Write approved sites in turn under one lock; one site failing doesn't stop the next."""
        try:
            for run_id in run_ids:
                await self._run_body(run_id, self._write)
        finally:
            self.lock.release()

    async def resume(self, run_id: int) -> None:
        if self.lock.locked() or (self.task and not self.task.done()):
            raise SyncAlreadyRunning("a sync is already running")
        if not self.runs.can_resume(run_id):
            raise SyncStateError("this sync cannot be resumed")
        await self.lock.acquire()
        self.runs.mark_resuming(run_id)
        self.task = self._watch(asyncio.create_task(self._guarded(run_id, self._write)))

    def resumable_run(self) -> int | None:
        """The Run a Resume would continue. None while something is running."""
        return None if self.lock.locked() or (self.task and not self.task.done()) else self.runs.resumable()

    def restore(self, run_id: int) -> None:
        if self.lock.locked() or (self.task and not self.task.done()):
            raise SyncAlreadyRunning("a sync is running; restore it when it finishes")
        self.runs.restore(run_id)

    # ---- running --------------------------------------------------------
    @contextmanager
    def _counting(self, run_id: int):
        s = self.services
        s.anilist.request_counter = lambda: self.repo.add_request(run_id, "anilist")
        s.mangadex.request_counter = lambda: self.repo.add_request(run_id, "mangadex")
        s.mal.request_counter = lambda: self.repo.add_request(run_id, "mal")
        try:
            yield
        finally:
            s.anilist.request_counter = None
            s.mangadex.request_counter = None
            s.mal.request_counter = None

    async def _run_body(self, run_id: int, body: Callable[[int], Awaitable[None]]) -> None:
        """One phase of one run, with its requests counted; errors end that run (halted or failed)."""
        try:
            with self._counting(run_id):
                await body(run_id)
        except HALTING as exc:
            log.warning("sync %s halted: %s", run_id, exc)
            self.repo.update_run(run_id, state="halted", error=halt_message(exc), finished_at=now_iso())
        except Exception as exc:
            log.exception("sync %s failed", run_id)
            self.repo.update_run(run_id, state="failed", error=failure_message(exc), finished_at=now_iso())

    async def _guarded(self, run_id: int, body: Callable[[int], Awaitable[None]]) -> None:
        try:
            await self._run_body(run_id, body)
        finally:
            self.lock.release()
            # Sync covering both sites: once this site's changes are ready, read the next one's.
            if self.queued:
                if body == self._read and self.repo.get_run(run_id)["state"] == "diffed":
                    asyncio.create_task(self._start_queued(asyncio.current_task()))
                else:
                    self.queued = []

    async def user_id(self) -> int:
        user_id = self.repo.get_setting("anilist_user_id")
        if user_id is None:
            v = await viewer(self.services.anilist)
            self.repo.set_setting("anilist_user_id", v["id"])
            self.repo.set_setting("anilist_user_name", v.get("name"))
            user_id = v["id"]
        return int(user_id)

    async def _read(self, run_id: int) -> None:
        await self.sites[self.repo.get_run(run_id)["target"]].read(run_id)

    async def _write(self, run_id: int) -> None:
        await self.sites[self.repo.get_run(run_id)["target"]].write(run_id)
