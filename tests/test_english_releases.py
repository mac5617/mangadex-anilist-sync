"""New releases come from new English chapters, favour newer series and move on between asks."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mdal.backup import restore
from mdal.clients.mangadex import API_URL, TOKEN_URL
from mdal.fetch.mangadex_new import fetch_new_releases, is_reupload
from mdal.recommend import fresh
from mdal.recommend.llm import PICKS
from mdal.recommend.profile import build_profile
from mdal.recommend.rotation import GOOD_NEW_MATCH, also_new, new_remember, new_shortlist, stored_new_picks
from mdal.web.app import create_app

OLLAMA = "http://127.0.0.1:11434"
NOW = datetime.now(UTC)


def uuid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat(timespec="seconds")


def chapter(n, number, at, chapter_id=None):
    return {"id": chapter_id or f"ch-{n}-{number}", "type": "chapter", "attributes": {"chapter": number, "readableAt": at},
            "relationships": [{"id": uuid(n), "type": "manga"}]}


def manga(n, title=None, year=2026, created=None):
    return {"id": uuid(n), "type": "manga", "attributes": {
        "title": {"en": title or f"Series {n}"}, "altTitles": [], "description": {"en": "A tale."}, "links": {},
        "originalLanguage": "ja", "year": year, "status": "ongoing", "contentRating": "safe", "lastChapter": None,
        "createdAt": created or ago(days=400), "tags": []}, "relationships": []}


def rec(n, overall=0.8, number="10", title=None):
    r = fresh.make_new_rec({"md_id": uuid(n), "title": title or f"Series {n}", "tags": "[]", "authors": "[]",
                            "en_chapter": number, "en_chapter_at": ago(days=2)})
    r.scores["overall"] = overall
    return r


def pool(n):
    return [rec(i + 1, 0.9 - i * 0.001) for i in range(n)]


def names(recs):
    return [r.md_id for r in recs]


# ---- pure ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("year, weight", [(2026, 1.0), (2025, 1.0), (2024, 0.9), (2023, 0.9), (2022, 0.75), (1999, 0.75), (None, 0.9)])
def test_newer_series_are_favoured(year, weight):
    assert fresh.year_weight(year, 2026) == weight


def test_a_new_series_can_pass_a_slightly_better_old_one():
    rows = [{"md_id": uuid(i), "title": f"T{i}", "tags": json.dumps([{"name": "Horror"}]), "authors": "[]", "year": y}
            for i, y in ((1, 2010), (2, 2026))]
    profile = build_profile([{"media_id": 1, "title": "A", "status": "COMPLETED", "score": 95, "progress": 10, "format": "MANGA",
                              "genres": ["Horror"], "tag_ranks": [], "staff": []}])
    recs = fresh.score_new(profile, rows, this_year=2026)
    assert fresh.ranked_new(recs)[0].md_id == uuid(2)


@pytest.mark.parametrize("value, number", [("91", 91.0), ("4.5", 4.5), (None, None), ("", None), ("Extra", None)])
def test_chapter_numbers(value, number):
    assert fresh.chapter_number(value) == number


@pytest.mark.parametrize("number, at, line", [
    ("91", timedelta(days=2), "EN ch. 91 · 2 days ago"), ("4.5", timedelta(hours=30), "EN ch. 4.5 · 1 day ago"),
    ("7", timedelta(hours=3), "EN ch. 7 · today"), (None, timedelta(days=3), "New EN chapter · 3 days ago"),
])
def test_the_card_says_what_was_released(number, at, line):
    r = fresh.make_new_rec({"md_id": uuid(1), "title": "T", "tags": "[]", "authors": "[]", "en_chapter": number,
                            "en_chapter_at": (NOW - at).isoformat()})
    assert fresh.english_line(r, NOW) == line


def test_a_picked_series_stays_out():
    past = new_remember([], [(uuid(1), "10"), (uuid(2), "10")])
    assert names(new_shortlist(pool(40), past, 40))[0] == uuid(3)


def test_a_higher_chapter_brings_a_pick_back():
    past = new_remember([], [(uuid(1), "9")])
    assert uuid(1) in names(new_shortlist(pool(40), past, 40))


def test_the_year_weight_leaves_the_plain_match_for_ask():
    rows = [{"md_id": uuid(1), "title": "Old", "tags": json.dumps([{"name": "Horror"}]), "authors": "[]", "year": 2010}]
    profile = build_profile([{"media_id": 1, "title": "A", "status": "COMPLETED", "score": 95, "progress": 10, "format": "MANGA",
                              "genres": ["Horror"], "tag_ranks": [], "staff": []}])
    r = fresh.score_new(profile, rows, this_year=2026)[0]
    assert r.scores["overall"] == pytest.approx(r.scores["match"] * 0.75) and r.scores["match"] > 0


@pytest.mark.parametrize("llm, ids", [("x", []), ({"picks": [1, {"md_id": 3}]}, []), ({"picks": [{"md_id": "a", "reason": "r"}]}, ["a"])])
def test_only_well_formed_stored_picks_are_read(llm, ids):
    assert [p["md_id"] for p in stored_new_picks(llm)] == ids


def test_damaged_stored_picks_leave_new_releases_working(services, candidates):
    services.repo.set_setting("new_llm", {"picks": [1]})
    with TestClient(create_app(services)) as c:
        assert c.get("/discover/new").status_code == 200 and c.get("/").status_code == 200


def test_current_picks_count_as_seen_at_their_chapter(services, candidates):
    with services.repo.conn:
        services.repo.conn.execute("INSERT INTO md_new(md_id, title, alt_titles, tags, authors, source, fetched_at, en_chapter) "
                                   "VALUES (?, 'T', '[]', '[]', '[]', 'updated', 'x', '10')", (uuid(1),))
    services.repo.set_setting("new_llm", {"model": "m", "picks": [{"md_id": uuid(1), "reason": "A fine pick."}]})
    assert services.releases.earlier_picks() == [{"id": uuid(1), "chapter": "10"}]
    services.releases.start_over()
    assert services.releases.earlier_picks() == []


@pytest.mark.parametrize("chapter_then", ["10", "11"])
def test_a_reupload_does_not_bring_a_pick_back(chapter_then):
    past = new_remember([], [(uuid(1), chapter_then)])
    assert uuid(1) not in names(new_shortlist(pool(40), past, 40))


def test_when_good_series_run_out_the_oldest_picks_return():
    recs = pool(30)
    past = new_remember([], [(r.md_id, "10") for r in reversed(recs[2:])])
    chosen = names(new_shortlist(recs, past, 40))
    assert chosen[:2] == [uuid(1), uuid(2)] and chosen[2:4] == [uuid(30), uuid(29)] and len(chosen) == PICKS * 2


def test_the_list_below_the_picks_keeps_to_good_unseen_series():
    recs = pool(4) + [rec(99, GOOD_NEW_MATCH - 0.01)]
    assert names(also_new(recs, new_remember([], [(uuid(1), "10")]), {uuid(2)})) == [uuid(3), uuid(4)]


# ---- the scan -----------------------------------------------------------------------------------


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def feed(services, mock, chapters, series, first=None):
    services.mangadex_queue.set_interval(0)
    mock.post(TOKEN_URL).respond(200, json={"access_token": "md-access-1111", "refresh_token": "md-refresh-1111"})

    def chapter_page(request):
        if md_id := request.url.params.get("manga"):
            at = (first or {}).get(md_id)
            data = [{"id": "first", "attributes": {"readableAt": at}}] if at else []
            return httpx.Response(200, json={"data": data, "total": len(data)})
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={"data": chapters[offset:offset + 100], "total": len(chapters)})

    def manga_page(request):
        ids = set(request.url.params.get_list("ids[]"))
        return httpx.Response(200, json={"data": [m for m in series if m["id"] in ids], "total": len(ids)})

    routes = {"chapter": mock.get(f"{API_URL}/chapter").mock(side_effect=chapter_page),
              "manga": mock.get(f"{API_URL}/manga").mock(side_effect=manga_page)}
    mock.get(f"{API_URL}/user/follows/manga").respond(200, json={"data": [], "total": 0})
    return routes


async def test_a_scan_reads_english_chapters_and_keeps_each_series_best(services, mock):
    chapters = [chapter(1, "91", ago(hours=1)), chapter(1, "90", ago(days=1)), chapter(1, "12", ago(hours=2), "reupload"),
                chapter(2, "4", ago(days=6)), chapter(3, "1", ago(days=8))]
    routes = feed(services, mock, chapters, [manga(1), manga(2), manga(3)])
    found, requests = await fetch_new_releases(services.mangadex, services.repo, "en")
    assert found == 2 and requests == 3
    sent = routes["chapter"].calls[0].request.url.params
    assert sent.get("translatedLanguage[]") == "en" and sent.get("order[readableAt]") == "desc"
    rows = {r["md_id"]: r for r in services.repo.new_releases()}
    assert set(rows) == {uuid(1), uuid(2)}                     # 3's chapter is past the week
    assert rows[uuid(1)]["en_chapter"] == "91" and rows[uuid(1)]["en_chapter_at"] == chapters[0]["attributes"]["readableAt"]


async def test_a_scan_stops_reading_at_the_end_of_the_week(services, mock):
    chapters = [chapter(i, "1", ago(days=1)) for i in range(1, 101)] + [chapter(i, "1", ago(days=9)) for i in range(101, 301)]
    routes = feed(services, mock, chapters, [manga(i) for i in range(1, 301)])
    await fetch_new_releases(services.mangadex, services.repo, "en")
    assert routes["chapter"].call_count == 2


async def test_a_series_added_this_week_is_marked(services, mock):
    feed(services, mock, [chapter(1, "1", ago(days=1)), chapter(2, "5", ago(days=1))],
         [manga(1, created=ago(days=2)), manga(2)])
    await fetch_new_releases(services.mangadex, services.repo, "en")
    rows = {r["md_id"]: r for r in services.repo.new_releases()}
    assert rows[uuid(1)]["source"] == "added" and rows[uuid(2)]["source"] == "updated"


async def test_series_without_a_recent_english_chapter_are_not_offered(services, mock):
    feed(services, mock, [chapter(1, "1", ago(days=1))], [manga(1)])
    await fetch_new_releases(services.mangadex, services.repo, "en")
    with services.repo.conn:
        services.repo.conn.execute("INSERT INTO md_new(md_id, title, alt_titles, tags, authors, source, fetched_at) "
                                   "SELECT ?, 'From The Old Scan', alt_titles, tags, authors, source, fetched_at FROM md_new", (uuid(7),))
        services.repo.conn.execute("INSERT INTO md_new(md_id, title, alt_titles, tags, authors, source, fetched_at, en_chapter, en_chapter_at) "
                                   "SELECT ?, 'Went Quiet', alt_titles, tags, authors, source, fetched_at, '3', ? FROM md_new WHERE md_id=?",
                                   (uuid(8), ago(days=9), uuid(1)))
    assert [r.md_id for r in services.releases.recs()[1]] == [uuid(1)]


# ---- re-uploads ---------------------------------------------------------------------------------


@pytest.mark.parametrize("entry, chapter, expected", [
    ({"chapter": "3", "first_at": ago(days=200)}, "3", True),
    ({"chapter": "3", "first_at": ago(days=2)}, "3", False),
    ({"chapter": "3", "first_at": ago(days=200)}, "4", False),    # a higher chapter hasn't been checked yet
    ({"chapter": "3", "first_at": None}, "3", False),
    (None, "3", False), ("junk", "3", False),
])
def test_what_counts_as_a_reupload(entry, chapter, expected):
    assert is_reupload(entry, chapter, NOW) is expected


async def scanned(services, mock, first):
    routes = feed(services, mock, [chapter(1, "3", ago(days=1)), chapter(2, "8", ago(days=1))], [manga(1), manga(2)], first)
    await fetch_new_releases(services.mangadex, services.repo, "en")
    return routes


def checks(route):
    return [c.request.url.params for c in route.calls if c.request.url.params.get("manga")]


async def test_a_series_whose_chapter_is_old_is_dropped(services, mock):
    routes = await scanned(services, mock, {uuid(1): ago(days=200), uuid(2): ago(days=1)})
    note, reads = await services.releases._drop_reuploads()
    assert note is None and reads == 2
    assert [r.md_id for r in services.releases.recs()[1]] == [uuid(2)]
    sent = checks(routes["chapter"])[0]
    assert sent.get("translatedLanguage[]") == "en" and sent.get("order[readableAt]") == "asc" and sent.get("limit") == "1"
    assert sent.get("chapter[]") in {"3", "8"}


async def test_a_checked_chapter_is_not_asked_about_again(services, mock):
    routes = await scanned(services, mock, {uuid(1): ago(days=200), uuid(2): ago(days=1)})
    await services.releases._drop_reuploads()
    before = len(checks(routes["chapter"]))
    assert (await services.releases._drop_reuploads()) == (None, 0)
    assert len(checks(routes["chapter"])) == before


async def test_a_higher_chapter_brings_a_reuploaded_series_back(services, mock):
    await scanned(services, mock, {uuid(1): ago(days=200), uuid(2): ago(days=1)})
    await services.releases._drop_reuploads()
    with services.repo.conn:
        services.repo.conn.execute("UPDATE md_new SET en_chapter='4' WHERE md_id=?", (uuid(1),))
    assert uuid(1) in {r.md_id for r in services.releases.recs()[1]}


async def test_only_the_best_series_are_checked(services, mock, monkeypatch):
    monkeypatch.setattr("mdal.recommend.releases.CHECK_BEST", 1)
    routes = await scanned(services, mock, {uuid(1): ago(days=1), uuid(2): ago(days=1)})
    assert (await services.releases._drop_reuploads())[1] == 1
    assert len(checks(routes["chapter"])) == 1


async def test_a_failed_check_keeps_what_it_learned(services, mock):
    routes = await scanned(services, mock, {uuid(1): ago(days=200), uuid(2): ago(days=200)})
    calls = {"n": 0}
    original = routes["chapter"].side_effect

    def fail_second(request):
        calls["n"] += 1
        return httpx.Response(500, json={"errors": [{"detail": "boom"}]}) if calls["n"] == 2 else original(request)

    routes["chapter"].side_effect = fail_second
    note, reads = await services.releases._drop_reuploads()
    assert "re-upload check stopped" in note and reads == 1
    assert len(services.releases.recs()[1]) == 1


async def test_the_memory_keeps_only_current_series(services, mock):
    services.repo.set_setting("new_first_uploads", {uuid(9): {"chapter": "1", "first_at": ago(days=1)}})
    await scanned(services, mock, {uuid(1): ago(days=1), uuid(2): ago(days=1)})
    await services.releases._drop_reuploads()
    assert set(services.repo.get_setting("new_first_uploads")) == {uuid(1), uuid(2)}


# ---- asking and the page ------------------------------------------------------------------------


@pytest.fixture
def candidates(services, monkeypatch):
    recs = pool(60)
    monkeypatch.setattr(services.releases, "recs", lambda include_adult=False: (build_profile([]), recs))
    return recs


def answers(mock):
    mock.get(f"{OLLAMA}/api/tags").respond(200, json={"models": [{"name": "gpt-oss:20b", "capabilities": ["completion"]}]})
    picks = [{"n": n, "reason": "Shares the slow dread you liked."} for n in range(1, PICKS + 1)]
    mock.post(f"{OLLAMA}/api/chat").respond(200, json={"message": {"content": json.dumps({"picks": picks})}})


async def test_each_ask_brings_new_series(services, candidates, mock):
    answers(mock)
    first = await services.releases._ask_model() or services.repo.get_setting("new_llm")["picks"]
    second = await services.releases._ask_model() or services.repo.get_setting("new_llm")["picks"]
    assert not {p["md_id"] for p in first} & {p["md_id"] for p in second}
    assert [p["id"] for p in services.repo.get_setting("new_past")] == [p["md_id"] for p in first + second]
    assert services.repo.get_setting("new_past")[0]["chapter"] == "10"


async def test_the_status_says_when_earlier_picks_come_back(services, candidates, mock):
    answers(mock)
    services.repo.set_setting("new_past", new_remember([], [(r.md_id, "10") for r in candidates]))
    assert "earlier picks" in await services.releases._ask_model()


def test_the_page_shows_the_release_and_can_start_over(services, candidates):
    services.repo.set_setting("new_past", new_remember([], [(uuid(1), "10")]))
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.get("/discover/new").text
        assert "EN ch. 10 · 2 days ago" in html and "Start over" in html and "Series 1<" not in html
        response = c.post("/discover-new-start-over")
        assert response.status_code == 204 and response.headers["HX-Refresh"] == "true"
        assert "Series 1<" in c.get("/discover/new").text
    assert services.repo.get_setting("new_past") == []


@pytest.mark.parametrize("bad", ["x", [1], [{"id": 3}], [{"id": "a", "chapter": 2}], {"id": "a"}])
def test_a_bad_history_is_refused_and_ignored(services, candidates, bad):
    restored = restore(services.repo, {"format": "shiori-backup", "tables": {}, "settings": {"new_past": bad}})
    assert services.repo.get_setting("new_past") is None and restored.kept == ["new past"]
    services.repo.set_setting("new_past", bad)
    with TestClient(create_app(services)) as c:
        assert c.get("/discover/new").status_code == 200
