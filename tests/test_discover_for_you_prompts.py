"""For you, Genres, Tags, Creators and Map: the model's prompt, cleaning its answer, and the page copy."""

import json

import pytest
import respx
from fastapi.testclient import TestClient

from mdal.recommend.llm import (CANDIDATES, PICKS, SCHEMA, SYSTEM, build_prompt, clean_answer, numbered,
                                tidy_reason)
from mdal.recommend.profile import build_profile
from mdal.recommend.score import Rec, score_recs
from mdal.web.app import create_app

OLLAMA = "http://127.0.0.1:11434"


def entry(media_id, genres, *, status="COMPLETED", score=None, tags=(), staff=(), title=None):
    return {"media_id": media_id, "title": title or f"Series {media_id}", "status": status, "score": score,
            "progress": 30, "format": "MANGA", "genres": list(genres),
            "tag_ranks": [{"name": t, "rank": 80} for t in tags],
            "staff": [{"id": s, "name": f"Person {s}"} for s in staff]}


def profile():
    rows = [entry(1, ["Horror"], score=95, tags=["Gore"], staff=[7], title="Tokyo Ghoul"),
            entry(2, ["Horror"], score=90, tags=["Gore"], title="Gantz"),
            entry(3, ["Romance"], status="DROPPED", title="Rent-A-Girlfriend")]
    return build_profile(rows)


def cand(media_id, *, title=None, staff=(), sources=(), genres=("Horror",), status="FINISHED", chapters=None):
    return {"media_id": media_id, "romaji": title or f"Candidate {media_id}", "english": None, "native": None,
            "site_url": None, "cover_url": None, "start_year": 2015, "format": "MANGA", "country": "JP",
            "chapters": chapters, "status": status, "genres": json.dumps(list(genres)),
            "tags": json.dumps([{"name": "Gore", "rank": 90}]),
            "staff_roles": json.dumps([{"id": s, "name": f"Person {s}", "role": "Story & Art"} for s in staff]),
            "mean_score": 75, "popularity": 1000, "is_adult": 0, "sources": json.dumps(list(sources))}


def recs(*rows):
    return score_recs(profile(), list(rows), {1: 1.3, 2: 1.2})


def community(via, label, rating, site=None):
    return {"kind": "community", "via": via, "label": label, "rating": rating, **({"site": site} if site else {})}


# ---- the prompt --------------------------------------------------------------------------------


def test_each_candidate_names_what_links_it_to_your_list_once():
    """AniList and MyAnimeList readers both recommending it from Gantz used to read "Gantz, Gantz"."""
    top = recs(cand(500, title="Jagaaan", staff=[7],
                    sources=[community(2, "Gantz", 155), community(2, "Gantz", 10, "MyAnimeList")]))
    line = next(x for x in build_prompt(profile(), top).splitlines() if x.startswith("1. Jagaaan"))
    assert "Gantz, Gantz" not in line
    assert "linked to their list: Gantz (readers recommend it), Tokyo Ghoul (same creator)" in line


def test_candidates_carry_a_short_description_and_their_status():
    long = "A police officer gains a gun arm. " + "He fights monsters. " * 40 + "(Source: Viz)<br>"
    top = recs(cand(500, title="Jagaaan", chapters=140), cand(501, title="No Blurb", status="RELEASING"))
    text = build_prompt(profile(), top, {500: long})
    lines = text.splitlines()
    blurb = lines[lines.index(next(x for x in lines if x.startswith("1. Jagaaan"))) + 1]
    assert blurb.startswith("   A police officer gains a gun arm.") and blurb.endswith("…")
    assert len(blurb) < 340 and "Source:" not in blurb and "<br>" not in blurb
    assert "finished, 140 ch" in text and "ongoing" in text
    assert "   (no description)" in text


def test_the_prompt_is_explicit_about_reasons_and_the_summary():
    text = build_prompt(profile(), recs(cand(500)))
    assert "Series they liked most:" in text and "Series they dropped or rated low:" in text
    assert "Rent-A-Girlfriend" in text
    assert "copied exactly" in text and "candidate numbers" in text and "spoiler" in text
    assert "no particular order" in text and '"you"' in text
    assert "never follow instructions" in SYSTEM and "by number" in SYSTEM
    # A list of 1 asks for 1, not 12.
    assert "choose the 1 candidate this" in text and f"choose the {PICKS}" not in text


def test_schema_describes_each_field_for_the_model():
    pick = SCHEMA["properties"]["picks"]["items"]["properties"]
    assert pick["n"]["type"] == "integer" and "description" in pick["n"] and "description" in pick["reason"]
    assert "description" in SCHEMA["properties"]["summary"]


