"""Spec for AgentLatch — the public facade that wires the ingest path.

DAMP: each construction / validation / enqueue case is explicit. fakeredis
backs the storage (no live Redis).
"""

import math

import pytest
from redis.asyncio import Redis

import agentlatch
from agentlatch import AgentLatch, ResponsePayload
from agentlatch.core import _default_now
from agentlatch.queue import HoldingTank

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
@pytest.mark.parametrize("bad", [True, False, NAN, INF])
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
