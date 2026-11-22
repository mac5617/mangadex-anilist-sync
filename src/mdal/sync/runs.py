"""Runs: one Sync's record for one site, and the rules for moving it through its states (architecture §9).

Only records, no requests and no background work: what may resume, be restored or discarded, and what a restart
leaves behind. The orchestrator decides when; this module decides whether.
"""

from __future__ import annotations

from typing import Any

from mdal.db.repo import Repo, now_iso
from mdal.sync.changes import open_diff

ACTIVE_STATES = ("fetching", "resolving", "diffing", "writing", "verifying")
INTERRUPTIBLE = ("fetching", "resolving", "diffing")  # cheap to redo: a restart marks them failed
RESUMABLE = ("writing", "verifying", "failed", "halted")


class SyncStateError(Exception):
    pass


def _in(states: tuple[str, ...]) -> str:
    return ",".join("?" * len(states))


class Runs:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo

    def active(self) -> Any:
        return self.repo.conn.execute(
            f"SELECT * FROM sync_run WHERE state IN ({_in(ACTIVE_STATES)}) ORDER BY run_id DESC LIMIT 1", ACTIVE_STATES,
        ).fetchone()

    def recover_interrupted(self) -> list[int]:
        """On startup: runs that died in a cheap phase are failed; start a new sync to recover."""
        ids = [r["run_id"] for r in self.repo.conn.execute(
            f"SELECT run_id FROM sync_run WHERE state IN ({_in(INTERRUPTIBLE)})", INTERRUPTIBLE
        )]
        for run_id in ids:
            self.repo.update_run(run_id, state="failed", error="interrupted; start a new sync", finished_at=now_iso())
        return ids

    def create(self, target: str) -> int:
        """A new Run for this site. An older diff for the site is Superseded: only the newest can be approved."""
        run_id = self.repo.create_run("fetching", target)
        for old in self.repo.conn.execute(
            "SELECT run_id FROM sync_run WHERE state='diffed' AND target=? AND run_id<?", (target, run_id)
        ).fetchall():
            self.repo.update_run(old["run_id"], state="cancelled", finished_at=now_iso(),
                                 error=f"superseded by sync #{run_id}")
        return run_id

    def can_resume(self, run_id: int) -> bool:
        """An approved Run that stopped with writes to send or to verify."""
        r = self.repo.get_run(run_id)
        if r is None or r["state"] not in RESUMABLE or r["approved_at"] is None:
            return False
        return bool(self.repo.items_in_state(run_id, "pending")) or bool(self.repo.items_in_state(run_id, "done"))

    def resumable(self) -> int | None:
        """The newest Run that can resume. A failed list add has nothing to resume and hides nothing."""
        for r in self.repo.conn.execute(
            f"SELECT run_id FROM sync_run WHERE state IN ({_in(RESUMABLE)}) AND approved_at IS NOT NULL "
            "ORDER BY run_id DESC", RESUMABLE,
        ).fetchall():
            if self.can_resume(r["run_id"]):
                return r["run_id"]
        return None

    def mark_resuming(self, run_id: int) -> None:
        self.repo.update_run(run_id, state="writing", error=None, finished_at=None, phase_detail="resuming")

    def can_restore(self, run_id: int) -> bool:
        """A discarded or Superseded sync that never reached approval."""
        r = self.repo.get_run(run_id)
        if r is None or r["state"] != "cancelled" or r["approved_at"] is not None:
            return False
        return self.repo.conn.execute("SELECT 1 FROM sync_item WHERE run_id=? LIMIT 1", (run_id,)).fetchone() is not None

    def restore(self, run_id: int) -> None:
        """Bring a discarded sync back to `diffed`. Any other open diff is closed (only one can be approved)."""
        if not self.can_restore(run_id):
            raise SyncStateError("only a discarded sync that was never approved can be restored")
        other = open_diff(self.repo, self.repo.get_run(run_id)["target"])
        if other is not None:
            self.repo.update_run(other["run_id"], state="cancelled", finished_at=now_iso(),
                                 error=f"superseded by restored sync #{run_id}")
        self.repo.update_run(run_id, state="diffed", error=None, finished_at=None)

    def discard(self, run_id: int) -> None:
        r = self.repo.get_run(run_id)
        if r is None or r["state"] != "diffed":
            raise SyncStateError("only a diffed run can be discarded")
        self.repo.update_run(run_id, state="cancelled", finished_at=now_iso(), error="discarded by you")
