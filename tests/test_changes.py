"""Changes through their interface: from list state to build, Dismissal, Selection and Write plan."""

import sqlite3

import pytest

from mdal.backup import restore
from mdal.db.connection import _migrations, connect, schema_version
from mdal.db.repo import Repo
from mdal.sync.changes import build_items, dismiss, undismiss
from tests.factories import al_media_row, md_manga_row


@pytest.fixture
def repo(tmp_path):
    return Repo(connect(tmp_path / "changes.db"))


def world(repo: Repo, rating: int | None = 8) -> None:
    """Read to 40. AniList is at 5 with no total (a jump Flag); MyAnimeList is at 39 of 30 (an exceeds-total Flag)."""
    repo.replace_md_snapshot([md_manga_row("f", "Series", rating=rating, links={"mal": "77"})], {"f": ["c40"]})
    repo.upsert_chapters([{"chapter_id": "c40", "md_id": "f", "chapter": "40", "volume": None, "missing": 0,
                           "fetched_at": "x"}])
    repo.upsert_media([al_media_row(1, "AL", id_mal=77, chapters=None)])
    repo.upsert_mapping({"md_id": "f", "al_media_id": 1, "state": "confirmed", "tier": 1, "reasons": []})
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
