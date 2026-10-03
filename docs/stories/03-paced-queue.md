# 03: PacedQueue (shared pacing mechanism)

**Covers:** NFR-2, NFR-5 (mechanism). Architecture §5 "PacedQueue".

## Context
Both clients use this. It must make concurrent requests impossible and enforce spacing, including after a pause. Time must be injectable so tests run instantly.

## Tasks
- `clients/ratelimit.py`: `PacedQueue(min_interval: float, clock=time.monotonic, sleep=asyncio.sleep)`.
- `slot()`: an async context manager. It holds an `asyncio.Lock` for the whole request, and before yielding it waits until `max(last_start + min_interval, paused_until)`.
- `pause_for(seconds)` extends `paused_until` monotonically: a shorter pause never shortens a longer one.
- `set_interval(min_interval)` lets settings changes take effect without a restart.

## Acceptance criteria
- [x] Two sequential slots with `min_interval=3` start ≥ 3.0 s apart (fake clock).
- [x] Ten concurrent tasks acquiring slots never overlap: at most one is inside a slot at any instant.
- [x] After `pause_for(65)`, the next slot starts no earlier than 65 s later. A later `pause_for(10)` does not shorten that.
- [x] Spacing still applies to the first request after a pause ends.

## Tests
`test_ratelimit.py`, with a fake clock and a fake sleep that advances it. Extra cases: the first slot is immediate; a pause extended while waiting is honoured; `set_interval` takes effect.

## Dev notes
- Done 2026-10-03; 41 tests pass in total.
- `tests/fakes.py::FakeClock` is the shared fake: callable as a clock, with `.sleep()` advancing time and recording sleeps. Reuse it in stories 04/05. `tests/` is now a package (`tests/__init__.py`), so import it as `from tests.fakes import FakeClock`.
- **For clients (04/05):**
  - Hold the slot only for the HTTP attempt itself.
  - On 429, call `queue.pause_for(wait)` *inside* the slot, then leave the slot and re-acquire it for the retry. The pause then blocks every caller, including the retry.
  - `queue.next_start()` and `queue.paused_until` are available for the UI ("next request in ~3 s").
