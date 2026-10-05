"""MyAnimeList as a sync target: client, matching, the dry run, writing and the pages."""

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mdal.clients.myanimelist import (
    API_URL,
    TOKEN_URL,
    MalAuthError,
    MalClient,
    MalRateLimited,
    MalUnavailable,
    MemorySessionStore,
)
from mdal.clients.ratelimit import PacedQueue
from mdal.config import Settings
from mdal.sync.orchestrator import SyncStateError, mal_target
from mdal.web.app import create_app
from tests.factories import FakeAniList, FakeMal, FakeMangaDex, FakeSeries, al_media, connect_mal


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


async def _no_sleep(_seconds):
    return None


def make_client(session=None, secret="", now=1_000_000.0):
    settings = Settings(_env_file=None, mal_client_id="mal-client", mal_client_secret=secret)
    store = MemorySessionStore(session)
    client = MalClient(lambda: settings, PacedQueue(0), store, wall_clock=lambda: now, sleep=_no_sleep)
    return client, store


FRESH = {"access": "old-access", "refresh": "old-refresh", "expires_at": 1_000_000.0 + 30 * 86400}


# ---- client -------------------------------------------------------------------


def test_authorize_url_uses_plain_pkce():
    client, _ = make_client()
    q = parse_qs(urlparse(client.authorize_url("st", "v" * 64)).query)
    assert q["code_challenge"] == ["v" * 64] and q["code_challenge_method"] == ["plain"]
    assert q["client_id"] == ["mal-client"] and q["state"] == ["st"] and q["response_type"] == ["code"]


async def test_exchange_code_saves_session_and_omits_empty_secret(mock):
    route = mock.post(TOKEN_URL).respond(200, json={"access_token": "a1", "refresh_token": "r1", "expires_in": 100})
    client, store = make_client()
    await client.exchange_code("the-code", "the-verifier")
    form = parse_qs(route.calls[0].request.content.decode())
    assert form["code_verifier"] == ["the-verifier"] and form["grant_type"] == ["authorization_code"]
    assert "client_secret" not in form
    assert store.session == {"access": "a1", "refresh": "r1", "expires_at": 1_000_100.0}


async def test_secret_sent_when_set(mock):
    route = mock.post(TOKEN_URL).respond(200, json={"access_token": "a1", "refresh_token": "r1", "expires_in": 100})
    client, _ = make_client(secret="s3cret")
    await client.exchange_code("c", "v")
    assert parse_qs(route.calls[0].request.content.decode())["client_secret"] == ["s3cret"]


async def test_401_refreshes_once_then_retries(mock):
    token = mock.post(TOKEN_URL).respond(200, json={"access_token": "new", "refresh_token": "r2", "expires_in": 9e6})
    me = mock.get(f"{API_URL}/users/@me").mock(side_effect=[httpx.Response(401), httpx.Response(200, json={"name": "x"})])
    client, store = make_client(FRESH)
    assert (await client.me())["name"] == "x"
    assert token.call_count == 1 and me.call_count == 2
    assert me.calls[1].request.headers["Authorization"] == "Bearer new"
    assert store.session["refresh"] == "r2"


async def test_refreshes_before_expiry(mock):
    token = mock.post(TOKEN_URL).respond(200, json={"access_token": "new", "refresh_token": "r2", "expires_in": 9e6})
    me = mock.get(f"{API_URL}/users/@me").respond(200, json={"name": "x"})
    client, _ = make_client({**FRESH, "expires_at": 1_000_000.0 + 3600})
    await client.me()
    assert token.call_count == 1 and me.calls[0].request.headers["Authorization"] == "Bearer new"


async def test_failed_refresh_asks_to_reconnect(mock):
    mock.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
    mock.get(f"{API_URL}/users/@me").respond(401)
    client, _ = make_client(FRESH)
    with pytest.raises(MalAuthError, match="reconnect"):
        await client.me()


@pytest.mark.parametrize("status, exc", [(429, MalRateLimited), (403, MalUnavailable)])
async def test_429_and_403_halt_without_retry(mock, status, exc):
    route = mock.get(f"{API_URL}/users/@me").respond(status)
    client, _ = make_client(FRESH)
    with pytest.raises(exc):
        await client.me()
    assert route.call_count == 1


async def test_server_errors_are_retried(mock):
    route = mock.get(f"{API_URL}/users/@me").mock(side_effect=[httpx.Response(502), httpx.Response(200, json={"name": "x"})])
    client, _ = make_client(FRESH)
    assert (await client.me())["name"] == "x" and route.call_count == 2


