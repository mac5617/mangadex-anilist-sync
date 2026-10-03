import asyncio
import logging

import httpx
import pytest
import respx

from mdal.clients.anilist import (
    ANILIST_URL,
    AniListAuthError,
    AniListClient,
    AniListComplexityError,
    AniListError,
    AniListGraphQLError,
    AniListRateLimited,
    AniListUnavailable,
)
from mdal.clients.ratelimit import PacedQueue
from mdal.config import USER_AGENT
from tests.fakes import FakeClock

TOKEN = "anilist-secret-token-abc123"
WALL = 1_800_000_000.0
OK = {"data": {"Viewer": {"id": 1}}}
CLOUDFLARE_HTML = "<html><head><title>429 Too Many Requests</title></head><body>cloudflare</body></html>"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
async def client(clock):
    queue = PacedQueue(3.0, clock=clock, sleep=clock.sleep)  # 20 rpm
    c = AniListClient(lambda: TOKEN, queue, wall_clock=lambda: WALL, sleep=clock.sleep)
    yield c
    await c.aclose()


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def timed(clock, responses):
    """respx side effect that records the fake time of each call."""
    calls = []
    it = iter(responses)

    def effect(request):
        calls.append(clock.now)
        return next(it)

    return effect, calls


async def test_success_sends_auth_and_user_agent(client, mock):
    route = mock.post(ANILIST_URL).respond(200, json=OK)
    data = await client.graphql("query { Viewer { id } }")
    assert data == {"Viewer": {"id": 1}}
    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.headers["User-Agent"] == USER_AGENT


async def test_variables_are_sent_as_json(client, mock):
    route = mock.post(ANILIST_URL).respond(200, json=OK)
    await client.graphql("query ($q: String) { x }", {"q": 'a "quoted" } title'})
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["variables"] == {"q": 'a "quoted" } title'}


async def test_429_json_with_retry_after_pauses_35s(client, mock, clock):
    effect, calls = timed(clock, [
        httpx.Response(429, json={"errors": [{"message": "Too Many Requests.", "status": 429}]}, headers={"Retry-After": "30"}),
        httpx.Response(200, json=OK),
    ])
    mock.post(ANILIST_URL).mock(side_effect=effect)
    assert await client.graphql("q") == {"Viewer": {"id": 1}}
    assert calls[1] - calls[0] >= 35


async def test_429_html_without_headers_pauses_65s(client, mock, clock):
    effect, calls = timed(clock, [
        httpx.Response(429, text=CLOUDFLARE_HTML, headers={"Content-Type": "text/html"}),
        httpx.Response(200, json=OK),
    ])
    mock.post(ANILIST_URL).mock(side_effect=effect)
    await client.graphql("q")
    assert calls[1] - calls[0] == pytest.approx(65)


async def test_429_with_only_reset_header_pauses_45s(client, mock, clock):
    effect, calls = timed(clock, [
        httpx.Response(429, text="", headers={"X-RateLimit-Reset": str(int(WALL + 40))}),
        httpx.Response(200, json=OK),
    ])
    mock.post(ANILIST_URL).mock(side_effect=effect)
    await client.graphql("q")
    assert calls[1] - calls[0] == pytest.approx(45)


async def test_pause_blocks_concurrent_caller(client, mock, clock):
    effect, calls = timed(clock, [
        httpx.Response(429, text=CLOUDFLARE_HTML, headers={"Retry-After": "30"}),
        httpx.Response(200, json=OK),
        httpx.Response(200, json=OK),
    ])
    mock.post(ANILIST_URL).mock(side_effect=effect)
    await asyncio.gather(client.graphql("a"), client.graphql("b"))
    assert len(calls) == 3
    assert calls[1] >= calls[0] + 35
    assert calls[2] >= calls[0] + 35


async def test_fourth_consecutive_429_raises_after_4_attempts(client, mock):
    route = mock.post(ANILIST_URL).respond(429, text=CLOUDFLARE_HTML)
    with pytest.raises(AniListRateLimited):
        await client.graphql("q")
    assert route.call_count == 4


