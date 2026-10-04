"""Recommendations: taste profile, scoring, the model's answer, the refresh and the Discover pages."""

import json
import re

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mdal.clients.ollama import OllamaClient, OllamaUnavailable
from mdal.recommend.graph import build_graph
from mdal.recommend.llm import build_prompt, clean_answer
from mdal.recommend.profile import build_profile, entry_weight
from mdal.recommend.score import ranked, score_recs
from mdal.web.app import create_app
from tests.factories import FakeAniList, al_media

OLLAMA = "http://127.0.0.1:11434"


# ---- profile ---------------------------------------------------------------------------


@pytest.mark.parametrize("status, score, progress, expected", [
    ("PLANNING", None, 0, None),
    ("CURRENT", 100, 3, 1.5),
    ("CURRENT", 25, 3, -1.0),
    ("COMPLETED", None, 50, 0.6),
    ("DROPPED", None, 10, -0.6),
])
def test_entry_weight(status, score, progress, expected):
    assert entry_weight(status, score, progress) == expected


def test_unscored_reading_grows_with_chapters_read():
    assert entry_weight("CURRENT", None, 0) < entry_weight("CURRENT", None, 20) < entry_weight("CURRENT", None, 300)


def entry(media_id, genres, *, status="CURRENT", score=None, progress=30, tags=(), staff=(), title=None):
    return {"media_id": media_id, "title": title or f"Series {media_id}", "status": status, "score": score,
            "progress": progress, "format": "MANGA", "genres": list(genres),
            "tag_ranks": [{"name": t, "rank": 80} for t in tags], "staff": [{"id": s, "name": f"Person {s}"} for s in staff]}


def library():
    rows = [entry(i, ["Fantasy"], tags=["Isekai"]) for i in range(1, 41)]                   # read a lot, unscored
    rows += [entry(100 + i, ["Horror"], score=95, tags=["Gore"], staff=[7]) for i in range(5)]  # few, loved
    rows += [entry(200 + i, ["Sports"], status="DROPPED") for i in range(6)]                 # dropped
    rows += [entry(300, ["Mystery"], status="PLANNING")]
    return rows


def test_profile_prefers_loved_over_merely_common():
    p = build_profile(library())
    order = [f.label for f in p.top("genres", 10, positive=False)]
    assert order[0] == "Horror" and order[-1] == "Sports"
    assert p.genres["Sports"].affinity < 0
    assert "Mystery" not in p.genres            # Planning carries no signal
    assert p.entries == 51
    assert p.staff["7"].count == 5 and p.staff["7"].affinity > 0
    assert p.favourites[0]["score"] == 95 and p.disliked[0]["status"] == "DROPPED"


# ---- scoring -----------------------------------------------------------------------------


def cand(media_id, genres, *, tags=(), staff=(), sources=(), mean=70, adult=False, title=None):
    return {"media_id": media_id, "romaji": title or f"Candidate {media_id}", "english": None, "native": None,
            "site_url": f"https://anilist.co/manga/{media_id}", "cover_url": None, "start_year": 2020, "format": "MANGA",
            "country": "JP", "chapters": None, "status": "FINISHED", "genres": json.dumps(list(genres)),
            "tags": json.dumps([{"name": t, "rank": 90} for t in tags]),
            "staff_roles": json.dumps([{"id": s, "name": f"Person {s}", "role": "Story & Art"} for s in staff]),
            "mean_score": mean, "popularity": 1000, "is_adult": int(adult), "sources": json.dumps(list(sources))}


def test_scoring_and_rankings():
    p = build_profile(library())
    weights = {e["media_id"]: entry_weight(e["status"], e["score"], e["progress"]) for e in library()}
    recs = score_recs(p, [
        cand(1000, ["Horror"], tags=["Gore"]),
        cand(1001, ["Sports"]),
        cand(1002, ["Fantasy"], staff=[7]),
        cand(1003, ["Comedy"], sources=[{"kind": "community", "via": 100, "label": "Series 100", "rating": 40}]),
    ], weights)
    by = {r.media_id: r for r in recs}
    assert ranked(recs, "genre")[0].media_id == 1000
    assert by[1001].scores["genre"] < 0                         # a genre you drop counts against it
    assert [r.media_id for r in ranked(recs, "staff")] == [1002]  # only series sharing a creator
    assert "Person 7 (Story & Art), who made" in by[1002].reasons["staff"][0]
    assert ranked(recs, "community")[0].media_id == 1003
    assert "recommended by AniList readers of Series 100 (+40)" in by[1003].reasons["community"][0]
    assert ranked(recs, "overall")[-1].media_id == 1001


# ---- the model ------------------------------------------------------------------------------


def test_clean_answer_keeps_only_real_unique_candidates():
    summary, picks = clean_answer({"summary": " Likes horror. ", "picks": [
        {"id": 1, "reason": "Because."}, {"id": 999, "reason": "Invented."}, {"id": 1, "reason": "Again."},
        {"id": 2, "reason": "  "}, {"id": "3", "reason": "Numeric string."}, {"id": None, "reason": "x"}]}, {1, 2, 3})
    assert summary == "Likes horror."
    assert picks == [{"id": 1, "reason": "Because."}, {"id": 3, "reason": "Numeric string."}]


