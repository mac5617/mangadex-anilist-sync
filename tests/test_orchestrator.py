import httpx
import pytest
import respx

from mdal.clients.mangadex import API_URL
from mdal.sync.orchestrator import SyncAlreadyRunning, SyncStateError
from tests.factories import FakeAniList, FakeMangaDex, FakeSeries, al_media


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fast(services):
    services.anilist_queue.set_interval(0)
    services.mangadex_queue.set_interval(0)
    return services


@pytest.fixture
def world(mock):
    md, al = FakeMangaDex(), FakeAniList()
    al.add_media(
        al_media(1, "Sousou no Frieren", id_mal=11, chapters=None, status="RELEASING", staff=["Author A"]),
        al_media(2, "Berserk", id_mal=22, chapters=None, staff=["Author A"]),
        al_media(3, "Finished Thing", id_mal=33, chapters=10, status="FINISHED", staff=["Author A"]),
        al_media(4, "Not Listed", id_mal=44, staff=["Author A"]),
    )
    al.add_list("Reading", [(101, 1, "CURRENT", 5), (102, 2, "CURRENT", 30), (103, 3, "CURRENT", 8)])
    md.add(FakeSeries("a", "Sousou no Frieren", links={"al": "1"}, reads=["a1", "a2"]), {"a1": "9", "a2": "10.5"})
    md.add(FakeSeries("b", "Berserk", links={"al": "2"}, reads=["b1"]), {"b1": "12"})
    md.add(FakeSeries("c", "Finished Thing", links={"al": "3"}, reads=["c1", "c2"]), {"c1": "10", "c2": "9"})
    md.add(FakeSeries("d", "Not Listed", links={"al": "4"}, reads=["d1"]), {"d1": "3"})
    md.add(FakeSeries("e", "Zzz Nothing Matches", reads=["e1"]), {"e1": "1"})
    md.add(FakeSeries("f", "Reset Volumes", links={"al": "1"}, numbers_reset=True, reads=["f1"]), {"f1": "40"})
    return md.install(mock), al.install(mock)


async def run(services):
    orch = services.orchestrator
    run_id = await orch.start_run()
    await orch.wait()
    return run_id


async def test_run_reaches_diffed_with_one_item_per_series(fast, world):
    run_id = await run(fast)
    r = fast.repo.get_run(run_id)
    assert r["state"] == "diffed", r["error"]
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert set(items) == {"a", "b", "c", "d", "e", "f"}
    assert all(i["reason"] for i in items.values())

    assert (items["a"]["action"], items["a"]["md_progress"], items["a"]["al_progress"]) == ("write", 10, 5)
    assert (items["b"]["action"], items["b"]["reason"]) == ("skip", "AniList at/ahead")
    assert (items["c"]["action"], items["c"]["set_status"], items["c"]["status_approved"]) == ("write", "COMPLETED", 1)
    assert (items["d"]["action"], items["d"]["al_entry_id"], items["d"]["md_progress"]) == ("add", None, 3)
    assert (items["e"]["action"], items["e"]["reason"]) == ("skip", "no match")
    assert (items["f"]["action"], items["f"]["flag_kind"]) == ("flag", "implausible")

    assert r["est_requests"] == 3 and r["est_seconds"] == 9  # 2 writes, batch 10, 20 rpm
    assert "2 to write" in r["phase_detail"]


async def test_request_counts_match_calls(fast, world):
    md, al = world
    run_id = await run(fast)
    r = fast.repo.get_run(run_id)
    assert r["req_anilist"] == al.call_count > 0
    assert r["req_mangadex"] == sum(md.data_calls().values()) > 0
    assert fast.anilist.request_counter is None and fast.mangadex.request_counter is None


async def test_second_start_while_running_raises(fast, world):
    orch = fast.orchestrator
    await orch.start_run()
    with pytest.raises(SyncAlreadyRunning):
        await orch.start_run()
    await orch.wait()
    # Once finished, a new run may start.
    await orch.start_run()
    await orch.wait()


