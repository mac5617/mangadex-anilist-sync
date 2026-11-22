"""The safety copy taken before a migration changes an existing database."""

import sqlite3

import pytest

import mdal.main
from mdal.db.connection import COPY_SUFFIX, MigrationCopyError, _migrations, connect, schema_version


def older_database(path, note: str = "old") -> int:
    """A database one migration behind, holding a marker row."""
    latest = _migrations()[-1][0]
    db = sqlite3.connect(path)
    schema_version(db)
    for version, sql in _migrations():
        if version < latest:
            db.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_version(version) VALUES ({version});\nCOMMIT;")
    db.execute("CREATE TABLE IF NOT EXISTS marker(note TEXT)")
    db.execute("DELETE FROM marker")
    db.execute("INSERT INTO marker VALUES (?)", (note,))
    db.commit()
    db.close()
    return latest - 1


def version_of(path) -> int:
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        db.close()


def test_a_new_database_needs_no_copy(tmp_path):
    connect(tmp_path / "new.db").close()
    assert not (tmp_path / f"new.db{COPY_SUFFIX}").exists()


def test_an_up_to_date_database_needs_no_copy(tmp_path):
    path = tmp_path / "same.db"
    connect(path).close()
    connect(path).close()
    assert not (tmp_path / f"same.db{COPY_SUFFIX}").exists()


def test_the_copy_holds_the_database_as_it_was(tmp_path):
    path = tmp_path / "sync.db"
    before = older_database(path)
    connect(path).close()
    copy = tmp_path / f"sync.db{COPY_SUFFIX}"
    assert version_of(copy) == before and version_of(path) == _migrations()[-1][0]
    db = sqlite3.connect(copy)
    assert db.execute("SELECT note FROM marker").fetchone()[0] == "old"
    db.close()
    assert not (tmp_path / f"sync.db{COPY_SUFFIX}.partial").exists()


def test_only_the_latest_copy_is_kept(tmp_path):
    path = tmp_path / "sync.db"
    copy = tmp_path / f"sync.db{COPY_SUFFIX}"
    copy.write_bytes(b"an earlier copy")
    older_database(path)
    connect(path).close()
    assert version_of(copy) == _migrations()[-1][0] - 1


def test_no_copy_means_no_migration(tmp_path):
    path = tmp_path / "sync.db"
    before = older_database(path)
    (tmp_path / f"sync.db{COPY_SUFFIX}").mkdir()        # the copy can't be put here
    with pytest.raises(MigrationCopyError, match="nothing was changed"):
        connect(path)
    assert version_of(path) == before
    assert not (tmp_path / f"sync.db{COPY_SUFFIX}.partial").exists()


def test_start_up_says_why(monkeypatch):
    def refuse():
        raise MigrationCopyError("Shiori couldn't save a copy")

    monkeypatch.setattr(mdal.main.Services, "from_env", refuse)
    with pytest.raises(SystemExit, match="couldn't save a copy"):
        mdal.main.run()
