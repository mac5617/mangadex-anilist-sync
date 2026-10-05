"""Search, year in review, comparing with a friend, and backup/export."""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mdal.backup import BackupError, make_backup, restore
from mdal.clients.anilist import ANILIST_URL
from mdal.compare import compare, pearson
from mdal.search import Hit, match_rank, search
from mdal.web.app import create_app
from mdal.year import review, years
from tests.factories import al_media, al_media_row, md_manga_row


# ---- pure -------------------------------------------------------------------------------------------


def test_search_ranks_exact_then_prefix_then_words():
    assert match_rank("tokyo ghoul", ["Tokyo Ghoul"]) == 0
    assert match_rank("tokyo", ["Tokyo Ghoul:re"]) == 1
    assert match_rank("ghoul re", ["Tokyo Ghoul:re"]) == 2
    assert match_rank("bleach", ["Tokyo Ghoul"]) is None
    hits = [Hit("al:1", "Tokyo Ghoul:re", None, "", "Seen elsewhere", names=["Tokyo Ghoul:re"]),
            Hit("al:2", "Tokyo Ghoul", None, "", "On your list", names=["Tokyo Ghoul", "東京喰種"]),
            Hit("al:3", "Ghoul Hunter", None, "", "On your list", names=["Ghoul Hunter"])]
    assert [h.key for h in search("tokyo ghoul", hits)] == ["al:2", "al:1"]
    assert [h.key for h in search("東京喰種", hits)] == ["al:2"]


def entry(media_id, title, *, status="COMPLETED", score=None, progress=10, started=None, completed=None,
          genres=("Action",), tags=(), staff=()):
    return {"media_id": media_id, "title": title, "status": status, "score": score, "progress": progress,
            "started_at": started, "completed_at": completed, "genres": list(genres), "tags": list(tags),
            "staff": [{"id": i, "name": n} for i, n in enumerate(staff)], "cover_url": None}


def test_year_in_review():
    entries = [entry(1, "A", score=90, progress=100, started="2025-01", completed="2025-03-02", genres=["Horror"], tags=["Gore"]),
               entry(2, "B", score=70, progress=20, started="2025-02", completed="2025-11-20", genres=["Horror"], tags=["Gore"]),
               entry(3, "C", progress=5, started="2025-06", completed="2025-06", genres=["Horror"], tags=["Gore"]),
               entry(4, "D", status="CURRENT", started="2024-01", genres=["Romance"]),
               entry(5, "E", status="DROPPED", started="2025-04", genres=["Romance"]),
               *[entry(10 + i, f"Old {i}", completed="2020-01", genres=["Romance"]) for i in range(6)]]
    assert years(entries) == [2025, 2024, 2020]
    r = review(entries, 2025)
    assert r["finished"] == 3 and r["started"] == 4 and r["chapters"] == 125 and r["mean_score"] == 80
    assert r["months"][2] == ("Mar", 1) and r["months"][10] == ("Nov", 1)
    assert [e["title"] for e in r["best"]] == ["A", "B"] and r["first"]["title"] == "A" and r["last"]["title"] == "B"
    assert r["more_than_usual"][0]["name"] in ("Horror", "Gore")            # 3 of 4 this year, 3 of 11 overall
    assert [e["title"] for e in r["dropped"]] == ["E"]


def test_compare_lists():
    mine = [entry(i, f"S{i}", score=s, genres=["Horror"]) for i, s in [(1, 90), (2, 80), (3, 40), (4, 70), (5, 60), (6, 95)]]
    theirs = [{"media_id": i, "status": "COMPLETED", "score": s, "progress": 1}
              for i, s in [(1, 85), (2, 75), (3, 50), (4, 65), (5, 55), (7, 90), (8, 30)]]
    media = {i: {"romaji": f"S{i}", "english": None, "native": None, "genres": json.dumps(["Horror"]), "cover_url": None}
             for i in range(1, 9)}
    r = compare(mine, theirs, media)
    assert r["shared"] == 5 and r["overlap"] == pytest.approx(100 * 5 / 8)
    assert r["gap"] == pytest.approx(0.6) and r["agreement"] > 0.9 and r["genre_match"] == pytest.approx(100)
    assert [x["media_id"] for x in r["their_picks"]] == [7] and [x["media_id"] for x in r["my_picks"]] == [6]
    assert [x["media_id"] for x in r["both_loved"]] == [1]
    assert pearson([(1, 1), (2, 2)]) is None


# ---- pages ---------------------------------------------------------------------------------------------