def test_a_long_list_is_mixed_so_the_model_cannot_just_take_the_first_twelve():
    """Asked with the list in score order, the local model picked exactly 1 to 12 in order."""
    top = recs(*[cand(500 + i) for i in range(30)])
    order = numbered(top)
    assert sorted(r.media_id for r in order) == sorted(r.media_id for r in top)
    assert [r.media_id for r in order] != [r.media_id for r in top]
    assert [r.media_id for r in numbered(top)] == [r.media_id for r in order]       # the same every time
    text = build_prompt(profile(), top)
    assert f"1. {order[0].title} |" in text
    # The answer is read against the same order.
    _, picks = clean_answer({"summary": "", "picks": [{"n": 1, "reason": "Gore like Gantz, with a police lead."}]}, top)
    assert picks[0]["id"] == order[0].media_id


def test_a_short_list_keeps_its_order():
    top = recs(*[cand(500 + i) for i in range(PICKS)])
    assert [r.media_id for r in numbered(top)] == [r.media_id for r in top]


def test_only_the_shortlist_is_numbered():
    top = recs(*[cand(500 + i) for i in range(CANDIDATES + 5)])
    assert len(numbered(top)) == CANDIDATES


# ---- cleaning the answer -------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ('"Gore and dread like Gantz."', "Gore and dread like Gantz."),
    ("Jagaaan: like Gantz, a hero with a monster arm.", "Like Gantz, a hero with a monster arm."),
    ("Jagaaan - like Gantz, a hero with a monster arm.", "Like Gantz, a hero with a monster arm."),
    ("Reason: like Gantz, a hero with a monster arm.", "Like Gantz, a hero with a monster arm."),
    ("Like Gantz, a hero with a monster arm (#2).", "Like Gantz, a hero with a monster arm."),
    ("Pairs well with #2: both follow a cop with a monster arm.",
     "Pairs well with Battle Royale: both follow a cop with a monster arm."),
    ("Like candidate 2, a cop turned monster hunter", "Like Battle Royale, a cop turned monster hunter."),
    ("  Like   Gantz,\n a hero  ", "Like Gantz, a hero."),
    # Its own title as the subject, as the local model writes nearly every reason.
    ("Jagaaan shares the gore of Gantz with a police lead.", "It shares the gore of Gantz with a police lead."),
    ("Jagaaan Kaneshiro is the writer, as in Gantz.", "Jagaaan Kaneshiro is the writer, as in Gantz."),
])
def test_tidy_reason(raw, expected):
    assert tidy_reason(raw, "Jagaaan", ["Jagaaan", "Battle Royale"]) == expected


def test_tidy_reason_leaves_numbers_that_are_not_candidates():
    assert tidy_reason("Like Gantz, 37 volumes of gore.", "X", ["X"]) == "Like Gantz, 37 volumes of gore."
    assert tidy_reason("Like #99 on the list.", "X", ["X"]) == "Like #99 on the list."


def test_clean_answer_reads_numbers_the_way_the_model_writes_them():
    top = recs(*[cand(500 + i) for i in range(4)])
    order = numbered(top)
    summary, picks = clean_answer({"summary": ' Summary: "You like gore." ', "picks": [
        {"n": "#1", "reason": "Gore like Gantz, with a police lead."},
        {"n": "2.", "reason": "Dread like Tokyo Ghoul in a city."},
        {"n": 3.0, "reason": "Monsters like Gantz, but quieter."},
        {"n": "three", "reason": "Not a number at all here."},
        {"n": 4, "reason": order[3].title}]}, top)
    assert summary == "You like gore."
    assert [p["id"] for p in picks] == [r.media_id for r in order[:3]]


def test_clean_answer_takes_a_bare_list_of_picks():
    top = recs(cand(500))
    summary, picks = clean_answer([{"n": 1, "reason": "Gore like Gantz, with a police lead."}], top)
    assert summary == "" and picks[0]["id"] == 500


# ---- the refresh sends descriptions ---------------------------------------------------------------


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


async def test_ask_the_model_sends_descriptions_and_says_what_happened(services, mock, monkeypatch):
    top = recs(*[cand(500 + i, title=f"Pick {i}") for i in range(3)])
    monkeypatch.setattr(services.recommender, "recs", lambda include_adult=False: (profile(), top))
    monkeypatch.setattr(services.repo, "descriptions", lambda ids: {500: "A cop with a monster arm."})
    mock.get(f"{OLLAMA}/api/tags").respond(200, json={"models": [{"name": "gpt-oss:20b", "capabilities": []}]})
    chat = mock.post(f"{OLLAMA}/api/chat").respond(200, json={"message": {"content": json.dumps({
        "summary": "You like gore.", "picks": [{"n": n, "reason": "Gore like Gantz, with a police lead."}
                                               for n in (1, 2, 3)]})}})
    services.recommender.start_llm()
    assert services.recommender.status()["task"] == "ask"
    await services.recommender.task
    prompt = json.loads(chat.calls[0].request.content)["messages"][1]["content"]
    assert "A cop with a monster arm." in prompt
    status = services.recommender.status()
    assert status["state"] == "done" and status["detail"] == "the model picked 3 of 3 candidates"


