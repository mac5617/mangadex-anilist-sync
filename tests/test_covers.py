"""MangaDex covers: fetched once by Shiori (MangaDex challenges browsers), stored, and served locally."""

import asyncio

import pytest
import respx
from fastapi.testclient import TestClient

from mdal.clients.mangadex import UPLOADS_URL
from mdal.config import USER_AGENT
from mdal.covers import RETRY_FAILED, CoverCache
from mdal.recommend.fresh import MD_COVER
from mdal.web.app import create_app

MD_ID = "bbed470c-5ca5-45ec-8101-474323387734"
FILE = "026c0c0e-5e2e-4252-bcf4-1f1df846f0a4.png"
URL = f"{UPLOADS_URL}/covers/{MD_ID}/{FILE}.256.jpg"
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
CHALLENGE = "https://mangadex.org/edge/challenge?src=L2NvdmVycy8"


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def covers(services, tmp_path):
    services.mangadex_queue.set_interval(0)
    now = [0.0]
    cache = CoverCache(services, root=tmp_path / "covers", clock=lambda: now[0])
    cache.now = now
    services.covers = cache
    return cache


async def test_a_cover_is_fetched_once_as_shiori_and_kept(covers, mock):
    route = mock.get(URL).respond(200, content=JPEG, headers={"content-type": "image/jpeg"})
    first, again = await asyncio.gather(covers.get(MD_ID, FILE, 256), covers.get(MD_ID, FILE, 256))
    assert first == again and first.read_bytes() == JPEG
    assert await covers.get(MD_ID, FILE, 256) == first
    assert route.call_count == 1                                   # two pages at once, then a later view: one request
    sent = route.calls[0].request
    assert sent.headers["user-agent"] == USER_AGENT and "authorization" not in sent.headers


async def test_the_browser_check_is_not_an_image_and_is_not_asked_again_soon(covers, mock):
    route = mock.get(URL).respond(302, headers={"location": CHALLENGE, "content-type": "text/plain"})
    assert await covers.get(MD_ID, FILE, 256) is None
    assert await covers.get(MD_ID, FILE, 256) is None
    assert route.call_count == 1
    covers.now[0] += RETRY_FAILED + 1
    route.respond(200, content=JPEG, headers={"content-type": "image/jpeg"})
    assert (await covers.get(MD_ID, FILE, 256)).read_bytes() == JPEG


async def test_a_403_starts_the_mangadex_cooldown(services, covers, mock):
    mock.get(URL).respond(403)
    assert await covers.get(MD_ID, FILE, 256) is None
    until, reason = services.mangadex_guard.cooldown()
    assert "403" in reason
    # During the cooldown nothing more is sent to MangaDex, covers included.
    other = mock.get(f"{UPLOADS_URL}/covers/{MD_ID}/other.jpg.256.jpg").respond(200, content=JPEG)
    assert await covers.get(MD_ID, "other.jpg", 256) is None and not other.called


@pytest.mark.parametrize("md_id, file, size", [
    ("../../etc", FILE, 256), (MD_ID, "../secret.jpg", 256), (MD_ID, "cover.exe", 256), (MD_ID, FILE, 1024),
])
async def test_only_mangadex_shaped_requests_reach_disk_or_network(covers, mock, md_id, file, size):
    assert await covers.get(md_id, file, size) is None
    assert not mock.calls


def test_pages_point_at_shiori_and_the_route_serves_or_404s(services, covers, mock):
    assert MD_COVER.format(md_id=MD_ID, file=FILE) == f"/covers/md/{MD_ID}/{FILE}?size=256"
    mock.get(URL).respond(200, content=JPEG, headers={"content-type": "image/jpeg"})
    mock.get(f"{UPLOADS_URL}/covers/{MD_ID}/gone.jpg.256.jpg").respond(404)
    with TestClient(create_app(services)) as c:
        ok = c.get(f"/covers/md/{MD_ID}/{FILE}")
        assert ok.status_code == 200 and ok.content == JPEG and "immutable" in ok.headers["cache-control"]
        assert c.get(f"/covers/md/{MD_ID}/gone.jpg").status_code == 404
        assert c.get(f"/covers/md/{MD_ID}/{FILE}?size=9").status_code == 404
