"""Progress rules (architecture §8). Pure: no I/O, no DB.

This is the core safety logic: never lower progress, never write past a known total,
flag anything implausible, and only ever propose the status COMPLETED.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

Action = Literal["write", "add", "skip", "flag"]

STATUS_LABELS = {
    "CURRENT": "Reading", "PLANNING": "Planning", "COMPLETED": "Completed",
    "DROPPED": "Dropped", "PAUSED": "Paused", "REPEATING": "Rereading",
}
OUTLIER_FACTOR, OUTLIER_GAP = 2, 20


@dataclass(frozen=True)
class AlEntry:
    entry_id: int
    status: str
    progress: int


@dataclass(frozen=True)
class AlMediaInfo:
    """Total and publication status (AniList's words), and the site they came from."""
    chapters: int | None
    status: str | None
    source: str = "AniList"


@dataclass(frozen=True)
class MdInfo:
    pub_status: str | None = None
    last_chapter: str | None = None
    chapter_numbers_reset: bool = False


@dataclass(frozen=True)
class DiffItem:
    action: Action
    reason: str
    md_progress: int | None = None
    al_progress: int | None = None
    flag_kind: str | None = None
    hint: str | None = None
    set_status: str | None = None
    status_source: str | None = None
    unresolved: int = 0
    total: int | None = None


@dataclass(frozen=True)
class Completion:
    total: int | None            # best known total, whatever the AniList status
    source: str | None           # media.source ("AniList" | "MyAnimeList") | "MangaDex"
    known_complete: bool         # the site says FINISHED and a total is known


def parse_chapter(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


def completion_info(media: AlMediaInfo, md: MdInfo) -> Completion:
    """§8 step 5. A wrong completion is worse than a missing one."""
    if media.chapters is not None:
        total, source = media.chapters, media.source
    else:
        last = parse_chapter(md.last_chapter) if md.pub_status == "completed" else None
        total, source = (math.floor(last), "MangaDex") if last is not None else (None, None)
    return Completion(total, source, media.status == "FINISHED" and total is not None)


def completion_default(media: AlMediaInfo, md: MdInfo, md_progress: int | None) -> bool:
    """Whether "add to AniList" (story 17) should default to Completed."""
    c = completion_info(media, md)
    return c.known_complete and md_progress is not None and md_progress == c.total


def _label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "", status or "unknown")


def _implausible(numbers: list[Decimal], md: MdInfo, al: int | None, md_progress: int, jump_limit: int) -> list[str]:
    problems: list[str] = []
    if md.chapter_numbers_reset:
        problems.append("MangaDex chapter numbers restart each volume")
    distinct = sorted(set(numbers), reverse=True)
    if len(distinct) >= 2:
        top, second = distinct[0], distinct[1]
        if top > OUTLIER_FACTOR * second and top - second > OUTLIER_GAP:
            problems.append(f"highest read chapter {top} is far above the next ({second})")
    if al is not None and al > 0 and md_progress - al > jump_limit:
        problems.append(f"jump of {md_progress - al} chapters exceeds the limit of {jump_limit}")
    return problems


def evaluate(
    read_chapters: list[str | None],
    unresolved: int,
    entry: AlEntry | None,
    media: AlMediaInfo,
    md: MdInfo,
    jump_limit: int,
    site: str = "AniList",
) -> DiffItem:
    """`site` is the sync target named in reasons ("AniList" or "MyAnimeList")."""
    # 1. Parse
    numbers: list[Decimal] = []
    for raw in read_chapters:
        d = parse_chapter(raw)
        if d is None:
            unresolved += 1
        else:
            numbers.append(d)
    # 2. Nothing numeric
    if not numbers:
        return DiffItem("skip", "nothing read", al_progress=entry.progress if entry else None, unresolved=unresolved)
    # 3. Proposed progress
    md_progress = math.floor(max(numbers))

    al = entry.progress if entry else None
    c = completion_info(media, md)
    complete = c.known_complete and md_progress == c.total and (entry is None or entry.status != "COMPLETED")

    hints: list[str] = []
    if c.total is not None and md_progress == c.total and not c.known_complete:
        hints.append(f"at last known chapter, but {media.source} lists the series as {media.status or 'unknown'}; status unchanged")
    if entry is not None and entry.status == "PLANNING" and not complete:
        hints.append("status is Planning; will remain Planning")

    def item(action: Action, reason: str, flag_kind: str | None = None, completing: bool = complete) -> DiffItem:
        return DiffItem(
            action, reason, md_progress=md_progress, al_progress=al, flag_kind=flag_kind,
            hint="; ".join(hints) or None,
            set_status="COMPLETED" if completing else None,
            status_source=c.source if completing else None,
            unresolved=unresolved, total=c.total,
        )

    # 4. Not on the user's list: an "add" row (created only after approval, re-checked before writing)
    if entry is None:
        if media.chapters is not None and md_progress > media.chapters:
            where = "; add it by hand from Unlisted" if site == "AniList" else f"; add it by hand on {site}"
            return item("flag", f"MangaDex {md_progress} exceeds {media.source}'s total of {media.chapters} chapters "
                        f"(not on your list{where})", "exceeds_total", completing=False)
        if media.chapters is None and c.total is not None and md_progress > c.total:
            hints.append(f"reads go beyond MangaDex's last chapter {c.total}")
        problems = _implausible(numbers, md, None, md_progress, jump_limit)
        if problems:
            return item("flag", "not on your list; " + "; ".join(problems), "implausible")
        return item("add", f"not on your {site} list; add as {'Completed' if complete else 'Reading'} at {md_progress}")

    # 6. Never lower; status-only completion
    if md_progress <= al:
        if complete and al == md_progress:
            return item("write", "at final chapter; mark completed")
        return item("skip", f"{site} at/ahead", completing=False)

    # 7. Totals
    if media.chapters is not None and md_progress > media.chapters:
        return item("flag", f"MangaDex {md_progress} exceeds {media.source}'s total of {media.chapters} chapters",
                    "exceeds_total", completing=False)
    if media.chapters is None and c.total is not None and md_progress > c.total:
        hints.append(f"reads go beyond MangaDex's last chapter {c.total}")

    # 8. Implausible (overridable)
    problems = _implausible(numbers, md, al, md_progress, jump_limit)
    if problems:
        return item("flag", "; ".join(problems), "implausible")

    # 9. Write
    return item("write", f"read to {md_progress} on MangaDex; {site} has {al}")


def completion_label(entry_status: str | None, status_source: str | None, total: int | None, site: str = "AniList") -> str:
    """e.g. "Reading → Completed (AniList: finished, 120 ch)" for the diff screen."""
    source = f"{site}: finished, total from MangaDex" if status_source == "MangaDex" else f"{status_source or site}: finished"
    return f"{_label(entry_status)} → Completed ({source}, {total} ch)"


def status_change_label(d: DiffItem, entry_status: str | None) -> str | None:
    if d.set_status != "COMPLETED":
        return None
    return completion_label(entry_status, d.status_source, d.total)
