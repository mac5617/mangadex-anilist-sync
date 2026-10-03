from fastapi.testclient import TestClient

from mdal import main
from mdal.web.app import create_app


def test_host_is_loopback_constant():
    assert main.HOST == "127.0.0.1"


def test_run_binds_loopback(monkeypatch, make_settings, tmp_path):
    calls = {}
    settings = make_settings(MDAL_DB_PATH=str(tmp_path / "db" / "sync.db"))
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kw: calls.update(kw))
    main.run()
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8765
    assert (tmp_path / "db").is_dir()


def test_index_returns_200():
    response = TestClient(create_app()).get("/")
    assert response.status_code == 200