async def test_not_connected_sends_nothing(mock):
    client, _ = make_client(None)
    with pytest.raises(MalAuthError):
        await client.me()
    assert len(mock.calls) == 0


async def test_manga_list_pages(mock):
    fake = FakeMal()
    for i in range(1, 2502):
        fake.listed(i, "reading", 1)
    fake.install(mock)
    client, _ = make_client(FRESH)
    items = await client.manga_list()
    assert len(items) == 2501 and fake.routes["list"].call_count == 3
    assert fake.routes["list"].calls[0].request.url.params["fields"] == "list_status,num_chapters,status"


async def test_update_only_sends_reading_or_completed(mock):
    client, _ = make_client(FRESH)
    for status in ("on_hold", "dropped", "plan_to_read", "CURRENT"):
        with pytest.raises(ValueError):
            await client.update_list_status(1, chapters=3, status=status)
    with pytest.raises(ValueError):
        await client.update_list_status(1, chapters=None, status=None)
    assert len(mock.calls) == 0


# ---- MyAnimeList id per series ----------------------------------------------------


def _row(links):
    import json
    return {"links": json.dumps(links)}


@pytest.mark.parametrize("links, mapping, media, expected", [
    ({"mal": "11"}, None, {}, (11, None, None)),
    ({}, {"state": "auto", "al_media_id": 1}, {1: {"id_mal": 22}}, (22, 1, None)),
    ({"mal": "22"}, {"state": "confirmed", "al_media_id": 1}, {1: {"id_mal": 22}}, (22, 1, None)),
    ({"mal": "x"}, None, {}, (None, None, "no MyAnimeList id (no AniList match, and no MyAnimeList link on MangaDex)")),
    ({"mal": "33"}, {"state": "unmatched", "al_media_id": None}, {}, (33, None, None)),
    ({"mal": "33"}, {"state": "review", "al_media_id": None}, {}, (None, None, "awaiting match review")),
    ({}, {"state": "auto", "al_media_id": 1}, {1: {"id_mal": None}}, (None, 1, "no MyAnimeList id (the AniList match has none)")),
])
def test_mal_target(links, mapping, media, expected):
    assert mal_target(_row(links), mapping, media) == expected


def test_mal_target_conflict_is_skipped():
    mal_id, _, reason = mal_target(_row({"mal": "99"}), {"state": "auto", "al_media_id": 1}, {1: {"id_mal": 22}})
    assert mal_id is None and "different MyAnimeList entries (22 and 99)" in reason


# ---- the sync ----------------------------------------------------------------------


@pytest.fixture
def fast(services):
    for q in (services.anilist_queue, services.mangadex_queue, services.mal_queue):
        q.set_interval(0)
    return services


@pytest.fixture
def world(mock, fast):
    md, al, mal = FakeMangaDex(), FakeAniList(), FakeMal()
    al.add_media(
        al_media(1, "Sousou no Frieren", id_mal=11),
        al_media(2, "Berserk", id_mal=22),
        al_media(3, "Finished Thing", id_mal=33, chapters=10, status="FINISHED"),
        al_media(4, "Not On MAL", id_mal=44),
        al_media(5, "Disputed", id_mal=98),
    )
    md.add(FakeSeries("a", "Sousou no Frieren", links={"al": "1"}, reads=["a1", "a2"]), {"a1": "9", "a2": "10.5"})
    md.add(FakeSeries("b", "Berserk", links={"al": "2"}, reads=["b1"]), {"b1": "12"})
    md.add(FakeSeries("c", "Finished Thing", links={"al": "3"}, reads=["c1", "c2"]), {"c1": "10", "c2": "9"})
    md.add(FakeSeries("d", "Not On MAL", links={"al": "4"}, reads=["d1"]), {"d1": "3"})
    md.add(FakeSeries("e", "Only A MAL Link", links={"mal": "55"}, reads=["e1"]), {"e1": "1"})
    md.add(FakeSeries("g", "Disputed", links={"al": "5", "mal": "99"}, reads=["g1"]), {"g1": "4"})
    mal.add(11, "Sousou no Frieren")
    mal.add(22, "Berserk")
    mal.add(33, "Finished Thing", chapters=10, status="finished")
    mal.listed(11, "reading", 5)
    mal.listed(22, "reading", 30)
    mal.listed(33, "reading", 8)
    fast.store_anilist_token("al-token-xyz")
    connect_mal(fast)
    return md.install(mock), al.install(mock), mal.install(mock)


