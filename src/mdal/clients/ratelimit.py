"""Single-flight, evenly spaced request queue shared by both API clients (architecture §5)."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class PacedQueue:
    """At most one request in flight; consecutive starts at least `min_interval` apart.

    `pause_for()` holds the whole queue (used on 429). Pauses only ever extend.
    """

    def __init__(self, min_interval: float, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_start = -math.inf
        self._paused_until = -math.inf

    def set_interval(self, min_interval: float) -> None:
        self.min_interval = min_interval

    def pause_for(self, seconds: float) -> None:
        self._paused_until = max(self._paused_until, self._clock() + seconds)

    @property
    def paused_until(self) -> float:
        return self._paused_until

    def next_start(self) -> float:
        return max(self._last_start + self.min_interval, self._paused_until)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._lock:
            # Re-check after every sleep: a pause may have been extended meanwhile.
            while (wait := self.next_start() - self._clock()) > 0:
                await self._sleep(wait)
            self._last_start = self._clock()
            yield
