"""Secrets and identity from `.env`. Tunables live in the DB `settings` table (story 02)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from mdal import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
APP_DIR_NAME = "mangadex-anilist-sync"
USER_AGENT = f"mangadex-anilist-sync/{__version__}"


def default_db_path() -> Path:
    """Outside OneDrive: %LOCALAPPDATA% on Windows, XDG data dir elsewhere."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".local" / "share"
    return base / APP_DIR_NAME / "sync.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    mangadex_username: str = ""
    mangadex_password: SecretStr = SecretStr("")
    mangadex_client_id: str = ""
    mangadex_client_secret: SecretStr = SecretStr("")

    anilist_client_id: str = ""
    anilist_client_secret: SecretStr = SecretStr("")
    anilist_redirect_uri: str = "http://127.0.0.1:8765/auth/anilist/callback"
    anilist_access_token: SecretStr = SecretStr("")

    mdal_db_path: Path | None = None
    mdal_port: int = 8765

    @property
    def db_path(self) -> Path:
        return self.mdal_db_path or default_db_path()

    def ensure_db_dir(self) -> Path:
        path = self.db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def secret_values(self) -> list[str]:
        """Every value that must never reach a log line or an HTML page."""
        values = [
            self.mangadex_password.get_secret_value(),
            self.mangadex_client_secret.get_secret_value(),
            self.anilist_client_secret.get_secret_value(),
            self.anilist_access_token.get_secret_value(),
        ]
        return [v for v in values if v]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