async def mal_run(services):
    run_id = await services.orchestrator.start_run("mal")
    await services.orchestrator.wait()
    return run_id


async def test_dry_run_builds_mal_rows(fast, world):
    run_id = await mal_run(fast)
    run = fast.repo.get_run(run_id)
    assert run["state"] == "diffed", run["error"]
    assert run["target"] == "mal" and run["req_mal"] == 1
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert (items["a"]["action"], items["a"]["al_progress"], items["a"]["md_progress"], items["a"]["mal_id"]) == ("write", 5, 10, 11)
    assert items["a"]["reason"] == "read to 10 on MangaDex; MyAnimeList has 5"
    assert (items["b"]["action"], items["b"]["reason"]) == ("skip", "MyAnimeList at/ahead")
    assert (items["c"]["action"], items["c"]["set_status"], items["c"]["status_source"]) == ("write", "COMPLETED", "MyAnimeList")
    assert (items["d"]["action"], items["d"]["al_entry_id"], items["d"]["mal_id"]) == ("add", None, 44)
    assert (items["e"]["action"], items["e"]["mal_id"], items["e"]["al_media_id"]) == ("add", 55, None)
    assert items["e"]["reason"].startswith("not on your MyAnimeList list")
    # Conflicting links put the AniList match in review; the MyAnimeList sync waits for that too.
    assert (items["g"]["action"], items["g"]["reason"]) == ("skip", "awaiting match review")
    assert world[2].patches == []  # a dry run never writes


async def test_mal_run_does_not_touch_anilist_diff(fast, world):
    al_run = fast.repo.create_run("diffed")
    await mal_run(fast)
    assert fast.repo.get_run(al_run)["state"] == "diffed"


async def test_recent_mangadex_read_is_reused(fast, world):
    md = world[0]
    await mal_run(fast)
    before = sum(md.data_calls().values())
    run_id = await mal_run(fast)
    assert sum(md.data_calls().values()) == before
    assert fast.repo.get_run(run_id)["req_mangadex"] == 0


async def test_needs_a_connection(services):
    with pytest.raises(SyncStateError, match="Connect MyAnimeList"):
        await services.orchestrator.start_run("mal")


async def approve_all(services, run_id):
    items = services.repo.items(run_id)
    sel = [i["md_id"] for i in items if i["action"] in ("write", "add")]
    mc = [i["md_id"] for i in items if i["set_status"]]
    await services.orchestrator.approve(run_id, sel, mc, [])
    await services.orchestrator.wait()


async def test_write_sends_only_progress_and_allowed_statuses(fast, world):
    mal = world[2]
    run_id = await mal_run(fast)
    await approve_all(fast, run_id)
    run = fast.repo.get_run(run_id)
    assert run["state"] == "done", run["error"]
    sent = dict(mal.patches)
    assert sent == {
        11: {"num_chapters_read": "10"},                         # update: progress only
        33: {"num_chapters_read": "10", "status": "completed"},  # approved completion
        44: {"num_chapters_read": "3", "status": "reading"},     # add
        55: {"num_chapters_read": "1", "status": "reading"},     # add from MangaDex's MAL link
    }
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert all(items[m]["write_state"] == "done" and items[m]["verify_note"] == "verified" for m in "acde")
    assert items["d"]["al_entry_id"] == 44


async def test_prewrite_never_lowers_or_overwrites(fast, world):
    mal = world[2]
    run_id = await mal_run(fast)
    mal.listed(11, "completed", 12)   # read further on MAL meanwhile
    mal.listed(44, "completed", 50)   # added on MAL by hand meanwhile
    await approve_all(fast, run_id)
    patched = {m for m, _ in mal.patches}
    assert 11 not in patched and 44 not in patched
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert items["a"]["write_state"] == "dropped" and "MyAnimeList is now at 12" in items["a"]["verify_note"]
    assert items["d"]["write_state"] == "dropped" and "already on your MyAnimeList list" in items["d"]["verify_note"]
    assert mal.entries[11] ["status"] == "completed" and mal.entries[44]["num_chapters_read"] == 50


async def test_one_failed_entry_does_not_stop_the_rest(fast, world):
    mal = world[2]
    mal.fail_ids[44] = 400
    run_id = await mal_run(fast)
    await approve_all(fast, run_id)
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert items["d"]["write_state"] == "failed" and "bad value" in items["d"]["verify_note"]
    assert items["e"]["write_state"] == "done"


