"""Architecture §11: request arithmetic for a 500-series library (NFR-6)."""

import pytest
import respx

from tests.factories import generate_library


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fast(services):
    services.anilist_queue.set_interval(0)
    services.mangadex_queue.set_interval(0)
    return services


async def test_500_series_budget(fast, mock):
    md, al = generate_library(500)
    md.install(mock)
    al.install(mock)
    orch = fast.orchestrator

    run1 = await orch.start_run()
    await orch.wait()
    r1 = fast.repo.get_run(run1)
    assert r1["state"] == "diffed", r1["error"]
    md_first = sum(md.data_calls().values())
    al_first = al.call_count
    # §11 dry-run budget (24) plus the one-time staff backfill for the 450 list entries (25 per request).
    staff_lookups = -(-450 // 25)
    assert al_first <= 24 + staff_lookups
    assert md_first <= 312
    assert (r1["req_anilist"], r1["req_mangadex"]) == (al_first, md_first)

    items = fast.repo.items(run1)
    assert len(items) == 500
    writes = sum(i["action"] == "write" for i in items)
    assert 50 <= writes <= 70

    run2 = await orch.start_run()
    await orch.wait()
    r2 = fast.repo.get_run(run2)
    assert r2["state"] == "diffed", r2["error"]
    assert r2["req_anilist"] == al.call_count - al_first == 1
    assert r2["req_mangadex"] == sum(md.data_calls().values()) - md_first <= 13
    assert md.data_calls()["chapter"] == 300  # all from run 1; none on run 2
