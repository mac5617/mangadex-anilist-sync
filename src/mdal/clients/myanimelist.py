"""The only way the app talks to myanimelist.net (OAuth) and api.myanimelist.net (API v2).

MyAnimeList documents no rate limit, so requests are paced conservatively (`mal_rpm`, default 30/min)
and the client stops on the first 429 or 403 instead of retrying into a ban. Server errors and network
failures are retried: every write is a PATCH with absolute values, so repeating one is harmless.

Sign-in is OAuth 2 with PKCE; MyAnimeList only supports the `plain` method (challenge = verifier).
Access and refresh tokens both last about 31 days; the client refreshes shortly before expiry and once
on a 401. The session is saved through `MalSessionStore` and never rendered.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from mdal.clients.ratelimit import PacedQueue
from mdal.config import USER_AGENT, Settings

log = logging.getLogger(__name__)

API_URL = "https://api.myanimelist.net/v2"
AUTHORIZE_URL = "https://myanimelist.net/v1/oauth2/authorize"
TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"
LIST_FIELDS = "list_status,num_chapters,status"
LIST_PAGE = 1000          # the documented maximum for user lists
REFRESH_MARGIN = 24 * 3600  # refresh a day before expiry
WRITE_STATUSES = ("reading", "completed")  # the only statuses a sync sends
EDIT_STATUSES = ("on_hold", "dropped")      # the only statuses a list edit sends (List → Stalled)


class MalError(Exception):
    pass


class MalAuthError(MalError):
    """Not connected, or the tokens were rejected and could not be refreshed: reconnect MyAnimeList."""


class MalRateLimited(MalError):
    """429. Halt: MyAnimeList does not say how long to wait."""


class MalUnavailable(MalError):
    """403 or a maintenance page. Halt, do not retry."""


class MalRequestError(MalError):
    """A 4xx for one request (e.g. an invalid value for that manga). Other requests can go on."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class MalSessionStore(Protocol):
    def load_session(self) -> dict | None: ...
    def save_session(self, session: dict | None) -> None: ...


class MemorySessionStore:
    def __init__(self, session: dict | None = None) -> None:
        self.session = session

    def load_session(self) -> dict | None:
        return self.session

    def save_session(self, session: dict | None) -> None:
        self.session = session


