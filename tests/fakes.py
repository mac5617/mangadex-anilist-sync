"""Shared test doubles."""

import asyncio


class FakeClock:
    """Monotonic clock whose sleep advances time instantly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(seconds, 0)
        await asyncio.sleep(0)  # let other tasks run
