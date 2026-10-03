"""Story 16: the writer. Every sent mutation is inspected."""

import math
import re

import pytest
import respx

import mdal.sync.writer as writer_module
from mdal.sync.orchestrator import ApprovalError, FIRST_WRITE_MESSAGE
from tests.factories import FakeAniList, al_media

FORBIDDEN_ARGS = ("score", "notes", "startedAt", "completedAt", "repeat", "private", "mediaId", "hiddenFromStatusLists")


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fast(services):
    services.anilist_queue.set_interval(0)
    services.anilist.RATE_LIMIT_MARGIN = 0.0
    services.repo.set_setting("anilist_user_id", 1)
    services.repo.set_setting("first_write_done", True)
    return services


@pytest.fixture
def fake(mock):
    return FakeAniList().install(mock)


def seed(services, fake, specs):
    """specs: list of dicts with md_progress, al_progress (on AniList now), and optional diff fields.

    Creates a `diffed` run whose items match; AniList's list holds the given current state.
    """
    repo = services.repo
    entries = []
    items = []
    run_id = repo.create_run("diffed")
    for i, s in enumerate(specs):
        media_id, entry_id = i + 1, 100 + i
        fake.add_media(al_media(media_id, f"Series {i}", chapters=s.get("chapters")))
        entries.append((entry_id, media_id, s.get("status", "CURRENT"), s.get("now", s.get("al_progress", 0))))
        items.append({
            "run_id": run_id, "md_id": f"md{i:02}", "al_media_id": media_id, "al_entry_id": entry_id,
            "al_progress": s.get("al_progress", 0), "md_progress": s["md_progress"],
            "action": s.get("action", "write"), "flag_kind": s.get("flag_kind"), "reason": "r",
            "set_status": s.get("set_status"), "status_source": "AniList" if s.get("set_status") else None,
            "status_approved": 1 if s.get("set_status") else 0,
        })
    fake.add_list("Reading", entries)
    repo.replace_items(run_id, items)
    return run_id


async def approve_all(services, run_id, completing=None, overrides=()):
    items = services.repo.items(run_id)
    sel = [i["md_id"] for i in items if i["action"] != "skip"]
    mc = [i["md_id"] for i in items if i["set_status"]] if completing is None else completing
    await services.orchestrator.approve(run_id, sel, mc, overrides)
    await services.orchestrator.wait()
    return {i["md_id"]: i for i in services.repo.items(run_id)}


def sent_aliases(fake):
    """[(args dict)] for every alias in every executed mutation document."""
    out = []
    for m in fake.mutations:
        for alias, arg_text in re.findall(r"(m\d+): SaveMediaListEntry\(([^)]*)\)", m["query"]):
            args = {}
            for name, value in re.findall(r"(\w+): (\$\w+|\w+)", arg_text):
                args[name] = m["variables"].get(value[1:]) if value.startswith("$") else value
            out.append(args)
    return out


# ---- what is sent -------------------------------------------------------------


async def test_documents_contain_only_allowed_fields(fast, fake):
    run_id = seed(fast, fake, [
        {"md_progress": 10, "al_progress": 5},                                         # progress
        {"md_progress": 120, "al_progress": 110, "chapters": 120, "set_status": "COMPLETED"},  # progress + completion
        {"md_progress": 50, "al_progress": 50, "chapters": 50, "set_status": "COMPLETED"},     # status-only
        {"md_progress": 30, "al_progress": 20, "chapters": 30, "set_status": "COMPLETED"},     # completion unticked
    ])
    items = await approve_all(fast, run_id, completing=["md01", "md02"])
    assert all(i["write_state"] == "done" for i in items.values()), [dict(i) for i in items.values()]

    for m in fake.mutations:
        q = m["query"]
        assert q.startswith("mutation (")
        for word in FORBIDDEN_ARGS:
            assert not re.search(rf"\b{word}:", q), word
        assert set(re.findall(r"status: (\w+)", q)) <= {"COMPLETED"}
        assert "$s" not in q
        assert set(m["variables"]) <= {f"{k}{n}" for k in "ep" for n in range(20)}

    by_entry = {a["id"]: a for a in sent_aliases(fake)}
    assert by_entry[100] == {"id": 100, "progress": 10}
    assert by_entry[101] == {"id": 101, "progress": 120, "status": "COMPLETED"}
    assert by_entry[102] == {"id": 102, "status": "COMPLETED"}          # status-only: no progress
    assert by_entry[103] == {"id": 103, "progress": 30}                 # unticked: no status


