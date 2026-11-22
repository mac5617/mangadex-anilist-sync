"""Changes: one series' proposed update for one site in a Sync (stored as `sync_item`).

The one home of the rules about them: which can be selected, what a Selection will actually send (its Write plan),
and what that costs. Approval and the estimate on the changes page read the same Write plan, so they can't disagree.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mdal.db.repo import Repo
from mdal.sync.estimate import estimate
from mdal.sync.rules import AlEntry, AlMediaInfo, MdInfo, evaluate, score_from_md

TARGETS = ("anilist", "mal")
SITE_NAMES = {"anilist": "AniList", "mal": "MyAnimeList"}
MAPPING_SKIPS = {
    None: "awaiting match review",
    "review": "awaiting match review",
    "unmatched": "no match",
    "not_on_anilist": "marked not on AniList",
}
NOTHING_LEFT = {"write_state": "dropped", "verify_note": "dropped: nothing left to send"}


class ApprovalError(Exception):
    """Shown to the user as-is."""


class DismissalError(Exception):
    """This Change can't be dismissed, or wasn't."""


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


def is_dismissed(item: Any) -> bool:
    """A Flag you set aside: skipped, but still naming what was wrong."""
    return item["action"] == "skip" and bool(item["flag_kind"])


def shown_reason(item: Any) -> str | None:
    return f"dismissed by you: {item['reason']}" if is_dismissed(item) else item["reason"]


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


# ---- building ----------------------------------------------------------


def _evaluated(
    repo: Repo, row: Any, entry: AlEntry | None, media: AlMediaInfo, jump_limit: int,
    dismissed: dict[str, Any], site: str,
) -> dict[str, Any]:
    """The rules applied to one series (§8): the item columns shared by both targets."""
    md_id = row["md_id"]
    reads = repo.read_chapters(md_id)
    chapters = [r["chapter"] for r in reads if r["missing"] in (0, None) and r["chapter"] is not None]
    d = evaluate(
        chapters, len(reads) - len(chapters), entry, media,
        MdInfo(row["pub_status"], row["last_chapter"], bool(row["chapter_numbers_reset"]), row["reading_status"]),
        jump_limit, site,
    )
    action, reason = d.action, d.reason
    gone = dismissed.get(md_id)
    if action == "flag" and gone is not None and (gone["md_progress"], gone["flag_kind"]) == (d.md_progress, d.flag_kind):
        action = "skip"
    return {
        "al_entry_id": entry.entry_id if entry else None,
        "al_progress": d.al_progress,
        "md_progress": d.md_progress,
        "set_status": d.set_status,
        "status_source": d.status_source,
        "status_approved": 1 if d.set_status else 0,
        "action": action,
        "flag_kind": d.flag_kind,
        "reason": reason,
        "hint": d.hint,
        "total": d.total,
        "unresolved_reads": d.unresolved,
    }


def _with_score(item: dict[str, Any], score: int | None, site: str) -> dict[str, Any]:
    """Offer your MangaDex rating for an entry the site hasn't scored. A row with nothing else to change becomes a
    score-only update; a dismissed flag stays dismissed, and a row that can't be written stays as it is."""
    if score is None or item["flag_kind"] == "exceeds_total":
        return item
    if item["action"] == "skip":
        if item["al_entry_id"] is None or is_dismissed(item):
            return item
        item = {**item, "action": "write", "reason": f"rated {score}/10 on MangaDex; {site} has no score"}
    return {**item, "set_score": score, "score_approved": 1}


def md_mal_link(row: Any) -> int | None:
    """The MyAnimeList id from MangaDex's links, if it is a plain number."""
    try:
        value = json.loads(row["links"] or "{}").get("mal")
    except (ValueError, AttributeError):
        return None
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def mal_target(row: Any, mapping: Any, media: dict[int, Any]) -> tuple[int | None, int | None, str | None]:
    """(mal_id, al_media_id, skip reason) for one series.

    The AniList match's idMal is preferred (it was matched or confirmed); MangaDex's own MAL link is
    the fallback, so series that are unmatched on AniList can still sync. A series whose AniList match
    awaits review waits too (its links are often the reason it needs review), and if the two ids
    disagree the series is skipped rather than guessed.
    """
    state = mapping["state"] if mapping else None
    if state == "review":
        return None, None, "awaiting match review"
    matched = state in ("auto", "confirmed") and mapping["al_media_id"] is not None
    al_id = mapping["al_media_id"] if matched else None
    al_mal = media[al_id]["id_mal"] if al_id is not None and al_id in media else None
    md_mal = md_mal_link(row)
    if al_mal and md_mal and al_mal != md_mal:
        return None, al_id, f"AniList and MangaDex link different MyAnimeList entries ({al_mal} and {md_mal})"
    mal_id = al_mal or md_mal
    if mal_id is None:
        why = "the AniList match has none" if matched else "no AniList match, and no MyAnimeList link on MangaDex"
        return None, al_id, f"no MyAnimeList id ({why})"
    return mal_id, al_id, None


