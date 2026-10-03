import asyncio

import pytest

from mdal.clients.ratelimit import PacedQueue
from tests.fakes import FakeClock


@pytest.fixture
def clock():
    return FakeClock()


def make_queue(clock, interval=3.0):
    return PacedQueue(interval, clock=clock, sleep=clock.sleep)


async def start_times(queue, clock, n):
    starts = []
    for _ in range(n):
        async with queue.slot():
            starts.append(clock.now)
    return starts


async def test_sequential_slots_are_spaced(clock):
    queue = make_queue(clock)
    starts = await start_times(queue, clock, 3)
    assert starts[1] - starts[0] >= 3.0
    assert starts[2] - starts[1] >= 3.0


async def test_first_slot_is_immediate(clock):
    queue = make_queue(clock)
    t0 = clock.now
    async with queue.slot():
        assert clock.now == t0


async def test_concurrent_callers_never_overlap(clock):
    queue = make_queue(clock, interval=0.5)
    inside = 0
    max_inside = 0
    starts = []

    async def worker():
        nonlocal inside, max_inside
        async with queue.slot():
            inside += 1
            max_inside = max(max_inside, inside)
            starts.append(clock.now)
            await clock.sleep(0.2)  # simulated request duration, yields to other tasks
            inside -= 1

    await asyncio.gather(*(worker() for _ in range(10)))
    assert max_inside == 1
    assert len(starts) == 10
    assert all(b - a >= 0.5 for a, b in zip(starts, starts[1:]))


async def test_pause_holds_next_slot(clock):
    queue = make_queue(clock)
    async with queue.slot():
        pass
    paused_at = clock.now
    queue.pause_for(65)
    async with queue.slot():
        assert clock.now >= paused_at + 65


async def test_shorter_pause_does_not_shorten_longer(clock):
    queue = make_queue(clock)
    t0 = clock.now
    queue.pause_for(65)
    queue.pause_for(10)
    async with queue.slot():
        assert clock.now >= t0 + 65


async def test_spacing_applies_after_short_pause(clock):
    queue = make_queue(clock, interval=3.0)
    async with queue.slot():
        first = clock.now
    queue.pause_for(1)  # ends before the spacing interval would
    async with queue.slot():
        assert clock.now >= first + 3.0


async def test_spacing_resumes_after_long_pause(clock):
    queue = make_queue(clock, interval=3.0)
    queue.pause_for(60)
    starts = await start_times(queue, clock, 2)
    assert starts[1] - starts[0] >= 3.0


async def test_pause_extended_while_waiting(clock):
    queue = make_queue(clock)
    t0 = clock.now
    queue.pause_for(10)
    original_sleep = clock.sleep

    async def sleep_and_extend(seconds):
        await original_sleep(seconds)
        if clock.now < t0 + 30:
            queue.pause_for(30 - (clock.now - t0))

    queue._sleep = sleep_and_extend
    async with queue.slot():
        assert clock.now >= t0 + 30


async def test_set_interval_takes_effect(clock):
    queue = make_queue(clock, interval=3.0)
    queue.set_interval(1.0)
    starts = await start_times(queue, clock, 2)
    assert starts[1] - starts[0] == pytest.approx(1.0)
