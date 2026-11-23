"""For you moves on: each Ask the model brings titles you haven't been shown."""

import json

import pytest
import respx
from fastapi.testclient import TestClient

from mdal.backup import restore
from mdal.recommend.llm import PICKS
from mdal.recommend.profile import build_profile
from mdal.recommend.rotation import GOOD_MATCH, also_worth, past_picks, remember, shortlist
from mdal.recommend.score import Rec
from mdal.web.app import create_app

OLLAMA = "http://127.0.0.1:11434"


def rec(media_id, overall=0.8):
    return Rec(media_id=media_id, title=f"Series {media_id}", url=None, cover=None, year=2020, format="MANGA", country="JP",
               chapters=None, status="FINISHED", genres=[], tags=[], staff=[], mean_score=80, popularity=1, is_adult=False,
               sources=[], scores={"overall": overall})


def pool(n, start=1):
    """Good matches, best first."""
    return [rec(start + i, 0.9 - i * 0.001) for i in range(n)]


def ids(recs):
    return [r.media_id for r in recs]


# ---- the shortlist ------------------------------------------------------------------------------


def test_the_shortlist_is_the_best_titles_you_have_not_seen():
    assert ids(shortlist(pool(60), [1, 2, 3], 45)) == list(range(4, 49))


def test_titles_below_the_bar_wait_while_good_ones_remain():
    recs = pool(20) + [rec(99, GOOD_MATCH - 0.01)]
    assert 99 not in ids(shortlist(recs, [], 45))


def test_when_good_titles_run_out_the_oldest_picks_return_first():
    recs = pool(30)
    past = list(range(30, 4, -1))         # 30 was picked longest ago; 1 to 4 are still unseen
    chosen = ids(shortlist(recs, past, 45))
    assert chosen[:4] == [1, 2, 3, 4] and chosen[4:8] == [30, 29, 28, 27]
    assert len(chosen) == PICKS * 2 and 5 not in chosen    # the most recent picks stay out


def test_a_small_pool_still_offers_what_it_has():
    recs = [rec(1, 0.8), rec(2, 0.2), rec(3, 0.1)]
    assert ids(shortlist(recs, [], 45)) == [1, 2, 3]


# ---- what has been picked ----------------------------------------------------------------------


def test_a_returning_pick_becomes_the_newest():
    assert remember([1, 2, 3], [2, 9]) == [1, 3, 2, 9]


def test_the_picks_from_before_rotation_count_as_seen():
    llm = {"picks": [{"id": 7, "reason": "x"}, {"id": 8, "reason": "y"}]}
    assert past_picks(None, llm) == [7, 8]


def test_starting_over_is_not_undone_by_the_stored_picks():
    assert past_picks([], {"picks": [{"id": 7, "reason": "x"}]}) == []


@pytest.mark.parametrize("stored", ["x", 3, {"a": 1}, [1, "2"], [True]])
def test_a_damaged_history_is_read_as_the_stored_picks(stored):
    assert past_picks(stored, {"picks": [{"id": 7, "reason": "x"}]}) == [7]


@pytest.mark.parametrize("llm", ["x", ["x"], {"picks": [1]}, {"picks": "ab"}, {"picks": [{"id": "7"}]}, None])
def test_damaged_stored_picks_count_as_none(llm):
    assert past_picks(None, llm) == []


def test_another_edition_of_a_picked_title_is_not_new():
    recs = pool(30)
    recs[1].title = recs[0].title
    assert 2 not in ids(shortlist(recs, [1], 45))


def test_two_editions_are_offered_once():
    recs = pool(30)
    recs[1].title = recs[0].title
    assert 2 not in ids(shortlist(recs, [], 45))


def test_the_longer_list_keeps_to_good_matches():
    recs = pool(5) + [rec(99, GOOD_MATCH - 0.01)]
    assert ids(also_worth(recs, [1], {2})) == [3, 4, 5]


# ---- asking the model again ---------------------------------------------------------------------


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def candidates(services, monkeypatch):
    recs = pool(60)
    monkeypatch.setattr(services.recommender, "recs", lambda include_adult=False: (build_profile([]), recs))
    return recs


def answers(mock, count=PICKS):
    mock.get(f"{OLLAMA}/api/tags").respond(200, json={"models": [{"name": "gpt-oss:20b", "capabilities": ["completion"]}]})
    picks = [{"n": n, "reason": "Shares the slow dread you liked."} for n in range(1, count + 1)]
    return mock.post(f"{OLLAMA}/api/chat").respond(200, json={"message": {"content": json.dumps({"summary": "You.", "picks": picks})}})


