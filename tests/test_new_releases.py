"""New releases (MangaDex scan, description matching, the model's picks) and the Ask chat."""

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mdal.clients.mangadex import API_URL, TOKEN_URL
from mdal.clients.mangaupdates import API_URL as MU_URL
from mdal.fetch.anilist_list import plain_text
from mdal.fetch.mangadex_new import plain_description
from mdal.recommend import chat, fresh
from mdal.recommend.profile import build_profile
from mdal.web.app import create_app
from tests.factories import al_media_row, md_manga_row

OLLAMA = "http://127.0.0.1:11434"
WORDS = ["horror", "curse", "romance", "school", "isekai", "cooking"]


def uuid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


# ---- pure ---------------------------------------------------------------------------------


def test_plain_descriptions():
    assert plain_text("A <i>cursed</i> town.<br><br>Read on &amp; on.<br>(Source: Kodansha)") == "A cursed town.\nRead on & on."
    assert plain_text(None) == ""
    md = {"en": "**Bold** start with [a link](https://x.y).\n\n---\n**Links:**\n- [Raw](https://raw)", "ja": "日本語"}
    assert plain_description(md) == "Bold start with a link."
    assert plain_description({"ja": "日本語"}) == "日本語"
    assert plain_description([]) == ""


def test_tag_names_and_people_match_across_sites():
    profile = build_profile([
        {"media_id": 1, "title": "A", "status": "COMPLETED", "score": 95, "progress": 10, "format": "MANGA",
         "genres": ["Horror"], "tag_ranks": [{"name": "Vampire", "rank": 90}, {"name": "Yuri", "rank": 90}],
         "staff": [{"id": 7, "name": "Yusuke Murata"}]}])
    terms = fresh.taste_terms(profile)
    assert fresh.match_term("Vampires", terms).label == "Vampire"
    assert fresh.match_term("Girls' Love", terms).label == "Yuri"
    assert fresh.match_term("horror", terms).label == "Horror"
    assert fresh.match_term("Cooking", terms) is None
    assert fresh.person_key("Murata Yuusuke") == fresh.person_key("Yusuke Murata")


def test_similarity_prefers_liked_and_penalises_dropped():
    shelf = fresh.Shelf(liked=[(1.0, "Loved", fresh.unit([1, 0, 0]))], disliked=[(-0.6, "Dropped", fresh.unit([0, 1, 0]))])
    close, titles = fresh.similarity(fresh.unit([1, 0.1, 0]), shelf)
    far, _ = fresh.similarity(fresh.unit([0.1, 1, 0]), shelf)
    assert titles == ["Loved"] and close > 0.9 and far < 0
    assert fresh.unpack(fresh.pack([3, 4])) == pytest.approx([0.6, 0.8])


def test_clean_picks_and_reply_keep_only_real_numbers():
    recs = [fresh.make_new_rec({"md_id": uuid(i), "title": f"T{i}", "tags": "[]", "authors": "[]"}) for i in range(1, 4)]
    picks = fresh.clean_picks({"picks": [{"n": 2, "reason": "Good."}, {"n": 2, "reason": "dup"}, {"n": 9, "reason": "x"},
                                         {"n": "1", "reason": ""}, {"n": "3", "reason": "Fine."}, "junk"]}, recs)
    assert picks == [{"md_id": uuid(2), "reason": "Good."}, {"md_id": uuid(3), "reason": "Fine."}]

    items = [chat.Item(key=f"md:{i}", title=f"Title {i}", url="u", cover=None, meta=[], tags=[], description=None,
                       taste=0.5, adult=False, source="New on MangaDex") for i in range(1, 4)]
    reply, picks = chat.clean_reply({"reply": "Try #2 or (3), not 12.", "picks": [{"n": 2, "reason": "r"}, {"n": 40}]}, items)
    assert reply == "Try Title 2 or Title 3, not 12." and picks == [{"key": "md:2", "reason": "r"}]
    # The cards already show each pick: lines copying the listing go, and Markdown emphasis becomes plain text.
    reply, _ = chat.clean_reply({"reply": "Here you go:\n\n3. Ajin | 2012, Japan | tags: Horror\nLike **GANTZ** and *Tokyo Ghoul*.",
                                 "picks": []}, items)
    assert reply == "Here you go:\nLike GANTZ and Tokyo Ghoul."
    reply, _ = chat.clean_reply({"reply": "Title 1 is creepy. Both are short.", "picks": [{"n": 1, "reason": "Title 1 is creepy."}]},
                                items)
    assert reply == "Both are short."


