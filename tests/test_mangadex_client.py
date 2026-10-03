import logging
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from mdal.clients.mangadex import (
    API_URL,
    TOKEN_URL,
    MangaDexAuthError,
    MangaDexBlocked,
    MangaDexClient,
    MangaDexCredentials,
    MangaDexRateLimited,
)
from mdal.clients.ratelimit import PacedQueue
from mdal.config import USER_AGENT
from tests.fakes import FakeClock

CREDS = MangaDexCredentials("reader", "md-password-secret", "personal-client-reader", "md-client-secret-xyz")
WALL = 1_800_000_000.0


def token_body(n: int) -> dict:
    return {"access_token": f"access-{n}-aaaaaaaa", "refresh_token": f"refresh-{n}-bbbbbbbb", "expires_in": 900}


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
async def client(clock):
    queue = PacedQueue(1 / 3, clock=clock, sleep=clock.sleep)  # 3 rps
    c = MangaDexClient(lambda: CREDS, queue, clock=clock, wall_clock=lambda: WALL, sleep=clock.sleep)
    yield c
    await c.aclose()


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def form(request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


async def test_first_get_does_password_grant_then_bearer(client, mock):
    token = mock.post(TOKEN_URL).respond(200, json=token_body(1))
    data = mock.get(f"{API_URL}/manga/status").respond(200, json={"result": "ok", "statuses": {}})
    await client.get("/manga/status")
    sent = form(token.calls.last.request)
    assert sent == {
        "grant_type": "password", "username": "reader", "password": "md-password-secret",
        "client_id": "personal-client-reader", "client_secret": "md-client-secret-xyz",
    }
    assert token.calls.last.request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert data.calls.last.request.headers["Authorization"] == "Bearer access-1-aaaaaaaa"


async def test_refresh_after_13_minutes(client, mock, clock):
    token = mock.post(TOKEN_URL).mock(side_effect=[httpx.Response(200, json=token_body(1)), httpx.Response(200, json=token_body(2))])
    data = mock.get(f"{API_URL}/manga/status").respond(200, json={"result": "ok"})
    await client.get("/manga/status")
    clock.now += 12 * 60
    await client.get("/manga/status")
    assert token.call_count == 1
    clock.now += 60
    await client.get("/manga/status")
    assert token.call_count == 2
    assert form(token.calls.last.request)["grant_type"] == "refresh_token"
    assert form(token.calls.last.request)["refresh_token"] == "refresh-1-bbbbbbbb"
    assert data.calls.last.request.headers["Authorization"] == "Bearer access-2-aaaaaaaa"


async def test_401_refreshes_once_and_falls_back_to_password(client, mock):
    token = mock.post(TOKEN_URL).mock(side_effect=[
        httpx.Response(200, json=token_body(1)),        # initial login
        httpx.Response(400, json={"error": "invalid_grant"}),  # refresh fails
        httpx.Response(200, json=token_body(3)),        # password grant again
    ])
    data = mock.get(f"{API_URL}/manga").mock(side_effect=[httpx.Response(401), httpx.Response(200, json={"data": []})])
    await client.get("/manga")
    grants = [form(c.request)["grant_type"] for c in token.calls]
    assert grants == ["password", "refresh_token", "password"]
    assert data.call_count == 2
    assert data.calls.last.request.headers["Authorization"] == "Bearer access-3-aaaaaaaa"


async def test_bad_credentials(client, mock):
    mock.post(TOKEN_URL).respond(401, json={"error": "invalid_grant"})
    with pytest.raises(MangaDexAuthError, match="check username"):
        await client.get("/manga")


async def test_unapproved_client(client, mock):
    mock.post(TOKEN_URL).respond(403)
    with pytest.raises(MangaDexAuthError, match="approved"):
        await client.check_login()


async def test_429_with_retry_after_unix_time(client, mock, clock):
    mock.post(TOKEN_URL).respond(200, json=token_body(1))
    times = []

    def effect(request):
        times.append(clock.now)
        if len(times) == 1:
            return httpx.Response(429, headers={"X-RateLimit-Retry-After": str(int(WALL + 12))})
        return httpx.Response(200, json={"data": []})

    mock.get(f"{API_URL}/manga").mock(side_effect=effect)
    await client.get("/manga")
    assert times[1] - times[0] >= 12


async def test_429_backoff_ladder_then_halt(client, mock, clock):
    mock.post(TOKEN_URL).respond(200, json=token_body(1))
    times = []

    def effect(request):
        times.append(clock.now)
        return httpx.Response(429)

    route = mock.get(f"{API_URL}/manga").mock(side_effect=effect)
    with pytest.raises(MangaDexRateLimited):
        await client.get("/manga")
    assert route.call_count == 4
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps[0] == pytest.approx(10) and gaps[1] == pytest.approx(30) and gaps[2] == pytest.approx(60)


async def test_success_resets_429_counter(client, mock):
    mock.post(TOKEN_URL).respond(200, json=token_body(1))
    route = mock.get(f"{API_URL}/manga").mock(side_effect=[
        httpx.Response(429), httpx.Response(429), httpx.Response(200, json={"data": []}),
        httpx.Response(429), httpx.Response(429), httpx.Response(200, json={"data": []}),
    ])
    await client.get("/manga")
    await client.get("/manga")
    assert route.call_count == 6


async def test_403_on_data_endpoint_halts_immediately(client, mock):
    mock.post(TOKEN_URL).respond(200, json=token_body(1))
    route = mock.get(f"{API_URL}/manga").respond(403)
    with pytest.raises(MangaDexBlocked, match="IP ban"):
        await client.get("/manga")
    assert route.call_count == 1


async def test_get_by_ids_chunks_and_sets_limit(client, mock):
    mock.post(TOKEN_URL).respond(200, json=token_body(1))
    route = mock.get(f"{API_URL}/chapter").mock(
        side_effect=lambda req: httpx.Response(
            200, json={"data": [{"id": i} for i in req.url.params.get_list("ids[]")]}
        )
    )
    ids = [f"id-{n}" for n in range(250)]
    result = await client.get_by_ids("/chapter", ids, {"includeUnavailable": "1"})
    assert route.call_count == 3
    for call in route.calls:
        params = call.request.url.params
        assert params["limit"] == "100"
        assert len(params.get_list("ids[]")) <= 100
        assert params["includeUnavailable"] == "1"
    assert [r["id"] for r in result] == ids


async def test_get_by_ids_merges_grouped_dicts_without_limit(client, mock):
    mock.post(TOKEN_URL).respond(200, json=token_body(1))
    route = mock.get(f"{API_URL}/manga/read").mock(
        side_effect=lambda req: httpx.Response(
            200, json={"result": "ok", "data": {i: ["c"] for i in req.url.params.get_list("ids[]")}}
        )
    )
    ids = [f"m-{n}" for n in range(150)]
    result = await client.get_by_ids("/manga/read", ids, {"grouped": "true"}, with_limit=False)
    assert len(result) == 150
    assert all("limit" not in call.request.url.params for call in route.calls)


async def test_user_agent_everywhere_and_no_via(client, mock):
    token = mock.post(TOKEN_URL).respond(200, json=token_body(1))
    data = mock.get(f"{API_URL}/manga").respond(200, json={"data": []})
    await client.get("/manga")
    for call in [*token.calls, *data.calls]:
        assert call.request.headers["User-Agent"] == USER_AGENT
        assert "Via" not in call.request.headers


async def test_pacing_three_per_second(client, mock, clock):
    mock.post(TOKEN_URL).respond(200, json=token_body(1))
    times = []

    def effect(request):
        times.append(clock.now)
        return httpx.Response(200, json={"data": []})

    mock.get(f"{API_URL}/manga").mock(side_effect=effect)
    for _ in range(5):
        await client.get("/manga")
    assert times[-1] - times[0] >= 4 / 3 - 1e-9


async def test_secrets_never_logged(client, mock, caplog):
    caplog.set_level(logging.DEBUG)
    mock.get(f"{API_URL}/manga").mock(side_effect=[httpx.Response(429), httpx.Response(401), httpx.Response(200, json={"data": []})])
    # login, then on 401: refresh fails (400) -> password grant again
    mock.post(TOKEN_URL).mock(side_effect=[
        httpx.Response(200, json=token_body(1)), httpx.Response(400), httpx.Response(200, json=token_body(2)),
    ])
    await client.get("/manga")
    for secret in ("md-password-secret", "md-client-secret-xyz", "access-1-aaaaaaaa", "refresh-1-bbbbbbbb"):
        assert secret not in caplog.text
