"""The Delivery Engine — releases a held payload only during a genuine pause.

On each poll the engine consumes the VAD signal: while the user is speaking it
records the moment in Redis; once they have been silent for at least
``silence_threshold`` seconds it LPOPs the next payload from the Holding Tank.
Silence is measured by *timestamp diffing* against an injectable clock — never
``asyncio.sleep``, which would block the audio event loop (SPEC §3.4, §9).

Single-loop contract: one audio poll loop per session per process. The silence
check and the LPOP are intentionally not atomic across concurrent pollers;
concurrency is out of scope (SPEC §1 scope discipline, §3.4 caveat). Callers
must not poll one session from multiple loops.
"""

import inspect
import math
from collections.abc import Callable

from redis.asyncio import Redis

from agentlatch.memory import ContextInjector, SessionLocks
from agentlatch.queue import HoldingTank
from agentlatch.schemas import ResponsePayload


class DeliveryEngine:
    """Times message delivery against VAD silence for a single session loop."""

    def __init__(
        self,
        redis: Redis,
        tank: HoldingTank,
        *,
        silence_threshold: float,
        session_ttl: int,
        now: Callable[[], float],
        injector: ContextInjector | None = None,
        locks: SessionLocks | None = None,
    ) -> None:
        # A ContextInjector is async by construction (the ABC enforces it); this
        # precondition catches a non-ContextInjector duck passed by direct misuse,
        # before any LPOP can drop a message. The public ValueError lives at the
        # facade. (Mirrors the is_user_speaking bool-assert below.)
        assert injector is None or inspect.iscoroutinefunction(injector.inject_context)
        self._redis = redis
        self._tank = tank
        self._silence_threshold = silence_threshold
        self._session_ttl = session_ttl
        self._now = now
        self._injector = injector
        # The facade passes its shared registry so memory_lock returns the same
        # lock acquired here; a standalone engine gets its own.
        self._locks = locks if locks is not None else SessionLocks()

    @staticmethod
    def _key(session_id: str) -> str:
        return f"agentlatch:last_speech:{session_id}"

    def _read_clock(self) -> float:
        """Read the injected clock, failing loudly on a non-finite value.

        A misbehaving custom clock (``NaN``/``inf``/non-number) would otherwise
        poison the stored timestamp or make the silence check deliver at once.
        """
        value = self._now()
        # bool is an int subclass; the clock must return a real, finite number.
        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not is_number or not math.isfinite(value):
            raise ValueError(f"clock returned a non-finite value: {value!r}")
        return float(value)

    @staticmethod
    def _parse_timestamp(raw: bytes | str) -> float | None:
        """Parse a stored last-speech value; return None if it is unusable.

        last_speech is only ever written by this engine with a finite clock, but
        a corrupt or non-finite stored value must fail safe (hold) rather than
        produce a bogus silence and pop immediately.
        """
        try:
            value = float(raw.decode() if isinstance(raw, bytes) else raw)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    async def _mark_speech(self, session_id: str) -> None:
        """Record 'now' as the last-speech time, atomically with its TTL."""
        await self._redis.set(
            self._key(session_id), str(self._read_clock()), ex=self._session_ttl
        )

    async def get_next_message(
        self, session_id: str, is_user_speaking: bool
    ) -> ResponsePayload | None:
        """Return the next due payload, or ``None`` while the user is not paused.

        ``is_user_speaking`` is a ``bool`` precondition; the public facade
        validates it strictly before delegating here.
        """
        assert isinstance(is_user_speaking, bool)

        if is_user_speaking:
            await self._mark_speech(session_id)
            return None

        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            # No pause observed yet: seed the baseline and hold, so delivery
            # requires >= threshold of *subsequent* observed silence (never 0s).
            await self._mark_speech(session_id)
            return None

        last_speech = self._parse_timestamp(raw)
        if last_speech is None:
            # A corrupt or non-finite stored timestamp must never trigger a pop;
            # treat it as a cold start — reseed the baseline and hold.
            await self._mark_speech(session_id)
            return None

        silence = self._read_clock() - last_speech
        if silence < self._silence_threshold:
            # Too soon — also holds on a negative delta from clock skew.
            return None

        payload = await self._tank.pop(session_id)
        if payload is not None:
            await self._maybe_inject(session_id, payload)
        return payload

    async def _maybe_inject(self, session_id: str, payload: ResponsePayload) -> None:
        """Update the live LLM's memory before the payload reaches TTS (SPEC §3.3).

        Only when the payload carries a ``silent_context_update`` and an injector
        is configured. The per-session lock guards the call so it can't race the
        developer's memory reads; it wraps only this await. The payload is already
        popped, so a failing injector propagates and is not re-queued (at-most-once).
        """
        if payload.silent_context_update is None or self._injector is None:
            return
        async with self._locks.get(session_id):
            await self._injector.inject_context(session_id, payload.silent_context_update)
