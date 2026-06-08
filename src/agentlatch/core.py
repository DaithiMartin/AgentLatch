"""The AgentLatch facade — the public entry point that wires the full path.

Constructs (or adopts) a Redis client and owns the two halves of the library: a
``HoldingTank`` (Receiver — ``enqueue``) and a ``DeliveryEngine`` (Delivery —
``get_next_message``). Silence is measured by timestamp diffing against an
injectable clock (default UTC wall clock) so the timing edge-cases are unit
testable without ever sleeping (SPEC §3.4).
"""

import math
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import StrictBool, TypeAdapter
from redis.asyncio import Redis

from agentlatch.engine import DeliveryEngine
from agentlatch.queue import HoldingTank
from agentlatch.schemas import NonEmptyStr, ResponsePayload

# Validate both public inputs strictly (SPEC §9). is_user_speaking is a StrictBool
# (no coercion — bool is an int subclass, so 1/"true"/None must all be rejected),
# and session_id is normalized through the SAME type ResponsePayload uses, so a
# delivery lookup key always matches the key enqueue wrote.
_IS_SPEAKING_ADAPTER = TypeAdapter(StrictBool)
_SESSION_ID_ADAPTER: TypeAdapter[str] = TypeAdapter(NonEmptyStr)


def _default_now() -> float:
    """Epoch seconds from the UTC wall clock — the default timing source."""
    return datetime.now(UTC).timestamp()


def _validate_silence_threshold(value: float) -> float:
    # bool is an int subclass and a NaN threshold makes `silence < threshold`
    # always False (delivering immediately), so demand a real, finite, positive
    # number — not True/False/NaN/inf.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("silence_threshold must be a finite positive number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("silence_threshold must be a finite positive number")
    return float(value)


def _validate_session_ttl(value: int) -> int:
    # A strict positive non-bool int: a float TTL (1.0/1.5) or NaN/inf is wrong.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("session_ttl must be a positive integer")
    if value <= 0:
        raise ValueError("session_ttl must be a positive integer")
    return value


def _validate_clock(now: Callable[[], float]) -> Callable[[], float]:
    # Catch a misbehaving injected clock at construction, mirroring the engine's
    # finite-on-read rule (bool/NaN/inf/non-number are all invalid). isfinite(True)
    # is True, so the bool check must come first.
    if not callable(now):
        raise ValueError("now must be a callable returning epoch seconds")
    sample = now()
    if (
        isinstance(sample, bool)
        or not isinstance(sample, (int, float))
        or not math.isfinite(sample)
    ):
        raise ValueError("now must return a finite number")
    return now


class AgentLatch:
    """Wires the Receiver and the Delivery Engine over one Redis source.

    Provide exactly one Redis source: a ``redis_url`` (a client is created and
    owned by this instance) or an existing ``redis_client`` (borrowed — the
    caller keeps ownership and is responsible for closing it).

    ``now`` is an optional keyword-only clock (``() -> float`` epoch seconds,
    default the UTC wall clock); it exists so timing is deterministically
    testable (SPEC §3.4) and, being keyword-only, is non-breaking to add.
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        redis_client: Redis | None = None,
        silence_threshold: float = 2.0,
        session_ttl: int = 3600,
        now: Callable[[], float] | None = None,
    ) -> None:
        if (redis_url is None) == (redis_client is None):
            raise ValueError("provide exactly one of redis_url or redis_client")

        self.silence_threshold = _validate_silence_threshold(silence_threshold)
        self.session_ttl = _validate_session_ttl(session_ttl)
        self._now = _validate_clock(now) if now is not None else _default_now

        if redis_client is not None:
            self._redis = redis_client
            self._owns_client = False
        else:
            assert redis_url is not None  # guaranteed by the exactly-one check above
            self._redis = Redis.from_url(redis_url)
            self._owns_client = True

        self._tank = HoldingTank(self._redis, self.session_ttl)
        self._engine = DeliveryEngine(
            self._redis,
            self._tank,
            silence_threshold=self.silence_threshold,
            session_ttl=self.session_ttl,
            now=self._now,
        )

    async def enqueue(self, payload: ResponsePayload) -> None:
        """Persist a payload to its session's holding queue."""
        await self._tank.push(payload.session_id, payload)

    async def get_next_message(
        self, session_id: str, is_user_speaking: bool
    ) -> ResponsePayload | None:
        """Return the next due payload, or ``None`` while the user is not paused.

        Both public inputs are validated strictly (SPEC §9): ``is_user_speaking``
        is a ``StrictBool`` (no coercion — ``1``/``"true"``/``None`` raise), and
        ``session_id`` is normalized exactly like ``ResponsePayload`` (whitespace
        stripped, non-empty) so the lookup key matches the key ``enqueue`` wrote.

        Concurrency contract (normative; SPEC §3.4): a given ``session_id`` must
        have at most one active poll loop at a time. The silence check and the
        ``LPOP`` are intentionally not atomic and there is no per-session lock, so
        any concurrent same-session polling — speaking or silent — is unsupported.
        """
        speaking = _IS_SPEAKING_ADAPTER.validate_python(is_user_speaking)
        normalized_id = _SESSION_ID_ADAPTER.validate_python(session_id)
        return await self._engine.get_next_message(normalized_id, speaking)

    async def aclose(self) -> None:
        """Close the Redis client, but only if this instance created it."""
        if self._owns_client:
            await self._redis.aclose()
