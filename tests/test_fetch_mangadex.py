import json

import httpx
import pytest
import respx

from mdal.clients.mangadex import API_URL, MangaDexBlocked
from mdal.fetch.mangadex_library import fetch_library
from tests.factories import FakeMangaDex, FakeSeries

ALL_RATINGS = {"safe", "suggestive", "erotica", "pornographic"}


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fast(services):
    """Real client and queue, but no pacing delay so tests run instantly."""
    services.mangadex_queue.set_interval(0)
    return services


def library(n_series: int, reads_per_series: int) -> FakeMangaDex:
    fake = FakeMangaDex()
    for i in range(n_series):
        md_id = f"m{i:04d}"
        chapters = {f"{md_id}-c{j}": str(j + 1) for j in range(reads_per_series)}
        fake.add(FakeSeries(md_id, f"Series {i}", reads=list(chapters)), chapters)
    return fake


async def test_request_counts_for_250_series_and_1000_reads(fast, mock):
    fake = library(250, 4).install(mock)
    summary = await fetch_library(fast.mangadex, fast.repo)
    calls = fake.data_calls()
    assert calls == {"token": 1, "status": 1, "manga": 3, "read": 3, "chapter": 10}
    assert summary.series == 250
    assert summary.read_ids == 1000
    assert summary.chapters_resolved == 1000


async def test_second_run_makes_no_chapter_requests(fast, mock):
    fake = library(30, 5).install(mock)
    await fetch_library(fast.mangadex, fast.repo)
    first = fake.data_calls()["chapter"]
    await fetch_library(fast.mangadex, fast.repo)
    assert fake.data_calls()["chapter"] == first


async def test_only_new_read_ids_are_resolved(fast, mock):
    fake = library(3, 2).install(mock)
    await fetch_library(fast.mangadex, fast.repo)
    fake.chapters["m0000-new"] = {"chapter": "3", "volume": None, "manga": "m0000"}
    fake.series["m0000"].reads.append("m0000-new")
    summary = await fetch_library(fast.mangadex, fast.repo)
    assert summary.chapters_resolved == 1
    last = fake.routes["chapter"].calls.last.request.url.params.get_list("ids[]")
    assert last == ["m0000-new"]


async def test_filters_overridden_on_every_call(fast, mock):
    fake = library(5, 2)
    fake.series["m0001"].content_rating = "pornographic"
    fake.chapters["m0002-c0"]["unavailable"] = True
    fake.install(mock)
    await fetch_library(fast.mangadex, fast.repo)

    for name in ("manga", "chapter"):
        for call in fake.routes[name].calls:
            params = call.request.url.params
            assert set(params.get_list("contentRating[]")) == ALL_RATINGS
            assert params["limit"] == "100"
    for call in fake.routes["chapter"].calls:
        assert call.request.url.params["includeUnavailable"] == "1"
    for call in fake.routes["read"].calls:
        assert call.request.url.params["grouped"] == "true"
        assert "limit" not in call.request.url.params

    assert {r["md_id"] for r in fast.repo.md_manga()} == {f"m000{i}" for i in range(5)}
    unavailable = [r for r in fast.repo.read_chapters("m0002") if r["chapter_id"] == "m0002-c0"][0]
    assert unavailable["chapter"] == "1"


