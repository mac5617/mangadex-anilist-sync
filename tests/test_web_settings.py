import pytest
from fastapi.testclient import TestClient

from mdal.web.app import create_app
from mdal.web.routes.settings import TUNABLES
from tests.conftest import FAKE_ENV

VALID = {
    "anilist_rpm": "20", "anilist_write_batch": "10", "anilist_search_batch": "5", "anilist_page_size": "50",
    "mangadex_rps": "3", "match_auto": "0.92", "match_review": "0.6", "match_margin": "0.05", "jump_limit": "200",
}


@pytest.fixture
def client(services):
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


def test_page_shows_every_tunable_and_db_path(client, services):
    html = client.get("/settings").text
    for t in TUNABLES:
        assert f'name="{t.key}"' in html
    assert str(services.settings.db_path) in html


def test_no_secret_values_in_html(client, services):
    services.store_anilist_token("al-access-token-secret-777")
    html = client.get("/settings").text
    for key in ("MANGADEX_PASSWORD", "MANGADEX_CLIENT_SECRET", "ANILIST_CLIENT_SECRET"):
        assert FAKE_ENV[key] not in html
    assert "al-access-token-secret-777" not in html
    assert "<code>ANILIST_ACCESS_TOKEN</code></td><td><span class=\"badge ok\">set</span>" in html


@pytest.mark.parametrize(
    "key, value, message",
    [
        ("anilist_rpm", "31", "between 1 and 30"),
        ("anilist_rpm", "0", "between 1 and 30"),
        ("mangadex_rps", "5", "between 0.2 and 4"),
        ("anilist_rpm", "20.5", "not a whole number"),
        ("jump_limit", "abc", "not a whole number"),
        ("match_auto", "1.5", "between 0 and 1"),
    ],
)
def test_out_of_range_rejected(client, services, key, value, message):
    response = client.post("/settings", data={**VALID, key: value})
    assert response.status_code == 400 and message in response.text
    assert services.repo.get_setting("anilist_rpm") == 20  # nothing saved


def test_threshold_order_enforced(client, services):
    response = client.post("/settings", data={**VALID, "match_auto": "0.6", "match_review": "0.7"})
    assert response.status_code == 400 and "must be lower than the auto-accept" in response.text
    assert services.repo.get_setting("match_review") == 0.60


def test_valid_values_persist_and_respace_queues(client, services):
    response = client.post("/settings", data={**VALID, "anilist_rpm": "30", "mangadex_rps": "2", "jump_limit": "300"})
    assert response.status_code == 200 and "Settings saved." in response.text
    assert services.repo.get_setting("anilist_rpm") == 30
    assert services.repo.get_setting("mangadex_rps") == 2.0
    assert services.repo.get_setting("jump_limit") == 300
    assert services.anilist_queue.min_interval == pytest.approx(2.0)
    assert services.mangadex_queue.min_interval == pytest.approx(0.5)


def test_reset_batch(client, services):
    services.repo.set_setting("anilist_write_batch", 2)
    response = client.post("/settings/reset-batch")
    assert "reset to 10" in response.text
    assert services.repo.get_setting("anilist_write_batch") == 10


def test_no_secret_values_on_any_page(client, services):
    from tests.factories import seed_diffed_run

    services.store_anilist_token("al-access-token-secret-777")
    run_id = seed_diffed_run(services.repo)
    secrets = [FAKE_ENV[k] for k in ("MANGADEX_PASSWORD", "MANGADEX_CLIENT_SECRET", "ANILIST_CLIENT_SECRET")]
    secrets.append("al-access-token-secret-777")
    for url in ("/", "/settings", "/history", f"/history/{run_id}", f"/sync/{run_id}", "/review", "/not-listed", "/sync/status"):
        html = client.get(url).text
        for s in secrets:
            assert s not in html, (url, s)