async def test_403_halts_after_one_attempt(client, mock):
    route = mock.post(ANILIST_URL).respond(403, text="blocked")
    with pytest.raises(AniListUnavailable):
        await client.graphql("q")
    assert route.call_count == 1


async def test_disabled_message_is_unavailable(client, mock):
    mock.post(ANILIST_URL).respond(
        200, json={"data": None, "errors": [{"message": "The AniList API has been temporarily disabled due to severe stability issues"}]}
    )
    with pytest.raises(AniListUnavailable):
        await client.graphql("q")


async def test_complexity_error(client, mock):
    mock.post(ANILIST_URL).respond(200, json={"data": None, "errors": [{"message": "Max query complexity exceeded"}]})
    with pytest.raises(AniListComplexityError):
        await client.graphql("q")


async def test_other_graphql_errors_carry_messages_and_partial_data(client, mock):
    mock.post(ANILIST_URL).respond(
        200, json={"data": {"m0": {"id": 1}, "m1": None}, "errors": [{"message": "Not Found.", "path": ["m1"]}]}
    )
    with pytest.raises(AniListGraphQLError) as info:
        await client.graphql("q")
    assert not isinstance(info.value, AniListComplexityError)
    assert "Not Found." in str(info.value)
    assert info.value.data == {"m0": {"id": 1}, "m1": None}


async def test_401_is_auth_error(client, mock):
    mock.post(ANILIST_URL).respond(401, json={"errors": [{"message": "Unauthorized"}]})
    with pytest.raises(AniListAuthError):
        await client.graphql("q")


async def test_invalid_token_400_is_auth_error(client, mock):
    mock.post(ANILIST_URL).respond(400, json={"data": None, "errors": [{"message": "Invalid token", "status": 400}]})
    with pytest.raises(AniListAuthError):
        await client.graphql("q")


async def test_remaining_header_does_not_speed_up(client, mock, clock):
    effect, calls = timed(clock, [
        httpx.Response(200, json=OK, headers={"X-RateLimit-Remaining": "89"}),
        httpx.Response(200, json=OK, headers={"X-RateLimit-Remaining": "88"}),
    ])
    mock.post(ANILIST_URL).mock(side_effect=effect)
    await client.graphql("a")
    await client.graphql("b")
    assert calls[1] - calls[0] >= 3.0


async def test_5xx_retries_then_succeeds(client, mock):
    route = mock.post(ANILIST_URL).mock(side_effect=[httpx.Response(502), httpx.Response(200, json=OK)])
    await client.graphql("q")
    assert route.call_count == 2


async def test_network_error_retry_cap(client, mock):
    route = mock.post(ANILIST_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(AniListError):
        await client.graphql("q")
    assert route.call_count == 4


async def test_request_counter_counts_every_attempt(clock, mock):
    count = 0

    def counter():
        nonlocal count
        count += 1

    queue = PacedQueue(3.0, clock=clock, sleep=clock.sleep)
    c = AniListClient(lambda: TOKEN, queue, request_counter=counter, wall_clock=lambda: WALL, sleep=clock.sleep)
    mock.post(ANILIST_URL).mock(side_effect=[httpx.Response(429, text=""), httpx.Response(200, json=OK)])
    await c.graphql("q")
    assert count == 2


async def test_no_token_means_no_auth_header(clock, mock):
    queue = PacedQueue(3.0, clock=clock, sleep=clock.sleep)
    c = AniListClient(lambda: "", queue, sleep=clock.sleep)
    route = mock.post(ANILIST_URL).respond(200, json=OK)
    await c.graphql("q")
    assert "Authorization" not in route.calls.last.request.headers


async def test_token_never_logged(client, mock, caplog):
    caplog.set_level(logging.DEBUG)
    mock.post(ANILIST_URL).mock(side_effect=[
        httpx.Response(429, text=CLOUDFLARE_HTML),
        httpx.Response(401, json={"errors": [{"message": "Unauthorized"}]}),
    ])
    with pytest.raises(AniListAuthError):
        await client.graphql("q")
    assert TOKEN not in caplog.text
