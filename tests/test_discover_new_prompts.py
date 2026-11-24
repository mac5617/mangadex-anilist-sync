"""New releases: what the local model is asked, how its answer is cleaned, and what the page says about it."""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from mdal.recommend import fresh
from mdal.recommend.llm import DATA_NOT_INSTRUCTIONS
from mdal.recommend.profile import build_profile
from mdal.web.app import create_app
from tests.test_new_releases import mock, model_auto, ollama, scan, uuid, world  # noqa: F401  (fixtures)


def rec(n: int, title: str | None = None, description: str | None = "A story.", **row) -> fresh.NewRec:
    return fresh.make_new_rec({"md_id": uuid(n), "title": title or f"T{n}", "tags": '[{"name": "Horror"}]',
                               "authors": '["Ito Junji"]', "description": description, **row})


PROFILE = build_profile([
    {"media_id": 1, "title": "Uzumaki", "status": "COMPLETED", "score": 95, "progress": 20, "format": "MANGA",
     "genres": ["Horror"], "tag_ranks": [{"name": "Body Horror", "rank": 90}], "staff": []},
    {"media_id": 2, "title": "Rent-A-Girlfriend", "status": "DROPPED", "score": None, "progress": 3, "format": "MANGA",
     "genres": ["Romance"], "tag_ranks": [], "staff": []},
])


# ---- the prompt -----------------------------------------------------------------------------------


def test_prompt_asks_for_no_more_picks_than_there_are_series():
    prompt = fresh.build_prompt(PROFILE, [rec(1), rec(2), rec(3)])
    assert "Choose the 3 " in prompt and f"Choose the {fresh.PICKS} " not in prompt
    prompt = fresh.build_prompt(PROFILE, [rec(i) for i in range(1, 50)])
    assert f"Choose the {fresh.PICKS} " in prompt and "\n40. T40 |" in prompt and "41. T41" not in prompt


def test_prompt_grounds_reasons_in_the_readers_list():
    prompt = fresh.build_prompt(PROFILE, [rec(1, similar_to='["Uzumaki"]')])
    assert "- Uzumaki (completed, scored 95/100)" in prompt
    assert "closest on their list: Uzumaki" in prompt
    assert "copied exactly" in prompt                          # names come from the list, not from memory
    assert "at most 30 words" in prompt
    # The rules the small model keeps breaking are said outright, in the system prompt too.
    for rule in ("by its number", "Never mention list numbers", "unique", "spoil"):
        assert rule in fresh.SYSTEM, rule
    assert DATA_NOT_INSTRUCTIONS in fresh.SYSTEM


def test_a_series_without_a_description_says_so():
    line = fresh.candidate_line(4, rec(4, description=None))
    assert line.endswith("\n   (no description)")
    assert "(no description)" in fresh.build_prompt(PROFILE, [rec(4, description=None)])


def test_schema_explains_each_field():
    item = fresh.SCHEMA["properties"]["picks"]["items"]["properties"]
    assert "number" in item["n"]["description"] and "30 words" in item["reason"]["description"]


# ---- cleaning the answer --------------------------------------------------------------------------


CANDIDATES = [rec(1, "Cursed Flesh"), rec(2, "Love Letters"), rec(3, "Quiet Isekai")]


def test_numbers_written_as_text_still_count():
    picks = fresh.clean_picks({"picks": [{"n": "#2", "reason": "A school romance like Horimiya."},
                                         {"n": "3.", "reason": "Calm isekai like Read 1."},
                                         {"n": "first", "reason": "No number at all here."}]}, CANDIDATES)
    assert [p["md_id"] for p in picks] == [uuid(2), uuid(3)]


def test_a_bare_list_of_picks_is_read_too():
    picks = fresh.clean_picks([{"n": 1, "reason": "A creeping curse, like Uzumaki."}], CANDIDATES)  # type: ignore[arg-type]
    assert picks == [{"md_id": uuid(1), "reason": "A creeping curse, like Uzumaki."}]
    assert fresh.clean_picks({"picks": "none"}, CANDIDATES) == []
    assert fresh.clean_picks("junk", CANDIDATES) == []  # type: ignore[arg-type]


def test_reasons_lose_list_numbers_labels_and_quotes():
    assert fresh.clean_reason("1. Reason: A curse spreads through a town, like Uzumaki.", CANDIDATES) \
        == "A curse spreads through a town, like Uzumaki."
    assert fresh.clean_reason('"A **slow** school romance, like Horimiya."', CANDIDATES) == "A slow school romance, like Horimiya."
    # A pointer to another entry by number becomes its title: the page has no numbers.
    assert fresh.clean_reason("Darker than #2, closer to Uzumaki.", CANDIDATES) == "Darker than Love Letters, closer to Uzumaki."
    assert fresh.clean_reason("Pairs well with candidate 3 and Uzumaki.", CANDIDATES) == "Pairs well with Quiet Isekai and Uzumaki."
    assert fresh.clean_reason("Like #9 on the list.", CANDIDATES) == "Like #9 on the list."     # not a real entry: left alone


