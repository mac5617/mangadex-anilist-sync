"""MangaDex covers, fetched once by Shiori and served from this computer.

MangaDex puts a browser-verification page in front of uploads.mangadex.org, so a cover the browser asks for
directly comes back as that page instead of an image. Shiori asks for it instead, identifying itself as it does
for the API, paced and stopped by a cooldown like every MangaDex request, and keeps the file: each cover is
fetched once (its file name changes when the cover does), not on every page view.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from mdal.clients.mangadex import MangaDexError

if TYPE_CHECKING:
    from mdal.services import Services

log = logging.getLogger(__name__)

SIZES = (256, 512)
MD_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FILE = re.compile(r"^[0-9A-Za-z_-]+(\.[0-9A-Za-z_-]+)*\.(jpe?g|png|gif|webp)$")
RETRY_FAILED = 60 * 60.0     # a cover MangaDex didn't send is asked for again after an hour, not on every view


class CoverCache:
    def __init__(self, services: Services, root: Path | None = None, clock=time.monotonic) -> None:
        self.services = services
        self.root = root or services.settings.db_path.parent / "covers"
        self._clock = clock
        self._failed: dict[str, float] = {}
        self._pending: dict[str, asyncio.Task[Path | None]] = {}

    @staticmethod
    def valid(md_id: str, file: str, size: int) -> bool:
        """Only MangaDex-shaped ids and file names reach the disk or the network (no path tricks)."""
        return bool(MD_ID.match(md_id) and FILE.match(file) and size in SIZES)

    def path(self, md_id: str, file: str, size: int) -> Path:
        return self.root / md_id / f"{file}.{size}.jpg"

    async def get(self, md_id: str, file: str, size: int) -> Path | None:
        """The cover on disk, fetching it first if needed; None when MangaDex can't be asked or sends no image."""
        if not self.valid(md_id, file, size):
            return None
        path = self.path(md_id, file, size)
        if path.exists():
            return path
        key = f"{md_id}/{file}/{size}"
        if self._clock() < self._failed.get(key, 0.0):
            return None
        task = self._pending.get(key)
        if task is None:           # several pages (or tabs) asking at once share one request
            task = self._pending[key] = asyncio.create_task(self._fetch(key, md_id, file, size, path))
            task.add_done_callback(lambda _t: self._pending.pop(key, None))
        return await asyncio.shield(task)

    async def _fetch(self, key: str, md_id: str, file: str, size: int, path: Path) -> Path | None:
        try:
            data = await self.services.mangadex.cover(md_id, file, size)
        except MangaDexError as exc:     # cooling down, blocked or unreachable: the page shows no cover
            log.info("cover %s not fetched: %s", key, exc)
            data = None
        if not data:
            self._failed[key] = self._clock() + RETRY_FAILED
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        partial.write_bytes(data)
        partial.replace(path)
        return path