async def test_mangadex_ban_halts_before_any_anilist_request(fast, world, mock):
    md, al = world
    mock.get(f"{API_URL}/manga/status").mock(return_value=httpx.Response(403, json={"result": "error"}))
    run_id = await run(fast)
    r = fast.repo.get_run(run_id)
    assert r["state"] == "halted"
    assert "temporary IP ban" in r["error"]
    assert al.call_count == 0
    assert fast.repo.items(run_id) == []


async def test_unexpected_error_fails_run(fast, world, mock):
    mock.get(f"{API_URL}/manga/status").mock(return_value=httpx.Response(500, json={"errors": []}))
    run_id = await run(fast)
    r = fast.repo.get_run(run_id)
    assert r["state"] == "failed" and "HTTP 500" in r["error"]
    assert not fast.orchestrator.lock.locked()


async def test_viewer_is_cached(fast, world):
    _, al = world
    await run(fast)
    assert fast.repo.get_setting("anilist_user_id") == 1
    viewer_calls = sum("Viewer" in r["query"] for r in al.requests)
    await run(fast)
    assert sum("Viewer" in r["query"] for r in al.requests) == viewer_calls == 1


@pytest.mark.parametrize("state", ["fetching", "resolving", "diffing"])
def test_restart_marks_interrupted_runs_failed(fast, state):
    run_id = fast.repo.create_run(state)
    assert fast.orchestrator.recover_interrupted() == [run_id]
    r = fast.repo.get_run(run_id)
    assert r["state"] == "failed" and "interrupted" in r["error"]


def test_restart_leaves_writing_runs_alone(fast):
    run_id = fast.repo.create_run("writing")
    assert fast.orchestrator.recover_interrupted() == []
    assert fast.repo.get_run(run_id)["state"] == "writing"


async def test_discard(fast, world):
    run_id = await run(fast)
    fast.orchestrator.discard(run_id)
    assert fast.repo.get_run(run_id)["state"] == "cancelled"
    with pytest.raises(SyncStateError):
        fast.orchestrator.discard(run_id)


async def test_status(fast, world):
    run_id = await run(fast)
    s = fast.orchestrator.status(run_id)
    assert s.state == "diffed" and s.req_anilist > 0
    assert fast.orchestrator.status(9999) is None


async def test_new_sync_supersedes_older_diffed_run(fast, world):
    first = await run(fast)
    second = await run(fast)
    r1 = fast.repo.get_run(first)
    assert (r1["state"], r1["error"]) == ("cancelled", f"superseded by sync #{second}")
    assert fast.repo.get_run(second)["state"] == "diffed"


async def test_summary_counts_all_mappings_not_just_new_ones(fast, world):
    await run(fast)
    second = await run(fast)
    detail = fast.repo.get_run(second)["phase_detail"]
    assert "(0 newly matched)" in detail
    assert "5 matched" in detail and "1 unmatched" in detail


async def test_dismissed_flag_stays_dismissed_until_progress_changes(fast, world):
    md, _ = world
    first = await run(fast)
    f = {i["md_id"]: i for i in fast.repo.items(first)}["f"]
    assert f["action"] == "flag"
    fast.repo.dismiss_flag("f", f["md_progress"], f["flag_kind"], f["reason"])

    second = await run(fast)
    f2 = {i["md_id"]: i for i in fast.repo.items(second)}["f"]
    assert (f2["action"], f2["flag_kind"]) == ("skip", "implausible")
    assert f2["reason"].startswith("dismissed by you: ")

    md.series["f"].reads.append("f2")
    md.chapters["f2"] = {"chapter": "41", "volume": None, "manga": "f"}
    third = await run(fast)
    assert {i["md_id"]: i for i in fast.repo.items(third)}["f"]["action"] == "flag"  # new reads: flagged again
