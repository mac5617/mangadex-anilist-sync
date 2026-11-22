"""Changes through their interface: from list state to build, Dismissal, Selection and Write plan."""

import sqlite3

import pytest

from mdal.backup import restore
from mdal.db.connection import _migrations, connect, schema_version
from mdal.db.repo import Repo
from mdal.sync.changes import (
    ApprovalError,
    Selection,
    approved_plan,
    build_items,
    dismiss,
    plan_estimate,
    refresh_item,
    undismiss,
    write_plan,
)
from tests.factories import al_media_row, md_manga_row, seed_diffed_run, match


@pytest.fixture
def repo(tmp_path):
    return Repo(connect(tmp_path / "changes.db"))


def world(repo: Repo, rating: int | None = 8) -> None:
    """Read to 40. AniList is at 5 with no total (a jump Flag); MyAnimeList is at 39 of 30 (an exceeds-total Flag)."""
    repo.replace_md_snapshot([md_manga_row("f", "Series", rating=rating, links={"mal": "77"})], {"f": ["c40"]})
    repo.upsert_chapters([{"chapter_id": "c40", "md_id": "f", "chapter": "40", "volume": None, "missing": 0,
                           "fetched_at": "x"}])
    repo.upsert_media([al_media_row(1, "AL", id_mal=77, chapters=None)])
    match(repo, "f", 1, "confirmed", tier=1)
    repo.replace_al_entries([{"entry_id": 101, "media_id": 1, "status": "CURRENT", "progress": 5, "fetched_at": "x"}])
    repo.replace_mal_entries([{"mal_id": 77, "status": "CURRENT", "progress": 39, "chapters": 30,
                               "media_status": "FINISHED", "title": "MAL", "fetched_at": "x"}])
    repo.set_setting("jump_limit", 10)


def built(repo: Repo, target: str) -> dict:
    return build_items(repo, 1, target=target)[0]


def test_a_dismissal_covers_one_site(repo):
    world(repo)
    flag = built(repo, "anilist")
    repo.dismiss_flag("f", "anilist", flag["md_progress"], flag["flag_kind"], flag["reason"])
    assert built(repo, "anilist")["action"] == "skip"
    assert (built(repo, "mal")["action"], built(repo, "mal")["flag_kind"]) == ("flag", "exceeds_total")


def test_a_dismissal_covers_one_kind_of_flag(repo):
    world(repo)
    flag = built(repo, "mal")
    repo.dismiss_flag("f", "mal", flag["md_progress"], "implausible", "an earlier problem")
    assert built(repo, "mal")["action"] == "flag"


