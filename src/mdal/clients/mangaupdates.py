"""MangaUpdates (Baka-Updates), read-only and without an account: series search and series details.

MangaUpdates publishes no rate limit, so requests are paced well below anything a person browsing
would send (1 per second by default) and nothing is retried: a failure just leaves the page without it.
"""

from __future__ import annotations

from typing import Any

import httpx

from mdal.clients.ratelimit import PacedQueue
from mdal.config import USER_AGENT

API_URL = "https://api.mangaupdates.com/v1"


class MangaUpdatesError(Exception):
    pass


class MangaUpdatesClient:
    def __init__(self, queue: PacedQueue, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.queue = queue
        self._http = httpx.AsyncClient(transport=transport, timeout=20.0,
                                       headers={"User-Agent": USER_AGENT, "Accept": "application/json"})

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async with self.queue.slot():
            try:
                return await self._http.request(method, f"{API_URL}{path}", **kwargs)
            except httpx.TransportError as exc:
                raise MangaUpdatesError(f"MangaUpdates could not be reached ({exc.__class__.__name__})") from exc

    async def search(self, title: str) -> list[dict[str, Any]]:
        """[{record: {series_id, title, url, year, type, ...}, hit_title}], best match first."""
        response = await self._send("POST", "/series/search", json={"search": title, "perpage": 10})
        if response.status_code != 200:
            raise MangaUpdatesError(f"MangaUpdates search answered HTTP {response.status_code}")
        return (response.json() or {}).get("results") or []

    async def series(self, series_id: int) -> dict[str, Any] | None:
        """The full series record, or None when there is no such series."""
        response = await self._send("GET", f"/series/{int(series_id)}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise MangaUpdatesError(f"MangaUpdates answered HTTP {response.status_code}")
        return response.json()
