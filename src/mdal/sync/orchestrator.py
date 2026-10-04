"""Sync state machine (architecture §9): fetching → resolving → diffing → diffed → writing → verifying → done.

A run syncs MangaDex to one target: AniList or MyAnimeList (`sync_run.target`).
"""

from __future__ import annotations

import asyncio
import json
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
from mdal.sync.estimate import estimate
from mdal.sync.rules import AlEntry, AlMediaInfo, MdInfo, evaluate
from mdal.sync.mal_writer import MalWriter
from mdal.sync.writer import Writer

if TYPE_CHECKING:
    from mdal.services import Services

log = logging.getLogger(__name__)

ACTIVE_STATES = ("fetching", "resolving", "diffing", "writing", "verifying")
INTERRUPTIBLE = ("fetching", "resolving", "diffing")  # cheap to redo: a restart marks them failed
HALTING = (MangaDexBlocked, MangaDexRateLimited, MangaDexUnreachable, MangaDexCoolingDown,
           AniListUnavailable, AniListRateLimited, MalRateLimited, MalUnavailable)
TARGETS = ("anilist", "mal")
SITE_NAMES = {"anilist": "AniList", "mal": "MyAnimeList"}
MD_REUSE_SECONDS = 600  # a MyAnimeList sync reuses a MangaDex read this recent instead of reading again

MAPPING_SKIPS = {
    None: "awaiting match review",
    "review": "awaiting match review",
    "unmatched": "no match",
    "not_on_anilist": "marked not on AniList",
}


DISMISSED_PREFIX = "dismissed by you: "
STAFF_REQUESTS_PER_SYNC = 20  # 25 series each; about 1 minute of AniList pacing


class SyncAlreadyRunning(Exception):
    pass


class SyncStateError(Exception):
    pass


class SyncCoolingDown(Exception):
    """MangaDex cooldown active: a new sync would only knock again."""


class ApprovalError(Exception):
    """Shown to the user as-is."""


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


def _evaluated(
    repo: Repo, row: Any, entry: AlEntry | None, media: AlMediaInfo, jump_limit: int,
    dismissed: dict[str, Any], site: str,
) -> dict[str, Any]:
    """The rules applied to one series (§8): the item columns shared by both targets."""
    md_id = row["md_id"]
    reads = repo.read_chapters(md_id)
    chapters = [r["chapter"] for r in reads if r["missing"] in (0, None) and r["chapter"] is not None]
    d = evaluate(
        chapters, len(reads) - len(chapters), entry, media,
        MdInfo(row["pub_status"], row["last_chapter"], bool(row["chapter_numbers_reset"])),
        jump_limit, site,
    )
    action, reason = d.action, d.reason
    gone = dismissed.get(md_id)
    if action == "flag" and gone is not None and gone["md_progress"] == d.md_progress:
        action, reason = "skip", f"{DISMISSED_PREFIX}{d.reason}"
    return {
        "al_entry_id": entry.entry_id if entry else None,
        "al_progress": d.al_progress,
        "md_progress": d.md_progress,
        "set_status": d.set_status,
        "status_source": d.status_source,
        "status_approved": 1 if d.set_status else 0,
        "action": action,
        "flag_kind": d.flag_kind,
        "reason": reason,
        "hint": d.hint,
        "unresolved_reads": d.unresolved,
    }


