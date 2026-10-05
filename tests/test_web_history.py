import re

import pytest
from fastapi.testclient import TestClient

from mdal.web.app import create_app
from tests.factories import seed_diffed_run


@pytest.fixture
def client(services):
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


def test_empty_history(client):
    assert "No syncs yet." in client.get("/history").text


def test_history_lists_runs_with_counts(client, services):
    repo = services.repo
    run_id = seed_diffed_run(repo)
    repo.update_items(run_id, [("w", {"approved": 1, "write_state": "done", "written_at": "2026-10-03T10:00:05+00:00",
                                      "verify_note": "verified", "al_status_before": "CURRENT"}),
                               ("c", {"approved": 1, "write_state": "failed", "verify_note": "write failed: boom"})])
    repo.update_run(run_id, state="done", started_at="2026-10-03T10:00:00+00:00",
                    finished_at="2026-10-03T10:01:05+00:00", req_anilist=5, req_mangadex=12)
    html = client.get("/history").text
    assert f'href="/history/{run_id}"' in html
    assert "3 write" in html and "2 flag · 1 skip" in html
    assert "1 done · 1 failed" in html
    assert "AniList 5</span>" in html and "MangaDex 12" in html
    assert "1 min 5 s" in html


def test_drill_down_shows_write_state_and_notes(client, services):
    run_id = seed_diffed_run(services.repo)
    services.repo.update_items(run_id, [("w", {"approved": 1, "write_state": "done", "written_at": "t1",
                                               "verify_note": "status changed by AniList: CURRENT→COMPLETED"})])
    html = client.get(f"/history/{run_id}").text
    row = re.search(r'<tr class="r-write w-done">.*?</tr>', html, re.S).group(0)
    assert "status changed by AniList: CURRENT→COMPLETED" in row and "t1" in row
    assert "not approved" in html
    assert 'name="sel"' not in html  # read-only


def test_add_runs_appear(client, services):
    repo = services.repo
    run_id = repo.create_run("done")
    repo.replace_items(run_id, [{"run_id": run_id, "md_id": "x", "action": "write", "reason": "added to AniList as Reading",
                                 "approved": 1, "write_state": "done", "md_progress": 7}])
    repo.update_run(run_id, phase_detail="added 1 entry: Reading, progress 7", req_anilist=1)
    html = client.get("/history").text
    assert "added 1 entry: Reading, progress 7" in html and "1 done" in html
    assert "added to AniList as Reading" in client.get(f"/history/{run_id}").text


def test_unknown_run_404(client):
    assert client.get("/history/999").status_code == 404
