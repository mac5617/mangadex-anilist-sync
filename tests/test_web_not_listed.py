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
    assert "Unlisted series (3)" in html
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
    assert "Unlisted series (2)" in client.get("/not-listed").text


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


def test_series_page_adds_to_anilist(client, services, fake):  # noqa: F811
    page = client.get("/series/al/3").text
    assert "Add to AniList" in page and 'id="add-progress"' in page and 'value="7"' in page
    response = client.post("/series/al/3/add", data={"status": "COMPLETED", "progress": "7"})
    assert response.status_code == 200 and "Added to AniList as Completed, progress 7." in response.text
    assert len(fake.mutations) == 1 and "On your list" in response.text      # the panel now edits the entry
    assert "Unlisted series (2)" in client.get("/not-listed").text
    refused = client.post("/series/al/2/add", data={"status": "COMPLETED", "progress": "51"}).text
    assert "exceeds AniList" in refused and len(fake.mutations) == 1


def test_series_page_add_goes_to_myanimelist_too(client, services, fake, mock):  # noqa: F811
    from tests.factories import al_media_row, connect_mal
    from tests.test_mal_mirror import FakeMal
    from mdal.clients.myanimelist import API_URL as MAL_URL

    services.mal_queue.set_interval(0)
    connect_mal(services)
    services.repo.set_setting("sync_targets", ["anilist", "mal"])
    services.repo.upsert_media([al_media_row(3, "Releasing", id_mal=703)])
    mal = FakeMal(set())
    mock.get(url__regex=rf"{MAL_URL}/manga/\d+(\?|$)").mock(side_effect=mal.get)
    mock.patch(url__regex=rf"{MAL_URL}/manga/\d+/my_list_status").mock(side_effect=mal.patch)

    assert "Add to AniList and MyAnimeList" in client.get("/series/al/3").text
    done = client.post("/series/al/3/add", data={"status": "CURRENT", "progress": "7"}).text
    assert "Added to AniList as Reading, progress 7. Added to MyAnimeList too, progress 7." in done
    assert mal.edits == [(703, {"status": "reading", "num_chapters_read": "7"})]


def test_listed_series_can_be_added_to_myanimelist(client, services, fake, mock):  # noqa: F811
    from tests.factories import al_media_row, connect_mal
    from tests.test_mal_mirror import FakeMal
    from mdal.clients.myanimelist import API_URL as MAL_URL

    services.mal_queue.set_interval(0)
    connect_mal(services)
    services.repo.set_setting("sync_targets", ["anilist", "mal"])
    services.repo.upsert_media([al_media_row(1, "Listed", id_mal=701)])       # on AniList (CURRENT, 1), not on MAL
    mal = FakeMal(set())
    mock.get(url__regex=rf"{MAL_URL}/manga/\d+(\?|$)").mock(side_effect=mal.get)
    mock.patch(url__regex=rf"{MAL_URL}/manga/\d+/my_list_status").mock(side_effect=mal.patch)

    page = client.get("/series/al/1").text
    assert "Add to MyAnimeList" in page and "adds it as Reading at ch. 1" in page
    done = client.post("/series/al/1/add-mal").text
    assert "Added to MyAnimeList as Reading, progress 1." in done and "Add to MyAnimeList</button>" not in done
    assert mal.edits == [(701, {"status": "reading", "num_chapters_read": "1"})]
    services.repo.set_setting("sync_targets", ["anilist"])                    # MAL not part of Sync: no button
    assert client.post("/series/al/1/add-mal").status_code == 404
