import json
import re

import pytest
import respx
from fastapi.testclient import TestClient

from mdal.fetch.anilist_list import fetch_list, fuzzy_date
from mdal.stats import Bar, chart, entries, list_stats, nice_max, sync_stats
from mdal.web.app import create_app
from tests.factories import FakeAniList, al_media, al_media_row, md_manga_row


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def seed_list(repo):
    repo.upsert_media([
        al_media_row(1, "A", format="MANGA", country="JP", start_year=2019, chapters=50, status="FINISHED",
                     genres=json.dumps(["Action", "Comedy"])),
        al_media_row(2, "B", format="MANGA", country="KR", start_year=2021, chapters=100, status="FINISHED",
                     genres=json.dumps(["Action"])),
        al_media_row(3, "C", format="ONE_SHOT", country="JP", start_year=2021, status="RELEASING", genres="[]"),
    ])
    repo.replace_al_entries([
        {"entry_id": 10, "media_id": 1, "status": "CURRENT", "progress": 46, "progress_volumes": 4, "score": 80,
         "started_at": "2021-03", "completed_at": None, "fetched_at": "x"},
        {"entry_id": 11, "media_id": 2, "status": "COMPLETED", "progress": 100, "progress_volumes": 10, "score": 95,
         "started_at": "2021-01-02", "completed_at": "2024-05-01", "fetched_at": "x"},
        {"entry_id": 12, "media_id": 3, "status": "COMPLETED", "progress": 1, "progress_volumes": None, "score": None,
         "started_at": None, "completed_at": "2022", "fetched_at": "x"},
    ])
    # MangaDex: A is "completed" there (differs from AniList Reading), B agrees.
    repo.replace_md_snapshot([md_manga_row("ma", "A md", reading_status="completed"),
                              md_manga_row("mb", "B md", reading_status="completed")], {})
    for md_id, media in (("ma", 1), ("mb", 2)):
        repo.upsert_mapping({"md_id": md_id, "al_media_id": media, "state": "auto", "tier": 2, "confidence": 1.0,
                             "reasons": [], "links_hash": "h"})


def test_list_stats(services):
    seed_list(services.repo)
    s = list_stats(services.repo)
    assert (s.total, s.chapters, s.volumes, s.scored, s.completed) == (3, 147, 14, 2, 2)
    assert s.mean_score == pytest.approx(87.5) and s.score_sd == pytest.approx(7.5)
    assert [(b.label, b.value, b.key) for b in s.status] == [("Reading", 1, "CURRENT"), ("Completed", 2, "COMPLETED")]
    assert [(b.label, b.value) for b in s.formats] == [("Manga", 2), ("One shot", 1)]
    assert [(b.label, b.value) for b in s.countries] == [("Japan", 2), ("South Korea", 1)]
    assert [(b.label, b.value) for b in s.years] == [("2019", 1), ("2020", 0), ("2021", 2)]  # gaps filled
    assert [(b.label, b.value, b.value2) for b in s.timeline] == [("2021", 2, 0), ("2022", 0, 1), ("2023", 0, 0), ("2024", 0, 1)]
    assert {b.key: b.value for b in s.scores}["80"] == 1 and {b.key: b.value for b in s.scores}["100"] == 1
    assert {b.label: b.value for b in s.length} == {"1": 1, "26–50": 1, "51–100": 1}
    assert [(b.label, b.value) for b in s.through] == [("90–99%", 1)] and s.nearly == 1
    assert [(b.label, b.value) for b in s.pub] == [("Finished", 1)]
    assert s.genres[0] == {"name": "Action", "count": 2, "chapters": 146, "scored": 2, "mean_score": 87.5}
    assert s.heat["mismatches"] == 1
    cells = {(r["key"], c["key"]): c for r in s.heat["rows"] for c in r["cells"]}
    assert cells[("completed", "CURRENT")]["value"] == 1 and not cells[("completed", "CURRENT")]["match"]
    assert cells[("completed", "COMPLETED")]["match"] and cells[("none", "COMPLETED")]["value"] == 1


