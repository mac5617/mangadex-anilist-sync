import re

import pytest
from fastapi.testclient import TestClient

from mdal.web.app import create_app
from tests.test_add_entry import fake, mock, repo  # noqa: F401  (fixtures)


@pytest.fixture
def client(services, repo, fake):  # noqa: F811
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


def test_page_lists_rows_with_defaults(client, fake):  # noqa: F811
    html = client.get("/not-listed").text
    assert "Not on my list (3)" in html
    fin = re.search(r'<tr id="nl-fin">.*?</tr>', html, re.S).group(0)
    assert '<option value="COMPLETED" selected>Completed</option>' in fin
    rel = re.search(r'<tr id="nl-rel">.*?</tr>', html, re.S).group(0)
    assert '<option value="CURRENT" selected>Reading</option>' in rel and 'value="7"' in rel
    over = re.search(r'<tr id="nl-over">.*?</tr>', html, re.S).group(0)
    assert "progress capped" in over and 'name="progress" value="50"' in over
    assert 'loading="lazy"' in html and 'referrerpolicy="no-referrer"' in html
    assert fake.call_count == 0


def test_add_swaps_row(client, services, fake):  # noqa: F811
    response = client.post("/not-listed/rel/add", data={"status": "CURRENT", "progress": "7"})
    assert response.status_code == 200
    assert response.text.startswith('<tr id="nl-rel" class="done">')
    assert "Added to AniList as Reading, progress 7." in response.text
    assert len(fake.mutations) == 1
    assert "Not on my list (2)" in client.get("/not-listed").text


def test_add_refusal_inline(client, fake):  # noqa: F811
    response = client.post("/not-listed/fin/add", data={"status": "COMPLETED", "progress": "51"})
    assert response.status_code == 400 and "exceeds AniList" in response.text
    assert 'id="nl-fin"' in response.text
    assert fake.call_count == 0


def test_add_refused_while_sync_running(client, services, fake):  # noqa: F811
    import asyncio

    async def hold():
        await services.orchestrator.lock.acquire()

    client.portal.call(hold)
    try:
        response = client.post("/not-listed/rel/add", data={"status": "CURRENT", "progress": "7"})
    finally:
        client.portal.call(lambda: asyncio.sleep(0))
        services.orchestrator.lock.release()
    assert response.status_code == 409 and "sync is running" in response.text
    assert fake.call_count == 0


def test_nav_points_here(client):
    assert 'href="/not-listed"' in client.get("/").text
