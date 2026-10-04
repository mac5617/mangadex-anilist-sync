from pathlib import Path

from mdal.config import USER_AGENT, default_db_path


def test_env_file_is_parsed(make_settings):
    s = make_settings(MANGADEX_USERNAME="reader", MANGADEX_PASSWORD="hunter2pw", MDAL_PORT="9000")
    assert s.mangadex_username == "reader"
    assert s.mangadex_password.get_secret_value() == "hunter2pw"
    assert s.mdal_port == 9000


def test_secrets_are_not_in_repr(make_settings):
    s = make_settings(MANGADEX_PASSWORD="hunter2pw", ANILIST_ACCESS_TOKEN="tok-abcdef")
    assert "hunter2pw" not in repr(s)
    assert "tok-abcdef" not in repr(s)


def test_secret_values_lists_only_set_secrets(make_settings):
    s = make_settings(MANGADEX_PASSWORD="hunter2pw", ANILIST_ACCESS_TOKEN="tok-abcdef")
    assert sorted(s.secret_values()) == ["hunter2pw", "tok-abcdef"]


def test_default_db_path_used_without_override(make_settings):
    s = make_settings()
    assert s.db_path == default_db_path()


def test_default_db_path_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_db_path() == tmp_path / "mangadex-anilist-sync" / "sync.db"


def test_db_path_override_and_dir_creation(make_settings, tmp_path):
    target = tmp_path / "nested" / "dir" / "x.db"
    s = make_settings(MDAL_DB_PATH=str(target))
    assert s.ensure_db_dir() == target
    assert target.parent.is_dir()


def test_user_agent_is_app_name_only():
    assert USER_AGENT.startswith("mangadex-anilist-sync/")
    assert "@" not in USER_AGENT and "Mozilla" not in USER_AGENT


def test_gitignore_covers_env():
    root = Path(__file__).resolve().parents[1]
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines
    assert "!.env.example" in lines