class MalClient:
    MAX_RETRIES = 3
    BACKOFF = (5.0, 15.0, 45.0)

    def __init__(
        self,
        settings: Callable[[], Settings],
        queue: PacedQueue,
        store: MalSessionStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        request_counter: Callable[[], None] | None = None,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self.queue = queue
        self.store = store
        self.request_counter = request_counter
        self._wall = wall_clock
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            transport=transport, timeout=30.0, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- OAuth ----------------------------------------------------------
    @property
    def connected(self) -> bool:
        return bool((self.store.load_session() or {}).get("access"))

    def authorize_url(self, state: str, verifier: str) -> str:
        s = self._settings()
        return f"{AUTHORIZE_URL}?" + urlencode({
            "response_type": "code", "client_id": s.mal_client_id, "state": state,
            "redirect_uri": s.mal_redirect_uri, "code_challenge": verifier, "code_challenge_method": "plain",
        })

    def _client_fields(self) -> dict[str, str]:
        s = self._settings()
        fields = {"client_id": s.mal_client_id}
        secret = s.mal_client_secret.get_secret_value()
        if secret:
            fields["client_secret"] = secret
        return fields

    async def _token_request(self, form: dict[str, str]) -> None:
        async with self.queue.slot():
            if self.request_counter:
                self.request_counter()
            try:
                response = await self._http.post(TOKEN_URL, data={**self._client_fields(), **form})
            except httpx.TransportError as exc:
                raise MalError(f"network error: {exc.__class__.__name__}") from exc
        if response.status_code != 200:
            raise MalAuthError(f"MyAnimeList token request failed (HTTP {response.status_code})")
        body = response.json()
        if not body.get("access_token"):
            raise MalAuthError("MyAnimeList returned no access token")
        self.store.save_session({
            "access": body["access_token"], "refresh": body.get("refresh_token"),
            "expires_at": self._wall() + float(body.get("expires_in") or 0),
        })

    async def exchange_code(self, code: str, verifier: str) -> None:
        await self._token_request({
            "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "redirect_uri": self._settings().mal_redirect_uri,
        })

    async def refresh(self) -> None:
        refresh = (self.store.load_session() or {}).get("refresh")
        if not refresh:
            raise MalAuthError("MyAnimeList is not connected")
        try:
            await self._token_request({"grant_type": "refresh_token", "refresh_token": refresh})
        except MalAuthError as exc:
            raise MalAuthError("MyAnimeList sign-in expired; reconnect MyAnimeList in Settings") from exc

    def disconnect(self) -> None:
        self.store.save_session(None)

    async def _access_token(self) -> str:
        session = self.store.load_session() or {}
        if not session.get("access"):
            raise MalAuthError("MyAnimeList is not connected")
        expires = float(session.get("expires_at") or 0)
        if expires and expires - self._wall() < REFRESH_MARGIN and session.get("refresh"):
            await self.refresh()
            session = self.store.load_session() or {}
        return session["access"]

    # ---- requests ---------------------------------------------------------
    async def _request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None) -> Any:
        retries = 0
        refreshed = False
        while True:
            token = await self._access_token()
            async with self.queue.slot():
                if self.request_counter:
                    self.request_counter()
                try:
                    response = await self._http.request(
                        method, f"{API_URL}{path}", params=params, data=data,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                except httpx.TransportError as exc:
                    response = None
                    error = exc
            if response is None:
                if retries >= self.MAX_RETRIES:
                    raise MalError(f"network error: {error.__class__.__name__}") from error
                await self._sleep(self.BACKOFF[retries])
                retries += 1
                continue

            status = response.status_code
            if status == 401 and not refreshed:
                refreshed = True
                await self.refresh()
                continue
            if status == 401:
                raise MalAuthError("MyAnimeList rejected the sign-in; reconnect MyAnimeList in Settings")
            if status == 429:
                raise MalRateLimited("MyAnimeList answered 429 (too many requests)")
            if status == 403:
                raise MalUnavailable("MyAnimeList refused the request (403)")
            if status >= 500:
                if retries >= self.MAX_RETRIES:
                    raise MalError(f"MyAnimeList server error {status} after 3 retries")
                await self._sleep(self.BACKOFF[retries])
                retries += 1
                continue
            if status >= 400:
                raise MalRequestError(status, _error_text(response))
            try:
                return response.json()
            except ValueError as exc:
                raise MalUnavailable(f"MyAnimeList returned non-JSON with status {status} (maintenance?)") from exc

    async def me(self) -> dict[str, Any]:
        return await self._request("GET", "/users/@me")

    async def manga_list(self) -> list[dict[str, Any]]:
        """The whole manga list: [{node: {id, title, main_picture, num_chapters, status}, list_status: {...}}]."""
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            body = await self._request("GET", "/users/@me/mangalist", params={
                "fields": LIST_FIELDS, "limit": LIST_PAGE, "offset": offset, "nsfw": "true"})
            page = body.get("data") or []
            items += page
            if not (body.get("paging") or {}).get("next") or not page:
                return items
            offset += len(page)

    async def recommendations(self, mal_id: int) -> list[dict[str, Any]]:
        """What MyAnimeList readers recommend for a manga: [{node: {id, title}, num_recommendations}]."""
        body = await self._request("GET", f"/manga/{int(mal_id)}", params={"fields": "recommendations"})
        return body.get("recommendations") or []

    async def my_list_status(self, mal_id: int) -> dict[str, Any] | None:
        """Your list entry for a manga ({status, comments, ...}), or None when it isn't on your list."""
        body = await self._request("GET", f"/manga/{int(mal_id)}", params={"fields": "my_list_status{comments}"})
        return body.get("my_list_status") or None

    async def update_comments(self, mal_id: int, comments: str) -> dict[str, Any]:
        """Replace your comments on an entry. Callers must check it's on your list first: MyAnimeList
        creates the entry when it isn't."""
        return await self._request("PATCH", f"/manga/{int(mal_id)}/my_list_status", data={"comments": comments})

    async def edit_entry(self, mal_id: int, *, score: int | None = None, status: str | None = None) -> dict[str, Any]:
        """A list edit outside syncs: a score (1-10) or on_hold/dropped. Callers must check the entry is on your
        list first: MyAnimeList creates the entry when it isn't."""
        form: dict[str, Any] = {}
        if score is not None:
            if not 1 <= int(score) <= 10:
                raise ValueError(f"refusing to send score {score!r}")
            form["score"] = int(score)
        if status is not None:
            if status not in EDIT_STATUSES:
                raise ValueError(f"refusing to send status {status!r}")
            form["status"] = status
        if not form:
            raise ValueError("nothing to send")
        return await self._request("PATCH", f"/manga/{int(mal_id)}/my_list_status", data=form)

    async def update_list_status(self, mal_id: int, *, chapters: int | None, status: str | None) -> dict[str, Any]:
        """The sync's MyAnimeList write. Sends chapters read and, optionally, reading/completed."""
        if status is not None and status not in WRITE_STATUSES:
            raise ValueError(f"refusing to send status {status!r}")
        form: dict[str, Any] = {}
        if chapters is not None:
            form["num_chapters_read"] = int(chapters)
        if status is not None:
            form["status"] = status
        if not form:
            raise ValueError("nothing to send")
        return await self._request("PATCH", f"/manga/{int(mal_id)}/my_list_status", data=form)


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    text = body.get("message") or body.get("error") or ""
    return f"HTTP {response.status_code}: {text}" if text else f"HTTP {response.status_code}"
