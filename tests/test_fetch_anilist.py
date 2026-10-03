import json

import pytest
import respx

from mdal.fetch.anilist_list import fetch_list, media_by_ids, media_by_mal_ids, search_batch, viewer
from tests.factories import FakeAniList, al_media


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fast(services):
    services.anilist_queue.set_interval(0)
    return services


@pytest.fixture
def fake(mock):
    f = FakeAniList()
    f.add_media(
        al_media(1, "Sousou no Frieren", english="Frieren", id_mal=126287, chapters=None),
        al_media(2, "Berserk", id_mal=2, status="RELEASING"),
        al_media(3, "Hidden One", id_mal=3),
        al_media(4, "Kusuriya no Hitorigoto", fmt="NOVEL", id_mal=4, staff=["Natsu Hyuuga"]),
        al_media(5, "Kusuriya no Hitorigoto", fmt="MANGA", id_mal=4, staff=["Natsu Hyuuga", "Nekokurage"]),
    )
    return f.install(mock)


async def test_list_dedupes_across_status_and_custom_lists(fast, fake):
    fake.add_list("Reading", [(10, 1, "CURRENT", 120), (11, 2, "CURRENT", 350)])
    fake.add_list("Favourites", [(10, 1, "CURRENT", 120)], custom=True)
    fake.add_list("Secret", [(12, 3, "PAUSED", 7)], custom=True)  # hidden from status lists

    summary = await fetch_list(fast.anilist, fast.repo, user_id=1)

    assert fake.call_count == 1
    assert summary.entries == 3
    assert summary.custom_only == 1
    entries = fast.repo.al_entries()
    assert set(entries) == {1, 2, 3}
    assert entries[3]["status"] == "PAUSED" and entries[3]["progress"] == 7
    assert fake.requests[0]["variables"] == {"userId": 1}


async def test_list_snapshot_is_replaced(fast, fake):
    fake.add_list("Reading", [(10, 1, "CURRENT", 1)])
    await fetch_list(fast.anilist, fast.repo, 1)
    fake.lists.clear()
    fake.add_list("Reading", [(11, 2, "CURRENT", 1)])
    await fetch_list(fast.anilist, fast.repo, 1)
    assert set(fast.repo.al_entries()) == {2}


async def test_cached_ids_cost_nothing(fast, fake):
    fake.add_list("Reading", [(10, 1, "CURRENT", 1), (11, 2, "CURRENT", 1)])
    await fetch_list(fast.anilist, fast.repo, 1)
    before = fake.call_count
    found = await media_by_ids(fast.anilist, fast.repo, [1, 2])
    assert fake.call_count == before
    assert set(found) == {1, 2}


async def test_120_unknown_ids_take_3_requests(fast, fake):
    for i in range(1000, 1120):
        fake.add_media(al_media(i, f"Series {i}"))
    found = await media_by_ids(fast.anilist, fast.repo, list(range(1000, 1120)))
    assert fake.call_count == 3
    assert len(found) == 120
    assert all(len(r["variables"]["ids"]) <= 50 and r["variables"]["perPage"] == 50 for r in fake.requests)


async def test_absent_or_non_manga_ids_are_invalid(fast, fake):
    fake.add_media(al_media(900, "An Anime", type_="ANIME"))
    found = await media_by_ids(fast.anilist, fast.repo, [1, 900, 99999])
    assert set(found) == {1}


async def test_mal_lookup_returns_all_sharing_media(fast, fake):
    found = await media_by_mal_ids(fast.anilist, fast.repo, [4, 126287, 555])
    assert fake.call_count == 1
    assert sorted(r["media_id"] for r in found[4]) == [4, 5]
    assert [r["media_id"] for r in found[126287]] == [1]
    assert 555 not in found
    # second call is served from cache
    await media_by_mal_ids(fast.anilist, fast.repo, [4])
    assert fake.call_count == 1


async def test_search_batches_and_keeps_order(fast, fake):
    titles = ["Berserk", "Frieren", "nothing-matches", "Kusuriya"] + [f"none {i}" for i in range(8)]
    results = await search_batch(fast.anilist, fast.repo, titles, batch=5)
    assert fake.call_count == 3
    assert len(results) == 12
    assert [m["id"] for m in results[0]] == [2]
    assert [m["id"] for m in results[1]] == [1]
    assert results[2] == []
    assert sorted(m["id"] for m in results[3]) == [4, 5]


async def test_search_titles_travel_as_variables(fast, fake):
    nasty = 'Title "with" } braces { and quotes'
    await search_batch(fast.anilist, fast.repo, [nasty], batch=5)
    body = fake.requests[-1]
    assert body["variables"] == {"q0": nasty}
    assert nasty not in body["query"]


async def test_search_caches_staff_and_list_fetch_keeps_it(fast, fake):
    await search_batch(fast.anilist, fast.repo, ["Kusuriya"], batch=5)
    assert json.loads(fast.repo.media([5])[5]["staff"]) == ["Natsu Hyuuga", "Nekokurage"]
    fake.add_list("Reading", [(20, 5, "CURRENT", 3)])
    await fetch_list(fast.anilist, fast.repo, 1)  # list query does not ask for staff
    assert json.loads(fast.repo.media([5])[5]["staff"]) == ["Natsu Hyuuga", "Nekokurage"]


async def test_viewer(fast, fake):
    assert await viewer(fast.anilist) == {"id": 1, "name": "Reader"}
