import json

import pytest
import respx

from mdal.fetch.anilist_list import fetch_list
from mdal.fetch.mangadex_library import links_hash
from mdal.matching.pipeline import (
    AniListFetch,
    alt_search_title,
    confirm,
    mark_not_on_anilist,
    resolve_all,
    retry_matching,
)
from tests.factories import FakeAniList, al_media


def md_row(md_id, title, *, links=None, alt_titles=(), year=2020, lang="ja", authors=("Author A",), last_chapter=None):
    links = links or {}
    return {
        "md_id": md_id, "in_library": 1, "reading_status": "reading", "title": title,
        "alt_titles": json.dumps(list(alt_titles)), "original_language": lang, "year": year,
        "pub_status": "ongoing", "last_chapter": last_chapter,
        "links": json.dumps(links, sort_keys=True), "links_hash": links_hash(links),
        "authors": json.dumps(list(authors)), "cover_file": None, "chapter_numbers_reset": 0,
        "fetched_at": "2026-10-03T00:00:00+00:00",
    }


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fast(services):
    services.anilist_queue.set_interval(0)
    return services


@pytest.fixture
def fake(mock):
    f = FakeAniList()
    f.add_media(
        al_media(1, "Sousou no Frieren", english="Frieren", id_mal=126287, year=2020, staff=["Kanehito Yamada"]),
        al_media(2, "Berserk", id_mal=2, year=1989, staff=["Kentarou Miura"]),
        al_media(3, "Kusuriya no Hitorigoto", fmt="NOVEL", id_mal=33, year=2011),
        al_media(4, "Shared Mal A", id_mal=44),
        al_media(5, "Shared Mal B", id_mal=44),
        al_media(6, "Off List Manga", id_mal=66),
        al_media(7, "Dungeon Meshi", english="Delicious in Dungeon", id_mal=77, year=2014, staff=["Ryouko Kui"]),
    )
    f.add_list("Reading", [(10, 1, "CURRENT", 100)])
    return f.install(mock)


@pytest.fixture
async def listed(fast, fake):
    """The list fetch has run (media 1 is on the list); request counter starts at 0."""
    await fetch_list(fast.anilist, fast.repo, 1)
    fake.requests.clear()
    fake.route.reset()
    return fast


def seed(services, *rows):
    services.repo.replace_md_snapshot(list(rows), {})


async def run(services):
    return await resolve_all(services.repo, AniListFetch(services.anilist, services.repo))


def mapping(services, md_id):
    return services.repo.get_mapping(md_id)


# ---- caching / tier 1 -----------------------------------------------------


async def test_confirmed_is_never_rematched_even_with_changed_links(listed, fake):
    seed(listed, md_row("a", "Berserk", links={"al": "2"}))
    confirm(listed.repo, "a", 999)
    seed(listed, md_row("a", "Berserk", links={"al": "6"}))
    summary = await run(listed)
    assert fake.call_count == 0
    assert summary.kept == 1
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("confirmed", 1, 999)


async def test_not_on_anilist_is_kept(listed, fake):
    seed(listed, md_row("a", "Obscure Doujin"))
    mark_not_on_anilist(listed.repo, "a")
    await run(listed)
    assert fake.call_count == 0
    assert mapping(listed, "a")["state"] == "not_on_anilist"


@pytest.mark.parametrize("title", ["Berserk", "Nothing Like It", "Berserk 2005"])
async def test_cached_results_cost_nothing_until_links_change(listed, fake, title):
    seed(listed, md_row("a", title, year=1989))
    await run(listed)
    first = fake.call_count
    assert first > 0
    assert mapping(listed, "a")["state"] in ("auto", "review", "unmatched")

    await run(listed)
    assert fake.call_count == first  # unchanged hash: 0 requests

    seed(listed, md_row("a", title, year=1989, links={"al": "6"}))
    await run(listed)
    assert fake.call_count > first
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("auto", 2, 6)


async def test_retry_matching_deletes_mapping_and_candidates(listed, fake):
    seed(listed, md_row("a", "Berserk", year=1989))
    await run(listed)
    assert listed.repo.candidates("a")
    retry_matching(listed.repo, "a")
    assert mapping(listed, "a") is None
    assert listed.repo.candidates("a") == []
    before = fake.call_count
    await run(listed)
    assert fake.call_count > before
    assert mapping(listed, "a") is not None


# ---- tier 2 ---------------------------------------------------------------


async def test_links_al_on_list_is_auto_with_zero_requests(listed, fake):
    seed(listed, md_row("a", "Totally Different Title", links={"al": "1"}))
    summary = await run(listed)
    assert fake.call_count == 0
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("auto", 2, 1)
    assert "on your list" in json.loads(m["reasons"])[0]
    assert summary.by_tier == {2: 1}


async def test_links_al_bulk_validation_is_one_request(listed, fake):
    seed(listed, *[md_row(f"s{i}", f"T{i}", links={"al": "6"}) for i in range(3)], md_row("x", "X", links={"al": "2"}))
    await run(listed)
    assert fake.call_count == 1
    assert sorted(fake.requests[0]["variables"]["ids"]) == [2, 6]


async def test_links_al_absent_from_anilist_falls_to_tier3(listed, fake):
    seed(listed, md_row("a", "Whatever", links={"al": "424242", "mal": "66"}))
    await run(listed)
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("auto", 3, 6)


