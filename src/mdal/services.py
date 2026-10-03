"""Process-wide objects: settings, DB, the two paced queues and the API clients.

There is exactly one instance per process (NFR-1), so every caller shares the same queues.
Tests build their own with a temp .env and DB.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import set_key, unset_key

from mdal.clients.anilist import AniListClient
from mdal.clients.anilist_oauth import AniListOAuth
from mdal.clients.mangadex import MangaDexClient, MangaDexCredentials
from mdal.clients.ratelimit import PacedQueue
from mdal.config import ENV_FILE, Settings
from mdal.db.connection import connect
from mdal.db.repo import Repo


class Services:
    def __init__(self, env_file: Path, repo: Repo, settings: Settings | None = None) -> None:
        self.env_file = env_file
        self.repo = repo
        self.settings = settings or Settings(_env_file=env_file)
        self.anilist_queue = PacedQueue(60.0 / repo.get_setting("anilist_rpm"))
        self.mangadex_queue = PacedQueue(1.0 / repo.get_setting("mangadex_rps"))
        self.anilist = AniListClient(self.anilist_token, self.anilist_queue)
        self.mangadex = MangaDexClient(self.mangadex_credentials, self.mangadex_queue)
        self.oauth = AniListOAuth(lambda: self.settings, self.anilist_queue)

    @classmethod
    def from_env(cls) -> Services:
        settings = Settings(_env_file=ENV_FILE)
        return cls(ENV_FILE, Repo(connect(settings.ensure_db_dir())), settings)

    async def aclose(self) -> None:
        await self.anilist.aclose()
        await self.mangadex.aclose()
        await self.oauth.aclose()

    # ---- settings / secrets ---------------------------------------------
    def reload_settings(self) -> None:
        self.settings = Settings(_env_file=self.env_file)

    def secret_values(self) -> list[str]:
        return self.settings.secret_values()

    def anilist_token(self) -> str:
        return self.settings.anilist_access_token.get_secret_value()

    def mangadex_credentials(self) -> MangaDexCredentials:
        s = self.settings
        return MangaDexCredentials(
            s.mangadex_username, s.mangadex_password.get_secret_value(),
            s.mangadex_client_id, s.mangadex_client_secret.get_secret_value(),
        )

    def store_anilist_token(self, token: str) -> None:
        set_key(self.env_file, "ANILIST_ACCESS_TOKEN", token, quote_mode="never")
        self.reload_settings()

    def clear_anilist_token(self) -> None:
        if self.env_file.exists():
            unset_key(self.env_file, "ANILIST_ACCESS_TOKEN", quote_mode="never")
        self.reload_settings()
        self.repo.set_setting("anilist_user_id", None)
        self.repo.set_setting("anilist_user_name", None)