def md_mal_link(row: Any) -> int | None:
    """The MyAnimeList id from MangaDex's links, if it is a plain number."""
    try:
        value = json.loads(row["links"] or "{}").get("mal")
    except (ValueError, AttributeError):
        return None
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def mal_target(row: Any, mapping: Any, media: dict[int, Any]) -> tuple[int | None, int | None, str | None]:
    """(mal_id, al_media_id, skip reason) for one series.

    The AniList match's idMal is preferred (it was matched or confirmed); MangaDex's own MAL link is
    the fallback, so series that are unmatched on AniList can still sync. A series whose AniList match
    awaits review waits too (its links are often the reason it needs review), and if the two ids
    disagree the series is skipped rather than guessed.
    """
    state = mapping["state"] if mapping else None
    if state == "review":
        return None, None, "awaiting match review"
    matched = state in ("auto", "confirmed") and mapping["al_media_id"] is not None
    al_id = mapping["al_media_id"] if matched else None
    al_mal = media[al_id]["id_mal"] if al_id is not None and al_id in media else None
    md_mal = md_mal_link(row)
    if al_mal and md_mal and al_mal != md_mal:
        return None, al_id, f"AniList and MangaDex link different MyAnimeList entries ({al_mal} and {md_mal})"
    mal_id = al_mal or md_mal
    if mal_id is None:
        why = "the AniList match has none" if matched else "no AniList match, and no MyAnimeList link on MangaDex"
        return None, al_id, f"no MyAnimeList id ({why})"
    return mal_id, al_id, None


def build_items(repo: Repo, run_id: int, md_ids: set[str] | None = None, target: str = "anilist") -> list[dict[str, Any]]:
    """One sync_item per library series (§8), or only `md_ids`. Pure DB reads; no API calls."""
    mappings = repo.mappings()
    dismissed = repo.dismissed_flags()
    jump_limit = repo.get_setting("jump_limit")
    rows = [r for r in repo.md_manga() if md_ids is None or r["md_id"] in md_ids]
    mapped_ids = [m["al_media_id"] for m in mappings.values() if m["al_media_id"] is not None]
    media = repo.media(mapped_ids)
    if target == "mal":
        return _build_mal_items(repo, run_id, rows, mappings, media, dismissed, jump_limit)
    entries = repo.al_entries()
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
        entry = AlEntry(entry_row["entry_id"], entry_row["status"], entry_row["progress"]) if entry_row else None
        info = AlMediaInfo(media_row["chapters"] if media_row else None, media_row["status"] if media_row else None)
        items.append({**base, "al_media_id": al_id, **_evaluated(repo, row, entry, info, jump_limit, dismissed, "AniList")})
    return items


def _build_mal_items(
    repo: Repo, run_id: int, rows: list[Any], mappings: dict[str, Any], media: dict[int, Any],
    dismissed: dict[str, Any], jump_limit: int,
) -> list[dict[str, Any]]:
    entries = repo.mal_entries()
    items: list[dict[str, Any]] = []
    for row in rows:
        md_id = row["md_id"]
        mal_id, al_id, skip = mal_target(row, mappings.get(md_id), media)
        base: dict[str, Any] = {"run_id": run_id, "md_id": md_id, "al_media_id": al_id, "mal_id": mal_id}
        if skip:
            items.append({**base, "action": "skip", "reason": skip})
            continue
        e = entries.get(mal_id)
        entry = AlEntry(mal_id, e["status"], e["progress"]) if e else None
        al = media.get(al_id) if al_id is not None else None
        if e is not None:
            info = AlMediaInfo(e["chapters"], e["media_status"], "MyAnimeList")
        elif al is not None and al["id_mal"] == mal_id:
            # Not on the MAL list yet: MyAnimeList's total is unknown here, AniList's stands in for it.
            info = AlMediaInfo(al["chapters"], al["status"], "AniList")
        else:
            info = AlMediaInfo(None, None, "MyAnimeList")
        items.append({**base, **_evaluated(repo, row, entry, info, jump_limit, dismissed, "MyAnimeList")})
    return items


def open_diff(repo: Repo, target: str | None = None) -> Any:
    """The sync waiting for approval (at most one per target; it may be an older, restored run)."""
    if target is None:
        return repo.conn.execute("SELECT * FROM sync_run WHERE state='diffed' ORDER BY run_id DESC LIMIT 1").fetchone()
    return repo.conn.execute(
        "SELECT * FROM sync_run WHERE state='diffed' AND target=? ORDER BY run_id DESC LIMIT 1", (target,)
    ).fetchone()


