from fastapi.testclient import TestClient

from mdal import main
from mdal.web.app import create_app


def test_host_is_loopback_constant():
    assert main.HOST == "127.0.0.1"


def test_run_binds_loopback(monkeypatch, services):
    calls = {}
    monkeypatch.setattr(main.Services, "from_env", classmethod(lambda cls: services))
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **kw: calls.update(kw))
    main.run()
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8765


def test_index_returns_200(services):
    with TestClient(create_app(services)) as client:
        assert client.get("/").status_code == 200
