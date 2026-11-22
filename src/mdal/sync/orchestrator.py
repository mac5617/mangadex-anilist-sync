"""Sync state machine (architecture §9): fetching → resolving → diffing → diffed → writing → verifying → done.

A run syncs MangaDex to one target: AniList or MyAnimeList (`sync_run.target`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
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
from mdal.fetch.anilist_list import fetch_list, fetch_staff, viewer
from mdal.fetch.mal_list import fetch_mal_list
from mdal.fetch.mangadex_library import fetch_library
from mdal.matching.pipeline import AniListFetch, resolve_all
from mdal.sync.changes import (
    SITE_NAMES,
    TARGETS,
    ApprovalError,
    Selection,
    WritePlan,
    approved_plan,
    build_items,
)
from mdal.sync.mal_writer import MalWriter
from mdal.sync.runs import Runs, SyncStateError
from mdal.sync.writer import Writer

if TYPE_CHECKING:
    from mdal.services import Services

log = logging.getLogger(__name__)

HALTING = (MangaDexBlocked, MangaDexRateLimited, MangaDexUnreachable, MangaDexCoolingDown,
           AniListUnavailable, AniListRateLimited, MalRateLimited, MalUnavailable)
MD_REUSE_SECONDS = 600  # a MyAnimeList sync reuses a MangaDex read this recent instead of reading again

STAFF_REQUESTS_PER_SYNC = 20  # 25 series each; about 1 minute of AniList pacing


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
        if not (target == "mal" and self.md_snapshot_fresh()):
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
        self.task = self._watch(asyncio.create_task(self._guarded(run_id, self._dry_run)))
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
                await self._run_body(run_id, self._write_phase)
        finally:
            self.lock.release()

    async def resume(self, run_id: int) -> None:
        if self.lock.locked() or (self.task and not self.task.done()):
            raise SyncAlreadyRunning("a sync is already running")
        if not self.runs.can_resume(run_id):
            raise SyncStateError("this sync cannot be resumed")
        await self.lock.acquire()
        self.runs.mark_resuming(run_id)
        self.task = self._watch(asyncio.create_task(self._guarded(run_id, self._write_phase)))

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
                if body == self._dry_run and self.repo.get_run(run_id)["state"] == "diffed":
                    asyncio.create_task(self._start_queued(asyncio.current_task()))
                else:
                    self.queued = []

    def _phase(self, run_id: int, state: str | None = None, detail: str | None = None) -> None:
        fields: dict[str, Any] = {"phase_detail": detail}
        if state:
            fields["state"] = state
        self.repo.update_run(run_id, **fields)

    async def user_id(self) -> int:
        user_id = self.repo.get_setting("anilist_user_id")
        if user_id is None:
            v = await viewer(self.services.anilist)
            self.repo.set_setting("anilist_user_id", v["id"])
            self.repo.set_setting("anilist_user_name", v.get("name"))
            user_id = v["id"]
        return int(user_id)

    async def _write_phase(self, run_id: int) -> None:
        repo = self.repo
        target = repo.get_run(run_id)["target"]

        def say(msg: str) -> None:
            self._phase(run_id, detail=msg)

        writer = (MalWriter(repo, self.services.mal, say) if target == "mal"
                  else Writer(repo, self.services.anilist, self.user_id, say))
        site = SITE_NAMES[target]
        self._phase(run_id, "writing", f"re-reading {site}")
        await writer.write(run_id)
        self._phase(run_id, "verifying", f"verifying on {site}")
        notes = await writer.verify(run_id)
        counts = {s: len(repo.items_in_state(run_id, s)) for s in ("done", "failed", "dropped", "pending")}
        if counts["done"] and not repo.get_setting("first_write_done"):
            repo.set_setting("first_write_done", True)
        detail = f"{counts['done']} written, {counts['failed']} failed, {counts['dropped']} dropped"
        if notes:
            detail += f"; {len(notes)} need a look: " + " | ".join(notes[:5])
        repo.update_run(run_id, state="done", phase_detail=detail, finished_at=now_iso())
        if target == "mal" and counts["done"]:
            self.services.ranker.start_save()   # series just added to MyAnimeList get the scores you set in Shiori

    def md_snapshot_fresh(self) -> bool:
        """Whether the last MangaDex read is recent enough for a MyAnimeList sync to reuse."""
        row = self.repo.conn.execute("SELECT MIN(fetched_at) FROM md_manga").fetchone()
        if not row or not row[0]:
            return False
        try:
            age = time.time() - datetime.fromisoformat(row[0]).timestamp()
        except ValueError:
            return False
        return 0 <= age < MD_REUSE_SECONDS

    async def _dry_run(self, run_id: int) -> None:
        if self.repo.get_run(run_id)["target"] == "mal":
            await self._mal_dry_run(run_id)
        else:
            await self._anilist_dry_run(run_id)

    async def _mal_dry_run(self, run_id: int) -> None:
        s, repo = self.services, self.repo
        if self.md_snapshot_fresh():
            series = len(repo.md_manga())
            self._phase(run_id, "fetching", "using the MangaDex library read a few minutes ago")
        else:
            self._phase(run_id, "fetching", "reading MangaDex library")
            series = (await fetch_library(s.mangadex, repo, lambda msg: self._phase(run_id, detail=msg))).series
        self._phase(run_id, detail="reading your MyAnimeList list")
        listed = await fetch_mal_list(s.mal, repo)

        # New series are matched on AniList when it is connected (its idMal is the best MAL link);
        # without AniList, MangaDex's own MAL links are used on their own.
        newly = 0
        if s.anilist_token():
            self._phase(run_id, "resolving", f"matching {series} series")
            match = await resolve_all(repo, AniListFetch(s.anilist, repo))
            newly = match.auto + match.review + match.unmatched

        self._phase(run_id, "diffing", "comparing progress")
        items = build_items(repo, run_id, target="mal")
        repo.replace_items(run_id, items)

        linked = sum(1 for i in items if i["mal_id"] is not None)
        detail = (
            f"{series} series, {listed} MyAnimeList entries; {linked} linked to MyAnimeList"
            + (f" ({newly} newly matched on AniList)" if newly else "")
            + f"; {sum(i['action'] == 'write' for i in items)} to update, {sum(i['action'] == 'add' for i in items)} to add"
        )
        repo.update_run(run_id, state="diffed", phase_detail=detail)

    async def _anilist_dry_run(self, run_id: int) -> None:
        s, repo = self.services, self.repo

        self._phase(run_id, "fetching", "reading MangaDex library")
        md = await fetch_library(s.mangadex, repo, lambda msg: self._phase(run_id, detail=msg))
        self._phase(run_id, detail="reading your AniList list")
        al = await fetch_list(s.anilist, repo, await self.user_id())

        # Staff for the stats page: a few lookups per sync until every list entry has it (cached for good).
        await fetch_staff(s.anilist, repo, STAFF_REQUESTS_PER_SYNC, lambda msg: self._phase(run_id, detail=msg))

        self._phase(run_id, "resolving", f"matching {md.series} series")
        match = await resolve_all(repo, AniListFetch(s.anilist, repo))

        self._phase(run_id, "diffing", "comparing progress")
        items = build_items(repo, run_id)
        repo.replace_items(run_id, items)

        n_write = sum(1 for i in items if i["action"] == "write")
        states = [m["state"] for m in repo.mappings().values()]
        newly = match.auto + match.review + match.unmatched
        detail = (
            f"{md.series} series, {al.entries} AniList entries; "
            f"{sum(s in ('auto', 'confirmed') for s in states)} matched ({newly} newly matched), "
            f"{states.count('review')} to review, {states.count('unmatched')} unmatched; "
            f"{n_write} to write"
        )
        repo.update_run(run_id, state="diffed", phase_detail=detail)
