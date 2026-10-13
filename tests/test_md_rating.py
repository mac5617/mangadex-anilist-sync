"""Your MangaDex rating fills in a score that AniList and MyAnimeList don't have yet."""

import re

import pytest
import respx
from fastapi.testclient import TestClient

from mdal.sync.orchestrator import ApprovalError
from mdal.sync.rules import score_from_md
from mdal.web.app import create_app
from tests.factories import FakeAniList, FakeMal, FakeMangaDex, FakeSeries, al_media, connect_mal, seed_diffed_run


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fast(services):
    for q in (services.anilist_queue, services.mangadex_queue, services.mal_queue):
        q.set_interval(0)
    return services


@pytest.mark.parametrize("rating, site, other, expected", [
    (8, None, None, 8),        # neither site has a score
    (8, None, 80, 8),          # the other site already has this one (from MangaDex on an earlier sync)
    (8, None, 70, None),       # the other site has a different score: yours wins, nothing is filled in
    (8, 60, None, None),       # this site has a score
    (None, None, None, None),  # not rated on MangaDex
])
def test_score_from_md(rating, site, other, expected):
    assert score_from_md(rating, site, other) == expected


@pytest.fixture
def world(mock, fast):
    md, al = FakeMangaDex(), FakeAniList()
    al.add_media(
        al_media(1, "Rated Behind", id_mal=11), al_media(2, "Rated Ahead", id_mal=22),
        al_media(3, "Scored On AniList", id_mal=33), al_media(4, "Not Listed", id_mal=44),
        al_media(5, "Scored On MAL", id_mal=55), al_media(6, "Unrated", id_mal=66),
    )
    al.add_list("Reading", [(101, 1, "CURRENT", 5), (102, 2, "CURRENT", 30), (103, 3, "CURRENT", 30, 60),
                            (105, 5, "CURRENT", 30), (106, 6, "CURRENT", 30)])
    md.add(FakeSeries("a", "Rated Behind", links={"al": "1"}, reads=["a1"], rating=8), {"a1": "10"})
    md.add(FakeSeries("b", "Rated Ahead", links={"al": "2"}, reads=["b1"], rating=7), {"b1": "12"})
    md.add(FakeSeries("c", "Scored On AniList", links={"al": "3"}, reads=["c1"], rating=9), {"c1": "12"})
    md.add(FakeSeries("d", "Not Listed", links={"al": "4"}, reads=["d1"], rating=6), {"d1": "3"})
    md.add(FakeSeries("e", "Scored On MAL", links={"al": "5"}, reads=["e1"], rating=5), {"e1": "12"})
    md.add(FakeSeries("f", "Unrated", links={"al": "6"}, reads=["f1"]), {"f1": "12"})
    fast.repo.add_mal_entry({"mal_id": 55, "status": "CURRENT", "progress": 30, "score": 7, "fetched_at": "x"})
    return md.install(mock), al.install(mock)


async def dry_run(services, target="anilist"):
    run_id = await services.orchestrator.start_run(target)
    await services.orchestrator.wait()
    assert services.repo.get_run(run_id)["state"] == "diffed", services.repo.get_run(run_id)["error"]
    return run_id, {i["md_id"]: i for i in services.repo.items(run_id)}


async def test_ratings_are_read_and_offered_where_neither_site_has_a_score(fast, world):
    _, items = await dry_run(fast)
    assert {r["md_id"]: r["rating"] for r in fast.repo.md_manga()} == {"a": 8, "b": 7, "c": 9, "d": 6, "e": 5, "f": None}
    assert (items["a"]["action"], items["a"]["set_score"], items["a"]["score_approved"]) == ("write", 8, 1)
    # Nothing else to change: the row becomes a score-only update.
    assert (items["b"]["action"], items["b"]["set_score"]) == ("write", 7)
    assert items["b"]["reason"] == "rated 7/10 on MangaDex; AniList has no score"
    assert (items["d"]["action"], items["d"]["set_score"]) == ("add", 6)
    for md_id in "cef":   # scored on AniList, scored on MyAnimeList, not rated on MangaDex
        assert items[md_id]["set_score"] is None and items[md_id]["action"] == "skip"


