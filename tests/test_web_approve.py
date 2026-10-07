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


def mal_run(services):
    """A MyAnimeList sync waiting for approval, beside the AniList one: one update for series "w"."""
    repo = services.repo
    rid = repo.create_run("diffed", "mal")
    repo.replace_items(rid, [{"run_id": rid, "md_id": "w", "al_media_id": 1, "al_entry_id": 9001, "mal_id": 9001,
                              "al_progress": 5, "md_progress": 12, "action": "write", "reason": "read to 12 on MangaDex"}])
    return rid


def test_both_sites_on_one_page_and_one_approval(client, services, run_id):
    m = mal_run(services)
    written = []

    async def fake_write(rid):                       # stands in for the AniList and MyAnimeList writers
        written.append(rid)
        services.repo.update_run(rid, state="done", finished_at="t")

    services.orchestrator._write_phase = fake_write
    page = client.get(f"/sync/{m}").text
    assert "Changes to approve" in page and "Write selected to AniList and MyAnimeList" in page
    assert f'value="{run_id}:w"' in page and f'value="{m}:w"' in page        # the same series, once per site
    est = client.post(f"/sync/{run_id}/estimate", data={"runs": [run_id, m], "sel": [f"{run_id}:w", f"{m}:w"]}).text
    assert "2 selected" in est and "AniList and MyAnimeList requests" in est

    response = client.post(f"/sync/{m}/approve", data={"runs": [run_id, m], "sel": [f"{run_id}:w", f"{m}:w"]})
    assert response.status_code == 303
    for _ in range(50):
        if len(written) == 2:
            break
        client.get("/sync/status")
    assert written == [run_id, m]                                              # AniList first, then MyAnimeList
    assert services.repo.get_run(run_id)["approved_at"] and services.repo.get_run(m)["approved_at"]


def test_a_site_with_nothing_ticked_is_closed(client, services, run_id):
    m = mal_run(services)
    written = []

    async def fake_write(rid):
        written.append(rid)
        services.repo.update_run(rid, state="done", finished_at="t")

    services.orchestrator._write_phase = fake_write
    assert client.post(f"/sync/{run_id}/approve", data={"runs": [run_id, m], "sel": [f"{m}:w"]}).status_code == 303
    for _ in range(50):
        if written:
            break
        client.get("/sync/status")
    assert written == [m]
    a = services.repo.get_run(run_id)
    assert (a["state"], a["phase_detail"]) == ("done", "nothing selected to write")
    nothing = client.post(f"/sync/{m}/approve", data={})                        # nothing open any more
    assert nothing.status_code == 400


def test_changes_that_arrive_after_the_page_loaded_are_left_waiting(client, services, run_id):
    """The page showed only AniList; MyAnimeList's changes landed before Write: they stay waiting, unseen ones
    are never closed."""
    written = []

    async def fake_write(rid):
        written.append(rid)
        services.repo.update_run(rid, state="done", finished_at="t")

    services.orchestrator._write_phase = fake_write
    page = client.get(f"/sync/{run_id}").text
    assert f'name="runs" value="{run_id}"' in page
    m = mal_run(services)                                           # arrives while the page is open
    others = client.get(f"/sync/{run_id}/others?shown={run_id}").text
    assert "MyAnimeList has changes too" in others and "Show them here" in others
    assert client.post(f"/sync/{run_id}/approve", data={"runs": [run_id], "sel": [f"{run_id}:w"]}).status_code == 303
    for _ in range(50):
        if written:
            break
        client.get("/sync/status")
    assert written == [run_id]
    assert services.repo.get_run(m)["state"] == "diffed"            # still there to review
    assert "Review changes" in client.get("/").text


def test_page_says_when_a_site_is_still_being_read(client, services, run_id):
    services.orchestrator.queued = ["mal"]
    page = client.get(f"/sync/{run_id}").text
    assert "MyAnimeList is still being read" in page and 'hx-trigger="every 3s"' in page
    services.orchestrator.queued = []
