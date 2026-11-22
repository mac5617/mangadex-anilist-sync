"""What differs per Site in a Sync: how its read Phase and write Phase run. The orchestrator only sees `SyncSite`."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from mdal.db.repo import Repo, now_iso
from mdal.fetch.anilist_list import fetch_list, fetch_staff
from mdal.fetch.mal_list import fetch_mal_list
from mdal.fetch.mangadex_library import fetch_library
from mdal.matching.matches import is_matched
from mdal.matching.pipeline import AniListFetch, resolve_all
from mdal.sync.changes import SITE_NAMES, build_items
from mdal.sync.mal_writer import MalWriter
from mdal.sync.writer import Writer

if TYPE_CHECKING:
    from mdal.services import Services

MD_REUSE_SECONDS = 600  # a MyAnimeList sync reuses a MangaDex read this recent instead of reading again
STAFF_REQUESTS_PER_SYNC = 20  # 25 series each; about 1 minute of AniList pacing


def md_snapshot_fresh(repo: Repo) -> bool:
    """Whether the last MangaDex read is recent enough for a MyAnimeList sync to reuse."""
    row = repo.conn.execute("SELECT MIN(fetched_at) FROM md_manga").fetchone()
    if not row or not row[0]:
        return False
    try:
        age = time.time() - datetime.fromisoformat(row[0]).timestamp()
    except ValueError:
        return False
    return 0 <= age < MD_REUSE_SECONDS


class SyncSite(Protocol):
    target: str

    def reads_mangadex(self) -> bool:
        """Whether this site's read Phase will call MangaDex, so a cooldown must stop it from starting."""
        ...

    async def read(self, run_id: int) -> None:
        """Read everything and leave the Run `diffed`, with its Changes stored."""
        ...

    async def write(self, run_id: int) -> None:
        """Write and verify the Run's approved Changes and leave it `done`."""
        ...


class _Site:
    target = ""

    def __init__(self, services: Services) -> None:
        self.services, self.repo = services, services.repo

    def _phase(self, run_id: int, state: str | None = None, detail: str | None = None) -> None:
        fields: dict[str, Any] = {"phase_detail": detail}
        if state:
            fields["state"] = state
        self.repo.update_run(run_id, **fields)

    def _writer(self, say: Callable[[str], None]) -> Any:
        raise NotImplementedError

    def _written(self) -> None:
        """After a write Phase that wrote something."""

    async def write(self, run_id: int) -> None:
        repo = self.repo
        site = SITE_NAMES[self.target]
        writer = self._writer(lambda msg: self._phase(run_id, detail=msg))
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
        if counts["done"]:
            self._written()


class AniListSync(_Site):
    target = "anilist"

    def __init__(self, services: Services, user_id: Callable[[], Awaitable[int]]) -> None:
        super().__init__(services)
        self.user_id = user_id

    def reads_mangadex(self) -> bool:
        return True

    def _writer(self, say: Callable[[str], None]) -> Writer:
        return Writer(self.repo, self.services.anilist, self.user_id, say)

    async def read(self, run_id: int) -> None:
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
        mappings = list(repo.mappings().values())
        states = [m["state"] for m in mappings]
        newly = match.auto + match.review + match.unmatched
        detail = (
            f"{md.series} series, {al.entries} AniList entries; "
            f"{sum(is_matched(m) for m in mappings)} matched ({newly} newly matched), "
            f"{states.count('review')} to review, {states.count('unmatched')} unmatched; "
            f"{n_write} to write"
        )
        repo.update_run(run_id, state="diffed", phase_detail=detail)


class MalSync(_Site):
    target = "mal"

    def reads_mangadex(self) -> bool:
        return not md_snapshot_fresh(self.repo)

    def _writer(self, say: Callable[[str], None]) -> MalWriter:
        return MalWriter(self.repo, self.services.mal, say)

    def _written(self) -> None:
        self.services.ranker.start_save()   # series just added to MyAnimeList get the scores you set in Shiori

    async def read(self, run_id: int) -> None:
        s, repo = self.services, self.repo
        if md_snapshot_fresh(repo):
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


def sync_sites(services: Services, user_id: Callable[[], Awaitable[int]]) -> dict[str, SyncSite]:
    return {"anilist": AniListSync(services, user_id), "mal": MalSync(services)}
