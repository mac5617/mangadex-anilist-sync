"""AniList OAuth (authorization-code grant, plus the pin flow). See api-notes.md → Auth.

The token request goes to anilist.co, not the GraphQL endpoint, but still waits
for a slot in the AniList queue, to be conservative.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlencode

import httpx

from mdal.clients.ratelimit import PacedQueue
from mdal.config import USER_AGENT, Settings

AUTHORIZE_URL = "https://anilist.co/api/v2/oauth/authorize"
TOKEN_URL = "https://anilist.co/api/v2/oauth/token"
PIN_REDIRECT = "https://anilist.co/api/v2/oauth/pin"


class AniListOAuthError(Exception):
    pass


class AniListOAuth:
    def __init__(self, settings: Callable[[], Settings], queue: PacedQueue) -> None:
        self._settings = settings
        self.queue = queue
        self._http = httpx.AsyncClient(timeout=30.0, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def uses_pin(self) -> bool:
        return self._settings().anilist_redirect_uri == PIN_REDIRECT

    def authorize_url(self, state: str) -> str:
        s = self._settings()
        return f"{AUTHORIZE_URL}?" + urlencode(
            {"client_id": s.anilist_client_id, "redirect_uri": s.anilist_redirect_uri, "response_type": "code", "state": state}
        )

    def pin_url(self) -> str:
        """Implicit grant shown on AniList's pin page: the user copies the token itself."""
        return f"{AUTHORIZE_URL}?" + urlencode({"client_id": self._settings().anilist_client_id, "response_type": "token"})

    async def exchange_code(self, code: str) -> str:
        s = self._settings()
        payload = {
            "grant_type": "authorization_code",
            "client_id": s.anilist_client_id,
            "client_secret": s.anilist_client_secret.get_secret_value(),
            "redirect_uri": s.anilist_redirect_uri,
            "code": code,
        }
        async with self.queue.slot():
            response = await self._http.post(TOKEN_URL, json=payload)
        if response.status_code != 200:
            raise AniListOAuthError(f"AniList token exchange failed (HTTP {response.status_code})")
        token = response.json().get("access_token")
        if not token:
            raise AniListOAuthError("AniList token exchange returned no access_token")
        return token
