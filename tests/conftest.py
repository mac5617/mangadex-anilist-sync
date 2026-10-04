import pytest

from mdal.config import Settings

ENV_KEYS = [
    "MANGADEX_USERNAME", "MANGADEX_PASSWORD", "MANGADEX_CLIENT_ID", "MANGADEX_CLIENT_SECRET",
    "ANILIST_CLIENT_ID", "ANILIST_CLIENT_SECRET", "ANILIST_REDIRECT_URI", "ANILIST_ACCESS_TOKEN",
    "MAL_CLIENT_ID", "MAL_CLIENT_SECRET", "MAL_REDIRECT_URI", "OLLAMA_URL",
    "MDAL_DB_PATH", "MDAL_PORT",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Tests never see the developer's real environment variables."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


FAKE_ENV = {
    "MANGADEX_USERNAME": "reader",
    "MANGADEX_PASSWORD": "md-password-secret",
    "MANGADEX_CLIENT_ID": "personal-client-reader",
    "MANGADEX_CLIENT_SECRET": "md-client-secret-xyz",
    "ANILIST_CLIENT_ID": "4242",
    "ANILIST_CLIENT_SECRET": "al-client-secret-qwe",
    "ANILIST_REDIRECT_URI": "http://127.0.0.1:8765/auth/anilist/callback",
}


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("".join(f"{k}={v}\n" for k, v in FAKE_ENV.items()), encoding="utf-8")
    return path


@pytest.fixture
def services(tmp_path, env_file):
    """Services wired to a temp .env and temp DB; never the real ones."""
    from mdal.db.connection import connect
    from mdal.db.repo import Repo
    from mdal.services import Services

    repo = Repo(connect(tmp_path / "test.db"))
    yield Services(env_file, repo)
    repo.conn.close()


@pytest.fixture
def make_settings(tmp_path):
    """Build Settings from a temp .env file instead of the real one."""

    def factory(**values: str) -> Settings:
        env_file = tmp_path / ".env"
        env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        return Settings(_env_file=env_file)

    return factory
