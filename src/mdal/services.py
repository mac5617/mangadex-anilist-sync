"""Process-wide objects: settings, DB, the paced queues and the API clients.

There is exactly one instance per process (NFR-1), so every caller shares the same queues.
Tests build their own with a temp .env and DB.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import set_key, unset_key

from mdal.clients.anilist import AniListClient
from mdal.clients.anilist_oauth import AniListOAuth
from mdal.clients.mangadex import MangaDexClient, MangaDexCredentials
from mdal.clients.myanimelist import MalClient
from mdal.clients.ratelimit import PacedQueue
from mdal.config import ENV_FILE, Settings
from mdal.db.connection import connect
from mdal.db.repo import Repo
from mdal.recommend.service import Recommender
from mdal.sync.orchestrator import SyncOrchestrator


class DbGuard:
    """MangaDex cooldown and saved login, kept in the settings table."""

    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.session: dict | None = repo.get_setting("mangadex_session")  # in memory too, for log redaction

    def cooldown(self) -> tuple[float, str | None] | None:
        until = self.repo.get_setting("mangadex_cooldown_until")
        return (float(until), self.repo.get_setting("mangadex_cooldown_reason")) if until else None

    def start_cooldown(self, until: float, reason: str) -> None:
        self.repo.set_setting("mangadex_cooldown_until", until)
        self.repo.set_setting("mangadex_cooldown_reason", reason)

    def clear_cooldown(self) -> None:
        self.repo.set_setting("mangadex_cooldown_until", None)
        self.repo.set_setting("mangadex_cooldown_reason", None)

    def load_session(self) -> dict | None:
        self.session = self.repo.get_setting("mangadex_session")
        return self.session

    def save_session(self, session: dict | None) -> None:
        self.session = session
        self.repo.set_setting("mangadex_session", session)


class DbMalStore:
    """MyAnimeList tokens in the settings table (cached in memory for log redaction). Never rendered."""

    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.session: dict | None = repo.get_setting("mal_session")

    def load_session(self) -> dict | None:
        return self.session

    def save_session(self, session: dict | None) -> None:
        self.session = session
        self.repo.set_setting("mal_session", session)
        if session is None:
            self.repo.set_setting("mal_user_name", None)


class Services:
    def __init__(self, env_file: Path, repo: Repo, settings: Settings | None = None) -> None:
        self.env_file = env_file
        self.repo = repo
        self.settings = settings or Settings(_env_file=env_file)
        self.anilist_queue = PacedQueue(60.0 / repo.get_setting("anilist_rpm"))
        self.mangadex_queue = PacedQueue(1.0 / repo.get_setting("mangadex_rps"))
        self.anilist = AniListClient(self.anilist_token, self.anilist_queue)
        self.mangadex_guard = DbGuard(repo)
        self.mangadex = MangaDexClient(self.mangadex_credentials, self.mangadex_queue, guard=self.mangadex_guard)
        self.oauth = AniListOAuth(lambda: self.settings, self.anilist_queue)
        self.mal_queue = PacedQueue(60.0 / repo.get_setting("mal_rpm"))
        self.mal_store = DbMalStore(repo)
        self.mal = MalClient(lambda: self.settings, self.mal_queue, self.mal_store)
        self.orchestrator = SyncOrchestrator(self)
        self.recommender = Recommender(self)

    @classmethod
    def from_env(cls) -> Services:
        settings = Settings(_env_file=ENV_FILE)
        return cls(ENV_FILE, Repo(connect(settings.ensure_db_dir())), settings)

    async def aclose(self) -> None:
        await self.anilist.aclose()
        await self.mangadex.aclose()
        await self.oauth.aclose()
        await self.mal.aclose()
        await self.recommender.aclose()

    # ---- settings / secrets ---------------------------------------------
    def reload_settings(self) -> None:
        self.settings = Settings(_env_file=self.env_file)

    def secret_values(self) -> list[str]:
        # No DB access here: the logging filter calls this for every record.
        session = self.mangadex_guard.session or {}
        mal = self.mal_store.session or {}
        saved = [session.get("access"), session.get("refresh"), mal.get("access"), mal.get("refresh")]
        return self.settings.secret_values() + [v for v in saved if v]

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
