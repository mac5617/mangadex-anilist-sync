"""Matches through their interface."""

import pytest

from mdal.db.connection import connect
from mdal.db.repo import Repo
from mdal.matching.matches import is_matched, library_series, matched, not_on_list
from tests.factories import al_media_row, md_manga_row


@pytest.fixture
def repo(tmp_path):
    r = Repo(connect(tmp_path / "matches.db"))
    r.replace_md_snapshot([md_manga_row(m, f"Series {m}") for m in ("a", "b", "c", "d", "e")], {})
    r.upsert_media([al_media_row(1, "One"), al_media_row(2, "Two")])
    for md_id, media_id, state in (("b", 1, "auto"), ("a", 1, "confirmed"), ("c", 2, "review"),
                                   ("d", None, "auto"), ("e", None, "unmatched")):
        r.upsert_mapping({"md_id": md_id, "al_media_id": media_id, "state": state, "tier": 1, "reasons": []})
    return r


@pytest.mark.parametrize("mapping, expected", [
    ({"state": "auto", "al_media_id": 1}, True),
    ({"state": "confirmed", "al_media_id": 1}, True),
    ({"state": "auto", "al_media_id": None}, False),   # names no AniList series
    ({"state": "review", "al_media_id": 1}, False),
    ({"state": "not_on_anilist", "al_media_id": None}, False),
    (None, False),
])
def test_is_matched(mapping, expected):
    assert is_matched(mapping) is expected


def test_matched_needs_an_anilist_series(repo):
    assert matched(repo) == {"a": 1, "b": 1}


def test_library_series_are_in_a_stable_order(repo):
    assert [r["md_id"] for r in library_series(repo)] == ["a", "b"]
    assert [r["al_media_id"] for r in library_series(repo, 1)] == [1, 1]
    assert library_series(repo, 2) == []


def test_not_on_list_leaves_out_what_is_listed(repo):
    assert [r["md_id"] for r in not_on_list(repo)] == ["a", "b"]
    repo.replace_al_entries([{"entry_id": 9, "media_id": 1, "status": "CURRENT", "progress": 1, "fetched_at": "x"}])
    assert not_on_list(repo) == []