async def test_never_lower_recheck_drops_without_mutation(fast, fake):
    run_id = seed(fast, fake, [{"md_progress": 10, "al_progress": 5, "now": 12}, {"md_progress": 8, "al_progress": 2}])
    items = await approve_all(fast, run_id)
    assert items["md00"]["write_state"] == "dropped" and "now at 12" in items["md00"]["verify_note"]
    assert [a["id"] for a in sent_aliases(fake)] == [101]
    assert fake.entry(100)["progress"] == 12


async def test_entry_gone_is_dropped(fast, fake):
    run_id = seed(fast, fake, [{"md_progress": 10, "al_progress": 5}, {"md_progress": 8, "al_progress": 2}])
    fake.lists[0]["entries"] = [e for e in fake.lists[0]["entries"] if e["id"] != 100]
    items = await approve_all(fast, run_id)
    assert items["md00"]["write_state"] == "dropped" and "no longer on your AniList list" in items["md00"]["verify_note"]


async def test_status_only_already_completed_is_dropped(fast, fake):
    run_id = seed(fast, fake, [
        {"md_progress": 50, "al_progress": 50, "chapters": 50, "set_status": "COMPLETED", "status": "COMPLETED"},
        {"md_progress": 8, "al_progress": 2},
    ])
    items = await approve_all(fast, run_id)
    assert items["md00"]["write_state"] == "dropped" and "already COMPLETED" in items["md00"]["verify_note"]
    assert 100 not in [a["id"] for a in sent_aliases(fake)]


async def test_status_only_progress_moved_is_dropped(fast, fake):
    run_id = seed(fast, fake, [
        {"md_progress": 50, "al_progress": 50, "now": 49, "chapters": 50, "set_status": "COMPLETED"},
        {"md_progress": 8, "al_progress": 2},
    ])
    items = await approve_all(fast, run_id)
    assert items["md00"]["write_state"] == "dropped"


async def test_progress_plus_completion_becomes_status_only(fast, fake):
    run_id = seed(fast, fake, [
        {"md_progress": 120, "al_progress": 110, "now": 120, "chapters": 120, "set_status": "COMPLETED"},
        {"md_progress": 8, "al_progress": 2},
    ])
    items = await approve_all(fast, run_id)
    assert items["md00"]["write_state"] == "done"
    assert {a["id"]: a for a in sent_aliases(fake)}[100] == {"id": 100, "status": "COMPLETED"}
    assert fake.entry(100)["status"] == "COMPLETED"


async def test_completion_dropped_if_already_completed_but_progress_still_written(fast, fake):
    run_id = seed(fast, fake, [
        {"md_progress": 120, "al_progress": 110, "chapters": 120, "set_status": "COMPLETED", "status": "COMPLETED"},
        {"md_progress": 8, "al_progress": 2},
    ])
    await approve_all(fast, run_id)
    assert {a["id"]: a for a in sent_aliases(fake)}[100] == {"id": 100, "progress": 120}


# ---- batching ---------------------------------------------------------------------


async def test_25_items_3_batches_aliases_mapped(fast, fake):
    run_id = seed(fast, fake, [{"md_progress": 10 + i, "al_progress": 1} for i in range(25)])
    items = await approve_all(fast, run_id)
    assert len(fake.mutations) == 3
    assert all(i["write_state"] == "done" and i["written_at"] for i in items.values())
    for i in range(25):
        assert fake.entry(100 + i)["progress"] == 10 + i