def test_filters_scope_everything(services):
    seed_list(services.repo)
    s = list_stats(services.repo, {"country": "JP"})
    assert s.total == 2 and s.chapters == 47
    s = list_stats(services.repo, {"status": "COMPLETED", "format": "MANGA"})
    assert s.total == 1 and [b.label for b in s.countries] == ["South Korea"]


@pytest.mark.parametrize("filters, expected", [
    ({"genre": "Comedy"}, ["A"]), ({"year": "2021"}, ["B", "C"]), ({"score": "80"}, ["A"]),
    ({"started": "2021"}, ["A", "B"]), ({"completed": "2022"}, ["C"]), ({"length": "51–100"}, ["B"]),
    ({"through": "90–99%"}, ["A"]), ({"pub": "RELEASING"}, ["C"]), ({"md_status": "none"}, ["C"]),
    ({"mismatch": "1"}, ["A"]), ({"q": "b"}, ["B"]), ({}, ["A", "B", "C"]),
])
def test_entry_filters_match_chart_buckets(services, filters, expected):
    seed_list(services.repo)
    assert [r["title"] for r in entries(services.repo, filters)] == expected


def test_entry_sorts(services):
    seed_list(services.repo)
    assert [r["title"] for r in entries(services.repo, {}, "progress")] == ["B", "A", "C"]
    assert [r["title"] for r in entries(services.repo, {}, "score")][0] == "B"


def test_genre_sorts(services):
    seed_list(services.repo)
    assert [g["name"] for g in list_stats(services.repo, genre_sort="chapters").genres] == ["Action", "Comedy"]


def test_long_tails_fold_into_other(services):
    repo = services.repo
    repo.upsert_media([al_media_row(i, f"M{i}", country=f"C{i}") for i in range(1, 11)])
    repo.replace_al_entries([{"entry_id": i, "media_id": i, "status": "CURRENT", "progress": 1, "fetched_at": "x"}
                             for i in range(1, 11)])
    labels = [b.label for b in list_stats(repo).countries]
    assert len(labels) == 8 and labels[-1] == "Other"


def seed_writes(repo):
    repo.replace_md_snapshot([md_manga_row("a", "Alpha"), md_manga_row("b", "Beta"), md_manga_row("c", "Gamma"),
                              md_manga_row("d", "Delta")], {})
    r1 = repo.create_run("done")
    repo.replace_items(r1, [
        {"run_id": r1, "md_id": "a", "action": "write", "al_entry_id": 1, "al_progress": 10, "md_progress": 15,
         "write_state": "done", "verify_note": "verified", "al_status_before": "CURRENT", "approved": 1},
        {"run_id": r1, "md_id": "b", "action": "add", "al_media_id": 2, "al_progress": None, "md_progress": 50,
         "set_status": "COMPLETED", "status_approved": 1, "write_state": "done", "verify_note": "verified", "approved": 1},
        {"run_id": r1, "md_id": "c", "action": "write", "al_entry_id": 3, "al_progress": 5, "md_progress": 9,
         "write_state": "failed", "verify_note": "write failed: boom", "approved": 1},
        {"run_id": r1, "md_id": "d", "action": "skip", "reason": "AniList at/ahead"},
    ])
    repo.update_run(r1, req_anilist=4, req_mangadex=40, approved_at="t")
    r2 = repo.create_run("done")
    repo.replace_items(r2, [
        {"run_id": r2, "md_id": "a", "action": "write", "al_entry_id": 1, "al_progress": 15, "md_progress": 15,
         "set_status": "COMPLETED", "status_approved": 1, "al_status_before": "PAUSED", "write_state": "done",
         "verify_note": "status changed by AniList: PAUSED→CURRENT", "approved": 1},
    ])
    return r1, r2


