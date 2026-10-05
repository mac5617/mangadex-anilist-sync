"""Stalled series and series ready to binge, from your AniList list. Pure: no I/O.

Stalled: marked Reading, but the entry hasn't changed on AniList for a while (default 90 days).
Ready to binge: series you paused, dropped or stalled on that have since finished publishing, with
chapters left to read. Finished and short, and the ones you liked, come first.
"""

from __future__ import annotations

import time
from typing import Any

DAY = 86400


def days_since(updated_at: int | None, now: float | None = None) -> int | None:
    if not updated_at:
        return None
    return int(((now or time.time()) - updated_at) // DAY)


def stalled(entries: list[dict[str, Any]], days: int, now: float | None = None) -> list[dict[str, Any]]:
    out = []
    for e in entries:
        idle = days_since(e.get("updated_at"), now)
        if e["status"] == "CURRENT" and idle is not None and idle >= days:
            out.append({**e, "idle_days": idle})
    return sorted(out, key=lambda e: -e["idle_days"])


def binge(entries: list[dict[str, Any]], stalled_days: int, mu: dict[str, Any] | None = None,
          now: float | None = None) -> list[dict[str, Any]]:
    """Finished series you stopped partway through, with what's left."""
    mu = mu or {}
    out = []
    for e in entries:
        idle = days_since(e.get("updated_at"), now)
        stopped = e["status"] in ("PAUSED", "DROPPED") or (e["status"] == "CURRENT" and idle is not None and idle >= stalled_days)
        info = mu.get(f"al:{e['media_id']}") or {}
        finished = e.get("pub") == "FINISHED" or bool(info.get("completed"))
        total = e.get("chapters") or None
        progress = e.get("progress") or 0
        if not stopped or not finished or (total is not None and progress >= total):
            continue
        out.append({**e, "idle_days": idle, "left": (total - progress) if total else None})
    return sorted(out, key=lambda e: (-(e.get("score") or 0), e["left"] if e["left"] is not None else 10**6))