def test_prompt_lists_candidates_by_id():
    p = build_profile(library())
    recs = score_recs(p, [cand(1000, ["Horror"], tags=["Gore"], title="Uzumaki")], {})
    text = build_prompt(p, recs)
    assert "1000: Uzumaki" in text and "Horror" in text and "Series they dropped" in text


def test_ollama_must_be_local():
    with pytest.raises(OllamaUnavailable):
        OllamaClient("http://example.com:11434")
    OllamaClient("http://localhost:11434")


# ---- the map --------------------------------------------------------------------------------


def test_graph_keeps_only_shared_hubs():
    p = build_profile(library())
    recs = score_recs(p, [cand(1000 + i, ["Horror"], tags=["Gore"], staff=[7]) for i in range(3)]
                      + [cand(2000, ["Fantasy"], tags=["Isekai"])], {})
    g = build_graph(p, ranked(recs, "overall"), {})
    ids = {n["id"] for n in g["nodes"]}
    assert {"t:Horror", "t:Gore", "c:7"} <= ids
    assert "t:Isekai" not in ids                  # a theme shared by only one recommendation is left out
    assert all(link["target"] in ids for link in g["links"])
    assert sum(n["kind"] == "rec" for n in g["nodes"]) == 4


# ---- refresh against fakes ----------------------------------------------------------------------


class RecsAniList(FakeAniList):
    """FakeAniList that also answers the recommendation queries."""

    def __init__(self) -> None:
        super().__init__()
        self.community: dict[int, list[tuple[int, int]]] = {}  # media -> [(rating, recommended media)]
        self.works: dict[int, list[int]] = {}                  # staff id -> media

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query, variables = body["query"], body.get("variables") or {}
        if "recommendations(" in query:
            self.requests.append(body)
            media = [{"id": i, "recommendations": {"nodes": [
                {"rating": rating, "mediaRecommendation": self._public(self.catalogue[rec])}
                for rating, rec in self.community.get(i, [])]}} for i in variables["ids"] if i in self.catalogue]
            return httpx.Response(200, json={"data": {"Page": {"media": media}}})
        if re.search(r"\br0: (Page|Staff)", query):
            self.requests.append(body)
            data = {}
            for i in range(len(variables)):
                value = variables[f"v{i}"]
                if "Staff(" in query:
                    data[f"r{i}"] = {"id": value, "staffMedia": {"nodes": [self._public(self.catalogue[m]) for m in self.works.get(value, [])]}}
                elif "tag_in" in query:
                    data[f"r{i}"] = {"media": [self._public(m) for m in self.catalogue.values()
                                               if value in [t["name"] for t in m.get("tags", [])]]}
                else:
                    data[f"r{i}"] = {"media": [self._public(m) for m in self.catalogue.values() if value in m["genres"]]}
            return httpx.Response(200, json={"data": data})
        return super().handle(request)


def tagged(media, tags=(), staff=()):
    media["tags"] = [{"name": t, "rank": 85, "category": "Theme", "isMediaSpoiler": False, "isGeneralSpoiler": False} for t in tags]
    media["staff"] = {"edges": [{"role": "Story & Art", "node": {"id": s, "name": {"full": f"Mangaka {s}"}}} for s in staff]}
    media["meanScore"] = 75
    media["isAdult"] = False
    return media


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def world(services, mock):
    services.anilist_queue.set_interval(0)
    services.store_anilist_token("al-token-xyz")
    al = RecsAniList()
    loved = [tagged(al_media(i, f"Loved {i}", genres=["Horror"]), ["Gore"], [7]) for i in range(1, 4)]
    read = [tagged(al_media(10 + i, f"Read {i}", genres=["Fantasy"]), ["Isekai"]) for i in range(4)]
    cands = [tagged(al_media(500, "Uzumaki", genres=["Horror"]), ["Gore"], [7]),
             tagged(al_media(501, "Another Horror", genres=["Horror"]), ["Gore"]),
             tagged(al_media(502, "<script>bad()</script> Title", genres=["Horror"]), ["Gore"]),
             tagged(al_media(503, "Adult One", genres=["Horror"]), ["Gore"])]
    cands[3]["isAdult"] = True
    al.add_media(*loved, *read, *cands)
    al.add_list("Reading", [(100 + m["id"], m["id"], "CURRENT", 40) for m in read])
    al.add_list("Done", [(100 + m["id"], m["id"], "COMPLETED", 50) for m in loved])
    for entry_id in (101, 102, 103):
        al.entry(entry_id)["score"] = 95
    al.community = {1: [(30, 501), (12, 11)]}  # 11 is already on the list: never recommended
    al.works = {7: [500, 2]}
    al.install(mock)
    return al


