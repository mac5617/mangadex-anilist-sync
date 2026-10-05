"""List: ranking (compare two at a time), scores saved to AniList, stalled series, ready to binge."""

import json
import re
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mdal.clients.anilist import ANILIST_URL
from mdal.listtools import binge, stalled
from mdal.recommend import ranking
from mdal.sync.list_edit import ListEditError, status_document
from mdal.web.app import create_app
from tests.factories import al_media_row

DAY = 86400


# ---- pure ---------------------------------------------------------------------------------------


def test_scores_spread_evenly_within_each_tier():
    s = ranking.scores([(1, "liked"), (2, "liked"), (3, "liked"), (4, "fine"), (5, "disliked"), (6, "disliked")])
    assert s == {1: 10.0, 2: 8.4, 3: 6.8, 4: 6.7, 5: 3.9, 6: 0.0}
    assert ranking.to_anilist(0.0) == 1 and ranking.to_anilist(8.4) == 84
    assert ranking.tier_for(85) == "liked" and ranking.tier_for(50) == "fine" and ranking.tier_for(None) is None


def test_binary_search_finds_the_place_in_few_questions():
    tier = list(range(16))                      # existing ranked series 0..15, best first
    secret = 9.5                                # the new series belongs between 9 and 10
    search, asked = ranking.start(len(tier)), 0
    while not search.done:
        search = search.answer(secret < tier[search.pivot])   # "better" when it beats the pivot
        asked += 1
    assert search.low == 10 and asked <= 5
    assert ranking.Search(3, 9).answer(None) == ranking.Search(6, 6)
    assert ranking.position_at([0, 1, 2], 1) == 0.5 and ranking.position_at([0, 1], 5) == 2


def test_queue_puts_unscored_and_most_read_first():
    entries = [{"media_id": 1, "status": "COMPLETED", "score": 80, "progress": 100},
               {"media_id": 2, "status": "COMPLETED", "score": None, "progress": 10},
               {"media_id": 3, "status": "CURRENT", "score": None, "progress": 50},
               {"media_id": 4, "status": "PLANNING", "score": None, "progress": 0},
               {"media_id": 5, "status": "DROPPED", "score": None, "progress": 99}]
    assert [e["media_id"] for e in ranking.queue(entries, ranked={1}, skipped=[5])] == [3, 2, 5]


def test_stalled_and_binge():
    now = 1_000 * DAY
    entries = [
        {"media_id": 1, "status": "CURRENT", "updated_at": now - 200 * DAY, "pub": "FINISHED", "chapters": 50, "progress": 20, "score": None},
        {"media_id": 2, "status": "CURRENT", "updated_at": now - 10 * DAY, "pub": "FINISHED", "chapters": 50, "progress": 20, "score": None},
        {"media_id": 3, "status": "PAUSED", "updated_at": now - 5 * DAY, "pub": "FINISHED", "chapters": 30, "progress": 25, "score": 90},
        {"media_id": 4, "status": "DROPPED", "updated_at": now, "pub": "RELEASING", "chapters": None, "progress": 3, "score": None},
        {"media_id": 5, "status": "PAUSED", "updated_at": now, "pub": "FINISHED", "chapters": 10, "progress": 10, "score": None},
    ]
    assert [e["media_id"] for e in stalled(entries, 90, now)] == [1]
    ready = binge(entries, 90, {"al:4": {"completed": True}}, now)
    assert [(e["media_id"], e["left"]) for e in ready] == [(3, 5), (1, 30), (4, None)]


def test_list_edit_only_sends_paused_or_dropped():
    assert "status: PAUSED" in status_document("PAUSED")
    with pytest.raises(ListEditError):
        status_document("COMPLETED")


# ---- pages, with a fake AniList that records what's sent ----------------------------------------------