def test_shortlist_blends_question_and_taste():
    def item(key, tags, taste):
        return chat.Item(key=key, title=key, url="u", cover=None, meta=[], tags=tags, description=None, taste=taste,
                         adult=False, source="AniList")
    items = [item("cook", ["Cooking"], 0.2), item("fav", ["Action"], 1.0), item("meh", ["Drama"], 0.1)]
    relevance = chat.keyword_relevance("any good cooking manga?", items)
    assert relevance["cook"] > 0 and relevance["fav"] == 0
    assert [i.key for i in chat.shortlist(items, relevance, 2)] == ["cook", "fav"]


# ---- a scan against fake MangaDex and Ollama --------------------------------------------------


def md_manga(n, title, *, tags=(), description="", rating="safe", links=None, authors=("Someone",)):
    return {"id": uuid(n), "type": "manga", "attributes": {
        "title": {"en": title}, "altTitles": [], "description": {"en": description} if description else {},
        "links": links or {}, "originalLanguage": "ja", "year": 2026, "status": "ongoing", "contentRating": rating,
        "publicationDemographic": "seinen", "lastChapter": None,
        "tags": [{"id": t, "type": "tag", "attributes": {"name": {"en": t}, "group": "genre"}} for t in tags]},
        "relationships": [{"id": f"a{n}", "type": "author", "attributes": {"name": a}} for a in authors]
        + [{"id": f"c{n}", "type": "cover_art", "attributes": {"fileName": f"{n}.jpg"}}]}


UPDATED = [
    md_manga(1, "Cursed Flesh", tags=["Horror", "Gore"], description="A horror tale of a curse that spreads."),
    md_manga(2, "Love Letters", tags=["Romance", "School Life"], description="A school romance."),
    md_manga(3, "Owned Already", tags=["Horror"], description="horror"),                     # in the library
    md_manga(4, "Followed One", tags=["Horror"], description="horror"),                      # followed
    md_manga(5, "Linked To List", tags=["Horror"], description="horror", links={"al": "11"}),  # on AniList
    md_manga(6, "Loved 2", tags=["Horror"], description="horror"),                         # same title as a list entry
    md_manga(7, "Adult Horror", tags=["Horror"], description="horror curse", rating="pornographic"),
    md_manga(8, "<script>bad()</script> Quiet Isekai", tags=["Isekai"]),                      # no description
]
ADDED = [md_manga(9, "Fresh Isekai", tags=["Isekai", "Fantasy"], description="An isekai adventure.")]


MU_SERIES = {"series_id": 4242, "title": "Cursed Flesh", "url": "https://www.mangaupdates.com/series/39u/cursed-flesh",
             "type": "Manga", "year": "2026", "status": "3 Volumes (Ongoing)", "latest_chapter": 31, "completed": False,
             "licensed": True, "bayesian_rating": 7.9, "rating_votes": 50,
             "publishers": [{"publisher_name": "Yen Press", "type": "English", "notes": "Ongoing"},
                            {"publisher_name": "Kodansha", "type": "Original", "notes": ""}],
             "categories": [{"category": "Curse/s", "votes": 9}, {"category": "Body Horror", "votes": 12}],
             "recommendations": [{"series_id": 77, "series_name": "Uzumaki", "series_url": "https://mu/uzumaki"}],
             "category_recommendations": []}


def fake_vector(text):
    lower = text.lower()
    return [lower.count(w) + 0.01 for w in WORDS]