def test_sync_stats_all_and_per_run(services):
    r1, r2 = seed_writes(services.repo)
    s = sync_stats(services.repo)
    assert (s.updated, s.added, s.chapters, s.completed, s.failed, s.needs_look) == (2, 1, 55, 2, 1, 1)
    assert [(b.label, b.value) for b in s.per_run] == [(f"#{r1}", 55), (f"#{r2}", 0)]
    assert {b.label: b.value for b in s.jumps}["2–5"] == 1 and {b.label: b.value for b in s.jumps}["26–50"] == 1
    assert {b.label for b in s.transitions} == {"New entry", "Paused"}
    assert [g["md_id"] for g in s.biggest][:2] == ["b", "a"] and s.biggest[0]["before"] is None
    assert {p["md_id"] for p in s.problems} == {"a", "c"}

    one = sync_stats(services.repo, r2)
    assert (one.updated, one.added, one.chapters, one.completed) == (1, 0, 0, 1)
    assert one.requests_anilist == 0 and s.requests_anilist == 4


def test_chart_scaling():
    assert [nice_max(v) for v in (0, 7, 10, 11, 380, 2045)] == [1, 10, 10, 20, 500, 5000]
    c = chart([Bar("a", 50), Bar("b", 100)])
    assert c["ticks"] == [100, 50, 0] and c["bars"][0]["pct"] == 50
    many = chart([Bar(str(y), 1) for y in range(1960, 2027)])
    assert sum(b["show_label"] for b in many["bars"]) <= 14


def test_fuzzy_date():
    assert fuzzy_date({"year": 2024, "month": 5, "day": 1}) == "2024-05-01"
    assert fuzzy_date({"year": 2024, "month": 5, "day": None}) == "2024-05"
    assert fuzzy_date({"year": None, "month": 5}) is None and fuzzy_date(None) is None


async def test_list_fetch_stores_stats_fields(services, mock):
    services.anilist_queue.set_interval(0)
    fake = FakeAniList()
    fake.add_media(al_media(1, "A", genres=["Drama"]))
    fake.lists.append({"name": "Reading", "isCustomList": False, "entries": [{
        "id": 10, "status": "COMPLETED", "progress": 20, "progressVolumes": 3, "score": 0, "updatedAt": 1700000000,
        "startedAt": {"year": 2023, "month": 1, "day": None}, "completedAt": {"year": 2024, "month": 2, "day": 3},
        "media": fake._public(fake.catalogue[1])}]})
    fake.install(mock)
    await fetch_list(services.anilist, services.repo, 1)
    e = services.repo.al_entries()[1]
    assert (e["score"], e["progress_volumes"], e["started_at"], e["completed_at"], e["updated_at"]) == \
        (None, 3, "2023-01", "2024-02-03", 1700000000)
    assert json.loads(services.repo.media([1])[1]["genres"]) == ["Drama"]
    assert "score(format: POINT_100)" in fake.requests[0]["query"]


# ---- pages ----------------------------------------------------------------------


@pytest.fixture
def client(services, mock):
    with TestClient(create_app(services)) as c:
        yield c


def test_pages_render_empty(client, mock):
    assert "No AniList list yet" in client.get("/stats").text
    assert "Nothing has been written yet" in client.get("/stats/syncs").text
    assert len(mock.calls) == 0


def test_list_page(client, services, mock):
    seed_list(services.repo)
    html = client.get("/stats").text
    assert "<title>Library stats · Shiori</title>" in html
    assert '<a href="/stats" aria-current="page">Stats</a>' in html
    assert 'class="hbar drill" href="/stats/entries?status=COMPLETED"' in html
    assert "<summary>Table</summary>" in html and "/static/charts.js" in html and "<summary>All 2</summary>" in html
    assert 'class="legend"' in html and "Started and completed" in html
    assert 'href="/stats/entries?started=2021"' in html and 'href="/stats/entries?completed=2024"' in html
    assert re.search(r'class="heat-cell h\d" href="/stats/entries\?md_status=completed&amp;status=CURRENT"', html)
    assert 'href="/stats/entries?mismatch=1"' in html
    assert "Nearly finished: 1 series" in html
    assert len(mock.calls) == 0


