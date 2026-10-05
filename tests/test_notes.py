"""Notes on list entries: read from and saved to AniList, mirrored to MyAnimeList's comments on request."""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mdal.clients.anilist import ANILIST_URL
from mdal.clients.myanimelist import API_URL as MAL_URL
from mdal.recommend.llm import _entry
from mdal.web.app import create_app
from tests.factories import al_media_row, connect_mal


@pytest.fixture
def listed(services):
    services.store_anilist_token("al-token-xyz")
    services.anilist_queue.set_interval(0)
    services.mal_queue.set_interval(0)
    services.repo.upsert_media([al_media_row(1, "Tokyo Ghoul", id_mal=501)])
    services.repo.replace_al_entries([{"entry_id": 101, "media_id": 1, "status": "COMPLETED", "progress": 144,
                                       "score": 100, "fetched_at": "x"}])            # notes not read yet (NULL)
    return services


def anilist(router, sent):
    def handle(request):
        body = json.loads(request.content)
        sent.append(body)
        if "MediaList(id" in body["query"]:
            return httpx.Response(200, json={"data": {"MediaList": {"id": 101, "notes": "Kaneki <3"}}})
        return httpx.Response(200, json={"data": {"SaveMediaListEntry": {"id": 101, "mediaId": 1, "status": "COMPLETED", "score": 100}}})
    router.post(ANILIST_URL).mock(side_effect=handle)


def test_notes_are_read_once_then_saved_by_entry_id(listed):
    sent = []
    with respx.mock(assert_all_mocked=True) as router:
        anilist(router, sent)
        with TestClient(create_app(listed)) as c:
            page = c.get("/series/al/1").text
            assert 'hx-get="/series/al/1/notes" hx-trigger="load"' in page          # first visit reads them
            panel = c.get("/series/al/1/notes").text
            assert "Kaneki &lt;3" in panel and listed.repo.al_entries()[1]["notes"] == "Kaneki <3"
            assert 'hx-get="/series/al/1/notes"' not in c.get("/series/al/1").text  # kept: no second read
            done = c.post("/series/al/1/notes", data={"notes": "  Reread the Aogiri arc.  "}).text
            assert "Saved to AniList." in done and "Also save to MyAnimeList" not in done
    save = sent[-1]
    assert "SaveMediaListEntry(id: $e, notes: $n)" in save["query"] and save["variables"] == {"e": 101, "n": "Reread the Aogiri arc."}
    assert listed.repo.al_entries()[1]["notes"] == "Reread the Aogiri arc."
    assert listed.repo.list_edits()[0]["field"] == "notes"


def test_notes_mirror_to_mal_only_for_entries_on_your_mal_list(listed):
    connect_mal(listed)
    listed.repo.replace_mal_entries([{"mal_id": 501, "status": "completed", "progress": 144, "fetched_at": "x"}])
    listed.repo.update_al_entry(1, notes="")
    sent = []
    with respx.mock(assert_all_mocked=True) as router:
        anilist(router, sent)
        check = router.get(f"{MAL_URL}/manga/501").respond(200, json={"id": 501, "my_list_status": {"status": "completed", "comments": ""}})
        patch = router.patch(f"{MAL_URL}/manga/501/my_list_status").respond(200, json={"comments": "Great"})
        with TestClient(create_app(listed)) as c:
            assert "Also save to MyAnimeList" in c.get("/series/al/1").text
            done = c.post("/series/al/1/notes", data={"notes": "Great", "mal": "1"}).text
            assert "Saved to AniList and MyAnimeList." in done
            assert check.calls[0].request.url.params["fields"] == "my_list_status{comments}"
            assert patch.calls[0].request.content == b"comments=Great"

            check.respond(200, json={"id": 501})                                     # no longer on the MAL list
            failed = c.post("/series/al/1/notes", data={"notes": "Again", "mal": "1"}).text
            assert "Saved to AniList." in failed and "isn&#39;t on your MyAnimeList list" in failed
            assert patch.call_count == 1                                             # nothing was sent to create it


def test_notes_reach_the_model():
    line = _entry({"title": "Tokyo Ghoul", "status": "COMPLETED", "score": 100, "notes": "The ending\nbroke me " + "x" * 300})
    assert line.startswith('Tokyo Ghoul (completed, scored 100/100) - their note: "The ending broke me x')
    assert line.endswith('…"') and len(line) < 260
