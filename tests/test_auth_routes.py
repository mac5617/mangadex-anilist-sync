import json
import logging
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from mdal.clients.anilist import ANILIST_URL
from mdal.clients.anilist_oauth import PIN_REDIRECT, TOKEN_URL as AL_TOKEN_URL
from mdal.clients.mangadex import TOKEN_URL as MD_TOKEN_URL
from mdal.web.app import create_app
from tests.conftest import FAKE_ENV

NEW_TOKEN = "fresh-anilist-token-zzz999"
VIEWER = {"data": {"Viewer": {"id": 77, "name": "Reader77"}}}


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def client(services):
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


def start_state(client) -> str:
    response = client.get("/auth/anilist/start")
    assert response.status_code == 303
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://anilist.co/api/v2/oauth/authorize"
    query = parse_qs(location.query)
    assert query["client_id"] == ["4242"]
    assert query["redirect_uri"] == [FAKE_ENV["ANILIST_REDIRECT_URI"]]
    assert query["response_type"] == ["code"]
    return query["state"][0]


def test_settings_page_hides_secrets(client):
    html = client.get("/settings").text
    for key in ("MANGADEX_PASSWORD", "MANGADEX_CLIENT_SECRET", "ANILIST_CLIENT_SECRET"):
        assert FAKE_ENV[key] not in html
    assert "Connect AniList" in html


@pytest.mark.parametrize("state", ["", "forged-state"])
def test_callback_with_bad_state_is_rejected_without_http(client, mock, state):
    response = client.get("/auth/anilist/callback", params={"code": "abc", "state": state})
    assert response.status_code == 400
    assert len(mock.calls) == 0


def test_callback_with_expired_state_is_rejected(client, mock, services):
    state = start_state(client)
    client.app.state.oauth_states[state] = 0.0  # already expired
    response = client.get("/auth/anilist/callback", params={"code": "abc", "state": state})
    assert response.status_code == 400
    assert len(mock.calls) == 0


def test_state_is_single_use(client, mock, services):
    mock.post(AL_TOKEN_URL).respond(200, json={"access_token": NEW_TOKEN})
    mock.post(ANILIST_URL).respond(200, json=VIEWER)
    state = start_state(client)
    assert client.get("/auth/anilist/callback", params={"code": "c", "state": state}).status_code == 200
    assert client.get("/auth/anilist/callback", params={"code": "c", "state": state}).status_code == 400


def test_successful_callback_stores_token_and_viewer(client, mock, services, env_file):
    token_route = mock.post(AL_TOKEN_URL).respond(200, json={"access_token": NEW_TOKEN})
    viewer_route = mock.post(ANILIST_URL).respond(200, json=VIEWER)
    state = start_state(client)
    response = client.get("/auth/anilist/callback", params={"code": "the-code", "state": state})

    assert response.status_code == 200
    assert "Reader77" in response.text
    assert NEW_TOKEN not in response.text
    sent = json.loads(token_route.calls.last.request.content)
    assert sent == {
        "grant_type": "authorization_code", "client_id": "4242", "client_secret": "al-client-secret-qwe",
        "redirect_uri": FAKE_ENV["ANILIST_REDIRECT_URI"], "code": "the-code",
    }
    assert viewer_route.calls.last.request.headers["Authorization"] == f"Bearer {NEW_TOKEN}"
    assert dotenv_values(env_file)["ANILIST_ACCESS_TOKEN"] == NEW_TOKEN
    assert dotenv_values(env_file)["MANGADEX_PASSWORD"] == FAKE_ENV["MANGADEX_PASSWORD"]  # other keys intact
    assert services.repo.get_setting("anilist_user_id") == 77
    assert services.repo.get_setting("anilist_user_name") == "Reader77"


def test_pin_flow_stores_token(client, mock, services, env_file):
    mock.post(ANILIST_URL).respond(200, json=VIEWER)
    response = client.post("/auth/anilist/token", data={"token": f"  {NEW_TOKEN}\n"})
    assert response.status_code == 200
    assert NEW_TOKEN not in response.text
    assert dotenv_values(env_file)["ANILIST_ACCESS_TOKEN"] == NEW_TOKEN
    assert services.repo.get_setting("anilist_user_name") == "Reader77"


def test_rejected_pin_token_is_not_kept(client, mock, env_file):
    mock.post(ANILIST_URL).respond(400, json={"data": None, "errors": [{"message": "Invalid token"}]})
    response = client.post("/auth/anilist/token", data={"token": "bogus-token-1234"})
    assert response.status_code == 400
    assert "ANILIST_ACCESS_TOKEN" not in dotenv_values(env_file)


def test_pin_mode_shows_pin_link(services, env_file):
    with env_file.open("a", encoding="utf-8") as f:
        f.write(f"ANILIST_REDIRECT_URI={PIN_REDIRECT}\n")
    services.reload_settings()
    with TestClient(create_app(services)) as c:
        html = c.get("/settings").text
    assert "response_type=token" in html


def test_disconnect_removes_token(client, mock, services, env_file):
    mock.post(ANILIST_URL).respond(200, json=VIEWER)
    client.post("/auth/anilist/token", data={"token": NEW_TOKEN})
    response = client.post("/auth/anilist/disconnect")
    assert response.status_code == 200
    assert "ANILIST_ACCESS_TOKEN" not in dotenv_values(env_file)
    assert services.anilist_token() == ""
    assert services.repo.get_setting("anilist_user_id") is None


@pytest.mark.parametrize(
    "status, expected_code, expected_text",
    [(200, 200, "MangaDex login works"), (401, 400, "check username"), (403, 400, "approved")],
)
def test_mangadex_check(client, mock, status, expected_code, expected_text):
    body = {"access_token": "a-tok-1234", "refresh_token": "r-tok-1234"} if status == 200 else {}
    route = mock.post(MD_TOKEN_URL).respond(status, json=body)
    response = client.post("/auth/mangadex/check")
    assert response.status_code == expected_code
    assert expected_text in response.text
    assert route.call_count == 1


def test_no_secrets_in_logs(client, mock, caplog):
    caplog.set_level(logging.DEBUG)
    mock.post(ANILIST_URL).respond(200, json=VIEWER)
    mock.post(MD_TOKEN_URL).respond(401)
    client.post("/auth/anilist/token", data={"token": NEW_TOKEN})
    client.post("/auth/mangadex/check")
    for secret in (NEW_TOKEN, FAKE_ENV["MANGADEX_PASSWORD"], FAKE_ENV["MANGADEX_CLIENT_SECRET"]):
        assert secret not in caplog.text
