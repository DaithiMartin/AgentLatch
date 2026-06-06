"""Spec for HoldingTank — the Redis-backed, per-session FIFO holding queue.

DAMP: each ordering / TTL / isolation case is explicit. fakeredis backs every
test (no live Redis), and the session key string is asserted directly to lock
the documented `agentlatch:queue:{session_id}` scheme.
"""

from redis.asyncio import Redis

from agentlatch.queue import HoldingTank
from agentlatch.schemas import ResponsePayload


def _payload(session_id: str = "s1", text: str = "hi") -> ResponsePayload:
    return ResponsePayload(session_id=session_id, text_to_speak=text)


async def test_push_then_pop_round_trips_payload(tank: HoldingTank) -> None:
    payload = ResponsePayload(
        session_id="s1",
        text_to_speak="your order shipped",
        silent_context_update={"order_id": 42, "status": "shipped"},
    )
    await tank.push("s1", payload)
    assert await tank.pop("s1") == payload


async def test_pops_in_fifo_order(tank: HoldingTank) -> None:
    first = _payload(text="first")
    second = _payload(text="second")
    third = _payload(text="third")
    await tank.push("s1", first)
    await tank.push("s1", second)
    await tank.push("s1", third)
    assert await tank.pop("s1") == first
    assert await tank.pop("s1") == second
    assert await tank.pop("s1") == third


async def test_pop_on_empty_queue_returns_none(tank: HoldingTank) -> None:
    assert await tank.pop("nobody-here") is None


async def test_length_counts_queued_items(tank: HoldingTank) -> None:
    assert await tank.length("s1") == 0
    await tank.push("s1", _payload())
    await tank.push("s1", _payload())
    assert await tank.length("s1") == 2
    await tank.pop("s1")
    assert await tank.length("s1") == 1


async def test_push_sets_ttl_within_bound(redis_client: Redis, tank: HoldingTank) -> None:
    await tank.push("s1", _payload())
    ttl = await redis_client.ttl("agentlatch:queue:s1")
    assert 0 < ttl <= 3600


async def test_push_refreshes_ttl(redis_client: Redis, tank: HoldingTank) -> None:
    await tank.push("s1", _payload())
    # Manually shorten the expiry, then push again: the second push must reset it.
    await redis_client.expire("agentlatch:queue:s1", 5)
    assert await redis_client.ttl("agentlatch:queue:s1") <= 5
    await tank.push("s1", _payload())
    assert 5 < await redis_client.ttl("agentlatch:queue:s1") <= 3600


async def test_sessions_are_isolated(tank: HoldingTank) -> None:
    a = _payload(session_id="a", text="for-a")
    b = _payload(session_id="b", text="for-b")
    await tank.push("a", a)
    await tank.push("b", b)
    assert await tank.length("a") == 1
    assert await tank.length("b") == 1
    assert await tank.pop("a") == a
    assert await tank.length("b") == 1
