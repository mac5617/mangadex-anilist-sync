"""The only way the app talks to graphql.anilist.co (architecture §5, NFR-1..4).

A 429 may arrive as a Cloudflare HTML page, so responses are classified by
status code before any attempt to parse JSON.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from mdal.clients.ratelimit import PacedQueue
from mdal.config import USER_AGENT

log = logging.getLogger(__name__)

ANILIST_URL = "https://graphql.anilist.co"


class AniListError(Exception):
    pass


class AniListRateLimited(AniListError):
    """Still 429 after the retry cap."""


class AniListUnavailable(AniListError):
    """403, or the API reports itself disabled. Halt, do not retry."""


class AniListAuthError(AniListError):
    """Token missing, invalid or expired: reconnect AniList."""


class AniListGraphQLError(AniListError):
    def __init__(self, errors: list[dict[str, Any]], data: dict[str, Any] | None = None) -> None:
        self.errors = errors
        self.data = data  # partial data, if any (aliased mutations, story 16)
        super().__init__("; ".join(str(e.get("message", e)) for e in errors) or "GraphQL error")


class AniListComplexityError(AniListGraphQLError):
    """Document rejected for complexity. Nothing was executed."""


def _messages(errors: list[dict[str, Any]]) -> str:
    return " ".join(str(e.get("message", "")) for e in errors).lower()


class AniListClient:
    MAX_RETRIES = 3
    RATE_LIMIT_MARGIN = 5.0
    FALLBACK_WAIT = 60.0
    BACKOFF = (5.0, 15.0, 45.0)

    def __init__(
        self,
        token_provider: Callable[[], str],
        queue: PacedQueue,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        request_counter: Callable[[], None] | None = None,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.queue = queue
        self.request_counter = request_counter
        self._token = token_provider
        self._wall = wall_clock
        self._sleep = sleep
        self.last_rate_headers: dict[str, str] = {}  # diagnostics only; never used for pacing
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=30.0,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _rate_limit_wait(self, response: httpx.Response) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after) + self.RATE_LIMIT_MARGIN
            except ValueError:
                pass
        reset = response.headers.get("X-RateLimit-Reset")
        if reset:
            try:
                delta = float(reset) - self._wall()
                if delta > 0:
                    return delta + self.RATE_LIMIT_MARGIN
            except ValueError:
                pass
        return self.FALLBACK_WAIT + self.RATE_LIMIT_MARGIN

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {}
        token = self._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with self.queue.slot():
            if self.request_counter:
                self.request_counter()
            response = await self._http.post(ANILIST_URL, json=payload, headers=headers)
            self.last_rate_headers = {
                k: v for k, v in response.headers.items() if k.lower().startswith("x-ratelimit") or k.lower() == "retry-after"
            }
            if response.status_code == 429:
                wait = self._rate_limit_wait(response)
                log.warning("AniList 429: pausing all AniList requests for %.0f s", wait)
                self.queue.pause_for(wait)
            return response

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one GraphQL document; return `data`. Raises an AniListError subclass on failure."""
        payload = {"query": query, "variables": variables or {}}
        retries = 0
        while True:
            try:
                response = await self._post(payload)
            except httpx.TransportError as exc:
                if retries >= self.MAX_RETRIES:
                    raise AniListError(f"network error: {exc.__class__.__name__}") from exc
                await self._sleep(self.BACKOFF[retries])
                retries += 1
                continue

            status = response.status_code
            log.debug("AniList %s, X-RateLimit-Remaining=%s", status, response.headers.get("X-RateLimit-Remaining"))

            if status == 429:
                if retries >= self.MAX_RETRIES:
                    raise AniListRateLimited("AniList kept answering 429 after 3 retries")
                retries += 1
                continue  # the queue is paused; the next slot waits it out
            if status == 403:
                raise AniListUnavailable("AniList refused the request (403); the API may be blocking or disabled")
            if status == 401:
                raise AniListAuthError("AniList rejected the token; reconnect AniList")
            if status >= 500:
                if retries >= self.MAX_RETRIES:
                    raise AniListError(f"AniList server error {status} after 3 retries")
                await self._sleep(self.BACKOFF[retries])
                retries += 1
                continue

            try:
                body = response.json()
            except ValueError as exc:
                raise AniListError(f"AniList returned non-JSON with status {status}") from exc

            errors = body.get("errors") or []
            data = body.get("data")
            if errors:
                text = _messages(errors)
                if "disabled" in text:
                    raise AniListUnavailable("AniList reports the API is temporarily disabled")
                if "invalid token" in text or "unauthorized" in text:
                    raise AniListAuthError("AniList rejected the token; reconnect AniList")
                if "complexity" in text:
                    raise AniListComplexityError(errors, data)
                raise AniListGraphQLError(errors, data)
            if status >= 400:
                raise AniListError(f"AniList returned HTTP {status}")
            return data or {}
