"""The only way the app talks to *.mangadex.org (architecture §5, NFR-1/5/8).

Sending requests through repeated 429s escalates to an IP ban, so this client
stops after 3 consecutive 429s instead of retrying further, and halts on 403.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from mdal.clients.ratelimit import PacedQueue
from mdal.config import USER_AGENT

log = logging.getLogger(__name__)

API_URL = "https://api.mangadex.org"
TOKEN_URL = "https://auth.mangadex.org/realms/mangadex/protocol/openid-connect/token"
ALL_CONTENT_RATINGS = ["safe", "suggestive", "erotica", "pornographic"]
IDS_PER_REQUEST = 100


class MangaDexError(Exception):
    pass


class MangaDexRateLimited(MangaDexError):
    """4th consecutive 429: stop before MangaDex escalates to a ban."""


class MangaDexBlocked(MangaDexError):
    """403 on a data endpoint: most likely a temporary IP ban. Halt."""


class MangaDexAuthError(MangaDexError):
    """Login failed: bad credentials, or the personal client is not approved."""


@dataclass(frozen=True)
class MangaDexCredentials:
    username: str
    password: str
    client_id: str
    client_secret: str


class MangaDexClient:
    REFRESH_AFTER = 13 * 60.0
    BACKOFF_429 = (10.0, 30.0, 60.0)
    BACKOFF_NETWORK = (5.0, 15.0, 45.0)

    def __init__(
        self,
        credentials: Callable[[], MangaDexCredentials],
        queue: PacedQueue,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        request_counter: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.queue = queue
        self.request_counter = request_counter
        self._credentials = credentials
        self._clock = clock
        self._wall = wall_clock
        self._sleep = sleep
        self._http = httpx.AsyncClient(transport=transport, timeout=30.0, headers={"User-Agent": USER_AGENT})
        self._access: str | None = None
        self._refresh: str | None = None
        self._issued_at = 0.0
        self._consecutive_429 = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- transport ------------------------------------------------------
    def _wait_for_429(self, response: httpx.Response) -> float:
        retry_at = response.headers.get("X-RateLimit-Retry-After")  # Unix time
        if retry_at:
            try:
                delta = float(retry_at) - self._wall()
                if delta > 0:
                    return delta
            except ValueError:
                pass
        retry_after = response.headers.get("Retry-After")  # seconds
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self.BACKOFF_429[min(self._consecutive_429, len(self.BACKOFF_429)) - 1]

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        network_retries = 0
        while True:
            try:
                async with self.queue.slot():
                    if self.request_counter:
                        self.request_counter()
                    response = await self._http.request(method, url, **kwargs)
                    if response.status_code == 429:
                        self._consecutive_429 += 1
                        if self._consecutive_429 > len(self.BACKOFF_429):
                            raise MangaDexRateLimited("MangaDex kept answering 429; stopped to avoid an IP ban")
                        wait = self._wait_for_429(response)
                        log.warning("MangaDex 429: pausing all MangaDex requests for %.0f s", wait)
                        self.queue.pause_for(wait)
                        continue
            except httpx.TransportError as exc:
                if network_retries >= len(self.BACKOFF_NETWORK):
                    raise MangaDexError(f"network error: {exc.__class__.__name__}") from exc
                await self._sleep(self.BACKOFF_NETWORK[network_retries])
                network_retries += 1
                continue
            self._consecutive_429 = 0
            return response

    # ---- auth -----------------------------------------------------------
    async def _token_grant(self, form: dict[str, str]) -> httpx.Response:
        return await self._send("POST", TOKEN_URL, data=form)

    async def _password_grant(self) -> None:
        c = self._credentials()
        if not (c.username and c.password and c.client_id and c.client_secret):
            raise MangaDexAuthError("MangaDex credentials are incomplete in .env")
        response = await self._token_grant({
            "grant_type": "password",
            "username": c.username,
            "password": c.password,
            "client_id": c.client_id,
            "client_secret": c.client_secret,
        })
        if response.status_code == 403:
            raise MangaDexAuthError("MangaDex refused the login (403): is the personal API client approved?")
        if response.status_code != 200:
            raise MangaDexAuthError(f"MangaDex login failed ({response.status_code}): check username, password and client")
        self._store_tokens(response.json())

    async def _refresh_grant(self) -> bool:
        if not self._refresh:
            return False
        c = self._credentials()
        response = await self._token_grant({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh,
            "client_id": c.client_id,
            "client_secret": c.client_secret,
        })
        if response.status_code != 200:
            log.info("MangaDex token refresh failed (%s); logging in again", response.status_code)
            return False
        self._store_tokens(response.json())
        return True

    def _store_tokens(self, body: dict[str, Any]) -> None:
        self._access = body["access_token"]
        self._refresh = body.get("refresh_token", self._refresh)
        self._issued_at = self._clock()

    async def _renew(self) -> None:
        if not await self._refresh_grant():
            await self._password_grant()

    async def ensure_token(self) -> None:
        if self._access is None:
            await self._password_grant()
        elif self._clock() - self._issued_at >= self.REFRESH_AFTER:
            await self._renew()

    async def check_login(self) -> None:
        """Token grant only (1 request); raises MangaDexAuthError on failure. Used by story 06."""
        self._access = None
        await self._password_grant()

    # ---- data -----------------------------------------------------------
    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.ensure_token()
        renewed = False
        while True:
            response = await self._send(
                "GET", f"{API_URL}{path}", params=params, headers={"Authorization": f"Bearer {self._access}"}
            )
            if response.status_code == 401 and not renewed:
                renewed = True
                await self._renew()
                continue
            break
        if response.status_code == 403:
            raise MangaDexBlocked(
                "MangaDex returned 403, most likely a temporary IP ban. Stop and wait before syncing again."
            )
        if response.status_code >= 400:
            raise MangaDexError(f"MangaDex {path} failed with HTTP {response.status_code}: {_error_detail(response)}")
        return response.json()

    async def get_by_ids(
        self,
        path: str,
        ids: Sequence[str],
        extra_params: dict[str, Any] | None = None,
        *,
        with_limit: bool = True,
    ) -> Any:
        """Call `path` for `ids` in chunks of 100 and merge `data` (list → concatenated, dict → merged)."""
        merged: Any = None
        for start in range(0, len(ids), IDS_PER_REQUEST):
            params: dict[str, Any] = {**(extra_params or {}), "ids[]": list(ids[start : start + IDS_PER_REQUEST])}
            if with_limit:
                params["limit"] = IDS_PER_REQUEST
            data = (await self.get(path, params)).get("data")
            if isinstance(data, dict):
                merged = {**(merged or {}), **data}
            else:
                merged = (merged or []) + list(data or [])
        return merged if merged is not None else []


def _error_detail(response: httpx.Response) -> str:
    try:
        errors = response.json().get("errors") or []
        return "; ".join(str(e.get("detail") or e.get("title")) for e in errors) or "no detail"
    except ValueError:
        return "no detail"
