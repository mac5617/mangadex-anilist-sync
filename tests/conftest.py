import pytest

from mdal.config import Settings

ENV_KEYS = [
    "MANGADEX_USERNAME", "MANGADEX_PASSWORD", "MANGADEX_CLIENT_ID", "MANGADEX_CLIENT_SECRET",
    "ANILIST_CLIENT_ID", "ANILIST_CLIENT_SECRET", "ANILIST_REDIRECT_URI", "ANILIST_ACCESS_TOKEN",
    "MDAL_DB_PATH", "MDAL_PORT",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Tests never see the developer's real environment variables."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def make_settings(tmp_path):
    """Build Settings from a temp .env file instead of the real one."""

    def factory(**values: str) -> Settings:
        env_file = tmp_path / ".env"
        env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        return Settings(_env_file=env_file)

    return factory
