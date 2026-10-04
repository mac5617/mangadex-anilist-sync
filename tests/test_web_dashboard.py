import pytest
import respx
from fastapi.testclient import TestClient

from mdal.sync.orchestrator import SyncAlreadyRunning
from mdal.web.app import create_app
from tests.factories import FakeAniList, FakeMangaDex, FakeSeries, al_media, seed_diffed_run


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client(services, mock):
    services.anilist_queue.set_interval(0)
    services.mangadex_queue.set_interval(0)
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


def test_dashboard_with_no_runs(client, mock):
    html = client.get("/").text
    assert "No syncs yet." in html
    assert 'hx-post="/sync"' in html
    assert 'hx-trigger="every 2s"' not in html
    assert len(mock.calls) == 0


def test_dashboard_with_active_run_polls(client, services, mock):
    run_id = services.repo.create_run("resolving")
    services.repo.update_run(run_id, phase_detail="matching 12 series")
    html = client.get("/").text
    assert 'hx-get="/sync/status"' in html and 'hx-trigger="every 2s"' in html
    assert "matching 12 series" in html
    assert 'hx-post="/sync"' not in html  # no second start button while running
    assert len(mock.calls) == 0


def test_dashboard_with_diffed_run(client, services, mock):
    run_id = seed_diffed_run(services.repo)
    html = client.get("/").text
    assert f'href="/sync/{run_id}"' in html and f"AniList changes (sync #{run_id})" in html
    assert 'hx-trigger="every 2s"' not in html
    assert len(mock.calls) == 0


def test_dashboard_auth_status(client):
    html = client.get("/").text
    assert "not connected" in html  # no AniList token in the fake .env
    assert "configured</span> for reader" in html


def test_status_fragment_stops_polling_when_idle(client, services):
    services.repo.create_run("diffed")
    html = client.get("/sync/status").text
    assert html.startswith('<div id="sync-status" class="stack">')


def test_nav_counts(client, services):
    seed_diffed_run(services.repo)
    services.repo.upsert_mapping({"md_id": "k", "al_media_id": None, "state": "review", "tier": 4,
                                  "confidence": 0.7, "reasons": [], "links_hash": "h"})
    html = client.get("/").text
    assert '<a href="/review">Matches <span class="count">1</span></a>' in html


def test_post_sync_starts_run_and_returns_fragment(client, services, mock):
    md, al = FakeMangaDex(), FakeAniList()
    al.add_media(al_media(1, "Frieren"))
    al.add_list("Reading", [(10, 1, "CURRENT", 1)])
    md.add(FakeSeries("a", "Frieren", links={"al": "1"}, reads=["a1"]), {"a1": "3"})
    md.install(mock)
    al.install(mock)
    response = client.post("/sync")
    assert response.status_code == 200
    assert response.text.startswith('<div id="sync-status"')
    assert services.repo.latest_run() is not None


def test_second_post_while_running(client, services, monkeypatch):
    async def busy(target="anilist"):
        raise SyncAlreadyRunning("busy")

    monkeypatch.setattr(services.orchestrator, "start_run", busy)
    response = client.post("/sync")
    assert response.status_code == 409
    assert "A sync is already running." in response.text


def test_sync_latest_redirect(client, services):
    assert client.get("/sync/latest").headers["location"] == "/"
    run_id = seed_diffed_run(services.repo)
    assert client.get("/sync/latest").headers["location"] == f"/sync/{run_id}"