def test_list_page_filters_carry_into_links(client, services):
    seed_list(services.repo)
    html = client.get("/stats?country=JP&gsort=chapters").text
    assert '<option value="JP" selected>' in html and "Clear filters" in html
    assert 'href="/stats/entries?country=JP&amp;status=CURRENT"' in html
    assert "Top ten by chapters read" in html
    assert "All 2 entries" in html


def test_entries_page(client, services, mock):
    seed_list(services.repo)
    html = client.get("/stats/entries?genre=Action&sort=progress").text
    assert "Entries <span class=\"muted\">(2)</span>" in html
    assert "Genre: Action" in html and 'href="/stats/entries?sort=progress"' in html  # chip removes the filter
    assert html.index(">B<") < html.index(">A<")
    assert "46 / 50" in html and 'class="meter"' in html
    assert "uploads.mangadex.org/covers/ma/c.jpg.256.jpg" in html  # MangaDex cover when the series is matched
    assert "https://mangadex.org/title/ma" in client.get("/stats/entries?q=A").text
    assert len(mock.calls) == 0


def test_entries_page_paging(client, services):
    repo = services.repo
    repo.upsert_media([al_media_row(i, f"T{i:03}") for i in range(1, 131)])
    repo.replace_al_entries([{"entry_id": i, "media_id": i, "status": "CURRENT", "progress": 1, "fetched_at": "x"}
                             for i in range(1, 131)])
    html = client.get("/stats/entries").text
    assert "Page 1 of 2" in html and "T100" in html and "T101" not in html
    assert "T130" in client.get("/stats/entries?page=2").text


def test_sync_page_and_run_filter(client, services, mock):
    r1, r2 = seed_writes(services.repo)
    html = client.get("/stats/syncs").text
    assert "Chapters added" in html and ">55<" in html
    assert "Chapters per sync" in html and "Largest updates" in html and "Problems (2)" in html
    one = client.get(f"/stats/syncs?run={r2}").text
    assert f'<option value="{r2}" selected>' in one and "Chapters per sync" not in one
    assert '<option value="" >' not in client.get("/stats/syncs?run=999").text
    assert "Chapters per sync" in client.get("/stats/syncs?run=999").text  # unknown run falls back to all
    assert f'href="/stats/syncs?run={r1}"' in client.get(f"/history/{r1}").text
    assert len(mock.calls) == 0


def test_titles_escaped_in_stats(client, services):
    seed_writes(services.repo)
    services.repo.replace_md_snapshot([md_manga_row("b", "<img src=x onerror=alert(1)>")], {})
    html = client.get("/stats/syncs").text
    assert "<img src=x onerror" not in html


# ---- tags, staff, sortable headers ---------------------------------------------------------------


def seed_tags_staff(repo):
    seed_list(repo)
    with repo.conn:
        repo.conn.execute("UPDATE al_media SET tags=?, staff_roles=? WHERE media_id=1",
                          (json.dumps([{"name": "Isekai", "rank": 90, "category": "Setting"}]),
                           json.dumps([{"id": 7, "name": "Kanehito Yamada", "role": "Story"}])))
        repo.conn.execute("UPDATE al_media SET tags=?, staff_roles=? WHERE media_id=2",
                          (json.dumps([{"name": "Isekai", "rank": 60, "category": "Setting"}]),
                           json.dumps([{"id": 7, "name": "Kanehito Yamada", "role": "Story & Art"},
                                       {"id": 8, "name": "Tsukasa Abe", "role": "Art"}])))


def test_tags_and_staff_stats_and_filters(services):
    seed_tags_staff(services.repo)
    s = list_stats(services.repo)
    assert s.tags[0]["name"] == "Isekai" and s.tags[0]["count"] == 2
    top = s.staff[0]
    assert (top["name"], top["key"], top["count"], top["chapters"]) == ("Kanehito Yamada", "7", 2, 146)
    assert set(top["roles"]) == {"Story", "Story & Art"}
    assert (s.tags_known, s.staff_known) == (2, 2)
    assert [r["title"] for r in entries(services.repo, {"tag": "Isekai"})] == ["A", "B"]
    assert [r["title"] for r in entries(services.repo, {"staff": "8"})] == ["B"]