def seed_list(repo):
    def media(i, title, genres, tags, description):
        return al_media_row(i, title, genres=json.dumps(genres), description=description,
                            tags=json.dumps([{"name": t, "rank": 90, "category": "Theme"} for t in tags]))
    loved = [media(i, f"Loved {i - 10}", ["Horror"], ["Gore"], "A horror story about a curse.") for i in (11, 12, 13)]
    read = [media(20 + i, f"Read {i}", ["Fantasy"], ["Isekai"], "An isekai adventure.") for i in range(4)]
    dropped = [media(30 + i, f"Dropped {i}", ["Romance"], ["School"], "A school romance.") for i in range(3)]
    repo.upsert_media(loved + read + dropped)
    entries = [(m["media_id"], "COMPLETED", 95) for m in loved] + [(m["media_id"], "CURRENT", None) for m in read]
    entries += [(m["media_id"], "DROPPED", None) for m in dropped]
    repo.replace_al_entries([{"entry_id": 100 + i, "media_id": m, "status": s, "progress": 20, "score": sc,
                              "fetched_at": "2026-10-03T00:00:00+00:00"} for i, (m, s, sc) in enumerate(entries)])
    repo.replace_md_snapshot([md_manga_row(uuid(3), "Owned Already")], {})


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def world(services, mock):
    services.mangadex_queue.set_interval(0)
    seed_list(services.repo)
    mock.post(TOKEN_URL).respond(200, json={"access_token": "md-access-1111", "refresh_token": "md-refresh-1111"})

    def manga(request):
        params = request.url.params
        data = UPDATED if params.get("order[latestUploadedChapter]") else ADDED
        return httpx.Response(200, json={"result": "ok", "data": data, "total": len(data)})

    services.mangaupdates_queue.set_interval(0)
    routes = {
        "mu_search": mock.post(f"{MU_URL}/series/search").respond(200, json={"results": [
            {"record": {"series_id": 4242, "title": "Cursed Flesh", "year": "2026"}, "hit_title": "Cursed Flesh"}]}),
        "mu_series": mock.get(url__regex=rf"{MU_URL}/series/\d+").respond(200, json=MU_SERIES),
        "manga": mock.get(f"{API_URL}/manga").mock(side_effect=manga),
        "follows": mock.get(f"{API_URL}/user/follows/manga").respond(
            200, json={"result": "ok", "data": [{"id": uuid(4)}], "total": 1}),
    }
    return routes


def ollama(mock, chat_content=None, *, embedder=True):
    models = [{"name": "gpt-oss:20b", "capabilities": ["completion", "thinking"]}]
    if embedder:
        models.append({"name": "qwen3-embedding:0.6b", "capabilities": ["embedding"]})
    mock.get(f"{OLLAMA}/api/tags").respond(200, json={"models": models})

    def embed(request):
        texts = json.loads(request.content)["input"]
        return httpx.Response(200, json={"embeddings": [fake_vector(t) for t in texts]})

    routes = {"embed": mock.post(f"{OLLAMA}/api/embed").mock(side_effect=embed)}
    if chat_content is not None:
        routes["chat"] = mock.post(f"{OLLAMA}/api/chat").respond(200, json={"message": {"content": json.dumps(chat_content)}})
    return routes


async def scan(services):
    services.releases.start_scan()
    await services.releases.task


