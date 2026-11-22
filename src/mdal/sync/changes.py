"""Changes: one series' proposed update for one site in a Sync (stored as `sync_item`).

The one home of the rules about them: which can be selected, what a Selection will actually send (its Write plan),
and what that costs. Approval and the estimate on the changes page read the same Write plan, so they can't disagree.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mdal.db.repo import Repo
from mdal.sync.estimate import estimate

TARGETS = ("anilist", "mal")
SITE_NAMES = {"anilist": "AniList", "mal": "MyAnimeList"}
NOTHING_LEFT = {"write_state": "dropped", "verify_note": "dropped: nothing left to send"}


class ApprovalError(Exception):
    """Shown to the user as-is."""


@dataclass(frozen=True)
class Selection:
    """The Changes (by md_id), and the parts of them, you ticked."""
    changes: frozenset[str] = frozenset()
    completing: frozenset[str] = frozenset()
    overrides: frozenset[str] = frozenset()
    scoring: frozenset[str] = frozenset()

    @classmethod
    def of(cls, changes: Iterable[str] = (), completing: Iterable[str] = (), overrides: Iterable[str] = (),
           scoring: Iterable[str] = ()) -> Selection:
        return cls(frozenset(changes), frozenset(completing), frozenset(overrides), frozenset(scoring))


@dataclass(frozen=True)
class PlannedWrite:
    md_id: str
    completes: bool
    scored: bool


@dataclass(frozen=True)
class WritePlan:
    """What a Selection will send. `refusal` is why approval must refuse it; the writes are the rows that are fine."""
    writes: tuple[PlannedWrite, ...] = ()
    refusal: str | None = None


@dataclass(frozen=True)
class Estimate:
    selected: int
    to_complete: int
    requests: int
    seconds: int
    site: str = "AniList"
    to_score: int = 0


def is_status_only(item: Any) -> bool:
    """No progress to send: the site is at or ahead (or nothing was read), so only the status or score can change."""
    return item["al_progress"] is not None and (item["md_progress"] is None or item["md_progress"] <= item["al_progress"])


def sends_completion(item: Any) -> bool:
    return item["set_status"] == "COMPLETED" and bool(item["status_approved"])


def sends_score(item: Any) -> bool:
    return item["set_score"] is not None and bool(item["score_approved"])


def _sends_nothing(item: Any, completes: bool, scored: bool) -> bool:
    return (item["md_progress"] is None or is_status_only(item)) and not completes and not scored


def nothing_to_send(item: Any) -> bool:
    """An approved Change with nothing left to send, as it stands after the writer's re-read."""
    return _sends_nothing(item, sends_completion(item), sends_score(item))


def is_selectable(item: Any) -> bool:
    return item["action"] in ("write", "add", "flag") and item["flag_kind"] != "exceeds_total"


def is_preselected(item: Any) -> bool:
    """Writes and adds start ticked; a Flag waits for you. Once approved, the row shows what you chose."""
    return bool(item["approved"]) if item["write_state"] != "none" else item["action"] in ("write", "add")


def _refusal(item: Any, md_id: str, selection: Selection) -> str | None:
    if item is None or item["action"] == "skip":
        return "The selection contains a row that cannot be written."
    if item["flag_kind"] == "exceeds_total":
        return "Rows that exceed AniList's total chapter count cannot be written."
    if item["flag_kind"] == "implausible" and md_id not in selection.overrides:
        return "Tick “override” on flagged rows you want to write anyway."
    return None


def plan_items(items: Iterable[Any], selection: Selection) -> WritePlan:
    """The Write plan for a Selection over a run's items."""
    by_id = {i["md_id"]: i for i in items}
    writes: list[PlannedWrite] = []
    refusal: str | None = None
    for md_id in sorted(selection.changes):
        item = by_id.get(md_id)
        problem = _refusal(item, md_id, selection)
        if problem:
            refusal = refusal or problem
            continue
        completes = item["set_status"] == "COMPLETED" and md_id in selection.completing
        scored = item["set_score"] is not None and md_id in selection.scoring
        if _sends_nothing(item, completes, scored):
            continue
        writes.append(PlannedWrite(md_id, completes, scored))
    return WritePlan(tuple(writes), refusal)


def preselection(items: Iterable[Any]) -> Selection:
    """What the changes page starts with ticked."""
    rows = list(items)
    chosen = [i["md_id"] for i in rows if is_preselected(i)]
    return Selection.of(chosen, [i["md_id"] for i in rows if i["status_approved"]], chosen,
                        [i["md_id"] for i in rows if i["score_approved"]])


def write_plan(repo: Repo, run_id: int, selection: Selection | None = None) -> WritePlan:
    """The Write plan for a run: for `selection`, or for what the page starts with ticked."""
    items = repo.items(run_id)
    return plan_items(items, preselection(items) if selection is None else selection)


def approved_plan(repo: Repo, run_id: int, selection: Selection, allow_empty: bool = False) -> WritePlan:
    """The Write plan to approve, or ApprovalError. `allow_empty`: a site with nothing ticked, when several sites are
    approved together."""
    run = repo.get_run(run_id)
    if run is None or run["state"] != "diffed":
        raise ApprovalError("Only a sync that is waiting for approval can be approved.")
    plan = write_plan(repo, run_id, selection)
    if plan.refusal:
        raise ApprovalError(plan.refusal)
    if not plan.writes and not allow_empty:
        raise ApprovalError("Nothing selected to write.")
    return plan


def plan_estimate(repo: Repo, target: str, plan: WritePlan) -> Estimate:
    """The write-phase cost of a Write plan. MyAnimeList has no batch write: one request per entry."""
    n = len(plan.writes)
    if target == "mal":
        requests, seconds = estimate(n, 1, repo.get_setting("mal_rpm"))
    else:
        requests, seconds = estimate(n, repo.get_setting("anilist_write_batch"), repo.get_setting("anilist_rpm"))
    return Estimate(n, sum(w.completes for w in plan.writes), requests, seconds, SITE_NAMES[target],
                    sum(w.scored for w in plan.writes))
