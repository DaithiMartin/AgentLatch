"""Deterministic unit test for LatchDeliveryProcessor (sandbox, T13).

Injected clock + fakeredis, **no sleep**: proves the silence-timing curve and the
in-flight poll guard against the **real** AgentLatch engine. It drives the poll
logic directly — the Pipecat pipeline harness needs a ``task_manager`` only a
running pipeline provides, and the live frame/timer wiring is exercised in CP-E.
"""

import asyncio

from fakeredis.aioredis import FakeRedis
from pipecat.frames.frames import (
    TextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)

from agentlatch import AgentLatch, ResponsePayload
from frame_processor import LatchDeliveryProcessor

SID = "sandbox-demo"


class Clock:
    """A manual clock: timing advances only when the test says so (never sleep)."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _processor(latch: object) -> tuple[LatchDeliveryProcessor, list]:
    """Build the processor with push_frame captured — no pipeline/task_manager."""
    proc = LatchDeliveryProcessor(latch, SID)
    pushed: list = []

    async def capture(frame, direction=None):
        pushed.append(frame)

    proc.push_frame = capture  # type: ignore[method-assign]
    return proc, pushed


def _latch(clock: Clock) -> AgentLatch:
    return AgentLatch(redis_client=FakeRedis(), now=clock, silence_threshold=2.0)


async def test_releases_only_after_2s_silence_from_last_speech() -> None:
    clock = Clock()
    latch = _latch(clock)
    await latch.enqueue(ResponsePayload(session_id=SID, text_to_speak="hello"))
    proc, pushed = _processor(latch)

    # User speaks; we re-poll throughout, each True poll re-stamping last_speech.
    proc._update_speaking(UserStartedSpeakingFrame())
    await proc._poll_once()  # t=1000.0  stamp last_speech
    clock.advance(5.0)
    await proc._poll_once()  # t=1005.0  re-stamp (still speaking — the timing trap)
    assert pushed == []  # nothing releases while speaking

    # User stops; silence is measured from the LAST speech (t=1005), not the start.
    # A start/stop-only impl would have last_speech=1000 and release early below.
    proc._update_speaking(UserStoppedSpeakingFrame())
    clock.advance(1.999)
    await proc._poll_once()  # t=1006.999  silence 1.999s < 2.0
    assert pushed == [], "released before 2.0s of real silence (stale last_speech?)"

    clock.advance(0.002)
    await proc._poll_once()  # t=1007.001  silence 2.001s >= 2.0
    assert len(pushed) == 1
    assert isinstance(pushed[0], TextFrame)  # never a raw str (§9 Never)
    assert pushed[0].text == "hello"

    # Exactly one: the queue is now empty.
    await proc._poll_once()
    assert len(pushed) == 1


async def test_polling_continues_after_stop_until_release() -> None:
    clock = Clock()
    latch = _latch(clock)
    await latch.enqueue(ResponsePayload(session_id=SID, text_to_speak="late"))
    proc, pushed = _processor(latch)

    proc._update_speaking(UserStartedSpeakingFrame())
    await proc._poll_once()  # stamp at t0
    proc._update_speaking(UserStoppedSpeakingFrame())

    # Repeated post-stop polls (injected ticks) below the threshold: polling
    # continues — not a one-shot fired at the stop event — but nothing releases.
    for _ in range(3):
        clock.advance(0.5)
        await proc._poll_once()  # t0+0.5, +1.0, +1.5
    assert pushed == []

    clock.advance(0.6)
    await proc._poll_once()  # t0+2.1 -> release
    assert len(pushed) == 1
    assert isinstance(pushed[0], TextFrame)
    assert pushed[0].text == "late"


async def test_in_flight_guard_blocks_concurrent_polls() -> None:
    """The frame-driven poll and the cadence poll must never run
    get_next_message concurrently for one session (SPEC §3.4)."""

    class BlockingLatch:
        def __init__(self) -> None:
            self.calls = 0
            self.concurrent = 0
            self.max_concurrent = 0
            self.gate = asyncio.Event()

        async def get_next_message(self, session_id: str, is_user_speaking: bool):
            self.calls += 1
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            try:
                await self.gate.wait()
                return None
            finally:
                self.concurrent -= 1

    latch = BlockingLatch()
    proc, pushed = _processor(latch)

    first = asyncio.create_task(proc._poll_once())
    await asyncio.sleep(0)  # scheduler yield (NOT a delay): let `first` reach the gate
    second = asyncio.create_task(proc._poll_once())
    await asyncio.sleep(0)

    # The guard let only ONE poll into get_next_message; the second saw the
    # in-flight flag and returned immediately.
    assert latch.calls == 1
    assert latch.max_concurrent == 1

    latch.gate.set()
    await asyncio.gather(first, second)
    assert latch.max_concurrent == 1
    assert pushed == []  # get_next_message returned None, so no frame pushed
