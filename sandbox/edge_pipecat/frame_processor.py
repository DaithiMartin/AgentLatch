"""Pipecat edge delivery — poll AgentLatch, speak a held message as a TextFrame.

A custom ``FrameProcessor`` for the Fast Edge (sandbox, T13). It tracks the live
``is_user_speaking`` state from VAD frames and polls
``latch.get_next_message(session_id, is_user_speaking)`` continuously, pushing a
``TextFrame`` downstream when the engine releases a held message — **never a raw
string** (SPEC §9 Never), and **never two concurrent polls** for one session
(SPEC §3.4).

THE TIMING TRAP (SPEC §3.4 / plan §4.9): the engine stamps ``last_speech`` **only**
on an ``is_user_speaking=True`` poll. Polling only on VAD start/stop would leave
``last_speech`` at speech-*start*, so the stop-poll would compute
``silence = whole utterance`` and release the instant the user stops. So we
**re-poll throughout speech** (each ``True`` poll re-stamps ``last_speech`` to
~now); only a genuine ≥2.0s gap after the *last* speech frame releases a message.

POLL CADENCE, NOT SILENCE MEASUREMENT: silence is measured in **core** by
timestamp diffing (``engine.py``). The periodic cadence task below uses
``asyncio.sleep`` **only as a poll interval** (how often we ask) — it never
evaluates the silence window, so it is *not* the sleep SPEC §9 bans. It exists
because Pipecat may emit no frames during silence, which would otherwise starve a
held message between VAD events.

ONE SERIALIZED POLL PATH (SPEC §3.4 / plan §4.13): the frame-driven poll and the
cadence-timer poll funnel through a single ``_poll_once()`` guarded by an
in-flight flag, so they never call ``get_next_message`` concurrently for one
session — exactly one logical poll loop per session.
"""

import asyncio

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    StartFrame,
    TextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class LatchDeliveryProcessor(FrameProcessor):
    """Polls AgentLatch and emits a held message as a ``TextFrame`` during silence."""

    def __init__(
        self, latch, session_id: str, *, poll_interval: float = 0.1, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._latch = latch
        self._session_id = session_id
        self._poll_interval = poll_interval
        self._is_user_speaking = False
        # In-flight guard for the single serialized poll path (see _poll_once).
        self._poll_in_flight = False
        self._cadence_task: asyncio.Task | None = None

    def _update_speaking(self, frame: Frame) -> None:
        """Track live VAD state — the engine's ``last_speech`` stamp depends on it."""
        if isinstance(frame, UserStartedSpeakingFrame):
            self._is_user_speaking = True
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._is_user_speaking = False

    async def _poll_once(self) -> None:
        """The single serialized poll. In-flight-guarded so the frame poll and the
        cadence poll never call ``get_next_message`` concurrently for this session
        (SPEC §3.4). The check-then-set is atomic under single-threaded asyncio
        (no ``await`` between the read and the set), so it is a correct guard."""
        if self._poll_in_flight:
            return
        self._poll_in_flight = True
        try:
            payload = await self._latch.get_next_message(
                self._session_id, self._is_user_speaking
            )
            if payload is not None:
                # Wrap in a TextFrame — NEVER push a raw string (SPEC §9 Never).
                await self.push_frame(
                    TextFrame(text=payload.text_to_speak), FrameDirection.DOWNSTREAM
                )
        finally:
            self._poll_in_flight = False

    async def _cadence_poll_loop(self) -> None:
        """Poll on a fixed cadence so a held message still surfaces while the user
        is silent and Pipecat emits no frames. The sleep is the poll *interval*,
        not silence measurement — silence is computed in core by timestamp diffing
        (SPEC §9 bans sleep-*measured* silence, not a consumer poll cadence)."""
        while True:
            await asyncio.sleep(self._poll_interval)
            await self._poll_once()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        self._update_speaking(frame)

        # Run the cadence poll for the life of the pipeline; tear it down cleanly.
        if isinstance(frame, StartFrame) and self._cadence_task is None:
            self._cadence_task = self.create_task(self._cadence_poll_loop())
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._stop_cadence()

        # Pass every frame through unchanged...
        await self.push_frame(frame, direction)
        # ...then poll on the frame cadence (serialized with the timer via the guard).
        await self._poll_once()

    async def _stop_cadence(self) -> None:
        if self._cadence_task is not None:
            await self.cancel_task(self._cadence_task)
            self._cadence_task = None
