"""Writing and verifying approved items on MyAnimeList. The only module that calls `update_list_status`.

Same safety rules as the AniList writer (sync/writer.py), sharing its re-check and verify logic:
- an update sends the chapters read and, for approved completions, the literal status `completed`;
  a status is never changed to anything else;
- an add sends `reading` or `completed` with the chapters read, and is dropped if the re-read shows
  the series is already on the list (MyAnimeList would update that entry instead);
- the list is re-read right before writing, and anything that would lower progress is dropped;
- MyAnimeList has no batch write, so each item is one request, committed before the next is sent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mdal.clients.myanimelist import MalClient, MalRequestError
from mdal.db.repo import Repo, now_iso
from mdal.fetch.mal_list import fetch_mal_list
from mdal.sync.writer import is_status_only, recheck, sends_completion, verify_note, with_note

SITE = "MyAnimeList"


def is_mal_add(item: Any) -> bool:
    return item["al_entry_id"] is None


def mal_request(item: Any) -> tuple[int | None, str | None]:
    """(chapters, status) to send. Status is one of two literals, or None to leave it alone."""
    adding = is_mal_add(item)
    chapters = None if (not adding and is_status_only(item)) else item["md_progress"]
    if sends_completion(item):
        status = "completed"
    elif adding:
        status = "reading"
    else:
        status = None
    return chapters, status


class MalWriter:
    def __init__(self, repo: Repo, client: MalClient, progress: Callable[[str], None] = lambda _msg: None) -> None:
        self.repo, self.client, self.progress = repo, client, progress

    async def prewrite(self, run_id: int) -> None:
        pending = self.repo.items_in_state(run_id, "pending")
        if not pending:
            return
        self.progress("re-reading your MyAnimeList list before writing")
        await fetch_mal_list(self.client, self.repo)
        entries = self.repo.mal_entries()
        self.repo.update_items(run_id, [
            (item["md_id"], recheck(item, is_mal_add(item), entries.get(item["mal_id"]), SITE)) for item in pending
        ])

    async def write(self, run_id: int) -> None:
        await self.prewrite(run_id)
        pending = self.repo.items_in_state(run_id, "pending")
        for n, item in enumerate(pending, start=1):
            chapters, status = mal_request(item)
            if chapters is None and status is None:
                self.repo.update_items(run_id, [(item["md_id"], {"write_state": "dropped",
                                                                 "verify_note": "dropped: nothing left to send"})])
                continue
            self.progress(f"writing {n}/{len(pending)}, about {self.client.queue.min_interval:.0f} s each")
            try:
                await self.client.update_list_status(item["mal_id"], chapters=chapters, status=status)
            except MalRequestError as exc:
                self.repo.update_items(run_id, [(item["md_id"], {"write_state": "failed", "verify_note": f"write failed: {exc}"})])
                continue
            fields: dict[str, Any] = {"write_state": "done", "written_at": now_iso()}
            if is_mal_add(item):
                fields["al_entry_id"] = item["mal_id"]  # now on the list
            self.repo.update_items(run_id, [(item["md_id"], fields)])

    async def verify(self, run_id: int) -> list[str]:
        done = self.repo.items_in_state(run_id, "done")
        if not done:
            return []
        self.progress("verifying on MyAnimeList")
        await fetch_mal_list(self.client, self.repo)
        entries = self.repo.mal_entries()
        notes: list[str] = []
        updates: list[tuple[str, dict[str, Any]]] = []
        for item in done:
            note, problem = verify_note(item, entries.get(item["mal_id"]), SITE)
            if problem:
                notes.append(f"{item['md_id']}: {note}")
            updates.append((item["md_id"], {"verify_note": with_note(item["verify_note"], note)}))
        self.repo.update_items(run_id, updates)
        return notes