def ollama_answer(mock, picks, summary="Loves horror.", status=200):
    mock.get(f"{OLLAMA}/api/tags").respond(200, json={"models": [
        {"name": "gpt-oss:20b", "capabilities": ["completion", "thinking"]}, {"name": "other:7b", "capabilities": ["completion"]}]})
    return mock.post(f"{OLLAMA}/api/chat").respond(status, json={"message": {"content": json.dumps({"summary": summary, "picks": picks})}})


async def refresh(services):
    services.recommender.start_refresh()
    await services.recommender.task


async def test_refresh_stores_candidates_and_model_picks(services, world, mock):
    chat = ollama_answer(mock, [{"id": 500, "reason": "Same mangaka as Loved 1."}, {"id": 4242, "reason": "invented"},
                                {"id": 501, "reason": "More body horror."}, {"id": 502, "reason": "Also horror."}])
    await refresh(services)
    status = services.recommender.status()
    assert status["state"] == "done", status
    assert world.call_count <= 15
    profile, recs = services.recommender.recs()
    ids = {r.media_id for r in recs}
    assert {500, 501, 502} <= ids and not ids & {1, 2, 3, 10, 11, 12, 13} and 503 not in ids
    assert 503 in {r.media_id for r in services.recommender.recs(include_adult=True)[1]}
    llm = services.repo.get_setting("rec_llm")
    assert [p["id"] for p in llm["picks"]] == [500, 501, 502] and llm["model"] == "gpt-oss:20b"
    sent = json.loads(chat.calls[0].request.content)
    assert sent["think"] == "low" and sent["format"]["required"] == ["summary", "picks"]
    assert "al-token-xyz" not in chat.calls[0].request.content.decode()


async def test_refresh_without_ollama_still_ranks(services, world, mock):
    mock.get(f"{OLLAMA}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    await refresh(services)
    status = services.recommender.status()
    assert status["state"] == "done" and "model not used" in status["detail"]
    assert services.repo.get_setting("rec_llm")["error"] == "Ollama is not running"
    assert services.recommender.recs()[1]


# ---- pages -------------------------------------------------------------------------------------


@pytest.fixture
def client(services):
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


def test_empty_pages_render_without_requests(client, mock):
    mock.get(f"{OLLAMA}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    for path in ("/discover", "/discover/genres", "/discover/tags", "/discover/creators", "/discover/map"):
        response = client.get(path)
        assert response.status_code == 200, path
    html = client.get("/discover").text
    assert 'href="/discover"' in html and "Connect AniList" in html
    assert "Ollama isn't running" in client.get("/discover-models").text
    assert client.post("/discover-refresh").status_code == 409
    assert len([c for c in mock.calls if "anilist" in str(c.request.url)]) == 0


async def test_pages_with_data(services, world, mock):
    ollama_answer(mock, [{"id": 500, "reason": "Same mangaka as Loved 1."}, {"id": 501, "reason": "More gore."},
                         {"id": 502, "reason": "Also horror."}])
    await refresh(services)
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.get("/discover").text
        assert "Loves horror." in html and "Same mangaka as Loved 1." in html and "chosen by gpt-oss:20b" in html
        assert "<script>bad()</script>" not in html
        assert "Adult One" not in html and "Adult One" in c.get("/discover?adult=1").text
        assert "Mangaka 7 (Story &amp; Art), who made" in c.get("/discover/creators").text
        assert "Gore" in c.get("/discover/tags").text
        page = c.get("/discover/map").text
        data = re.search(r'<script id="rec-map" type="application/json">(.*?)</script>', page, re.S).group(1)
        assert "</script>" not in data and json.loads(data)["nodes"]

        assert c.post("/discover-hide/500").text == ""
        assert "Uzumaki" not in c.get("/discover").text
        c.post("/discover-unhide-all")
        assert "Uzumaki" in c.get("/discover").text

        assert c.post("/discover-model", data={"model": "other:7b"}).status_code == 200
        assert services.repo.get_setting("ollama_model") == "other:7b"
        assert c.post("/discover-model", data={"model": "not-installed"}).status_code == 400


@pytest.mark.parametrize("path, method", [("/discover-refresh", "_refresh"), ("/discover-ask", "_llm_only")])
def test_buttons_start_work_and_refuse_a_second_click(services, monkeypatch, path, method):
    """Regression: these handlers ran in a worker thread, where starting the background task failed."""
    services.store_anilist_token("al-token-xyz")
    started = []

    async def slow(self):
        started.append(True)
        await asyncio.sleep(3600)

    import asyncio
    monkeypatch.setattr(type(services.recommender), method, slow)
    with TestClient(create_app(services), follow_redirects=False) as c:
        first = c.post(path)
        assert first.status_code == 200 and 'hx-trigger="every 2s"' in first.text  # the panel follows progress
        second = c.post(path)
        assert second.status_code == 409 and "already being refreshed" in second.text
        services.recommender.task.cancel()
    assert started == [True]