def test_older_dismissals_are_kept_for_both_sites(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    schema_version(old)
    for version, sql in _migrations():
        if version <= 15:
            old.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_version(version) VALUES ({version});\nCOMMIT;")
    old.execute("INSERT INTO dismissed_flag VALUES ('f', 40, 'implausible', 'why', 'then')")
    old.commit()
    old.close()
    repo = Repo(connect(path))
    assert repo.dismissed_flags("anilist")["f"]["flag_kind"] == "implausible"
    assert repo.dismissed_flags("mal")["f"]["md_progress"] == 40


def test_a_backup_from_before_per_site_dismissals_restores_to_both(repo):
    row = {"md_id": "f", "md_progress": 40, "flag_kind": "implausible", "reason": "why", "dismissed_at": "then"}
    counts = restore(repo, {"format": "shiori-backup", "tables": {"dismissed_flag": [row]}})
    assert counts["dismissed_flag"] == 1
    assert "f" in repo.dismissed_flags("anilist") and "f" in repo.dismissed_flags("mal")


def open_change(repo: Repo, target: str = "anilist"):
    run_id = repo.create_run("diffed", target)
    repo.replace_items(run_id, build_items(repo, run_id, target=target))
    return repo.get_run(run_id), repo.items(run_id)[0]


def test_show_again_brings_the_score_offer_back(repo):
    world(repo)
    run, item = open_change(repo)
    dismiss(repo, run, item)
    repo.replace_items(run["run_id"], build_items(repo, run["run_id"]))   # a rebuilt dismissed row offers no score
    assert repo.items(run["run_id"])[0]["set_score"] is None
    undismiss(repo, run, repo.items(run["run_id"])[0])
    back = repo.items(run["run_id"])[0]
    assert (back["action"], back["set_score"]) == ("flag", 8)


def test_show_again_after_the_series_left_the_library(repo):
    world(repo)
    run, item = open_change(repo)
    dismiss(repo, run, item)
    repo.replace_md_snapshot([], {})
    undismiss(repo, run, repo.items(run["run_id"])[0])
    assert repo.items(run["run_id"])[0]["action"] == "flag"
    assert "f" not in repo.dismissed_flags("anilist")


def test_stored_dismissed_reasons_lose_their_prefix(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    schema_version(old)
    for version, sql in _migrations():
        if version <= 16:
            old.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_version(version) VALUES ({version});\nCOMMIT;")
    old.execute("INSERT INTO sync_run(run_id, started_at, state) VALUES (1, 'then', 'cancelled')")
    old.execute("INSERT INTO sync_item(run_id, md_id, action, flag_kind, reason) "
                "VALUES (1, 'f', 'skip', 'implausible', 'dismissed by you: a big jump')")
    old.commit()
    old.close()
    assert Repo(connect(path)).items(1)[0]["reason"] == "a big jump"


@pytest.mark.parametrize("selection, writes, refused", [
    (Selection.of(["w"]), {"w"}, False),
    (Selection.of(["c"], ["c"]), {"c"}, False),
    (Selection.of(["c"]), {"c"}, False),                  # progress still goes out without the completion
    (Selection.of(["s"], ["s"]), {"s"}, False),
    (Selection.of(["s"]), set(), False),                  # status-only, completion unticked: nothing to send
    (Selection.of(["i"]), set(), True),                   # a Flag needs override
    (Selection.of(["i"], overrides=["i"]), {"i"}, False),
    (Selection.of(["x"], overrides=["x"]), set(), True),  # past the total: never
    (Selection.of(["k"]), set(), True),
    (Selection.of(["w", "i"]), {"w"}, True),
])
def test_approval_and_estimate_read_one_write_plan(repo, selection, writes, refused):
    run_id = seed_diffed_run(repo)
    plan = write_plan(repo, run_id, selection)
    assert {w.md_id for w in plan.writes} == writes
    assert plan_estimate(repo, "anilist", plan).selected == len(writes)
    if refused or not writes:
        with pytest.raises(ApprovalError):
            approved_plan(repo, run_id, selection)
    else:
        assert approved_plan(repo, run_id, selection) == plan


def test_the_page_opens_on_the_plan_of_what_it_ticked(repo):
    run_id = seed_diffed_run(repo)
    assert {w.md_id for w in write_plan(repo, run_id).writes} == {"w", "c", "s"}


def shared_world(repo: Repo, read=(50, 30), site_progress: int = 10) -> None:
    """Two MangaDex series, a and b, matched to one AniList entry."""
    repo.replace_md_snapshot([md_manga_row("a", "Main upload", links={"mal": "77"}),
                              md_manga_row("b", "Other upload", links={"mal": "77"})],
                             {"a": [f"a{read[0]}"], "b": [f"b{read[1]}"]})
    repo.upsert_chapters([{"chapter_id": f"{m}{n}", "md_id": m, "chapter": str(n), "volume": None, "missing": 0,
                           "fetched_at": "x"} for m, n in zip("ab", read)])
    repo.upsert_media([al_media_row(1, "Shared", id_mal=77)])
    match(repo, "a", 1)
    match(repo, "b", 1)
    repo.replace_al_entries([{"entry_id": 101, "media_id": 1, "status": "CURRENT", "progress": site_progress,
                              "fetched_at": "x"}])
    repo.replace_mal_entries([{"mal_id": 77, "status": "CURRENT", "progress": site_progress, "fetched_at": "x"}])


@pytest.mark.parametrize("target", ["anilist", "mal"])
@pytest.mark.parametrize("read, kept, held", [((50, 30), "a", "b"), ((30, 50), "b", "a"), ((40, 40), "a", "b")])
def test_a_shared_entry_gets_the_highest_change(repo, target, read, kept, held):
    shared_world(repo, read)
    items = {i["md_id"]: i for i in build_items(repo, 1, target=target)}
    assert items[kept]["action"] == "write"
    assert items[held]["action"] == "skip" and items[held]["reason"].startswith("same entry as ")
    assert items[held]["flag_kind"] is None


def test_a_flag_on_the_highest_holds_the_shared_entry(repo):
    shared_world(repo, (400, 30))
    items = {i["md_id"]: i for i in build_items(repo, 1)}
    assert (items["a"]["action"], items["a"]["flag_kind"]) == ("flag", "implausible")
    assert items["b"]["action"] == "skip"


def test_dismissing_the_highest_sends_nothing_lower_until_shown_again(repo):
    shared_world(repo, (400, 30))
    run_id = repo.create_run("diffed")
    repo.replace_items(run_id, build_items(repo, run_id))
    run = repo.get_run(run_id)
    dismiss(repo, run, repo.item(run_id, "a"))
    refresh_item(repo, "a")
    assert {i["md_id"]: i["action"] for i in repo.items(run_id)} == {"a": "skip", "b": "skip"}
    undismiss(repo, run, repo.item(run_id, "a"))
    assert repo.item(run_id, "a")["action"] == "flag" and repo.item(run_id, "b")["action"] == "skip"


def test_a_new_match_refreshes_every_change_on_the_entry(repo):
    shared_world(repo, (30, 50))
    match(repo, "b", None, "unmatched")
    run_id = repo.create_run("diffed")
    repo.replace_items(run_id, build_items(repo, run_id))
    assert repo.item(run_id, "a")["action"] == "write"
    match(repo, "b", 1)
    refresh_item(repo, "b")
    assert (repo.item(run_id, "a")["action"], repo.item(run_id, "b")["action"]) == ("skip", "write")
    match(repo, "b", None, "unmatched")
    refresh_item(repo, "b")
    assert repo.item(run_id, "a")["action"] == "write"                  # the other series goes out again


def test_the_write_plan_never_sends_two_changes_to_one_entry(repo):
    shared_world(repo)
    run_id = repo.create_run("diffed")
    both = [{"run_id": run_id, "md_id": m, "al_media_id": 1, "site_entry_id": 101, "site_progress": 10,
             "md_progress": p, "action": "write", "reason": "r"} for m, p in (("a", 30), ("b", 50))]
    repo.replace_items(run_id, both)                                     # a stale row from before the rule
    plan = write_plan(repo, run_id, Selection.of(["a", "b"]))
    assert [w.md_id for w in plan.writes] == ["b"] and "same list entry" in plan.refusal
    with pytest.raises(ApprovalError, match="same list entry"):
        approved_plan(repo, run_id, Selection.of(["a", "b"]))
