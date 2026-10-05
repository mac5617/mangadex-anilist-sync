"""Your ratings, series pages, MangaUpdates, MyAnimeList recommendations, and the background scan."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from mdal.clients.anilist import ANILIST_URL
from mdal.clients.myanimelist import API_URL as MAL_URL
from mdal.fetch.mal_recs import favourites_on_mal
from mdal.fetch.mangaupdates import mu_id_from_link, pick_match, summarize
from mdal.recommend import feedback, fresh
from mdal.web.app import create_app
from tests.factories import al_media, connect_mal
from tests.test_new_releases import MU_SERIES, mock, ollama, scan, uuid, world  # noqa: F401  (fixtures)


# ---- pure ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, verdict", [
    ("I just read Cursed Flesh, can you give me another like this?", "liked"),
    ("I just read Cursed Flesh", "read"),
    ("Loved Cursed Flesh, more please", "loved"),
    ("Cursed Flesh was boring", "disliked"),
    ("I didn't like Cursed Flesh", "disliked"),
    ("Something like Cursed Flesh", None),
    ("I'd love something like Cursed Flesh", None),
])
def test_detect_verdict(text, verdict):
    assert feedback.detect_verdict(text) == verdict


def test_a_verdict_counts_like_a_score():
    row = {"key": "md:x", "verdict": "loved", "title": "X", "genres": '["Horror"]', "tags": '["Gore"]',
           "staff": "[]", "description": "d"}
    e = feedback.pseudo_entry(row)
    assert e["score"] == 95 and e["genres"] == ["Horror"] and e["tag_ranks"][0]["name"] == "Gore"
    assert feedback.pseudo_entry({**row, "verdict": "read"}) is None
    assert feedback.split_tags(["Action", "Isekai", "Girls' Love", "Romance"]) == (["Action", "Romance"], ["Isekai", "Girls' Love"])
    assert feedback.stronger("read", "loved") is False and feedback.stronger("liked", "read") is True


def test_mangaupdates_matching():
    assert mu_id_from_link("671g9vp") == 13486396165
    assert mu_id_from_link("12345") is None and mu_id_from_link(None) is None
    results = [{"record": {"series_id": 1, "title": "Choujin X", "year": "1990"}, "hit_title": "Choujin X"},
               {"record": {"series_id": 2, "title": "Choujin X", "year": "2021"}, "hit_title": "Choujin X"},
               {"record": {"series_id": 3, "title": "Choujin Locke", "year": "2021"}, "hit_title": "Choujin Locke"}]
    assert pick_match(results, ["Choujin X"], 2021) == 2
    assert pick_match(results, ["Something Else"], 2021) is None
    s = summarize(MU_SERIES)
    assert s["latest_chapter"] == 31 and s["english"] == [{"name": "Yen Press", "notes": "Ongoing"}]
    assert fresh.english_meta(s) == ["EN ch. 31", "licensed in English"]


# ---- ratings ------------------------------------------------------------------------------------


@pytest.fixture
async def scanned(services, world, mock):
    ollama(mock, {"picks": [{"n": 1, "reason": "Curses."}]})
    await scan(services)
    return services


def client_for(services):
    return TestClient(create_app(services), follow_redirects=False)


async def test_rating_a_series_shapes_taste_and_stops_recommending_it(scanned, mock):
    services = scanned
    before = services.recommender.profile()[0]
    with client_for(services) as c:
        html = c.post(f"/series/md/{uuid(2)}/rate", data={"verdict": "loved"}).text
        assert "Loved it" in html and 'aria-pressed="true"' in html
        row = services.repo.feedback()[f"md:{uuid(2)}"]
        assert row["title"] == "Love Letters" and json.loads(row["genres"]) == ["Romance"]   # snapshot kept
        assert uuid(2) not in {r.md_id for r in services.releases.recs()[1]}            # read: not recommended
        after = services.recommender.profile()[0]
        assert after.genres["Romance"].affinity > before.genres["Romance"].affinity

        page = c.get("/discover/ratings").text
        assert "Love Letters" in page and f"/series/md/{uuid(2)}" in page
        assert "Removed." in c.post(f"/series/md/{uuid(2)}/rate", data={"verdict": "clear"},
                                    headers={"HX-Target": f"rating-md-{uuid(2)}"}).text
        assert f"md:{uuid(2)}" not in services.repo.feedback()
        assert c.post(f"/series/md/{uuid(2)}/rate", data={"verdict": "meh"}).status_code == 400
        assert c.post("/series/md/not-a-uuid/rate", data={"verdict": "loved"}).status_code == 404


async def test_not_interested_is_a_mild_dislike_and_undone_by_show_hidden(scanned):
    services = scanned
    with client_for(services) as c:
        c.post(f"/discover-new-hide/{uuid(2)}")
        assert services.repo.feedback()[f"md:{uuid(2)}"]["verdict"] == "not_interested"
        c.post("/discover-new-unhide-all")
        assert f"md:{uuid(2)}" not in services.repo.feedback()


async def test_ask_saves_what_you_say_about_a_series(scanned, mock):
    services = scanned
    mock.post(f"{ollama_url()}/api/chat").respond(200, json={"message": {"content": json.dumps(
        {"reply": "Here.", "picks": []})}})
    with client_for(services) as c:
        html = c.post("/discover/ask", data={"message": "I just read Love Letters and loved it, more like that?"}).text
        assert "Saved: you loved Love Letters" in html and "/discover/ratings" in html
        assert services.repo.feedback()[f"md:{uuid(2)}"]["source"] == "ask"
        assert "Saved: you loved Love Letters" in c.get("/discover/ask").text   # kept with the message


def ollama_url():
    return "http://127.0.0.1:11434"


# ---- series pages -------------------------------------------------------------------------------


async def test_series_page_and_its_lookup(scanned, mock):
    services = scanned
    services.anilist_queue.set_interval(0)
    services.store_anilist_token("al-token-xyz")
    neighbour = al_media(900, "Curse Garden", genres=["Horror"])
    neighbour.update(tags=[], isAdult=False, meanScore=80, staff={"edges": []}, description="A garden of curses.")
    anchor = al_media(950, "Cursed Flesh", genres=["Horror"])
    anchor.update(isAdult=False, tags=[{"name": "Curses", "rank": 90}], description="x",
                  recommendations={"nodes": [{"rating": 25, "mediaRecommendation": neighbour}]})
    mock.post(ANILIST_URL).respond(200, json={"data": {"Media": anchor}})
    with client_for(services) as c:
        page = c.get(f"/series/md/{uuid(1)}").text
        assert "Cursed Flesh" in page and "A horror tale of a curse that spreads." in page
        assert f'hx-post="/series/md/{uuid(1)}/lookup" hx-trigger="load"' in page      # first visit looks it up
        assert "Description close to" in page                                          # why it's recommended
        details = c.post(f"/series/md/{uuid(1)}/lookup").text
        assert "Yen Press" in details and "EN ch. 31" in details and "Body Horror" in details
        assert "AniList readers also recommend" in details and "/series/al/900" in details and "+25" in details
        assert "Uzumaki" in details                                                     # MangaUpdates readers
        again = c.get(f"/series/md/{uuid(1)}").text
        assert 'hx-trigger="load"' not in again                                         # cached for a week
        assert c.get("/series/al/900").status_code == 200                               # AniList pages work too
        assert c.get(f"/series/md/{uuid(99)}").status_code == 404
        assert c.get("/series/xx/1").status_code == 404
        cards = c.get("/discover/new").text
        assert f'href="/series/md/{uuid(1)}"' in cards and "EN ch. 31" in cards        # cards open the page


async def test_list_entries_have_pages_that_say_so(scanned):
    with client_for(scanned) as c:
        page = c.get("/series/al/11").text
        assert "On your AniList list" in page and "Your score" in page and "/series/al/11/score" in page


# ---- MyAnimeList ----------------------------------------------------------------------------------


async def test_favourites_on_mal_become_sources(services, mock):
    connect_mal(services)
    services.anilist_queue.set_interval(0)
    services.mal_queue.set_interval(0)
    services.store_anilist_token("al-token-xyz")
    services.repo.upsert_media([{**_media_row(1, "Fav"), "id_mal": 501}])
    mal = mock.get(f"{MAL_URL}/manga/501").respond(200, json={"recommendations": [
        {"node": {"id": 777, "title": "MAL Pick"}, "num_recommendations": 9}]})
    pick = al_media(70, "MAL Pick", genres=["Horror"], id_mal=777)
    pick.update(tags=[], isAdult=False, meanScore=70, staff={"edges": []}, description="d")
    mock.post(ANILIST_URL).respond(200, json={"data": {"Page": {"media": [pick]}}})
    sources, sent = await favourites_on_mal(services.mal, services.anilist, services.repo,
                                            [{"media_id": 1, "title": "Fav", "weight": 1.0}])
    assert sources == {70: [{"kind": "community", "via": 1, "label": "Fav", "rating": 9, "site": "MyAnimeList"}]}
    assert sent == 2 and mal.calls[0].request.url.params["fields"] == "recommendations"
    await favourites_on_mal(services.mal, services.anilist, services.repo, [{"media_id": 1, "title": "Fav", "weight": 1.0}])
    assert mal.call_count == 1                                   # cached for a week


def _media_row(media_id, title):
    from tests.factories import al_media_row
    return al_media_row(media_id, title)


# ---- background scan and the Home digest ------------------------------------------------------------


def test_scan_is_due_only_when_safe(services):
    r = services.releases
    assert r.scan_due()                                          # never scanned
    services.repo.set_setting("new_status", {"state": "done", "finished_at": datetime.now(UTC).isoformat()})
    assert not r.scan_due()
    old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    services.repo.set_setting("new_status", {"state": "done", "finished_at": old})
    assert r.scan_due()
    services.mangadex_guard.start_cooldown(9e12, "test")
    assert not r.scan_due()                                      # MangaDex cooldown
    services.mangadex_guard.clear_cooldown()
    services.repo.set_setting("new_scan_hours", 0)
    assert not r.scan_due() and r.next_scan() is None            # turned off


async def test_schedule_starts_a_scan(services, monkeypatch):
    started = []
    monkeypatch.setattr(type(services.releases), "start_scan", lambda self, scheduled=False: started.append(scheduled))
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 2:
            raise RuntimeError("stop")

    with pytest.raises(RuntimeError):
        await services.releases.run_schedule(sleep=fake_sleep)
    assert started == [True, True] and sleeps[0] == 120.0


async def test_home_shows_new_picks_until_you_look(scanned):
    with client_for(scanned) as c:
        home = c.get("/").text
        assert "4 new picks since you last looked" in home and f"/series/md/{uuid(1)}" in home
        c.get("/discover/new")
        again = c.get("/").text
        assert "Nothing new since you last looked" in again and "Next scan" in again


async def test_an_anilist_only_series_is_found_on_mangadex_by_its_link(services, mock):
    from mdal.clients.mangadex import API_URL as MD_URL, TOKEN_URL
    from mdal.clients.mangaupdates import API_URL as MU_URL
    from tests.factories import al_media_row

    services.mangadex_queue.set_interval(0)
    services.mangaupdates_queue.set_interval(0)
    services.repo.upsert_media([al_media_row(103843, "Kemono Michi", id_mal=107261)])
    mock.post(TOKEN_URL).respond(200, json={"access_token": "md-access-1111", "refresh_token": "md-refresh-1111"})
    search = mock.get(f"{MD_URL}/manga").respond(200, json={"result": "ok", "data": [
        {"id": uuid(81), "attributes": {"title": {"en": "Kemono Michi"}, "links": {"al": "81123"}}},     # same title
        {"id": uuid(82), "attributes": {"title": {"ja-ro": "Kemono Michi"}, "links": {"al": "103843"}}},
    ]})
    mock.post(f"{MU_URL}/series/search").respond(200, json={"results": []})
    with client_for(services) as c:
        before = c.get("/series/al/103843").text
        assert "mangadex.org/search?q=Kemono%20Michi" in before and "Search MangaDex" in before
        c.post("/series/al/103843/lookup")
        assert search.calls[0].request.url.params["title"] == "Kemono Michi"
        after = c.get("/series/al/103843").text
        assert f"https://mangadex.org/title/{uuid(82)}" in after and uuid(81) not in after
        assert "Search MangaDex" not in after


def test_big_covers_only_ask_anilist_for_sizes_that_exist():
    from mdal.recommend.series import big_cover

    base = "https://s4.anilist.co/file/anilistcdn/media/manga/cover"
    assert big_cover(f"{base}/small/bx101311-abc.jpg") == f"{base}/large/bx101311-abc.jpg"
    assert big_cover(f"{base}/small/b75275-6sw70Ah3Y94V.jpg") == f"{base}/medium/b75275-6sw70Ah3Y94V.jpg"   # old upload
    assert big_cover("https://uploads.mangadex.org/covers/x/c.jpg.256.jpg").endswith(".512.jpg")


def test_mangaupdates_recommendations_keep_their_covers_and_old_records_are_refetched(services):
    rec = {"series_id": 5, "series_name": "Bones", "series_url": "https://mu/bones",
           "series_image": {"url": {"original": "https://cdn.mangaupdates.com/image/i1.jpg",
                                    "thumb": "https://cdn.mangaupdates.com/image/thumb/i1.jpg"}}}
    s = summarize({**MU_SERIES, "recommendations": [rec]})
    assert s["recommendations"][0]["cover"] == "https://cdn.mangaupdates.com/image/thumb/i1.jpg"
    services.repo.upsert_media([_media_row(7, "Seven")])
    old = {**s, "recommendations": [{"id": 5, "name": "Bones", "url": "https://mu/bones"}]}   # saved before covers
    services.repo.cache("al:7", "mu", old)
    services.repo.cache("al:7", "checked", {"problems": []})
    assert services.series.looked_up("al:7") is False          # so the page looks it up again
    services.repo.cache("al:7", "mu", s)
    assert services.series.looked_up("al:7") is True
