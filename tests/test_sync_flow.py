"""The orchestrator's live coordination, with fake sites: Busy, the site queue and what you're told."""

import asyncio
from types import SimpleNamespace

import pytest

from mdal.lists import YourList
from mdal.sync.orchestrator import SyncAlreadyRunning
from mdal.sync.runs import SyncStateError
from tests.factories import connect_mal, fake_sites, seed_diffed_run


async def settle(orch) -> None:
    for _ in range(30):
        await asyncio.sleep(0)
        if orch.task and not orch.task.done():
            await orch.wait()


def read_ok(services):
    async def read(run_id):
        services.repo.update_run(run_id, state="diffed")
    return read


async def test_nothing_starts_in_the_gap_between_sites(services):
    orch = services.orchestrator
    run_id = seed_diffed_run(services.repo)
    orch.queued = ["mal"]
    with pytest.raises(SyncAlreadyRunning):
        await orch.approve(run_id, ["w"], [], [])
    with pytest.raises(SyncAlreadyRunning):
        await orch.start_run("anilist")
    assert orch.queued == ["mal"] and services.repo.get_run(run_id)["state"] == "diffed"
    assert orch.resumable_runs() == []


async def test_a_sync_waits_for_list_edits_to_save(services):
    orch = services.orchestrator
    orch.sites = fake_sites(read=read_ok(services))
    run_id = seed_diffed_run(services.repo)
    orch.editing = lambda: True
    for attempt in (orch.start_run("anilist"), orch.start_sync(["anilist"]), orch.approve(run_id, ["w"], [], [])):
        with pytest.raises(SyncStateError, match="Saving your list edits"):
            await attempt


async def test_a_list_edit_counts_as_saving_until_it_ends():
    seen = []

    async def set_notes(_series, _notes):
        seen.append(edits.saving())
        raise RuntimeError("stop here")

    repo = SimpleNamespace(mappings=dict, media=lambda _ids: {}, md_manga=list)
    edits = YourList(repo, SimpleNamespace(set_notes=set_notes), SimpleNamespace(), busy=lambda: False,
                     mirroring=lambda: False)
    with pytest.raises(RuntimeError):
        await edits.set_notes("al:1", "a note")
    assert seen == [True] and not edits.saving()


async def test_a_queued_site_that_cannot_start_says_so(services):
    orch = services.orchestrator
    orch.sites = fake_sites(read=read_ok(services))          # MyAnimeList isn't connected
    await orch.start_sync(["anilist", "mal"])
    await settle(orch)
    assert orch.not_read == ["MyAnimeList wasn't read: Connect MyAnimeList in Settings first."]
    assert not orch.busy()
    await orch.start_sync(["anilist"])
    await settle(orch)
    assert orch.not_read == []


async def test_a_failed_read_says_the_next_site_was_not_read(services):
    connect_mal(services)
    orch = services.orchestrator

    async def failing(_run_id):
        raise RuntimeError("boom")

    orch.sites = fake_sites(read=failing)
    await orch.start_sync(["anilist", "mal"])
    await settle(orch)
    assert orch.not_read == ["MyAnimeList wasn't read because AniList's read failed."]


async def test_one_resume_continues_both_sites_anilist_first(services):
    orch = services.orchestrator
    written = []

    async def write(run_id):
        written.append(services.repo.get_run(run_id)["target"])
        services.repo.update_run(run_id, state="done")

    orch.sites = fake_sites(write=write)
    anilist = seed_diffed_run(services.repo)
    services.repo.update_items(anilist, [("w", {"approved": 1, "write_state": "pending"})])
    services.repo.update_run(anilist, state="halted", approved_at="then")
    mal = services.repo.create_run("failed", "mal")
    services.repo.replace_items(mal, [{"run_id": mal, "md_id": "w", "action": "write", "reason": "r", "approved": 1,
                                       "write_state": "pending"}])
    services.repo.update_run(mal, approved_at="then")
    assert orch.resumable_runs() == [anilist, mal]
    await orch.resume(mal)
    await orch.wait()
    assert written == ["anilist", "mal"] and not orch.lock.locked()