async def test_request_count_matches_estimate(fast, fake):
    run_id = seed(fast, fake, [{"md_progress": 10 + i, "al_progress": 1} for i in range(25)])
    before = fast.repo.get_run(run_id)["req_anilist"]
    await approve_all(fast, run_id)
    r = fast.repo.get_run(run_id)
    assert r["req_anilist"] - before == 2 + math.ceil(25 / 10) == fake.call_count
    assert r["state"] == "done"


async def test_resume_after_crash_sends_only_remaining(fast, fake, monkeypatch):
    run_id = seed(fast, fake, [{"md_progress": 10 + i, "al_progress": 1} for i in range(25)])
    real = writer_module.send_batch
    calls = {"n": 0}

    async def crashing(client, ops):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated crash")
        return await real(client, ops)

    monkeypatch.setattr(writer_module, "send_batch", crashing)
    items = await approve_all(fast, run_id)
    r = fast.repo.get_run(run_id)
    assert r["state"] == "failed" and "simulated crash" in r["error"]
    assert sum(i["write_state"] == "done" for i in items.values()) == 20
    assert sum(i["write_state"] == "pending" for i in items.values()) == 5
    assert fast.orchestrator.resumable_run() == run_id

    monkeypatch.setattr(writer_module, "send_batch", real)
    await fast.orchestrator.resume(run_id)
    await fast.orchestrator.wait()
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert fast.repo.get_run(run_id)["state"] == "done"
    assert all(i["write_state"] == "done" for i in items.values())
    assert len(fake.mutations) == 3
    assert len(fake.applied) == 25  # nothing written twice


async def test_restart_while_writing_can_resume(fast, fake):
    run_id = seed(fast, fake, [{"md_progress": 10, "al_progress": 1}])
    fast.repo.update_items(run_id, [("md00", {"approved": 1, "write_state": "pending"})])
    fast.repo.update_run(run_id, state="writing", approved_at="2026-10-03T00:00:00+00:00")
    assert fast.orchestrator.recover_interrupted() == []
    assert fast.orchestrator.resumable_run() == run_id
    await fast.orchestrator.resume(run_id)
    await fast.orchestrator.wait()
    assert fast.repo.items(run_id)[0]["write_state"] == "done"


async def test_complexity_halves_and_persists(fast, fake):
    fake.complexity_limit = 5
    run_id = seed(fast, fake, [{"md_progress": 10 + i, "al_progress": 1} for i in range(10)])
    items = await approve_all(fast, run_id)
    assert fast.repo.get_setting("anilist_write_batch") == 5
    assert [len(re.findall(r"SaveMediaListEntry", m["query"])) for m in fake.mutations] == [5, 5]
    assert all(i["write_state"] == "done" for i in items.values())


async def test_partial_error_on_one_alias(fast, fake):
    run_id = seed(fast, fake, [{"md_progress": 10 + i, "al_progress": 1} for i in range(6)])
    fake.alias_errors[103] = "Validation error: progress"
    items = await approve_all(fast, run_id)
    assert items["md03"]["write_state"] == "failed"
    assert "Validation error: progress" in items["md03"]["verify_note"]
    assert all(items[f"md0{i}"]["write_state"] == "done" for i in (0, 1, 2, 4, 5))
    assert fast.repo.get_run(run_id)["state"] == "done"


async def test_429_during_writing_retries_without_double_write(fast, fake):
    fake.status_429 = 1
    run_id = seed(fast, fake, [{"md_progress": 10 + i, "al_progress": 1} for i in range(3)])
    items = await approve_all(fast, run_id)
    assert all(i["write_state"] == "done" for i in items.values())
    assert len(fake.applied) == 3
    assert len(fake.mutations) == 1
    assert fake.status_429 == 0  # the 429 was actually served
    mutation_posts = [r for r in fake.requests if r["query"].startswith("mutation")]
    assert len(mutation_posts) == 2  # the 429'd attempt + the retry