def test_staff_and_tag_charts_link_to_entries(client, services):
    seed_tags_staff(services.repo)
    html = client.get("/stats").text
    assert 'href="/stats/entries?tag=Isekai"' in html and 'href="/stats/entries?staff=7"' in html
    chips = client.get("/stats/entries?staff=7").text
    assert "Staff: Kanehito Yamada" in chips


def test_column_headers_sort_both_ways(client, services):
    seed_list(services.repo)
    html = client.get("/stats/entries").text
    assert 'aria-sort="ascending"' in html  # title, A→Z by default
    assert 'href="/stats/entries?sort=title&amp;dir=desc"' in html  # clicking the active column flips it
    assert 'href="/stats/entries?sort=progress&amp;dir=desc"' in html  # numbers start biggest-first
    desc = client.get("/stats/entries?sort=title&dir=desc").text
    assert desc.index(">C<") < desc.index(">A<")
    asc = client.get("/stats/entries?sort=progress&dir=asc").text
    assert asc.index(">C<") < asc.index(">B<")
    assert '<select name="sort"' not in html  # the dropdown is gone


def test_heat_map_fills_its_panel(client, services):
    seed_list(services.repo)
    html = client.get("/stats").text
    assert '<colgroup><col class="heat-rowhead">' in html
    css = client.get("/static/app.css").text
    assert "table.heat { width: 100%; table-layout: fixed;" in css


async def test_tags_fall_back_when_too_complex(services, mock):
    import httpx

    services.anilist_queue.set_interval(0)
    fake = FakeAniList()
    fake.add_media(al_media(1, "A"))
    fake.add_list("Reading", [(10, 1, "CURRENT", 3)])
    fake.install(mock)
    real = fake.handle

    def complex_once(request):
        if b"tags {" in request.content:
            return httpx.Response(400, json={"data": None, "errors": [{"message": "Max query complexity exceeded"}]})
        return real(request)

    fake.route.side_effect = complex_once
    await fetch_list(services.anilist, services.repo, 1)
    assert 1 in services.repo.al_entries()


async def test_staff_backfill_is_capped_and_cached(services, mock):
    from mdal.fetch.anilist_list import fetch_staff

    services.anilist_queue.set_interval(0)
    fake = FakeAniList()
    for i in range(1, 61):
        fake.add_media(al_media(i, f"M{i}"))
    fake.add_list("Reading", [(100 + i, i, "CURRENT", 1) for i in range(1, 61)])
    fake.install(mock)
    await fetch_list(services.anilist, services.repo, 1)
    before = fake.call_count
    left = await fetch_staff(services.anilist, services.repo, max_requests=2)
    assert fake.call_count - before == 2 and left == 10  # 25 per request
    left = await fetch_staff(services.anilist, services.repo, max_requests=2)
    assert left == 0 and fake.call_count - before == 3
    await fetch_staff(services.anilist, services.repo, max_requests=2)
    assert fake.call_count - before == 3  # nothing left to look up


def test_tag_and_role_cleaning():
    from mdal.fetch.anilist_list import clean_tags, creator_roles

    tags = clean_tags([{"name": "Isekai", "rank": 80}, {"name": "Weak", "rank": 30},
                       {"name": "Twist", "rank": 90, "isMediaSpoiler": True}])
    assert [t["name"] for t in tags] == ["Isekai"]
    roles = creator_roles({"edges": [
        {"role": "Story & Art", "node": {"id": 1, "name": {"full": "Author"}}},
        {"role": "Translator (English)", "node": {"id": 2, "name": {"full": "Translator"}}},
        {"role": "Original Creator", "node": {"id": 3, "name": {"full": "Creator"}}},
    ]})
    assert [r["id"] for r in roles] == [1, 3]