async def test_approved_scores_are_written_and_verified(fast, world):
    _, al = world
    run_id, _ = await dry_run(fast)
    await fast.orchestrator.approve(run_id, ["a", "b", "d"], [], [], ["a", "b", "d"])
    await fast.orchestrator.wait()
    assert fast.repo.get_run(run_id)["state"] == "done", fast.repo.get_run(run_id)["error"]
    sent = {entry_id: args for entry_id, args in al.applied}
    assert sent[101] == {"id": 101, "progress": 10, "scoreRaw": 80}
    assert sent[102] == {"id": 102, "scoreRaw": 70}                       # no progress: AniList is ahead
    assert next(a for a in sent.values() if a.get("mediaId") == 4)["scoreRaw"] == 60
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert all(items[m]["verify_note"] == "verified" for m in "abd")


async def test_unticked_score_is_not_sent(fast, world):
    _, al = world
    run_id, _ = await dry_run(fast)
    with pytest.raises(ApprovalError, match="Nothing selected"):
        await fast.orchestrator.approve(run_id, ["b"], [], [], [])   # score-only row without its score
    await fast.orchestrator.approve(run_id, ["a"], [], [], [])
    await fast.orchestrator.wait()
    assert {e: a for e, a in al.applied}[101] == {"id": 101, "progress": 10}


async def test_a_score_set_meanwhile_is_kept(fast, world):
    _, al = world
    run_id, _ = await dry_run(fast)
    al.entry(101)["score"] = 50
    al.entry(102)["score"] = 90
    await fast.orchestrator.approve(run_id, ["a", "b"], [], [], ["a", "b"])
    await fast.orchestrator.wait()
    assert {e: a for e, a in al.applied} == {101: {"id": 101, "progress": 10}}
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert items["a"]["verify_note"] == "verified"
    assert items["b"]["write_state"] == "dropped"


async def test_myanimelist_gets_the_rating_too(fast, world, mock):
    mal = FakeMal()
    for mal_id in (11, 22, 33):
        mal.add(mal_id, f"MAL {mal_id}")
    mal.listed(11, "reading", 5)
    mal.listed(22, "reading", 30)
    mal.listed(33, "reading", 30, score=6)
    mal.install(mock)
    fast.store_anilist_token("al-token-xyz")
    connect_mal(fast)
    run_id, items = await dry_run(fast, "mal")
    assert (items["a"]["set_score"], items["b"]["set_score"], items["c"]["set_score"]) == (8, 7, None)
    await fast.orchestrator.approve(run_id, ["a", "b"], [], [], ["a", "b"])
    await fast.orchestrator.wait()
    assert dict(mal.patches) == {11: {"num_chapters_read": "10", "score": "8"}, 22: {"score": "7"}}
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert items["a"]["verify_note"] == items["b"]["verify_note"] == "verified"


def test_the_changes_page_offers_the_score(services, mock):
    run_id = seed_diffed_run(services.repo)
    services.repo.update_items(run_id, [("w", {"set_score": 8, "score_approved": 1})])
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.get(f"/sync/{run_id}").text
        assert re.search(rf'name="sc" value="{run_id}:w"\s*checked', html)
        assert "score 8/10" in html and "your MangaDex rating; AniList has none" in html
        assert "1 to score" in html
        assert "1 to score" not in c.post(f"/sync/{run_id}/estimate", data={"sel": ["w"]}).text
        c.post(f"/sync/{run_id}/approve", data={"sel": ["w"], "sc": ["w"]})
    assert {i["md_id"]: i["score_approved"] for i in services.repo.items(run_id)}["w"] == 1
