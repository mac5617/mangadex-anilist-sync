"""MangaDex safety: cooldown, no retry on dropped connections, early slow-down, saved login."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mdal.clients.mangadex import (
    API_URL,
    TOKEN_URL,
    MangaDexBlocked,
    MangaDexClient,
    MangaDexCoolingDown,
    MangaDexCredentials,
    MangaDexRateLimited,
    MangaDexUnreachable,
    MemoryGuard,
)
from mdal.clients.ratelimit import PacedQueue
from mdal.sync.orchestrator import SyncCoolingDown
from mdal.web.app import create_app
from tests.fakes import FakeClock

CREDS = MangaDexCredentials("reader", "md-password-secret", "personal-client-reader", "md-client-secret-xyz")
START = 1_800_000_000.0


class Wall:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def wall():
    return Wall()


@pytest.fixture
def guard():
    return MemoryGuard()


def make(clock, wall, guard, creds=CREDS):
    queue = PacedQueue(1 / 3, clock=clock, sleep=clock.sleep)
    return MangaDexClient(lambda: creds, queue, clock=clock, wall_clock=wall, sleep=clock.sleep, guard=guard)


@pytest.fixture
async def client(clock, wall, guard):
    c = make(clock, wall, guard)
    yield c
    await c.aclose()


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def token(n=1):
    return {"access_token": f"access-{n}-aaaaaaaa", "refresh_token": f"refresh-{n}-bbbbbbbb"}


# ---- dropped connections & cooldown ---------------------------------------------------


@pytest.mark.parametrize("error", [httpx.RemoteProtocolError("Server disconnected"), httpx.ReadTimeout("slow"),
                                   httpx.ConnectError("refused")])
async def test_dropped_connection_is_not_retried_and_starts_cooldown(client, mock, guard, error):
    mock.post(TOKEN_URL).respond(200, json=token())
    route = mock.get(f"{API_URL}/manga/status").mock(side_effect=error)
    with pytest.raises(MangaDexUnreachable, match="dropped the connection"):
        await client.get("/manga/status")
    assert route.call_count == 1
    until, reason = guard.cooldown()
    assert until == START + 3600 and error.__class__.__name__ in reason


async def test_nothing_is_sent_during_cooldown(client, mock, guard, wall):
    guard.start_cooldown(START + 600, "test")
    token_route = mock.post(TOKEN_URL).respond(200, json=token())
    route = mock.get(f"{API_URL}/manga").respond(200, json={"data": []})
    with pytest.raises(MangaDexCoolingDown, match="nothing was sent"):
        await client.get("/manga")
    with pytest.raises(MangaDexCoolingDown):
        await client.check_login()
    assert token_route.call_count == 0 and route.call_count == 0
    wall.now = START + 601  # cooldown over
    await client.get("/manga")
    assert route.call_count == 1


async def test_403_and_429_limit_start_cooldown(clock, wall, mock):
    g1, g2 = MemoryGuard(), MemoryGuard()
    mock.post(TOKEN_URL).respond(200, json=token())
    mock.get(f"{API_URL}/a").respond(403)
    mock.get(f"{API_URL}/b").respond(429)
    with pytest.raises(MangaDexBlocked):
        await make(clock, wall, g1).get("/a")
    with pytest.raises(MangaDexRateLimited):
        await make(clock, wall, g2).get("/b")
    assert "403" in g1.cooldown()[1] and "429" in g2.cooldown()[1]


# ---- early slow-down ----------------------------------------------------------------


async def test_low_remaining_waits_for_reset(client, mock, clock):
    mock.post(TOKEN_URL).respond(200, json=token())
    times = []

    def effect(request):
        times.append(clock.now)
        return httpx.Response(200, json={"data": []}, headers={
            "X-RateLimit-Limit": "40", "X-RateLimit-Remaining": "2", "X-RateLimit-Retry-After": str(int(START + 25)),
        })

    mock.get(f"{API_URL}/manga").mock(side_effect=effect)
    await client.get("/manga")
    await client.get("/manga")
    assert times[1] - times[0] >= 25


async def test_plenty_remaining_does_not_wait(client, mock, clock):
    mock.post(TOKEN_URL).respond(200, json=token())
    times = []

    def effect(request):
        times.append(clock.now)
        return httpx.Response(200, json={"data": []}, headers={
            "X-RateLimit-Limit": "40", "X-RateLimit-Remaining": "30", "X-RateLimit-Retry-After": str(int(START + 25)),
        })

    mock.get(f"{API_URL}/manga").mock(side_effect=effect)
    await client.get("/manga")
    await client.get("/manga")
    assert times[1] - times[0] < 1


async def test_far_reset_halts_instead_of_waiting(client, mock, guard):
    mock.post(TOKEN_URL).respond(200, json=token())
    mock.get(f"{API_URL}/manga").respond(200, json={"data": []}, headers={
        "X-RateLimit-Limit": "40", "X-RateLimit-Remaining": "0", "X-RateLimit-Retry-After": str(int(START + 3600)),
    })
    with pytest.raises(MangaDexRateLimited, match="stopped early"):
        await client.get("/manga")
    assert guard.cooldown() is not None


# ---- saved login ---------------------------------------------------------------------


async def test_restart_reuses_saved_login(clock, wall, guard, mock):
    token_route = mock.post(TOKEN_URL).respond(200, json=token())
    mock.get(f"{API_URL}/manga").respond(200, json={"data": []})
    await make(clock, wall, guard).get("/manga")
    assert token_route.call_count == 1
    await make(clock, wall, guard).get("/manga")  # a "restarted" app with the same saved session
    assert token_route.call_count == 1


async def test_old_saved_login_is_refreshed_not_relogged(clock, wall, guard, mock):
    grants = []
    mock.post(TOKEN_URL).mock(side_effect=lambda r: grants.append(r.content.decode()) or httpx.Response(200, json=token(len(grants))))
    mock.get(f"{API_URL}/manga").respond(200, json={"data": []})
    await make(clock, wall, guard).get("/manga")
    wall.now = START + 14 * 60
    await make(clock, wall, guard).get("/manga")
    assert "grant_type=password" in grants[0] and "grant_type=refresh_token" in grants[1] and len(grants) == 2


async def test_saved_login_for_another_account_is_ignored(clock, wall, guard, mock):
    token_route = mock.post(TOKEN_URL).respond(200, json=token())
    mock.get(f"{API_URL}/manga").respond(200, json={"data": []})
    await make(clock, wall, guard).get("/manga")
    other = MangaDexCredentials("someone-else", "pw-xxxxxxxx", "client", "secret-yyyyyyy")
    await make(clock, wall, guard, other).get("/manga")
    assert token_route.call_count == 2


# ---- app level -----------------------------------------------------------------------


def test_services_persist_guard_and_redact_saved_tokens(services):
    services.mangadex.guard.save_session({"username": "reader", "access": "saved-access-token-123", "refresh": "saved-refresh-456"})
    assert {"saved-access-token-123", "saved-refresh-456"} <= set(services.secret_values())
    services.mangadex_guard.start_cooldown(START, "x")
    assert services.repo.get_setting("mangadex_cooldown_until") == START


async def test_sync_refused_during_cooldown(services):
    import time

    services.mangadex_guard.start_cooldown(time.time() + 600, "MangaDex dropped the connection")
    with pytest.raises(SyncCoolingDown, match="cooldown"):
        await services.orchestrator.start_run()
    assert services.repo.latest_run() is None


def test_dashboard_shows_cooldown_and_hides_start(services):
    import time

    services.mangadex_guard.start_cooldown(time.time() + 1800, "MangaDex dropped the connection (RemoteProtocolError)")
    with TestClient(create_app(services)) as c:
        html = c.get("/").text
        assert "MangaDex is paused until" in html and 'hx-post="/sync"' not in html
        response = c.post("/sync")
        assert response.status_code == 409 and "cooldown" in response.text
        settings = c.get("/settings").text
        assert "Clear cooldown" in settings
        c.post("/settings/clear-cooldown")
        assert services.mangadex_guard.cooldown() is None
        assert 'hx-post="/sync"' in c.get("/").text