async def test_rate_limit_halts_and_can_resume(fast, world, mock):
    run_id = await mal_run(fast)
    patch = world[2].routes["patch"]
    side_effect = patch.side_effect
    patch.side_effect = [httpx.Response(429)]
    await approve_all(fast, run_id)
    run = fast.repo.get_run(run_id)
    assert run["state"] == "halted" and "429" in run["error"]
    patch.side_effect = side_effect
    assert fast.orchestrator.can_resume(run_id)
    await fast.orchestrator.resume(run_id)
    await fast.orchestrator.wait()
    assert fast.repo.get_run(run_id)["state"] == "done"


def test_tokens_are_secret_values(services):
    connect_mal(services)
    assert {"mal-access-1111", "mal-refresh-1111"} <= set(services.secret_values())


# ---- pages ----------------------------------------------------------------------


@pytest.fixture
def client(services):
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


def test_settings_explains_setup_without_client_id(client):
    html = client.get("/settings").text
    assert "MyAnimeList" in html and "MAL_CLIENT_ID" in html and "/auth/mal/callback" in html


def _with_client_id(services):
    services.env_file.write_text(services.env_file.read_text() + "MAL_CLIENT_ID=mal-client\n")
    services.reload_settings()


def test_connect_flow(client, services, mock):
    _with_client_id(services)
    fake = FakeMal().install(mock)
    start = client.get("/auth/mal/start")
    assert start.status_code == 303
    q = parse_qs(urlparse(start.headers["location"]).query)
    callback = client.get("/auth/mal/callback", params={"code": "c0de", "state": q["state"][0]})
    assert "Connected to MyAnimeList as MalReader" in callback.text
    form = parse_qs(fake.routes["token"].calls[0].request.content.decode())
    assert form["code_verifier"] == q["code_challenge"]
    assert services.mal.connected and services.repo.get_setting("mal_user_name") == "MalReader"
    html = client.get("/settings").text
    assert "mal-access-2222" not in html and "mal-refresh-2222" not in html
    assert "Sync AniList and MyAnimeList" in client.get("/").text

    client.post("/auth/mal/disconnect")
    assert not services.mal.connected
    home = client.get("/").text
    assert "Sync AniList and MyAnimeList" not in home and "Sync AniList" in home


def test_callback_rejects_unknown_state(client, services, mock):
    _with_client_id(services)
    response = client.get("/auth/mal/callback", params={"code": "c", "state": "forged"})
    assert response.status_code == 400 and len(mock.calls) == 0


def test_start_mal_sync_without_connection(client):
    response = client.post("/sync", data={"target": "mal"})
    assert response.status_code == 409 and "Connect MyAnimeList" in response.text


async def test_diff_page_names_the_site(fast, world):
    run_id = await mal_run(fast)
    with TestClient(create_app(fast), follow_redirects=False) as c:
        html = c.get(f"/sync/{run_id}?f=all").text
        assert "Write selected to MyAnimeList" in html and "MyAnimeList requests" in html
        assert 'href="https://myanimelist.net/manga/11"' in html
        assert "No MyAnimeList link" in html
        assert "MyAnimeList" in c.get("/history").text


async def test_sync_covers_both_sites_one_after_the_other(services, monkeypatch):
    """Sync reads AniList, then MyAnimeList; Settings can leave either out; a failed first run stops the second."""
    import asyncio

    connect_mal(services)
    order = []
    orch = services.orchestrator

    async def fake_dry_run(run_id):
        order.append(services.repo.get_run(run_id)["target"])
        services.repo.update_run(run_id, state="diffed")

    monkeypatch.setattr(orch, "_dry_run", fake_dry_run)
    monkeypatch.setattr(orch, "md_snapshot_fresh", lambda: True)
    await orch.start_sync()
    for _ in range(50):
        await asyncio.sleep(0)
        if len(order) == 2 and not orch.queued and orch.task.done():
            break
    await orch.wait()
    assert order == ["anilist", "mal"]

    services.repo.set_setting("sync_targets", ["mal"])
    assert orch.sync_targets() == ["mal"]
    services.repo.set_setting("sync_targets", [])
    with pytest.raises(SyncStateError):
        await orch.start_sync()

    async def failing(run_id):
        order.append("failed " + services.repo.get_run(run_id)["target"])
        raise RuntimeError("boom")

    monkeypatch.setattr(orch, "_dry_run", failing)
    await orch.start_sync(["anilist", "mal"])
    await orch.wait()
    for _ in range(20):
        await asyncio.sleep(0)
    assert order[-1] == "failed anilist" and orch.queued == []      # MyAnimeList wasn't started