async def ask(services):
    services.recommender.start_llm()
    await services.recommender.task
    return [p["id"] for p in services.repo.get_setting("rec_llm")["picks"]]


async def test_each_ask_brings_new_titles(services, candidates, mock):
    answers(mock)
    first, second, third = [await ask(services) for _ in range(3)]
    assert not set(first) & set(second) and not set(second) & set(third) and not set(first) & set(third)
    assert services.repo.get_setting("rec_past") == first + second + third


async def test_your_current_picks_are_out_of_the_very_next_ask(services, candidates, mock):
    answers(mock)
    services.repo.set_setting("rec_llm", {"model": "m", "summary": "s", "picks": [{"id": 1, "reason": "Your top match."}]})
    assert 1 not in await ask(services)


async def test_a_failed_answer_uses_up_nothing(services, candidates, mock):
    answers(mock, count=2)
    services.recommender.start_llm()
    await services.recommender.task
    assert services.repo.get_setting("rec_past") is None
    assert "only 2 usable picks" in services.repo.get_setting("rec_llm")["error"]


async def test_the_status_says_when_earlier_picks_come_back(services, candidates, mock):
    answers(mock)
    assert "earlier picks" not in (await ask(services) and services.recommender.status()["detail"])
    services.repo.set_setting("rec_past", ids(candidates))
    await ask(services)
    assert "earlier picks" in services.recommender.status()["detail"]


# ---- the page -----------------------------------------------------------------------------------


def test_the_longer_list_skips_earlier_picks(services, candidates):
    services.repo.set_setting("rec_llm", {"model": "m", "summary": "s", "picks": [{"id": 20, "reason": "A fine current pick."}]})
    services.repo.set_setting("rec_past", [1, 2, 20])
    with TestClient(create_app(services)) as c:
        html = c.get("/discover").text
    assert "Series 20" in html and "Series 3<" in html
    assert "Series 1<" not in html and "Series 2<" not in html


def test_start_over_brings_earlier_picks_back(services, candidates):
    services.repo.set_setting("rec_past", [1, 2])
    with TestClient(create_app(services), follow_redirects=False) as c:
        assert "Start over" in c.get("/discover").text
        response = c.post("/discover-start-over")
        assert response.status_code == 204 and response.headers["HX-Refresh"] == "true"
        html = c.get("/discover").text
    assert services.repo.get_setting("rec_past") == [] and "Series 1<" in html and "Start over" not in html


def test_the_page_is_never_empty_once_everything_was_picked(services, candidates):
    services.repo.set_setting("rec_past", ids(candidates))
    with TestClient(create_app(services)) as c:
        assert "Series 1<" in c.get("/discover").text


def test_top_matches_fill_the_page_late_in_the_rotation(services, candidates):
    services.repo.set_setting("rec_past", ids(candidates)[:-1])
    with TestClient(create_app(services)) as c:
        html = c.get("/discover").text
    assert "Series 60<" in html and "Series 1<" in html and "Series 11<" in html


@pytest.mark.parametrize("llm", ["x", ["x"], {"picks": [1]}])
def test_damaged_stored_picks_leave_discover_working(services, candidates, llm):
    services.repo.set_setting("rec_llm", llm)
    with TestClient(create_app(services)) as c:
        assert c.get("/discover").status_code == 200 and c.get("/discover/tags").status_code == 200


# ---- backups ------------------------------------------------------------------------------------


def test_picks_from_an_older_backup_count_as_seen(services):
    services.repo.set_setting("rec_past", [5])
    llm = {"model": "m", "summary": "s", "picks": [{"id": 7, "reason": "From the backup."}]}
    restore(services.repo, {"format": "shiori-backup", "tables": {}, "settings": {"rec_llm": llm}})
    assert services.repo.get_setting("rec_past") == [5, 7]




@pytest.mark.parametrize("bad", ["x", 3, {"a": 1}, [1, "2"], [True]])
def test_a_bad_restored_history_keeps_yours(services, bad):
    services.repo.set_setting("rec_past", [5])
    restored = restore(services.repo, {"format": "shiori-backup", "tables": {}, "settings": {"rec_past": bad}})
    assert services.repo.get_setting("rec_past") == [5] and restored.kept == ["rec past"]