class FakeLists:
    def __init__(self):
        self.sent = []

    def handle(self, request):
        body = json.loads(request.content)
        self.sent.append(body)
        data = {}
        v = body["variables"]
        if "scoreRaw" in body["query"]:
            for i in range(len(v) // 2):
                data[f"m{i}"] = {"id": v[f"e{i}"], "mediaId": 0, "status": "COMPLETED", "score": v[f"s{i}"]}
        else:
            status = re.search(r"status: (\w+)", body["query"]).group(1)
            data["SaveMediaListEntry"] = {"id": v["e"], "mediaId": 0, "status": status, "score": 0}
        return httpx.Response(200, json={"data": data})


@pytest.fixture
def lists(services):
    services.store_anilist_token("al-token-xyz")
    services.anilist_queue.set_interval(0)
    now = int(time.time())
    media = [al_media_row(i, f"Series {i}", status="FINISHED", chapters=100) for i in range(1, 7)]
    services.repo.upsert_media(media)
    services.repo.replace_al_entries([
        {"entry_id": 100 + i, "media_id": i, "status": "COMPLETED" if i < 5 else "CURRENT", "progress": 10 * i,
         "score": None, "updated_at": now - (200 * DAY if i == 6 else DAY), "fetched_at": "2026-10-04T00:00:00+00:00"}
        for i in range(1, 7)])
    fake = FakeLists()
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(ANILIST_URL).mock(side_effect=fake.handle)
        yield fake


def test_ranking_a_series_by_comparison_saves_scores(services, lists):
    with TestClient(create_app(services), follow_redirects=False) as c:
        page = c.get("/list/rank").text
        assert "How was it?" in page and "Series 6" in page          # unscored, most read first
        first = c.post("/list/rank/tier", data={"media_id": 4, "tier": "liked"}).text
        assert "#1 of 1" in first and "10.0" in first                # first in its tier: no questions
        second = c.post("/list/rank/tier", data={"media_id": 3, "tier": "liked"}).text
        assert "Which did you like more?" in second and "Series 4" in second
        done = c.post("/list/rank/compare", data={"media_id": 3, "tier": "liked", "low": 0, "high": 1, "answer": "better"}).text
        assert "#1 of 2" in done and "10.0" in done
    # the background save sent both scores (series 3 now 10.0, series 4 moved down to 6.8), by entry id
    sent = [b for b in lists.sent if "scoreRaw" in b["query"]]
    assert sent and "mediaId:" not in sent[-1]["query"]
    entries = services.repo.al_entries()
    assert entries[3]["score"] == 100 and entries[4]["score"] == 68
    assert [r["media_id"] for r in services.repo.ranking()] == [3, 4]
    assert services.repo.list_edits()[0]["state"] == "done"


def test_quick_score_and_bad_input(services, lists):
    with TestClient(create_app(services), follow_redirects=False) as c:
        ok = c.post("/list/rank/score", data={"media_id": 2, "score": "8.5"}).text
        assert "Saved 8.5/10 for Series 2" in ok and services.repo.al_entries()[2]["score"] == 85
        assert "number from 0.1 to 10" in c.post("/list/rank/score", data={"media_id": 2, "score": "11"}).text
        assert c.post("/list/rank/tier", data={"media_id": 999, "tier": "liked"}).status_code == 404
        assert c.post("/list/rank/compare", data={"media_id": 2, "tier": "liked", "low": 5, "high": 9,
                                                   "answer": "better"}).status_code == 400


def test_stalled_page_pauses_on_anilist(services, lists):
    with TestClient(create_app(services), follow_redirects=False) as c:
        page = c.get("/list/stalled").text
        assert "Series 6" in page and "Series 5" not in page
        done = c.post("/list/status/6", data={"status": "PAUSED"}).text
        assert "Marked Paused on AniList" in done and services.repo.al_entries()[6]["status"] == "PAUSED"
        assert c.post("/list/status/6", data={"status": "COMPLETED"}).status_code == 400
        assert "Series 6" in c.get("/list/binge").text               # finished, 30 chapters left