def test_long_reasons_end_at_a_word_not_midway():
    long = "A curse spreads through a quiet seaside town, " * 12
    cleaned = fresh.clean_reason(long, CANDIDATES)
    assert len(cleaned) <= fresh.REASON_CHARS and cleaned.endswith("…") and not cleaned.endswith(" …")
    assert cleaned[:-1] == long[: len(cleaned) - 1]
    two = "A curse spreads, like Uzumaki. " + "Then a long second sentence that rambles on and on. " * 6
    cut = fresh.clean_reason(two, CANDIDATES)          # whole sentences only, as many as fit
    assert len(cut) <= fresh.REASON_CHARS and cut.endswith("on and on.") and two.startswith(cut)


def test_bare_titles_and_empty_reasons_are_still_refused():
    picks = fresh.clean_picks({"picks": [{"n": 1, "reason": "Cursed Flesh"}, {"n": 2, "reason": "  "},
                                         {"n": 3, "reason": "Calm isekai, like Read 1."}]}, CANDIDATES)
    assert [p["md_id"] for p in picks] == [uuid(3)]


# ---- the model's run and its messages ---------------------------------------------------------------


async def test_ask_the_model_says_how_many_it_picked(services, world, mock):
    services.repo.set_setting("llm_auto", False)
    route = ollama(mock, {"picks": [{"n": 1, "reason": "Curses, like Loved 1."}, {"n": 2, "reason": "A school romance."},
                                    {"n": 3, "reason": "Also a fine isekai."}]})
    await scan(services)
    services.releases.start_llm()
    assert services.releases.status()["task"] == "ask"
    await services.releases.task
    status = services.releases.status()
    assert status["state"] == "done" and status["detail"].startswith("3 picks from the best 4 new releases"), status
    sent = json.loads(route["chat"].calls[-1].request.content)
    assert "Choose the 4 " in sent["messages"][-1]["content"]


async def test_too_few_usable_picks_keeps_the_earlier_ones_and_says_so(services, world, mock):
    ollama(mock, {"picks": [{"n": 1, "reason": "Curses, like Loved 1."}, {"n": 2, "reason": "A school romance."},
                            {"n": 3, "reason": "Also a fine isekai."}]})
    await scan(services)
    earlier = services.repo.get_setting("new_llm")["picks"]
    mock.post("http://127.0.0.1:11434/api/chat").respond(200, json={"message": {"content": json.dumps({"picks": []})}})
    services.releases.start_llm()
    await services.releases.task
    llm = services.repo.get_setting("new_llm")
    assert llm["picks"] == earlier
    assert llm["error"] == "the model returned only 0 usable picks; the earlier picks are kept"


async def test_nothing_left_to_choose_from_says_why(services, world, mock):
    ollama(mock, {"picks": []})
    await scan(services)
    for r in services.releases.recs()[1]:
        services.repo.hide_new(r.md_id)
    services.releases.start_llm()
    await services.releases.task
    assert "everything with a new chapter this week is read, followed or hidden" in services.repo.get_setting("new_llm")["error"]


# ---- the page --------------------------------------------------------------------------------------


def test_status_panel_names_what_is_running(services, mock):
    services.repo.set_setting("new_status", {"state": "running", "task": "ask", "detail": "comparing descriptions"})
    services.releases.task = SimpleNamespace(done=lambda: False)
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.get("/discover-new-status").text
        assert "asking gpt-oss:20b" in html and "scanning" not in html
        services.repo.set_setting("new_status", {"state": "running", "detail": "reading MangaDex"})
        assert "scanning" in c.get("/discover-new-status").text


def test_a_failed_ask_is_not_called_a_failed_scan(services, mock):
    services.repo.set_setting("new_status", {"state": "failed", "task": "ask", "error": "boom"})
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.get("/discover-new-status").text
        assert "Ask the model failed: boom" in html and "Scan failed" not in html
        services.repo.set_setting("new_status", {"state": "failed", "error": "HTTP 500"})
        assert "Scan failed: HTTP 500" in c.get("/discover-new-status").text


def test_after_asking_the_scan_time_is_still_the_scans(services, mock):
    services.repo.set_setting("new_status", {"state": "done", "task": "ask", "detail": "3 picks from the best 3 new releases",
                                             "scanned_at": "2026-11-20T10:00:00+00:00",
                                             "finished_at": "2026-11-24T10:00:00+00:00"})
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.get("/discover-new-status").text
    assert "2026-11-20" in html and "2026-11-24" not in html and "3 picks from the best 3 new releases" in html


def test_a_model_error_says_which_picks_are_shown(services, mock):
    services.repo.set_setting("new_llm", {"model": "gpt-oss:20b", "picks": [{"md_id": uuid(1), "reason": "x y z w."}],
                                          "created_at": "2026-11-20T10:00:00+00:00", "error": "Ollama is not running.",
                                          "error_at": "2026-11-24T10:00:00+00:00"})
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.get("/discover-new-status").text
    assert "Ask the model failed" in html and "Ollama is not running." in html
    assert "The picks below are from 2026-11-20" in html


async def test_page_says_when_nothing_is_new_to_you(services, world, mock):
    services.repo.set_setting("llm_auto", False)
    ollama(mock)
    await scan(services)
    for r in services.releases.recs()[1]:
        services.repo.hide_new(r.md_id)
    with TestClient(create_app(services), follow_redirects=False) as c:
        html = c.get("/discover/new").text
    assert "Nothing new to you this week" in html and "Best matches" not in html



def test_a_fractional_pick_number_is_refused():
    assert fresh.pick_number(3.7) is None and fresh.pick_number(3.0) == 3
