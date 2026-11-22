"""List edits repeated on MyAnimeList: scores from ranking, Paused/Dropped from Stalled."""

import time
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mdal.clients.anilist import ANILIST_URL
from mdal.clients.myanimelist import API_URL as MAL_URL
from mdal.scores import to_mal
from mdal.web.app import create_app
from tests.factories import al_media_row, connect_mal
from tests.test_list import FakeLists

DAY = 86400


def test_mal_scores_are_whole_numbers():
    assert [to_mal(s) for s in (0.1, 6.8, 8.4, 8.5, 10)] == [1, 7, 8, 8, 10]   # 8.5 rounds half to even: 8


class FakeMal:
    """Entries on the MAL list answer my_list_status; edits are recorded."""

    def __init__(self, listed):
        self.listed = set(listed)
        self.edits = []

    def get(self, request):
        mal_id = int(request.url.path.rsplit("/", 1)[-1])
        if mal_id in self.listed:
            return httpx.Response(200, json={"id": mal_id, "my_list_status": {"status": "reading", "score": 0}})
        return httpx.Response(200, json={"id": mal_id})

    def patch(self, request):
        mal_id = int(request.url.path.split("/")[-2])
        self.edits.append((mal_id, {k: v[0] for k, v in parse_qs(request.content.decode()).items()}))
        return httpx.Response(200, json={})


@pytest.fixture
def setup(services):
    services.store_anilist_token("al-token-xyz")
    services.anilist_queue.set_interval(0)
    services.mal_queue.set_interval(0)
    connect_mal(services)
    now = int(time.time())
    services.repo.upsert_media([al_media_row(i, f"Series {i}", id_mal=500 + i, status="FINISHED", chapters=100) for i in range(1, 5)])
    services.repo.replace_al_entries([
        {"entry_id": 100 + i, "media_id": i, "status": "CURRENT", "progress": 10 * i, "score": None,
         "updated_at": now - 200 * DAY, "fetched_at": "x"} for i in range(1, 5)])
    # series 4 is not on the MyAnimeList list
    services.repo.replace_mal_entries([{"mal_id": 500 + i, "status": "CURRENT", "progress": 10 * i, "score": 8 if i == 3 else None,
                                        "fetched_at": "x"} for i in range(1, 4)])
    fake_al, fake_mal = FakeLists(), FakeMal({501, 502, 503})
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(ANILIST_URL).mock(side_effect=fake_al.handle)
        router.get(url__regex=rf"{MAL_URL}/manga/\d+(\?|$)").mock(side_effect=fake_mal.get)
        router.patch(url__regex=rf"{MAL_URL}/manga/\d+/my_list_status").mock(side_effect=fake_mal.patch)
        yield services, fake_mal


def test_quick_scores_go_to_both_sites(setup):
    services, mal = setup
    with TestClient(create_app(services)) as c:
        done = c.post("/list/rank/score", data={"media_id": 1, "score": "8.4"}).text
        assert "Series 1: Saved 8.4/10 to AniList and MyAnimeList." in done
        assert mal.edits == [(501, {"score": "8"})]
        assert services.repo.mal_entries()[501]["score"] == 8
    # which scores each site sends, and the notes for them: tests/test_your_list.py


def test_ranking_saves_repeat_on_mal_in_the_background(setup):
    services, mal = setup
    with TestClient(create_app(services)) as c:
        c.post("/list/rank/tier", data={"media_id": 1, "tier": "liked"})
        c.post("/list/rank/tier", data={"media_id": 2, "tier": "fine"})
        c.get("/list/rank-status")                                                # let the background save finish
    assert sorted(mal.edits) == [(501, {"score": "10"}), (502, {"score": "7"})]
    assert "on MyAnimeList" in services.repo.get_setting("rank_save")["detail"]


def test_stalled_status_goes_to_both_sites_and_the_switch_turns_it_off(setup):
    services, mal = setup
    with TestClient(create_app(services)) as c:
        done = c.post("/list/status/2", data={"status": "DROPPED"}).text
        assert "Marked Dropped on AniList and MyAnimeList." in done
        assert mal.edits == [(502, {"status": "dropped"})]
        assert services.repo.mal_entries()[502]["status"] == "DROPPED"

        assert "List edits now only go to AniList" in c.post("/settings/mal-mirror", data={}).text
        assert "Marked Paused on AniList." in c.post("/list/status/1", data={"status": "PAUSED"}).text
        assert len(mal.edits) == 1                                                  # switched off: MAL untouched
        assert "now also go to MyAnimeList" in c.post("/settings/mal-mirror", data={"on": "1"}).text


def test_scores_saved_before_mirroring_catch_up_on_mal(setup):
    """Scores Shiori put on AniList earlier reach MyAnimeList as soon as Shiori starts (e.g. series added to
    MyAnimeList since); your own MAL scores for series Shiori never scored are left alone."""
    services, mal = setup
    services.repo.update_al_entry(1, score=84)
    services.repo.log_edit(1, 101, "score", None, 84, "done")          # Shiori set this one (before mirroring)
    services.repo.update_al_entry(2, score=50)                         # you set this one yourself on AniList
    assert services.ranker.pending_mal() == {1: 84}
    with TestClient(create_app(services)) as c:                       # no page needed: startup catches up
        c.get("/list/rank-status")
    assert mal.edits == [(501, {"score": "8"})]
    assert services.ranker.pending_mal() == {}
    assert services.ranker.status()["detail"] == "1 on MyAnimeList"


def test_series_header_merges_lists_that_agree(setup):
    services, _ = setup
    services.repo.replace_mal_entries([
        {"mal_id": 501, "status": "CURRENT", "progress": 10, "score": None, "fetched_at": "x"},     # same as AniList
        {"mal_id": 502, "status": "COMPLETED", "progress": 18, "score": 9, "fetched_at": "x"}])    # differs
    with TestClient(create_app(services)) as c:
        same = c.get("/series/al/1").text
        assert "On AniList and MyAnimeList</span> Reading · 10 ch read" in same
        differs = c.get("/series/al/2").text
        assert "On AniList</span> Reading · 20 ch read" in differs
        assert "On MyAnimeList</span> Completed · 18 ch read · scored 9" in differs
        only_al = c.get("/series/al/4").text                                       # not on the MAL list
        assert "On AniList</span>" in only_al and "On MyAnimeList" not in only_al
        stalled = c.get("/list/stalled").text                                      # edits go to both: copy says so
        assert "updates AniList and MyAnimeList" in stalled and "Dropped on AniList and MyAnimeList?" in stalled