@pytest.fixture
def seeded(services):
    services.repo.upsert_media([al_media_row(1, "Tokyo Ghoul", english="Tokyo Ghoul", start_year=2011),
                                al_media_row(2, "Ajin")])
    services.repo.replace_al_entries([{"entry_id": 101, "media_id": 1, "status": "COMPLETED", "progress": 144, "score": 100,
                                       "started_at": "2025-01", "completed_at": "2025-05-03", "fetched_at": "x"}])
    services.repo.replace_md_snapshot([md_manga_row("00000000-0000-4000-8000-000000000001", "Unmatched Library One")], {})
    return services


def test_search_pages(seeded):
    with TestClient(create_app(seeded)) as c:
        assert 'action="/search"' in c.get("/").text                       # the box in the header
        page = c.get("/search?q=tokyo").text
        assert "Tokyo Ghoul" in page and "On your list" in page and "/series/al/1" in page
        assert "Unmatched Library One" in c.get("/search/results?q=unmatched").text
        assert "Nothing stored here matches" in c.get("/search/results?q=zzz").text


def test_search_anilist(seeded):
    seeded.store_anilist_token("al-token-xyz")
    seeded.anilist_queue.set_interval(0)
    found = al_media(55, "Kemono Michi", english="Kemono Michi: Rise Up")
    with respx.mock(assert_all_mocked=True) as router:
        route = router.post(ANILIST_URL).respond(200, json={"data": {"Page": {"media": [found]}}})
        with TestClient(create_app(seeded)) as c:
            html = c.get("/search/anilist?q=kemono").text
            assert "Kemono Michi" in html and "/series/al/55" in html
            assert json.loads(route.calls[0].request.content)["variables"] == {"q": "kemono"}
            assert c.get("/series/al/55").status_code == 200                # cached, so the page opens


def test_year_and_compare_pages(seeded):
    seeded.store_anilist_token("al-token-xyz")
    seeded.anilist_queue.set_interval(0)
    their = al_media(2, "Ajin")
    their.update(tags=[], description="d")
    body = {"data": {"User": {"id": 9, "name": "Friend"}, "MediaListCollection": {"lists": [{"entries": [
        {"status": "COMPLETED", "progress": 80, "score": 90, "media": their},
        {"status": "COMPLETED", "progress": 144, "score": 95, "media": {**al_media(1, "Tokyo Ghoul"), "tags": []}}]}]}}}
    with TestClient(create_app(seeded), follow_redirects=True) as c:
        year = c.get("/stats/year").text
        assert "2025 in review" in year and "Tokyo Ghoul" in year
        with respx.mock(assert_all_mocked=True) as router:
            router.post(ANILIST_URL).respond(200, json=body)
            page = c.post("/stats/compare", data={"user": "friend"}).text
            assert "Friend loved, you haven" in page and "Ajin" in page and "You both loved" in page
        assert c.get("/stats/compare?user=Friend").status_code == 200        # saved: no request
        with respx.mock(assert_all_mocked=True) as router:
            router.post(ANILIST_URL).respond(404, json={"errors": [{"message": "Not Found.", "status": 404}],
                                                         "data": {"User": None}})
            assert "no user called nobody" in c.post("/stats/compare", data={"user": "nobody"}).text


def test_backup_round_trip_without_secrets(seeded):
    repo = seeded.repo
    repo.set_feedback("al:2", "loved", "Ajin", "page", genres=["Action"])
    repo.set_rank(1, "liked", 0)
    repo.set_setting("mangadex_session", {"access": "md-access-SECRET", "refresh": "r"})
    repo.set_setting("new_scan_hours", 12)
    data = make_backup(repo)
    text = json.dumps(data)
    assert "md-access-SECRET" not in text and "mangadex_session" not in text
    assert data["tables"]["rec_feedback"][0]["title"] == "Ajin"

    repo.clear_feedback("al:2")
    repo.unrank(1)
    repo.set_setting("new_scan_hours", 24)
    counts = restore(repo, data)
    assert counts["rec_feedback"] == 1 and counts["ranking"] == 1
    assert "al:2" in repo.feedback() and repo.get_setting("new_scan_hours") == 12
    with pytest.raises(BackupError):
        restore(repo, {"tables": {}})

    with TestClient(create_app(seeded)) as c:
        download = c.get("/backup/shiori.json")
        assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
        assert "md-access-SECRET" not in download.text
        assert "Tokyo Ghoul" in c.get("/backup/list.csv").text
        assert "Ajin" in c.get("/backup/ratings.csv").text
        ok = c.post("/backup/restore", files={"file": ("b.json", download.content, "application/json")})
        assert ok.status_code == 200 and "Restored" in ok.text
        bad = c.post("/backup/restore", files={"file": ("b.json", b"not json", "application/json")})
        assert bad.status_code == 400 and "a readable Shiori backup" in bad.text