async def test_scan_excludes_what_you_have_and_ranks_by_description(services, world, mock):
    model = ollama(mock, {"picks": [{"n": 1, "reason": "Curses, like Loved 1."}, {"n": 2, "reason": "Isekai."},
                                    {"n": 3, "reason": "Also fine."}, {"n": 99, "reason": "invented"}]})
    await scan(services)
    status = services.releases.status()
    assert status["state"] == "done", status
    assert status["requests"] == 3                                   # 2 pages of /manga, 1 of follows

    sent = world["manga"].calls[0].request.url.params
    assert sent.get("availableTranslatedLanguage[]") == "en" and sent.get("hasAvailableChapters") == "true"
    assert set(sent.get_list("contentRating[]")) == {"safe", "suggestive", "erotica", "pornographic"}

    _, recs = services.releases.recs()
    ranked = [r.title for r in fresh.ranked_new(recs)]
    assert set(ranked) == {"Cursed Flesh", "Love Letters", "<script>bad()</script> Quiet Isekai", "Fresh Isekai"}
    assert ranked[0] == "Cursed Flesh" and ranked.index("Fresh Isekai") < ranked.index("Love Letters")
    top = next(r for r in recs if r.title == "Cursed Flesh")
    assert top.similar_to[0].startswith("Loved") and "Tags you like: Horror, Gore" in top.reasons
    assert "Adult Horror" in {r.title for r in services.releases.recs(include_adult=True)[1]}

    picks = services.repo.get_setting("new_llm")["picks"]
    assert [p["md_id"] for p in picks] == [r.md_id for r in fresh.ranked_new(recs, 3)]
    prompt = json.loads(model["chat"].calls[0].request.content)["messages"][1]["content"]
    assert "A horror tale of a curse that spreads." in prompt and "Loved 1" in prompt

    # The picks were looked up on MangaUpdates: by title here, since these test series have no MangaUpdates link.
    mu = json.loads(services.repo.cached(f"md:{uuid(1)}", "mu")["data"])
    assert mu["latest_chapter"] == 31 and mu["licensed"] and mu["categories"][0] == "Body Horror"
    assert mu["english"] == [{"name": "Yen Press", "notes": "Ongoing"}]

    # A second scan only embeds what changed.
    embedded = sum(len(json.loads(c.request.content)["input"]) for c in model["embed"].calls)
    await scan(services)
    assert sum(len(json.loads(c.request.content)["input"]) for c in model["embed"].calls) == embedded


async def test_scan_without_embedding_model_still_ranks_by_tags(services, world, mock):
    ollama(mock, {"picks": []}, embedder=False)
    await scan(services)
    status = services.releases.status()
    assert status["state"] == "done" and "description matching skipped (install qwen3-embedding:0.6b" in status["detail"]
    assert "model not used: the model returned only 0 usable picks" in status["detail"]
    _, recs = services.releases.recs()
    assert fresh.ranked_new(recs)[0].title == "Cursed Flesh" and all(r.similarity is None for r in recs)


async def test_scan_stops_cleanly_when_mangadex_fails(services, world, mock):
    world["manga"].respond(500, json={"errors": [{"detail": "boom"}]})
    await scan(services)
    status = services.releases.status()
    assert status["state"] == "failed" and "HTTP 500" in status["error"]


# ---- pages ---------------------------------------------------------------------------------


@pytest.fixture
def client(services):
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


