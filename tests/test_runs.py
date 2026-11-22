"""Run rules through their interface: records only, no requests and no background work."""

import pytest

from mdal.db.connection import connect
from mdal.db.repo import Repo, now_iso
from mdal.sync.runs import Runs, SyncStateError
from tests.factories import seed_diffed_run


@pytest.fixture
def repo(tmp_path):
    return Repo(connect(tmp_path / "runs.db"))


def stopped_write(repo: Repo) -> int:
    run_id = seed_diffed_run(repo)
    repo.update_items(run_id, [("w", {"approved": 1, "write_state": "pending"})])
    repo.update_run(run_id, state="halted", approved_at=now_iso(), error="rate-limited")
    return run_id


def test_a_failed_list_add_does_not_hide_resume(repo):
    run_id = stopped_write(repo)
    add = repo.create_run("failed")
    repo.update_run(add, approved_at=now_iso(), error="AniList refused the add")
    assert Runs(repo).resumable() == run_id


def test_a_run_with_nothing_to_send_or_verify_cannot_resume(repo):
    run_id = stopped_write(repo)
    repo.update_items(run_id, [("w", {"write_state": "failed"})])
    assert not Runs(repo).can_resume(run_id)
    assert Runs(repo).resumable() is None


def test_a_new_run_supersedes_the_open_diff_for_its_site_only(repo):
    anilist = seed_diffed_run(repo)
    mal = repo.create_run("diffed", "mal")
    newer = Runs(repo).create("anilist")
    assert repo.get_run(anilist)["error"] == f"superseded by sync #{newer}"
    assert repo.get_run(mal)["state"] == "diffed"


def test_restore_closes_the_other_open_diff(repo):
    runs = Runs(repo)
    old = seed_diffed_run(repo)
    runs.discard(old)
    newer = repo.create_run("diffed")
    runs.restore(old)
    assert repo.get_run(old)["state"] == "diffed"
    assert repo.get_run(newer)["state"] == "cancelled"


def test_only_an_open_diff_can_be_discarded(repo):
    run_id = stopped_write(repo)
    with pytest.raises(SyncStateError):
        Runs(repo).discard(run_id)
