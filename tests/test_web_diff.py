import re

import pytest
import respx
from fastapi.testclient import TestClient

from mdal.web.app import create_app
from tests.factories import seed_diffed_run


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client(services, mock):
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


@pytest.fixture
def run_id(services):
    services.repo.set_setting("first_write_done", True)
    return seed_diffed_run(services.repo)


def row(html: str, md_id: str) -> str:
    m = re.search(rf'<tr class="r-\w+" data-md-id="{md_id}">.*?</tr>', html, re.S)
    assert m, md_id
    return m.group(0)


def test_diff_page_summary_and_estimate(client, run_id, mock):
    html = client.get(f"/sync/{run_id}").text
    assert "3 to write · 0 to add · 2 flagged · 1 skipped" in html
    assert "2 to mark completed" in html
    # Default selection: the 3 writes (status-only counts because its completion is checked).
    assert "3 selected, 2 to mark completed" in html
    assert "≈ 3 AniList requests, ≈ 9 s" in html
    assert len(mock.calls) == 0


def test_filters(client, run_id):
    html = client.get(f"/sync/{run_id}?f=flag").text
    assert re.search(r'id="f-flag" value="flag" class="tab-radio" checked', html)
    for f in ("write", "add", "flag", "skip", "all"):
        assert f'for="f-{f}"' in html
    assert 'class="r-write"' in html and 'class="r-flag"' in html and 'class="r-skip"' in html
    # Unknown filters fall back to "write".
    assert re.search(r'id="f-write" value="write" class="tab-radio" checked', client.get(f"/sync/{run_id}?f=zzz").text)


def test_checkbox_states(client, run_id):
    html = client.get(f"/sync/{run_id}").text
    assert re.search(r'name="sel" value="w"[^>]*checked', row(html, "w"))
    implausible = row(html, "i")
    assert 'name="sel"' in implausible and "checked" not in implausible and "disabled" not in implausible
    exceeds = row(html, "x")
    assert re.search(r'name="sel" value="x"[^>]*disabled', exceeds)
    assert 'name="sel"' not in row(html, "k")


def test_status_column(client, run_id):
    html = client.get(f"/sync/{run_id}").text
    c = row(html, "c")
    assert "Reading → Completed (AniList: finished, 120 ch)" in c
    assert re.search(r'name="mc" value="c"\s*checked', c)
    s = row(html, "s")
    assert "50 (unchanged)" in s
    assert "110 → 120" in c
    assert "2 unresolved" in row(html, "w")


def test_approve_button_enabled_for_diffed_run(client, run_id):
    html = client.get(f"/sync/{run_id}").text
    assert f'formaction="/sync/{run_id}/approve"' in html
    assert "First live write" not in html


def test_approve_button_disabled_for_other_states(client, services, run_id):
    services.repo.update_run(run_id, state="done")
    html = client.get(f"/sync/{run_id}").text
    assert '<button type="button" id="approve" disabled>' in html


def test_nothing_preselected_before_first_write(client, services, run_id):
    services.repo.set_setting("first_write_done", False)
    html = client.get(f"/sync/{run_id}").text
    assert "First live write: select exactly one entry" in html
    assert "0 selected" in html
    assert not re.search(r'name="sel" value="\w"[^>]*checked', html)


def test_implausible_rows_have_override(client, run_id):
    html = client.get(f"/sync/{run_id}").text
    assert 'name="ov" value="i"' in row(html, "i")
    assert 'name="ov"' not in row(html, "w")


def test_titles_are_escaped(client, run_id):
    html = client.get(f"/sync/{run_id}?f=all").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; Evil" in html


def test_links(client, run_id):
    html = row(client.get(f"/sync/{run_id}").text, "w")
    assert 'href="https://mangadex.org/title/w"' in html
    assert 'href="https://anilist.co/manga/1"' in html


@pytest.mark.parametrize(
    "data, expected",
    [
        ({"sel": ["w"]}, "1 selected · ≈ 3 AniList requests, ≈ 9 s"),
        ({"sel": ["w", "c", "s"], "mc": ["c", "s"]}, "3 selected, 2 to mark completed"),
        ({"sel": ["w", "c", "s"], "mc": []}, "2 selected"),           # status-only without completion: nothing to send
        ({"sel": ["x", "i"]}, "1 selected"),                         # exceeds_total never counts
        ({}, "0 selected · ≈ 0 AniList requests, ≈ 0 s"),
    ],
)
def test_estimate_endpoint(client, run_id, mock, data, expected):
    response = client.post(f"/sync/{run_id}/estimate", data=data)
    assert response.status_code == 200
    assert expected in " ".join(response.text.split())
    assert len(mock.calls) == 0


def test_unknown_run_404(client):
    assert client.get("/sync/999").status_code == 404
    assert client.post("/sync/999/estimate").status_code == 404


def test_discard(client, services, run_id):
    response = client.post(f"/sync/{run_id}/discard")
    assert response.status_code == 200 and "discarded" in response.text
    assert services.repo.get_run(run_id)["state"] == "cancelled"
    assert client.post(f"/sync/{run_id}/discard").status_code == 409


def test_every_page_makes_no_api_calls(client, run_id, mock):
    for url in ("/", f"/sync/{run_id}", "/sync/status", "/settings", "/sync/latest"):
        client.get(url)
    assert len(mock.calls) == 0


def test_add_rows(client, services, run_id):
    repo = services.repo
    repo.upsert_item({"run_id": run_id, "md_id": "w2", "al_media_id": 2, "al_entry_id": None, "al_progress": None,
                      "md_progress": 7, "action": "add", "reason": "not on your AniList list; add as Reading at 7"})
    repo.upsert_item({"run_id": run_id, "md_id": "w3", "al_media_id": 2, "al_entry_id": None, "al_progress": None,
                      "md_progress": 120, "action": "add", "reason": "add as Completed", "set_status": "COMPLETED",
                      "status_source": "AniList", "status_approved": 1})
    html = client.get(f"/sync/{run_id}?f=add").text
    assert "To add (2)" in html
    a = row(html, "w2")
    assert "new → 7" in a and "New entry: Reading" in a
    assert re.search(r'name="sel" value="w2"[^>]*checked', a)
    c = row(html, "w3")
    assert "New entry → Completed (AniList: finished, 120 ch); untick to add as Reading" in c
    assert re.search(r'name="mc" value="w3"\s*checked', c)
