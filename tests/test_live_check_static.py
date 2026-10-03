"""The live check script is never run against real APIs in tests; these check its safety properties."""

import importlib.util
import re
from pathlib import Path

import pytest
import respx

from tests.conftest import FAKE_ENV
from tests.factories import FakeAniList, FakeMangaDex, FakeSeries, al_media

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_check.py"


@pytest.fixture(scope="module")
def live_check():
    spec = importlib.util.spec_from_file_location("live_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def full_env(tmp_path):
    path = tmp_path / ".env"
    values = {**FAKE_ENV, "ANILIST_ACCESS_TOKEN": "live-check-token-123", "MDAL_DB_PATH": str(tmp_path / "lc.db")}
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


def test_script_contains_no_write_operations():
    text = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"mutation", text, re.IGNORECASE)
    assert "SaveMediaListEntry" not in text
    assert not re.search(r"\.(post|put|delete|patch)\(", text)


def test_missing_env_lists_names_only(live_check, tmp_path):
    env = tmp_path / ".env"
    env.write_text("MANGADEX_USERNAME=reader\nMANGADEX_PASSWORD=secret-pw-999\n", encoding="utf-8")
    lines: list[str] = []
    assert live_check.main([], env_file=env, out=lines.append) == 2
    text = "\n".join(lines)
    assert "MANGADEX_CLIENT_ID" in text and "ANILIST_ACCESS_TOKEN" in text
    assert "secret-pw-999" not in text


def test_dry_run_sends_nothing(live_check, full_env):
    lines: list[str] = []
    with respx.mock(assert_all_mocked=True) as router:
        assert live_check.main(["--dry"], env_file=full_env, out=lines.append) == 0
        assert len(router.calls) == 0
    assert "Planned requests" in lines[0]


def test_full_run_against_fakes_is_read_only(live_check, full_env, monkeypatch):
    from mdal.clients.ratelimit import PacedQueue

    monkeypatch.setattr(PacedQueue, "next_start", lambda self: 0.0)  # no real waiting in tests
    al = FakeAniList()
    al.add_media(al_media(1, "Berserk"), al_media(2, "Frieren"))
    al.add_list("Reading", [(10, 1, "CURRENT", 5)])
    al.add_list("Hidden", [(11, 2, "CURRENT", 3)], custom=True)
    md = FakeMangaDex()
    md.add(FakeSeries("m1", "Berserk", reads=["c1", "c2"]), {"c1": "1", "c2": None})
    lines: list[str] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        al.install(router)
        md.install(router)
        assert live_check.main([], env_file=full_env, out=lines.append) == 0
    report = "\n".join(lines)
    assert "FAILED" not in report, report
    assert "2 entries, 1 only in custom lists" in report
    assert "2 asked, 2 returned, 1 with null chapter number" in report
    assert all("mutation" not in body["query"].lower() for body in al.requests)
    assert al.call_count == 5
    assert "live-check-token-123" not in report