def build_items(repo: Repo, run_id: int, md_ids: set[str] | None = None, target: str = "anilist") -> list[dict[str, Any]]:
    """One sync_item per library series (§8), or only `md_ids`. Pure DB reads; no API calls."""
    mappings = repo.mappings()
    dismissed = repo.dismissed_flags(target)
    jump_limit = repo.get_setting("jump_limit")
    rows = [r for r in repo.md_manga() if md_ids is None or r["md_id"] in md_ids]
    mapped_ids = [m["al_media_id"] for m in mappings.values() if m["al_media_id"] is not None]
    media = repo.media(mapped_ids)
    if target == "mal":
        return _build_mal_items(repo, run_id, rows, mappings, media, dismissed, jump_limit)
    entries = repo.al_entries()
    mal_scores = {mal_id: e["score"] for mal_id, e in repo.mal_entries().items()}
    items: list[dict[str, Any]] = []
    for row in rows:
        md_id = row["md_id"]
        m = mappings.get(md_id)
        base: dict[str, Any] = {"run_id": run_id, "md_id": md_id}
        state = m["state"] if m else None
        if state not in ("auto", "confirmed") or m["al_media_id"] is None:
            items.append({**base, "action": "skip", "reason": MAPPING_SKIPS.get(state, "no match")})
            continue
        al_id = m["al_media_id"]
        entry_row = entries.get(al_id)
        media_row = media.get(al_id)
        entry = AlEntry(entry_row["entry_id"], entry_row["status"], entry_row["progress"]) if entry_row else None
        info = AlMediaInfo(media_row["chapters"] if media_row else None, media_row["status"] if media_row else None)
        item = {**base, "al_media_id": al_id, **_evaluated(repo, row, entry, info, jump_limit, dismissed, "AniList")}
        mal_score = mal_scores.get(media_row["id_mal"]) if media_row and media_row["id_mal"] else None
        score = score_from_md(row["rating"], entry_row["score"] if entry_row else None, (mal_score or 0) * 10)
        items.append(_with_score(item, score, "AniList"))
    return items


def _build_mal_items(
    repo: Repo, run_id: int, rows: list[Any], mappings: dict[str, Any], media: dict[int, Any],
    dismissed: dict[str, Any], jump_limit: int,
) -> list[dict[str, Any]]:
    entries = repo.mal_entries()
    al_entries = repo.al_entries()
    items: list[dict[str, Any]] = []
    for row in rows:
        md_id = row["md_id"]
        mal_id, al_id, skip = mal_target(row, mappings.get(md_id), media)
        base: dict[str, Any] = {"run_id": run_id, "md_id": md_id, "al_media_id": al_id, "mal_id": mal_id}
        if skip:
            items.append({**base, "action": "skip", "reason": skip})
            continue
        e = entries.get(mal_id)
        entry = AlEntry(mal_id, e["status"], e["progress"]) if e else None
        al = media.get(al_id) if al_id is not None else None
        if e is not None:
            info = AlMediaInfo(e["chapters"], e["media_status"], "MyAnimeList")
        elif al is not None and al["id_mal"] == mal_id:
            # Not on the MAL list yet: MyAnimeList's total is unknown here, AniList's stands in for it.
            info = AlMediaInfo(al["chapters"], al["status"], "AniList")
        else:
            info = AlMediaInfo(None, None, "MyAnimeList")
        item = {**base, **_evaluated(repo, row, entry, info, jump_limit, dismissed, "MyAnimeList")}
        al_entry = al_entries.get(al_id) if al_id is not None else None
        score = score_from_md(row["rating"], (e["score"] or 0) * 10 if e else None, al_entry["score"] if al_entry else None)
        items.append(_with_score(item, score, "MyAnimeList"))
    return items


def open_diff(repo: Repo, target: str | None = None) -> Any:
    """The sync waiting for approval (at most one per target; it may be an older, restored run)."""
    if target is None:
        return repo.conn.execute("SELECT * FROM sync_run WHERE state='diffed' ORDER BY run_id DESC LIMIT 1").fetchone()
    return repo.conn.execute(
        "SELECT * FROM sync_run WHERE state='diffed' AND target=? ORDER BY run_id DESC LIMIT 1", (target,)
    ).fetchone()


def refresh_item(repo: Repo, md_id: str) -> bool:
    """After a match or dismiss decision: recompute this series' row in each open `diffed` run. No requests."""
    refreshed = False
    for target in TARGETS:
        run = open_diff(repo, target)
        if run is None:
            continue
        if not repo.conn.execute("SELECT 1 FROM sync_item WHERE run_id=? AND md_id=?", (run["run_id"], md_id)).fetchone():
            continue
        _rebuild(repo, run["run_id"], md_id, target)
        refreshed = True
    return refreshed


def _rebuild(repo: Repo, run_id: int, md_id: str, target: str) -> bool:
    """Recompute one Change from stored data. False when the series has left the library (the row stays)."""
    items = build_items(repo, run_id, {md_id}, target)
    for item in items:
        with repo.conn:
            repo.conn.execute("DELETE FROM sync_item WHERE run_id=? AND md_id=?", (run_id, md_id))
        repo.upsert_item(item)
    return bool(items)


def dismiss(repo: Repo, run: Any, item: Any) -> None:
    """Set this Flag aside on the run's site, until your MangaDex progress changes."""
    if item["action"] != "flag" or item["md_progress"] is None:
        raise DismissalError("only flagged rows can be dismissed")
    repo.dismiss_flag(item["md_id"], run["target"], item["md_progress"], item["flag_kind"], item["reason"])
    repo.update_items(run["run_id"], [(item["md_id"], {"action": "skip"})])


def undismiss(repo: Repo, run: Any, item: Any) -> None:
    """Bring the Change back as the rules now see it, including any score offer."""
    if not is_dismissed(item):
        raise DismissalError("this row was not dismissed")
    repo.undismiss_flag(item["md_id"], run["target"])
    if not _rebuild(repo, run["run_id"], item["md_id"], run["target"]):
        repo.update_items(run["run_id"], [(item["md_id"], {"action": "flag"})])
