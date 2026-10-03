"""Write-phase request estimate shown before approval (architecture §11). Pure."""

from __future__ import annotations

import math


def estimate(n_items: int, batch: int, rpm: int) -> tuple[int, int]:
    """(requests, seconds): pre-write re-read + batches + verify re-read. Nothing to write → (0, 0)."""
    if n_items <= 0:
        return 0, 0
    requests = 2 + math.ceil(n_items / max(batch, 1))
    return requests, math.ceil(requests * 60 / rpm)
