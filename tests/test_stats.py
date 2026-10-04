import json
import re

import pytest
import respx
from fastapi.testclient import TestClient

from mdal.fetch.anilist_list import fetch_list, fuzzy_date
from mdal.stats import Bar, chart, list_stats, nice_max, sync_stats
from mdal.web.app import create_app
from tests.factories import FakeAniList, al_media, al_media_row, md_manga_row


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def seed_list(repo):
    repo.upsert_media([
        al_media_row(1, "A", format="MANGA", country="JP", start_year=2019, genres=json.dumps(["Action", "Comedy"])),
        al_media_row(2, "B", format="MANGA", country="KR", start_year=2021, genres=json.dumps(["Action"])),
        al_media_row(3, "C", format="ONE_SHOT", country="JP", start_year=2021, genres="[]"),
    ])
    repo.replace_al_entries([
        {"entry_id": 10, "media_id": 1, "status": "CURRENT", "progress": 40, "progress_volumes": 4, "score": 80,
         "completed_at": None, "fetched_at": "x"},
        {"entry_id": 11, "media_id": 2, "status": "COMPLETED", "progress": 100, "progress_volumes": 10, "score": 95,
         "completed_at": "2024-05-01", "fetched_at": "x"},
        {"entry_id": 12, "media_id": 3, "status": "COMPLETED", "progress": 1, "progress_volumes": None, "score": None,
         "completed_at": "2022", "fetched_at": "x"},
    ])


def test_list_stats(services):
    seed_list(services.repo)
    s = list_stats(services.repo)
    assert (s.total, s.chapters, s.volumes, s.scored) == (3, 141, 14, 2)
    assert s.mean_score == pytest.approx(87.5) and s.score_sd == pytest.approx(7.5)
    assert [(b.label, b.value) for b in s.status] == [("Reading", 1), ("Completed", 2)]
    assert [(b.label, b.value) for b in s.formats] == [("Manga", 2), ("One shot", 1)]
    assert [(b.label, b.value) for b in s.countries] == [("Japan", 2), ("South Korea", 1)]
    assert [(b.label, b.value) for b in s.years] == [("2019", 1), ("2020", 0), ("2021", 2)]  # gaps filled
    assert [(b.label, b.value) for b in s.completed_by_year] == [("2022", 1), ("2023", 0), ("2024", 1)]
    assert {b.label: b.value for b in s.scores}["80"] == 1 and {b.label: b.value for b in s.scores}["100"] == 1
    assert s.genres[0] == {"name": "Action", "count": 2, "chapters": 140, "mean_score": 87.5}


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
    assert "<title>Stats · MangaDex → AniList</title>" in html
    assert '<a href="/stats" aria-current="page">Stats</a>' in html
    assert re.search(r'class="hbar" tabindex="0" data-tip="Completed · 2 entries"', html)
    assert "Show table" in html and "/static/charts.js" in html
    assert "All 2 genres" in html
    assert len(mock.calls) == 0


def test_sync_page_and_run_filter(client, services, mock):
    r1, r2 = seed_writes(services.repo)
    html = client.get("/stats/syncs").text
    assert "chapters added to AniList" in html and ">55<" in html
    assert "Chapters added per sync" in html and "Biggest updates" in html and "Need a look (2)" in html
    one = client.get(f"/stats/syncs?run={r2}").text
    assert f'<option value="{r2}" selected>' in one and "Chapters added per sync" not in one
    assert "All syncs" in client.get("/stats/syncs?run=999").text  # unknown run falls back
    assert f'href="/stats/syncs?run={r1}"' in client.get(f"/history/{r1}").text
    assert len(mock.calls) == 0


def test_titles_escaped_in_stats(client, services):
    seed_writes(services.repo)
    services.repo.replace_md_snapshot([md_manga_row("b", "<img src=x onerror=alert(1)>")], {})
    html = client.get("/stats/syncs").text
    assert "<img src=x onerror" not in html