async def test_links_al_absent_and_no_mal_falls_to_tier4(listed, fake):
    seed(listed, md_row("a", "Berserk", year=1989, authors=("Miura Kentarou",), links={"al": "424242"}))
    await run(listed)
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("auto", 4, 2)


async def test_links_al_novel_goes_to_review(listed, fake):
    seed(listed, md_row("a", "Kusuriya", links={"al": "3"}))
    await run(listed)
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("review", 2, None)
    assert any("NOVEL" in r for r in json.loads(m["reasons"]))
    assert [c["al_media_id"] for c in listed.repo.candidates("a")] == [3]


async def test_links_al_and_mal_disagree_goes_to_review(listed, fake):
    seed(listed, md_row("a", "Frieren", links={"al": "1", "mal": "2"}))
    await run(listed)
    m = mapping(listed, "a")
    assert (m["state"], m["tier"]) == ("review", 2)
    assert any("differs" in r for r in json.loads(m["reasons"]))


async def test_links_al_and_mal_agree_is_auto(listed, fake):
    seed(listed, md_row("a", "Frieren", links={"al": "1", "mal": "126287"}))
    await run(listed)
    assert mapping(listed, "a")["state"] == "auto"


@pytest.mark.parametrize("bad", ["abc", "", "-5", "0", "12abc", None])
async def test_non_numeric_links_are_absent(listed, fake, bad):
    seed(listed, md_row("a", "Nothing Like It", links={"al": bad, "mal": bad}))
    await run(listed)
    assert mapping(listed, "a")["tier"] == 4
    assert all("id_in" not in r["query"] and "idMal_in" not in r["query"] for r in fake.requests)


# ---- tier 3 ---------------------------------------------------------------


async def test_mal_shared_by_several_media_goes_to_review(listed, fake):
    seed(listed, md_row("a", "Shared", links={"mal": "44"}))
    await run(listed)
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("review", 3, None)
    assert sorted(c["al_media_id"] for c in listed.repo.candidates("a")) == [4, 5]


# ---- tier 4 ---------------------------------------------------------------


async def test_tier4_auto(listed, fake):
    seed(listed, md_row("a", "Berserk", year=1989, authors=("Miura Kentarou",)))
    await run(listed)
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("auto", 4, 2)
    assert m["confidence"] == 1.0
    cands = listed.repo.candidates("a")
    assert [c["al_media_id"] for c in cands] == [2] and cands[0]["rank"] == 1


async def test_tier4_review_on_disagreement(listed, fake):
    seed(listed, md_row("a", "Berserk", year=2005))
    await run(listed)
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("review", 4, None)
    assert any(r.startswith("disagrees: year 2005 vs 1989") for r in json.loads(m["reasons"]))
    assert [c["al_media_id"] for c in listed.repo.candidates("a")] == [2]


async def test_tier4_unmatched_keeps_low_candidates(listed, fake):
    seed(listed, md_row("a", "Frieren Gaiden Special Edition Collection", year=1990, lang="ko"))
    await run(listed)
    m = mapping(listed, "a")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("unmatched", 4, None)
    assert [c["al_media_id"] for c in listed.repo.candidates("a")] == [1]


async def test_tier4_no_results(listed, fake):
    seed(listed, md_row("a", "Zzzz Nothing"))
    await run(listed)
    m = mapping(listed, "a")
    assert (m["state"], m["confidence"]) == ("unmatched", None)
    assert listed.repo.candidates("a") == []


async def test_second_search_uses_alt_title(listed, fake):
    seed(listed, md_row("a", "ダンジョン飯 Unknown Romanisation", year=2014, authors=("Kui Ryouko",),
                        alt_titles=["ダンジョン飯", "Delicious in Dungeon"]))
    summary = await run(listed)
    searched = [v for r in fake.requests for v in r["variables"].values()]
    assert searched == ["ダンジョン飯 Unknown Romanisation", "Delicious in Dungeon"]
    assert summary.searched == 2
    m = mapping(listed, "a")
    assert (m["state"], m["al_media_id"]) == ("auto", 7)


async def test_no_second_search_when_first_is_good_enough(listed, fake):
    seed(listed, md_row("a", "Berserk", year=1989, alt_titles=["Beruseruku"]))
    await run(listed)
    assert fake.call_count == 1


async def test_50_tier4_series_make_at_most_20_requests(listed, fake):
    seed(listed, *[md_row(f"s{i:02}", f"Unknown Series {i}", alt_titles=[f"Alt Name {i}"]) for i in range(50)])
    summary = await run(listed)
    assert listed.repo.get_setting("anilist_search_batch") == 5
    assert fake.call_count <= 20
    assert summary.unmatched == 50


def test_alt_search_title_prefers_latin_and_skips_primary_duplicates():
    row = md_row("a", "Frieren", alt_titles=["FRIEREN!", "葬送のフリーレン", "Sousou no Frieren"])
    assert alt_search_title(row) == "Sousou no Frieren"
    assert alt_search_title(md_row("b", "X", alt_titles=["葬送のフリーレン"])) == "葬送のフリーレン"
    assert alt_search_title(md_row("c", "X")) is None
