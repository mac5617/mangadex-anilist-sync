import sqlite3

import pytest

from mdal.db.connection import connect, schema_version
from mdal.db.repo import SETTING_DEFAULTS, Repo

TABLES = {
    "schema_version", "settings", "md_manga", "md_read", "md_chapter", "al_media",
    "al_entry", "mapping", "match_candidate", "sync_run", "sync_item",
}


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sync.db"


@pytest.fixture
def repo(db_path):
    conn = connect(db_path)
    yield Repo(conn)
    conn.close()


def manga_row(md_id: str, title: str = "Title") -> dict:
    return {
        "md_id": md_id, "in_library": 1, "reading_status": "reading", "title": title,
        "alt_titles": "[]", "links": "{}", "links_hash": "x", "authors": "[]",
        "fetched_at": "2026-10-03T00:00:00+00:00",
    }


def test_fresh_db_creates_all_tables_and_reopen_is_noop(db_path):
    conn = connect(db_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert schema_version(conn) == 1
    conn.close()

    conn = connect(db_path)
    assert schema_version(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    conn.close()


def test_wal_mode(repo):
    assert repo.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_settings_defaults_and_persistence(db_path):
    conn = connect(db_path)
    repo = Repo(conn)
    assert repo.get_setting("anilist_rpm") == 20
    assert repo.get_setting("first_write_done") is False
    repo.set_setting("anilist_rpm", 15)
    conn.close()

    repo2 = Repo(connect(db_path))
    assert repo2.get_setting("anilist_rpm") == 15
    assert repo2.all_settings().keys() == SETTING_DEFAULTS.keys()
    repo2.conn.close()


def test_unknown_setting_is_rejected(repo):
    with pytest.raises(KeyError):
        repo.get_setting("nope")
    with pytest.raises(KeyError):
        repo.set_setting("nope", 1)


def test_snapshot_replace_keeps_chapter_cache(repo):
    repo.upsert_chapters([{"chapter_id": "c1", "md_id": "m1", "chapter": "1", "fetched_at": "t"}])
    repo.replace_md_snapshot([manga_row("m1"), manga_row("m2")], {"m1": ["c1", "c2"]})
    repo.replace_md_snapshot([manga_row("m3")], {"m3": ["c9"]})

    assert [r["md_id"] for r in repo.md_manga()] == ["m3"]
    reads = repo.conn.execute("SELECT md_id, chapter_id FROM md_read").fetchall()
    assert [tuple(r) for r in reads] == [("m3", "c9")]
    assert repo.chapter_count() == 1


def test_mapping_state_check_constraint(repo):
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert_mapping({"md_id": "m1", "state": "maybe", "tier": 4, "reasons": []})
    repo.upsert_mapping({"md_id": "m1", "al_media_id": 5, "state": "auto", "tier": 2, "reasons": ["links.al"]})
    assert repo.get_mapping("m1")["state"] == "auto"


def test_set_status_only_completed(repo):
    run_id = repo.create_run()
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert_item({"run_id": run_id, "md_id": "m1", "action": "write", "set_status": "DROPPED"})
    repo.upsert_item({"run_id": run_id, "md_id": "m1", "action": "write", "set_status": "COMPLETED"})
    assert repo.items(run_id)[0]["set_status"] == "COMPLETED"


def test_committed_write_survives_crash(db_path):
    conn = connect(db_path)
    repo = Repo(conn)
    run_id = repo.create_run()
    repo.upsert_item({"run_id": run_id, "md_id": "m1", "action": "write", "write_state": "pending"})
    repo.upsert_item({"run_id": run_id, "md_id": "m1", "action": "write", "write_state": "done"})
    repo.add_request(run_id, "anilist", 2)
    del repo
    conn.close()  # simulate the process dying right after the commit

    repo = Repo(connect(db_path))
    assert repo.items(run_id)[0]["write_state"] == "done"
    assert repo.get_run(run_id)["req_anilist"] == 2
    repo.conn.close()


def test_delete_mapping_removes_candidates(repo):
    repo.upsert_mapping({"md_id": "m1", "state": "review", "tier": 4, "reasons": []})
    repo.conn.execute(
        "INSERT INTO match_candidate VALUES ('m1', 7, 0.8, '[]', 1)"
    )
    repo.conn.commit()
    repo.delete_mapping("m1")
    assert repo.get_mapping("m1") is None
    assert repo.conn.execute("SELECT COUNT(*) FROM match_candidate").fetchone()[0] == 0
