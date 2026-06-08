"""Spec for AgentLatch — the public facade that wires the ingest path.

DAMP: each construction / validation / enqueue case is explicit. fakeredis
backs the storage (no live Redis).
"""

import asyncio
import math

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis

import agentlatch
from agentlatch import AgentLatch, ResponsePayload
from agentlatch.core import _default_now
from agentlatch.queue import HoldingTank
from tests.conftest import FakeInjector

NAN = float("nan")
INF = float("inf")


async def test_enqueue_persists_payload(redis_client: Redis) -> None:
    latch = AgentLatch(redis_client=redis_client)
    payload = ResponsePayload(
        session_id="s1",
        text_to_speak="hi",
        silent_context_update={"k": "v"},
    )
    await latch.enqueue(payload)
    # Pop with the verified HoldingTank to confirm it actually landed in Redis.
    tank = HoldingTank(redis_client, session_ttl=3600)
    assert await tank.length("s1") == 1
    assert await tank.pop("s1") == payload


async def test_requires_a_client_source() -> None:
    with pytest.raises(ValueError):
        AgentLatch()  # neither redis_url nor redis_client


async def test_rejects_two_client_sources(redis_client: Redis) -> None:
    with pytest.raises(ValueError):
        AgentLatch(redis_url="redis://localhost:6379", redis_client=redis_client)


async def test_rejects_non_positive_silence_threshold(redis_client: Redis) -> None:
    with pytest.raises(ValueError):
        AgentLatch(redis_client=redis_client, silence_threshold=0)
    with pytest.raises(ValueError):
        AgentLatch(redis_client=redis_client, silence_threshold=-1.0)


async def test_rejects_non_positive_session_ttl(redis_client: Redis) -> None:
    with pytest.raises(ValueError):
        AgentLatch(redis_client=redis_client, session_ttl=0)
    with pytest.raises(ValueError):
        AgentLatch(redis_client=redis_client, session_ttl=-1)


# bool is an int/float subclass and a NaN threshold makes `silence < threshold`
# always False (pops immediately) — both must be rejected as non-finite/non-real.
# A huge int overflows the float conversion and must reject cleanly (ValueError),
# not leak OverflowError.
@pytest.mark.parametrize("bad", [True, False, NAN, INF, 10**1000])
async def test_rejects_non_finite_silence_threshold(redis_client: Redis, bad) -> None:
    with pytest.raises(ValueError):
        AgentLatch(redis_client=redis_client, silence_threshold=bad)


# session_ttl must be a strict positive non-bool int: a float that looks
# integer-valued (1.0) is still wrong, as is 1.5/NaN/inf and bool.
@pytest.mark.parametrize("bad", [True, False, 1.0, 1.5, NAN, INF])
async def test_rejects_non_int_session_ttl(redis_client: Redis, bad) -> None:
    with pytest.raises(ValueError):
        AgentLatch(redis_client=redis_client, session_ttl=bad)


# A misbehaving injected clock must be caught at construction, mirroring the
# engine's finite-on-read rule (bool/NaN/inf/non-number are all invalid).
@pytest.mark.parametrize(
    "bad_now",
    [
        lambda: True,  # math.isfinite(True) is True, but a bool is not a clock
        lambda: NAN,
        lambda: INF,
        lambda: "now",  # non-number
        lambda: 10**1000,  # overflows float conversion — reject, don't leak
    ],
)
async def test_rejects_invalid_now_callable(redis_client: Redis, bad_now) -> None:
    with pytest.raises(ValueError):
        AgentLatch(redis_client=redis_client, now=bad_now)


async def test_rejects_non_callable_now(redis_client: Redis) -> None:
    with pytest.raises(ValueError):
        AgentLatch(redis_client=redis_client, now=42)  # type: ignore[arg-type]


async def test_default_now_is_the_utc_wall_clock(redis_client: Redis) -> None:
    # No `now` provided => the facade uses the default UTC wall clock, which must
    # yield a finite epoch timestamp.
    latch = AgentLatch(redis_client=redis_client)
    assert latch._now is _default_now
    assert math.isfinite(_default_now())