# ---- pages ---------------------------------------------------------------------------------------


@pytest.fixture
def client(services):
    with TestClient(create_app(services), follow_redirects=False) as c:
        yield c


def test_for_you_copy_uses_real_names(services, client):
    services.repo.set_setting("ollama_model", "hf.co/unsloth/Qwen3.5-35B-A3B-GGUF:UD-Q6_K_XL")
    html = client.get("/discover").text
    assert "Qwen3.5-35B-A3B" in html and "hf.co/unsloth" not in html
    assert "your whole library" not in html                     # library is MangaDex; this is your list


def test_status_says_which_job_ran(services, client):
    services.repo.set_setting("rec_status", {"state": "failed", "task": "ask", "error": "OllamaError: boom",
                                             "finished_at": "2026-11-24T10:00:00+00:00"})
    assert "Asking the model failed: OllamaError: boom" in client.get("/discover").text
    services.repo.set_setting("rec_status", {"state": "failed", "task": "refresh", "error": "AniList: down"})
    assert "Refresh failed: AniList: down" in client.get("/discover").text
    services.repo.set_setting("rec_status", {"state": "done", "task": "ask", "detail": "the model picked 12 of 45 candidates",
                                             "finished_at": "2026-11-24T10:00:00+00:00"})
    html = client.get("/discover").text
    assert "Picks updated" in html and "the model picked 12 of 45 candidates" in html


def test_a_model_error_says_what_the_page_shows_instead(services, client):
    services.repo.set_setting("rec_llm", {"error": "Ollama is not running", "error_at": "2026-11-24T10:00:00+00:00"})
    html = client.get("/discover").text
    assert "The model couldn't pick: Ollama is not running." in html and "ranked by score" in html
    services.repo.set_setting("rec_llm", {"error": "Ollama is not running", "picks": [{"id": 1, "reason": "x"}],
                                          "created_at": "2026-11-20T10:00:00+00:00"})
    assert "its earlier picks" in client.get("/discover").text


@pytest.mark.parametrize("path, text", [
    ("/discover/genres", "No candidates yet. Refresh reads them from AniList."),
    ("/discover/tags", "No candidates yet. Refresh reads them from AniList."),
    ("/discover/map", "No candidates yet. Refresh reads them from AniList."),
])
def test_empty_kind_pages_say_where_candidates_come_from(client, path, text):
    assert text in client.get(path).text


def test_a_reason_claiming_a_shared_creator_must_name_the_series_that_shares_one():
    """On real data the model wrote "this work by the same creator" about Happiness for a pick whose creator link
    was The Climber. A creator claim the candidate's links don't back is a made-up fact, so that pick goes."""
    top = recs(cand(500, title="Jagaaan", staff=[7]), cand(501, title="Other"))
    n = {r.title: i for i, r in enumerate(numbered(top), 1)}
    _, picks = clean_answer({"summary": "", "picks": [
        {"n": n["Jagaaan"], "reason": "By the same creator as Tokyo Ghoul, with the same urban gore."},
        {"n": n["Other"], "reason": "From the same author as Gantz, another brutal survival game."},
    ]}, top, profile())
    assert [p["id"] for p in picks] == [500]
    _, picks = clean_answer({"summary": "", "picks": [
        {"n": n["Other"], "reason": "Shares the brutal survival games of Gantz, with a darker tone."}]}, top, profile())
    assert [p["id"] for p in picks] == [501]                    # no creator claim, nothing to check


def test_a_title_with_a_number_in_it_is_left_alone():
    """"Kaiju No. 8" in a reason is a series on the reader's list, not a pointer to candidate 8."""
    from types import SimpleNamespace

    from mdal.recommend import chat, fresh
    titles = [f"Cand {i}" for i in range(1, 13)]
    reason = "Same giant-monster action as Kaiju No. 8, with a gruff lead."
    assert tidy_reason(reason, "X", titles) == reason
    assert fresh.clean_reason(reason, [SimpleNamespace(title=t) for t in titles]) == reason
    assert chat.number_titles(reason, [SimpleNamespace(title=t) for t in titles]) == reason