async def test_links_and_attributes_stored(fast, mock):
    fake = FakeMangaDex()
    fake.add(FakeSeries("good", "Good", links={"al": "12345", "mal": "678"}, alt_titles=[{"ja": "グッド"}, {"en": "Good"}],
                        numbers_reset=True, last_chapter="120.5", pub_status="completed"))
    fake.add(FakeSeries("bad", "Bad", links={"al": "abc"}))
    fake.add(FakeSeries("none", "None", links=[]))  # MangaDex sends [] for empty maps
    fake.install(mock)
    await fetch_library(fast.mangadex, fast.repo)
    rows = {r["md_id"]: r for r in fast.repo.md_manga()}

    assert json.loads(rows["good"]["links"]) == {"al": "12345", "mal": "678"}
    assert json.loads(rows["bad"]["links"]) == {"al": "abc"}
    assert json.loads(rows["none"]["links"]) == {}
    assert json.loads(rows["good"]["alt_titles"]) == ["グッド"]  # duplicate of primary dropped
    assert rows["good"]["chapter_numbers_reset"] == 1
    assert rows["good"]["last_chapter"] == "120.5"
    assert rows["good"]["pub_status"] == "completed"
    assert json.loads(rows["good"]["authors"]) == ["Author A"]
    assert rows["good"]["cover_file"] == "cover.jpg"
    assert rows["good"]["links_hash"] != rows["none"]["links_hash"]


async def test_null_chapter_and_missing_ids(fast, mock):
    fake = FakeMangaDex()
    fake.add(FakeSeries("m1", "One", reads=["c-null", "c-gone", "c-ok"]), {"c-null": None, "c-ok": "4", "c-gone": "9"})
    fake.hidden_chapters.add("c-gone")
    fake.install(mock)

    summary = await fetch_library(fast.mangadex, fast.repo)
    state = fast.repo.chapter_cache_state()
    assert state == {"c-null": 0, "c-ok": 0, "c-gone": 1}
    assert summary.chapters_missing == 1
    rows = {r["chapter_id"]: r for r in fast.repo.read_chapters("m1")}
    assert rows["c-null"]["chapter"] is None

    # retried once on the next sync, then permanently missing and never requested again
    await fetch_library(fast.mangadex, fast.repo)
    assert fake.routes["chapter"].calls.last.request.url.params.get_list("ids[]") == ["c-gone"]
    assert fast.repo.chapter_cache_state()["c-gone"] == 2
    before = fake.routes["chapter"].call_count
    await fetch_library(fast.mangadex, fast.repo)
    assert fake.routes["chapter"].call_count == before


async def test_missing_id_that_reappears_is_resolved(fast, mock):
    fake = FakeMangaDex()
    fake.add(FakeSeries("m1", "One", reads=["c1"]), {"c1": "7"})
    fake.hidden_chapters.add("c1")
    fake.install(mock)
    await fetch_library(fast.mangadex, fast.repo)
    fake.hidden_chapters.clear()
    await fetch_library(fast.mangadex, fast.repo)
    assert fast.repo.chapter_cache_state()["c1"] == 0


async def test_single_manga_ungrouped_response_is_accepted(fast, mock):
    fake = FakeMangaDex()
    fake.add(FakeSeries("solo", "Solo", reads=["c1"]), {"c1": "1"})
    fake.install(mock)
    fake.routes["read"].mock(side_effect=lambda req: httpx.Response(200, json={"result": "ok", "data": ["c1"]}))
    await fetch_library(fast.mangadex, fast.repo)
    assert [r["chapter_id"] for r in fast.repo.read_chapters("solo")] == ["c1"]


async def test_blocked_mid_fetch_keeps_completed_steps(fast, mock):
    fake = library(3, 2).install(mock)
    await fetch_library(fast.mangadex, fast.repo)  # populates the chapter cache
    cached = fast.repo.chapter_count()
    fake.series["m0000"].reads.append("m0000-new")
    fake.chapters["m0000-new"] = {"chapter": "9", "volume": None, "manga": "m0000"}
    fake.routes["chapter"].mock(side_effect=lambda req: httpx.Response(403))

    with pytest.raises(MangaDexBlocked):
        await fetch_library(fast.mangadex, fast.repo)
    assert fast.repo.chapter_count() == cached
    assert "m0000-new" in [r["chapter_id"] for r in fast.repo.read_chapters("m0000")]


async def test_empty_library(fast, mock):
    FakeMangaDex().install(mock)
    summary = await fetch_library(fast.mangadex, fast.repo)
    assert summary.series == 0