async def test_accepts_int_silence_threshold(redis_client: Redis) -> None:
    # An int threshold is a valid finite-positive number, stored as a float.
    latch = AgentLatch(redis_client=redis_client, silence_threshold=3)
    assert latch.silence_threshold == 3.0


async def test_redis_url_constructs_lazily() -> None:
    # from_url does not connect until a command runs, so this needs no server.
    latch = AgentLatch(redis_url="redis://localhost:6379")
    assert latch.silence_threshold == 2.0
    await latch.aclose()


async def test_stores_silence_threshold(redis_client: Redis) -> None:
    latch = AgentLatch(redis_client=redis_client, silence_threshold=3.5)
    assert latch.silence_threshold == 3.5


async def test_top_level_exports_resolve() -> None:
    assert agentlatch.AgentLatch is AgentLatch
    assert agentlatch.ResponsePayload is ResponsePayload


async def test_aclose_leaves_injected_client_usable(redis_client: Redis) -> None:
    latch = AgentLatch(redis_client=redis_client)
    await latch.aclose()
    # We borrow an injected client; aclose must not close it.
    assert await redis_client.llen("anything") == 0


# --- T9: context_injector wiring + the memory_lock cooperation lock ---


async def test_accepts_context_injector_instance(redis_client: Redis) -> None:
    # The facade owns ONE SessionLocks and hands both the injector and that same
    # registry down to the engine, so memory_lock and the injection share locks.
    injector = FakeInjector()
    latch = AgentLatch(redis_client=redis_client, context_injector=injector)
    assert latch._engine._injector is injector
    assert latch._engine._locks is latch._locks


async def test_default_construction_has_no_injector(redis_client: Redis) -> None:
    latch = AgentLatch(redis_client=redis_client)
    assert latch._engine._injector is None


# A bare object, a plain function, an int, a string — none is a ContextInjector
# instance, so the facade rejects each at construction (plan §4.8), before a bad
# injector could fail only after a message was already dequeued. `match` pins the
# catch to the facade's real ValueError, not the engine's -O-strippable assert —
# so dropping the facade check fails as a facade regression even under `python -O`.
@pytest.mark.parametrize("bad", [object(), lambda s, d: None, 42, "injector"])
async def test_rejects_non_context_injector(redis_client: Redis, bad) -> None:
    with pytest.raises(ValueError, match="ContextInjector"):
        AgentLatch(redis_client=redis_client, context_injector=bad)


async def test_memory_lock_returns_an_asyncio_lock(redis_client: Redis) -> None:
    latch = AgentLatch(redis_client=redis_client)
    assert isinstance(latch.memory_lock("s1"), asyncio.Lock)


async def test_memory_lock_normalizes_session_id(redis_client: Redis) -> None:
    # Same NonEmptyStr adapter as enqueue/get_next_message: a padded id resolves
    # to the same lock object. (The first result stays referenced on the stack
    # across the second call, so the WeakValueDictionary entry can't be GC'd
    # between them — this asserts normalization, not weak-ref behavior.)
    latch = AgentLatch(redis_client=redis_client)
    assert latch.memory_lock(" s1 ") is latch.memory_lock("s1")


async def test_memory_lock_distinct_sessions_get_distinct_locks(
    redis_client: Redis,
) -> None:
    latch = AgentLatch(redis_client=redis_client)
    a = latch.memory_lock("s1")
    b = latch.memory_lock("s2")
    assert a is not b


async def test_memory_lock_rejects_empty_session_id(redis_client: Redis) -> None:
    # Normalized through NonEmptyStr, a whitespace-only id strips to empty and is
    # rejected loudly rather than minting a lock under a degenerate key.
    latch = AgentLatch(redis_client=redis_client)
    with pytest.raises(ValidationError):
        latch.memory_lock("   ")
