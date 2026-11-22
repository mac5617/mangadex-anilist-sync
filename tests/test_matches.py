"""Matches through their interface."""

import json

import pytest

from mdal.db.connection import connect
from mdal.db.repo import Repo
from mdal.matching.mal_link import media_link, series_link
from mdal.matching.matches import is_matched, library_series, matched, not_on_list
from tests.factories import al_media_row, match, md_manga_row


@pytest.fixture
def repo(tmp_path):
    r = Repo(connect(tmp_path / "matches.db"))
    r.replace_md_snapshot([md_manga_row(m, f"Series {m}") for m in ("a", "b", "c", "d", "e")], {})
    r.upsert_media([al_media_row(1, "One"), al_media_row(2, "Two")])
    for md_id, media_id, state in (("b", 1, "auto"), ("a", 1, "confirmed"), ("c", 2, "review"),
                                   ("d", None, "auto"), ("e", None, "unmatched")):
        match(r, md_id, media_id, state)
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


def linked(mal=None):
    return {"links": json.dumps({"mal": mal} if mal else {})}


@pytest.mark.parametrize("al_mal, links, expected", [
    (22, [], 22),                     # no library series: AniList's link
    (22, [None], 22),
    (22, ["22", None], 22),
    (None, ["33"], 33),               # linked only by MangaDex
    (None, ["33", "33"], 33),
    (None, [None], None),
    (None, [], None),
    (22, ["33"], None),               # disagree: no link, so nothing is written to a guess
    (22, ["22", "33"], None),
    (None, ["33", "44"], None),
])
def test_media_link(al_mal, links, expected):
    mal_id, why = media_link(al_mal, [linked(m) for m in links])
    assert mal_id == expected and (why is None) == (expected is not None)


def test_series_link_waits_for_a_match_under_review():
    assert series_link(linked("33"), {"state": "review", "al_media_id": None}, {}) == (None, None, "awaiting match review")
