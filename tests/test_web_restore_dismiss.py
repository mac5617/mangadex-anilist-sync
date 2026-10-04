"""Restoring discarded syncs and dismissing flagged rows."""

import re

import pytest
from fastapi.testclient import TestClient

from mdal.sync.orchestrator import open_diff, refresh_item
from mdal.web.app import create_app
from tests.factories import seed_diffed_run


@pytest.fixture
def client(services):
    services.repo.set_setting("first_write_done", True)
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


@pytest.fixture
def run_id(services):
    return seed_diffed_run(services.repo)


def row(html: str, md_id: str) -> str:
    m = re.search(rf'<tr class="r-\w+" data-md-id="{md_id}"[^>]*>.*?</tr>', html, re.S)
    assert m, md_id
    return m.group(0)


# ---- restore ---------------------------------------------------------------------


def test_discard_offers_undo_and_restore_brings_it_back(client, services, run_id):
    html = client.post(f"/sync/{run_id}/discard").text
    assert f'action="/sync/{run_id}/restore"' in html and f"Undo: restore sync #{run_id}" in html
    r = services.repo.get_run(run_id)
    assert (r["state"], r["error"]) == ("cancelled", "discarded by you")

    response = client.post(f"/sync/{run_id}/restore")
    assert response.status_code == 303 and response.headers["location"] == f"/sync/{run_id}"
    r = services.repo.get_run(run_id)
    assert (r["state"], r["error"], r["finished_at"]) == ("diffed", None, None)
    assert f'formaction="/sync/{run_id}/approve"' in client.get(f"/sync/{run_id}").text


def test_discarded_diff_page_and_history_offer_restore(client, services, run_id):
    services.orchestrator.discard(run_id)
    page = client.get(f"/sync/{run_id}").text
    assert f'action="/sync/{run_id}/restore"' in page and "This sync was discarded by you." in page
    assert f'action="/sync/{run_id}/restore"' in client.get("/history").text
    assert f'action="/sync/{run_id}/restore"' in client.get(f"/history/{run_id}").text


def test_restoring_closes_the_other_open_diff(client, services, run_id):
    services.orchestrator.discard(run_id)
    newer = seed_diffed_run(services.repo)
    client.post(f"/sync/{run_id}/restore")
    r = services.repo.get_run(newer)
    assert (r["state"], r["error"]) == ("cancelled", f"superseded by restored sync #{run_id}")
    assert open_diff(services.repo)["run_id"] == run_id
    assert services.orchestrator.can_restore(newer)  # and that one can come back too


@pytest.mark.parametrize("state, approved", [("done", True), ("failed", True), ("diffed", False), ("cancelled", True)])
def test_only_unapproved_cancelled_runs_restore(client, services, run_id, state, approved):
    services.repo.update_run(run_id, state=state, approved_at="2026-10-03T00:00:00+00:00" if approved else None)
    assert client.post(f"/sync/{run_id}/restore").status_code == 409
    assert services.repo.get_run(run_id)["state"] == state


def test_match_decisions_update_a_restored_older_run(services, run_id):
    services.orchestrator.discard(run_id)
    newer = services.repo.create_run("cancelled")  # newest run is not the open one
    services.orchestrator.restore(run_id)
    assert services.repo.latest_run()["run_id"] == newer
    assert open_diff(services.repo)["run_id"] == run_id
    assert refresh_item(services.repo, "k") is True


# ---- dismiss ---------------------------------------------------------------------


def test_flagged_rows_have_dismiss(client, run_id):
    html = client.get(f"/sync/{run_id}?f=flag").text
    for md_id in ("i", "x"):
        assert f'hx-post="/sync/{run_id}/dismiss/{md_id}"' in row(html, md_id)
    assert "/dismiss/" not in row(html, "w")


def test_dismiss_moves_row_to_skipped_and_updates_counts(client, services, run_id):
    response = client.post(f"/sync/{run_id}/dismiss/x")
    assert response.status_code == 200
    html = response.text
    x = row(html, "x")
    assert 'class="r-skip"' in x and "dismissed by you: MangaDex 11 exceeds" in x
    assert f'hx-post="/sync/{run_id}/undismiss/x"' in x and 'name="sel"' not in x
    assert 'id="diff-stats" hx-swap-oob="true"' in html and 'id="diff-tabs" hx-swap-oob="true"' in html
    assert "Flagged (1)" in html and "Skipped (2)" in html
    assert services.repo.dismissed_flags()["x"]["md_progress"] == 11


def test_show_again(client, services, run_id):
    client.post(f"/sync/{run_id}/dismiss/x")
    html = client.post(f"/sync/{run_id}/undismiss/x").text
    assert 'class="r-flag"' in row(html, "x") and "Flagged (2)" in html
    assert "x" not in services.repo.dismissed_flags()


def test_dismissed_row_cannot_be_approved(client, services, run_id):
    client.post(f"/sync/{run_id}/dismiss/i")
    response = client.post(f"/sync/{run_id}/approve", data={"sel": ["i"], "ov": ["i"]})
    assert response.status_code == 400 and "cannot be written" in response.text


def test_dismiss_only_flags_on_open_diff(client, services, run_id):
    assert client.post(f"/sync/{run_id}/dismiss/w").status_code == 409
    services.repo.update_run(run_id, state="done")
    assert client.post(f"/sync/{run_id}/dismiss/x").status_code == 409
    assert client.post(f"/sync/{run_id}/dismiss/nope").status_code == 409
