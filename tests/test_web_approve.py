import pytest
import respx
from fastapi.testclient import TestClient

from mdal.web.app import create_app
from tests.factories import FakeAniList, al_media, seed_diffed_run


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fake(mock):
    f = FakeAniList()
    for i, (status, progress) in {1: ("CURRENT", 5), 2: ("CURRENT", 110), 3: ("CURRENT", 50),
                                  4: ("CURRENT", 10), 5: ("CURRENT", 9)}.items():
        f.add_media(al_media(i, f"AL {i}"))
    f.add_list("Reading", [(100 + i, i, "CURRENT", p) for i, p in [(1, 5), (2, 110), (3, 50), (4, 10), (5, 9)]])
    return f.install(mock)


@pytest.fixture
def client(services, fake):
    services.anilist_queue.set_interval(0)
    services.repo.set_setting("anilist_user_id", 1)
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


@pytest.fixture
def run_id(services):
    return seed_diffed_run(services.repo)


def test_approve_writes_selection(client, services, run_id, fake):
    response = client.post(f"/sync/{run_id}/approve", data={"sel": ["w"]})
    assert response.status_code == 303 and response.headers["location"] == "/"
    # The TestClient's loop runs the task; poll the status endpoint until it finishes.
    for _ in range(50):
        if services.repo.get_run(run_id)["state"] in ("done", "failed", "halted"):
            break
        client.get("/sync/status")
    r = services.repo.get_run(run_id)
    assert r["state"] == "done", r["error"]
    assert len(fake.mutations) == 1 and fake.entry(101)["progress"] == 12
    assert services.repo.get_setting("first_write_done") is True


def test_implausible_needs_override(client, services, run_id):
    services.repo.set_setting("first_write_done", True)
    response = client.post(f"/sync/{run_id}/approve", data={"sel": ["i"]})
    assert response.status_code == 400 and "override" in response.text


def test_exceeds_total_rejected(client, services, run_id):
    services.repo.set_setting("first_write_done", True)
    response = client.post(f"/sync/{run_id}/approve", data={"sel": ["x"]})
    assert response.status_code == 400 and "exceed" in response.text


def test_approve_marks_items(client, services, run_id):
    services.repo.set_setting("first_write_done", True)
    response = client.post(f"/sync/{run_id}/approve", data={"sel": ["w", "c", "s", "i"], "mc": ["c"], "ov": ["i"]})
    assert response.status_code == 303
    items = {i["md_id"]: i for i in services.repo.items(run_id)}
    assert items["w"]["approved"] == 1 and items["c"]["status_approved"] == 1
    assert items["s"]["approved"] == 0  # status-only with completion unticked: nothing to send
    assert items["i"]["approved"] == 1
    assert items["k"]["approved"] == 0


def test_resume_button_and_endpoint(client, services, run_id, fake):
    services.repo.set_setting("first_write_done", True)
    services.repo.update_items(run_id, [("w", {"approved": 1, "write_state": "pending"})])
    services.repo.update_run(run_id, state="failed", approved_at="2026-10-03T00:00:00+00:00", error="boom")
    html = client.get("/").text
    assert f'hx-post="/sync/{run_id}/resume"' in html
    response = client.post(f"/sync/{run_id}/resume")
    assert response.status_code == 200
    for _ in range(50):
        if services.repo.get_run(run_id)["state"] == "done":
            break
        client.get("/sync/status")
    assert services.repo.get_run(run_id)["state"] == "done"
    assert fake.entry(101)["progress"] == 12


def test_resume_rejected_for_diffed_run(client, run_id):
    assert client.post(f"/sync/{run_id}/resume").status_code == 409
