import re

import pytest
import respx
from fastapi.testclient import TestClient

from mdal.matching.pipeline import AniListFetch, resolve_all
from mdal.web.app import create_app
from tests.factories import FakeAniList, al_media, al_media_row, md_manga_row


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fake(mock):
    f = FakeAniList()
    f.add_media(al_media(30013, "Pasted Manga"), al_media(555, "An Anime", type_="ANIME"))
    return f.install(mock)


@pytest.fixture
def client(services, fake):
    services.anilist_queue.set_interval(0)
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


@pytest.fixture
def seeded(services):
    repo = services.repo
    repo.replace_md_snapshot(
        [md_manga_row("k", "Kusuriya", alt_titles=["The Apothecary Diaries"]),
         md_manga_row("u", "Unmatched One", cover_file=None)],
        {"k": ["k1"]},
    )
    repo.upsert_chapters([{"chapter_id": "k1", "md_id": "k", "chapter": "10", "volume": None, "missing": 0, "fetched_at": "x"}])
    repo.upsert_media([al_media_row(7, "Kusuriya no Hitorigoto", english="The Apothecary Diaries"),
                       al_media_row(8, "Kusuriya no Hitorigoto (Novel)", format="NOVEL")])
    repo.replace_al_entries([{"entry_id": 70, "media_id": 7, "status": "CURRENT", "progress": 3, "fetched_at": "x"}])
    lh = md_manga_row("k", "Kusuriya", alt_titles=["The Apothecary Diaries"])["links_hash"]
    repo.save_match(
        {"md_id": "k", "al_media_id": None, "state": "review", "tier": 4, "confidence": 0.8,
         "reasons": ["title 0.80"], "links_hash": lh},
        [{"al_media_id": 7, "score": 0.8, "reasons": ["title 0.80 (title 'Kusuriya' ~ romaji)"]},
         {"al_media_id": 8, "score": 0.5, "reasons": ["format NOVEL -0.30"]}],
    )
    repo.save_match(
        {"md_id": "u", "al_media_id": None, "state": "unmatched", "tier": 4, "confidence": None,
         "reasons": ["no AniList search results"], "links_hash": md_manga_row("u", "U")["links_hash"]},
        [],
    )
    return repo


async def rematch(services, fake):
    before = fake.call_count
    await resolve_all(services.repo, AniListFetch(services.anilist, services.repo))
    return fake.call_count - before


def test_page_lists_review_and_unmatched(client, seeded, fake):
    html = client.get("/review").text
    assert "Needs review (1)" in html and "No match found (1)" in html
    assert "Kusuriya no Hitorigoto" in html and "score 0.80" in html
    assert "The Apothecary Diaries" in html
    assert fake.call_count == 0


def test_covers_are_lazy_and_no_referrer(client, seeded):
    html = client.get("/review").text
    imgs = re.findall(r"<img [^>]*>", html)
    assert imgs
    assert all('loading="lazy"' in i and 'referrerpolicy="no-referrer"' in i for i in imgs)
    assert "https://uploads.mangadex.org/covers/k/c.jpg.256.jpg" in html


async def test_accept_confirms_and_is_never_rematched(client, services, seeded, fake):
    response = client.post("/review/k/accept", data={"al_media_id": "7"})
    assert response.status_code == 200 and 'id="card-k"' in response.text and "Confirmed" in response.text
    m = seeded.get_mapping("k")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("confirmed", 1, 7)
    assert "Needs review (0)" in client.get("/review").text
    # Next sync: the confirmed series is not looked at again (u is unchanged and cached too).
    assert await rematch(services, fake) == 0
    assert seeded.get_mapping("k")["state"] == "confirmed"


def test_accept_rejects_non_candidates(client, seeded):
    response = client.post("/review/k/accept", data={"al_media_id": "999"})
    assert response.status_code == 400 and "no longer offered" in response.text
    assert seeded.get_mapping("k")["state"] == "review"


def test_manual_uncached_costs_one_request(client, seeded, fake):
    response = client.post("/review/u/manual", data={"text": "https://anilist.co/manga/30013/x"})
    assert response.status_code == 200 and "Confirmed: AniList 30013" in response.text
    assert fake.call_count == 1
    m = seeded.get_mapping("u")
    assert (m["state"], m["tier"], m["al_media_id"]) == ("confirmed", 1, 30013)


def test_manual_cached_costs_nothing(client, seeded, fake):
    client.post("/review/u/manual", data={"text": "7"})
    assert fake.call_count == 0
    assert seeded.get_mapping("u")["al_media_id"] == 7


@pytest.mark.parametrize(
    "text, error",
    [
        ("https://anilist.co/anime/555/x", "Paste an AniList manga id or URL"),
        ("555", "has no manga with id 555"),
        ("424242", "has no manga with id 424242"),
        ("garbage", "Paste an AniList manga id or URL"),
    ],
)
def test_manual_errors_change_nothing(client, seeded, text, error):
    response = client.post("/review/u/manual", data={"text": text})
    assert response.status_code == 400
    assert error in response.text and 'id="card-u"' in response.text
    assert seeded.get_mapping("u")["state"] == "unmatched"


async def test_not_on_anilist_and_undo(client, services, seeded, fake):
    response = client.post("/review/u/not-on-anilist")
    assert "not on AniList" in response.text and "Undo" in response.text
    html = client.get("/review").text
    assert "No match found (0)" in html and "Marked not on AniList (1)" in html
    assert await rematch(services, fake) == 0
    assert seeded.get_mapping("u")["state"] == "not_on_anilist"

    client.post("/review/u/retry")  # undo
    assert seeded.get_mapping("u") is None
    await rematch(services, fake)
    assert seeded.get_mapping("u") is not None  # matched again


def test_retry_deletes_mapping(client, seeded):
    client.post("/review/k/retry")
    assert seeded.get_mapping("k") is None
    assert seeded.candidates("k") == []


def test_decision_updates_open_diffed_run(client, services, seeded):
    run_id = seeded.create_run("diffed")
    seeded.replace_items(run_id, [{"run_id": run_id, "md_id": "k", "action": "skip", "reason": "awaiting match review"}])
    client.post("/review/k/accept", data={"al_media_id": "7"})
    item = {i["md_id"]: i for i in seeded.items(run_id)}["k"]
    assert (item["action"], item["md_progress"], item["al_progress"], item["al_entry_id"]) == ("write", 10, 3, 70)


def test_decision_leaves_older_runs_alone(client, services, seeded):
    run_id = seeded.create_run("cancelled")
    seeded.replace_items(run_id, [{"run_id": run_id, "md_id": "k", "action": "skip", "reason": "awaiting match review"}])
    client.post("/review/k/accept", data={"al_media_id": "7"})
    assert seeded.items(run_id)[0]["action"] == "skip"


def test_titles_escaped(client, services, seeded):
    seeded.replace_md_snapshot([md_manga_row("k", "<b>bold</b>")], {})
    html = client.get("/review").text
    assert "<b>bold</b>" not in html and "&lt;b&gt;bold&lt;/b&gt;" in html