def test_empty_pages_render_without_requests(client, mock):
    mock.get(f"{OLLAMA}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    for path in ("/discover/new", "/discover/ask"):
        assert client.get(path).status_code == 200, path
    assert "Not scanned yet" in client.get("/discover/new").text
    assert "nothing to suggest yet" in client.get("/discover/ask").text
    assert "Start it to ask" in client.get("/discover-models?panel=ask").text
    assert client.post("/discover/ask", data={"message": "  "}).status_code == 204
    answer = client.post("/discover/ask", data={"message": "horror please"}).text
    assert "Ollama is not running" in answer and "horror please" in answer
    assert "data-retry" in answer and 'data-question="horror please"' in answer   # the page offers Try again
    assert client.get("/discover-new-status").status_code == 200
    assert not [c for c in mock.calls if "mangadex" in str(c.request.url)]


async def test_new_releases_page_and_hiding(services, world, mock):
    ollama(mock, {"picks": [{"n": 1, "reason": "Curses, like Loved 1."}, {"n": 2, "reason": "Isekai."},
                            {"n": 3, "reason": "Also fine."}]})
    await scan(services)
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.get("/discover/new").text
        assert "Curses, like Loved 1." in html and "chosen by gpt-oss:20b" in html
        assert "A horror tale of a curse that spreads." in html
        assert "<script>bad()</script>" not in html and "&lt;script&gt;" in html
        for gone in range(3, 8):                                  # owned, followed, on AniList, same title, adult
            assert uuid(gone) not in html, gone
        assert "Adult Horror" in c.get("/discover/new?adult=1").text
        assert f"https://mangadex.org/title/{uuid(1)}" in html and f"covers/{uuid(1)}/1.jpg.256.jpg" in html

        assert c.post(f"/discover-new-hide/{uuid(1)}").text == ""
        assert "Cursed Flesh" not in c.get("/discover/new").text
        assert c.post("/discover-new-hide/not-a-uuid").status_code == 404
        c.post("/discover-new-unhide-all")
        assert "Cursed Flesh" in c.get("/discover/new").text

        picker = c.get("/discover-models?panel=new").text
        assert 'hx-target="#new-status"' in picker and "qwen3-embedding:0.6b" in picker
        assert c.post("/discover-embed-model", data={"model": "gpt-oss:20b", "panel": "new"}).status_code == 400
        assert c.post("/discover-model", data={"model": "gpt-oss:20b", "panel": "new"}).text.startswith('<div id="new-status"')


async def test_ask_answers_from_the_pool_only(services, world, mock):
    ollama(mock, {"picks": [{"n": 1, "reason": "Curses."}]})
    await scan(services)
    reply = {"reply": "Start with #1.", "picks": [{"n": 1, "reason": "A creeping curse."}, {"n": 500, "reason": "invented"}]}
    route = mock.post(f"{OLLAMA}/api/chat").respond(200, json={"message": {"content": json.dumps(reply)}})
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.post("/discover/ask", data={"message": "Something with a curse"}).text
        assert "Something with a curse" in html and "A creeping curse." in html
        assert "invented" not in html and "#1" not in html

        sent = json.loads(route.calls[-1].request.content)
        assert sent["format"]["required"] == ["picks", "reply"]
        assert "Loved 1" in sent["messages"][0]["content"]                 # your taste goes in the system prompt
        listing = sent["messages"][-1]["content"]
        assert listing.startswith("Something with a curse") and "1. Cursed Flesh" in listing
        assert "Owned Already" not in listing and ". Loved 2 |" not in listing

        c.post("/discover/ask", data={"message": "more like that"})
        follow_up = json.loads(route.calls[-1].request.content)["messages"]
        assert [m["role"] for m in follow_up[1:]] == ["user", "assistant", "user"]
        assert follow_up[2]["content"] == "I suggested: Cursed Flesh."     # titles only, never its old wording

        page = c.get("/discover/ask").text
        assert page.count('class="chat-msg user"') == 2 and "A creeping curse." in page
        c.post("/discover-chat-clear")
        assert services.repo.chat_messages() == []


def test_list_request_stores_descriptions_and_other_requests_keep_them(services):
    from mdal.fetch.anilist_list import LIST_QUERY, media_row

    assert "description(asHtml: false)" in LIST_QUERY
    media = {"id": 5, "title": {"romaji": "X"}, "description": "Old <b>town</b>."}
    services.repo.upsert_media([media_row(media, "2026-10-03T00:00:00+00:00")])
    del media["description"]                                    # e.g. a lookup by id, which doesn't ask for it
    services.repo.upsert_media([media_row(media, "2026-10-04T00:00:00+00:00")])
    assert services.repo.descriptions([5]) == {5: "Old town."}


async def test_page_reloads_when_the_scan_it_watched_finishes(services, world, mock):
    ollama(mock, {"picks": []})
    await scan(services)
    with TestClient(create_app(services), follow_redirects=False) as c:
        assert c.get("/discover-new-status?watch=1").headers.get("HX-Refresh") == "true"
        assert "HX-Refresh" not in c.get("/discover-new-status").headers


def test_descriptions_drop_publisher_notes_and_credits():
    assert plain_text("A stone.<br>(Source: VIZ Media)<br>Note: Includes eight extra chapters.") == "A stone."
    assert plain_text("The battle.<br><br>Note: Includes 7 extra chapters.") == "The battle."
    assert plain_text("Notes are passed in class.") == "Notes are passed in class."
    assert plain_description({"en": "He bites back.\n\nAlt. Official English: Yen Press"}) == "He bites back."


def test_model_label_shortens_hugging_face_names():
    from mdal.web.app import model_label

    assert model_label("hf.co/unsloth/Qwen3.5-35B-A3B-GGUF:UD-Q6_K_XL") == "Qwen3.5-35B-A3B"
    assert model_label("gpt-oss:20b") == "gpt-oss:20b"


# ---- named series: the reference, never a suggestion ---------------------------------------------


def test_find_named_prefers_the_longest_title_and_ignores_short_ones():
    index = {"cursed flesh": ("md:1", "Cursed Flesh"), "cursed": ("md:2", "Cursed"), "real": ("al:3", "Real")}
    assert chat.find_named("I just read Cursed Flesh, more like it? Real talk.", index) == [("md:1", "Cursed Flesh")]
    assert chat.find_named("anything cursed", index) == [("md:2", "Cursed")]


def test_reply_sentences_naming_unpicked_series_are_dropped():
    items = [chat.Item(key=f"md:{i}", title=t, url="u", cover=None, meta=[], tags=[], description=None, taste=0.5,
                       adult=False, source="MangaDex") for i, t in enumerate(["Choujin X", "Kane no Naru Mori"], 1)]
    reply, _ = chat.clean_reply({"reply": "I recommend Choujin X for horror. Kane no Naru Mori is darker. Both are short.",
                                 "picks": [{"n": 2, "reason": "Dark."}]}, items, others=["Isekai Neko"])
    assert reply == "Kane no Naru Mori is darker. Both are short."


def test_shortlist_keeps_room_for_anilist_candidates():
    def item(key, taste):
        return chat.Item(key=key, title=key, url="u", cover=None, meta=[], tags=[], description=None, taste=taste,
                         adult=False, source="x")
    items = [item(f"md:{i}", 1.0) for i in range(10)] + [item(f"al:{i}", 0.1) for i in range(5)]
    picked = chat.shortlist(items, {}, n=6, reserve=2)
    assert len(picked) == 6 and sum(i.key.startswith("al:") for i in picked) == 2


async def test_a_named_series_is_the_reference_and_anilist_adds_its_neighbours(services, world, mock):
    from mdal.clients.anilist import ANILIST_URL
    from tests.factories import al_media

    ollama(mock, {"picks": [{"n": 1, "reason": "Curses."}]})
    await scan(services)
    services.anilist_queue.set_interval(0)
    services.store_anilist_token("al-token-xyz")
    neighbour = al_media(900, "Curse Garden", genres=["Horror"], status="FINISHED", chapters=40)
    neighbour.update(description="A garden where every <i>curse</i> takes root.", tags=[], isAdult=False, meanScore=80,
                     staff={"edges": []})
    anchor = al_media(950, "Cursed Flesh", genres=["Horror"])
    anchor.update(description="A horror tale of a curse that spreads.", isAdult=False,
                  recommendations={"nodes": [{"rating": 25, "mediaRecommendation": neighbour}]})
    anilist = mock.post(ANILIST_URL).respond(200, json={"data": {"Media": anchor}})
    route = mock.post(f"{OLLAMA}/api/chat").respond(200, json={"message": {"content": json.dumps({
        "reply": "Curse Garden is the closest. Love Letters is lighter.", "picks": [{"n": 1, "reason": "Same creeping curse."}]})}})
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.post("/discover/ask", data={"message": "I just read Cursed Flesh, can you give me another like this?"}).text
        sent = json.loads(anilist.calls[0].request.content)
        assert sent["variables"] == {"q": "Cursed Flesh"} and "recommendations" in sent["query"]
        prompt = json.loads(route.calls[-1].request.content)["messages"][-1]["content"]
        reference, listing = prompt.split("---")
        assert "Cursed Flesh (I've read it; don't suggest it)" in reference
        assert ". Cursed Flesh |" not in listing                               # never offered back
        assert "1. Curse Garden" in listing and "takes root" in listing          # AniList's neighbour, with its description
        assert "Same creeping curse." in html and "Curse Garden" in html and "anilist.co/manga/900" in html
        assert "Love Letters is lighter" not in html                              # named but not picked: dropped
        assert "Same creeping curse." in c.get("/discover/ask").text             # the card survives a reload


def test_reply_never_cites_list_numbers():
    items = [chat.Item(key=f"md:{i}", title=f"Title {i}", url="u", cover=None, meta=[], tags=[], description=None,
                       taste=0.5, adult=False, source="MangaDex") for i in range(1, 17)]
    reply, _ = chat.clean_reply({"reply": "Number 1 is closest. Option 2 is lighter, while 15 and 16 are funnier. "
                                          "It runs 120 chapters.", "picks": [{"n": i, "reason": "r"} for i in (1, 2, 15, 16)]}, items)
    assert reply == "Title 1 is closest. It runs 120 chapters."


# ---- what was asked decides --------------------------------------------------------------------


def tagged_item(key, tags, taste=0.5):
    return chat.Item(key=key, title=key, url="u", cover=None, meta=[], tags=tags, description=None, taste=taste,
                     adult=False, source="x")


def test_tags_in_the_question_filter_the_pool():
    items = [tagged_item("iso", ["Isekai", "Comedy"]), tagged_item("vamp", ["Romance", "Vampires", "Gore"]),
             tagged_item("hor", ["Horror", "Gore"], taste=1.0), tagged_item("fmt", ["Long Strip"])]
    assert chat.asked_tags("A isekai manga", items) == ({"isekai"}, set())
    assert chat.asked_tags("something with vampires but no gore", items) == ({"vampire"}, {"gore"})
    assert chat.asked_tags("a long strip one", items) == (set(), set())                  # format tags don't count
    assert [i.key for i in chat.filter_by_tags(items, {"isekai"}, set())] == ["iso"]
    assert [i.key for i in chat.filter_by_tags(items, set(), {"gore"})] == ["iso", "fmt"]
    assert chat.tag_overlap(["Horror", "Gore", "Adaptation"], items)["vamp"] == 0.5


async def test_a_genre_question_only_offers_that_genre_and_nothing_already_suggested(services, world, mock):
    ollama(mock, {"picks": [{"n": 1, "reason": "Curses."}]})
    await scan(services)
    route = mock.post(f"{OLLAMA}/api/chat").respond(200, json={"message": {"content": json.dumps(
        {"reply": "Here you go.", "picks": [{"n": 1, "reason": "Fits."}]})}})
    with TestClient(create_app(services), follow_redirects=False) as c:
        c.post("/discover/ask", data={"message": "Something creepy"})
        first = json.loads(route.calls[-1].request.content)["messages"][-1]["content"].split("---")[1]
        picked = first.split("\n")[1].split(". ", 1)[1].split(" |")[0]       # the series it suggested
        c.post("/discover/ask", data={"message": "A isekai manga"})
        listing = json.loads(route.calls[-1].request.content)["messages"][-1]["content"].split("---")[1]
        offered = [line.split(". ", 1)[1].split(" |")[0] for line in listing.splitlines() if line[:1].isdigit()]
        assert offered and set(offered) <= {"Fresh Isekai", "<script>bad()</script> Quiet Isekai"}
        assert picked not in offered or "Isekai" in picked
        assert "fit with their taste" not in listing


async def test_a_series_named_earlier_stays_read(services, world, mock):
    ollama(mock, {"picks": [{"n": 1, "reason": "Curses."}]})
    await scan(services)
    route = mock.post(f"{OLLAMA}/api/chat").respond(200, json={"message": {"content": json.dumps(
        {"reply": "Here you go.", "picks": []})}})
    with TestClient(create_app(services), follow_redirects=False) as c:
        c.post("/discover/ask", data={"message": "I just read Cursed Flesh, another like it?"})
        c.post("/discover/ask", data={"message": "A horror manga"})
        listing = json.loads(route.calls[-1].request.content)["messages"][-1]["content"].split("---")[1]
        assert ". Cursed Flesh |" not in listing