# ---- verify ---------------------------------------------------------------------


async def test_verify_ok_and_status_change_flagged(fast, fake):
    run_id = seed(fast, fake, [
        {"md_progress": 10, "al_progress": 5},
        {"md_progress": 50, "al_progress": 40, "chapters": 50, "set_status": "COMPLETED"},
        {"md_progress": 20, "al_progress": 5},
    ])
    fake.server_status_after_write[102] = "COMPLETED"  # AniList completes it by itself
    items = await approve_all(fast, run_id)
    assert items["md00"]["verify_note"] == "verified"
    assert items["md01"]["verify_note"] == "verified"  # COMPLETED was sent, so expected
    assert "status changed by AniList: CURRENT→COMPLETED" in items["md02"]["verify_note"]
    assert items["md00"]["al_status_before"] == "CURRENT"
    assert "1 need a look" in fast.repo.get_run(run_id)["phase_detail"]


# ---- approval / first write --------------------------------------------------------


async def test_first_write_guard(fast, fake):
    fast.repo.set_setting("first_write_done", False)
    run_id = seed(fast, fake, [{"md_progress": 10, "al_progress": 5}, {"md_progress": 8, "al_progress": 2}])
    with pytest.raises(ApprovalError, match=FIRST_WRITE_MESSAGE):
        await fast.orchestrator.approve(run_id, ["md00", "md01"], [], [])
    assert fast.repo.get_run(run_id)["state"] == "diffed"

    await fast.orchestrator.approve(run_id, ["md00"], [], [])
    await fast.orchestrator.wait()
    assert len(fake.mutations) == 1 and [a["id"] for a in sent_aliases(fake)] == [100]
    assert fast.repo.get_setting("first_write_done") is True
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert items["md01"]["write_state"] == "none"


@pytest.mark.parametrize(
    "spec, sel, ov, message",
    [
        ({"md_progress": 11, "al_progress": 9, "action": "flag", "flag_kind": "exceeds_total"}, ["md00"], [], "exceed"),
        ({"md_progress": 400, "al_progress": 9, "action": "flag", "flag_kind": "implausible"}, ["md00"], [], "override"),
        ({"md_progress": 4, "al_progress": 9, "action": "skip"}, ["md00"], [], "cannot be written"),
        ({"md_progress": 10, "al_progress": 9}, [], [], "Nothing selected"),
    ],
)
async def test_approval_validation(fast, fake, spec, sel, ov, message):
    run_id = seed(fast, fake, [spec])
    with pytest.raises(ApprovalError, match=message):
        await fast.orchestrator.approve(run_id, sel, [], ov)
    assert fake.mutations == []


async def test_implausible_with_override_is_written(fast, fake):
    run_id = seed(fast, fake, [{"md_progress": 400, "al_progress": 9, "action": "flag", "flag_kind": "implausible"}])
    await fast.orchestrator.approve(run_id, ["md00"], [], ["md00"])
    await fast.orchestrator.wait()
    assert fake.entry(100)["progress"] == 400


async def test_status_only_without_completion_is_not_approved(fast, fake):
    run_id = seed(fast, fake, [
        {"md_progress": 50, "al_progress": 50, "chapters": 50, "set_status": "COMPLETED"},
        {"md_progress": 8, "al_progress": 2},
    ])
    await fast.orchestrator.approve(run_id, ["md00", "md01"], [], [])
    await fast.orchestrator.wait()
    items = {i["md_id"]: i for i in fast.repo.items(run_id)}
    assert items["md00"]["write_state"] == "none" and items["md00"]["approved"] == 0
    assert [a["id"] for a in sent_aliases(fake)] == [101]


async def test_only_diffed_runs_can_be_approved(fast, fake):
    run_id = seed(fast, fake, [{"md_progress": 10, "al_progress": 5}])
    fast.repo.update_run(run_id, state="cancelled")
    with pytest.raises(ApprovalError):
        await fast.orchestrator.approve(run_id, ["md00"], [], [])