def refresh_item(repo: Repo, md_id: str) -> bool:
    """After a match or dismiss decision: recompute this series' row in each open `diffed` run. No requests."""
    refreshed = False
    for target in TARGETS:
        run = open_diff(repo, target)
        if run is None:
            continue
        if not repo.conn.execute("SELECT 1 FROM sync_item WHERE run_id=? AND md_id=?", (run["run_id"], md_id)).fetchone():
            continue
        for item in build_items(repo, run["run_id"], {md_id}, target):
            with repo.conn:
                repo.conn.execute("DELETE FROM sync_item WHERE run_id=? AND md_id=?", (run["run_id"], md_id))
            repo.upsert_item(item)
        refreshed = True
    return refreshed


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
            run_id = self.repo.create_run("fetching", target)
            # An older diff for the same site is superseded: only the newest one can be approved.
            for old in self.repo.conn.execute(
                "SELECT run_id FROM sync_run WHERE state='diffed' AND target=? AND run_id<?", (target, run_id)
            ).fetchall():
                self.repo.update_run(old["run_id"], state="cancelled", finished_at=now_iso(),
                                     error=f"superseded by sync #{run_id}")
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

    def can_restore(self, run_id: int) -> bool:
        """A discarded or superseded sync that never reached approval."""
        r = self.repo.get_run(run_id)
        if r is None or r["state"] != "cancelled" or r["approved_at"] is not None:
            return False
        return self.repo.conn.execute("SELECT 1 FROM sync_item WHERE run_id=? LIMIT 1", (run_id,)).fetchone() is not None

    def restore(self, run_id: int) -> None:
        """Bring a discarded sync back to `diffed`. Any other open diff is closed (only one can be approved)."""
        if self.lock.locked() or (self.task and not self.task.done()):
            raise SyncAlreadyRunning("a sync is running; restore it when it finishes")
        if not self.can_restore(run_id):
            raise SyncStateError("only a discarded sync that was never approved can be restored")
        other = open_diff(self.repo, self.repo.get_run(run_id)["target"])
        if other is not None:
            self.repo.update_run(other["run_id"], state="cancelled", finished_at=now_iso(),
                                 error=f"superseded by restored sync #{run_id}")
        self.repo.update_run(run_id, state="diffed", error=None, finished_at=None)

    def discard(self, run_id: int) -> None:
        r = self.repo.get_run(run_id)
        if r is None or r["state"] != "diffed":
            raise SyncStateError("only a diffed run can be discarded")
        self.repo.update_run(run_id, state="cancelled", finished_at=now_iso(), error="discarded by you")

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

        n_write = sum(1 for i in items if i["action"] in ("write", "add"))
        req, sec = estimate(n_write, 1, repo.get_setting("mal_rpm"))
        linked = sum(1 for i in items if i["mal_id"] is not None)
        detail = (
            f"{series} series, {listed} MyAnimeList entries; {linked} linked to MyAnimeList"
            + (f" ({newly} newly matched on AniList)" if newly else "")
            + f"; {sum(i['action'] == 'write' for i in items)} to update, {sum(i['action'] == 'add' for i in items)} to add"
        )
        repo.update_run(run_id, state="diffed", phase_detail=detail, est_requests=req, est_seconds=sec)

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
        req, sec = estimate(n_write, repo.get_setting("anilist_write_batch"), repo.get_setting("anilist_rpm"))
        states = [m["state"] for m in repo.mappings().values()]
        newly = match.auto + match.review + match.unmatched
        detail = (
            f"{md.series} series, {al.entries} AniList entries; "
            f"{sum(s in ('auto', 'confirmed') for s in states)} matched ({newly} newly matched), "
            f"{states.count('review')} to review, {states.count('unmatched')} unmatched; "
            f"{n_write} to write"
        )
        repo.update_run(run_id, state="diffed", phase_detail=detail, est_requests=req, est_seconds=sec)
