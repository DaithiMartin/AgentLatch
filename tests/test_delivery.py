"""DAMP round-trip tests for the delivery side of the AgentLatch facade.

These drive ``AgentLatch.get_next_message`` end-to-end through a real (fake)
Redis with an injected ``clock`` (the ``now`` seam) — never ``asyncio.sleep``.
Each case sets up its own scenario explicitly (DAMP over DRY, SPEC §7).
"""

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis

from agentlatch import AgentLatch, ResponsePayload


async def test_round_trip_holds_then_delivers(redis_client: Redis, clock) -> None:
    latch = AgentLatch(redis_client=redis_client, now=clock)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi")
    await latch.enqueue(payload)

    # Speaking records the moment and never delivers, even with a non-empty queue.
    assert await latch.get_next_message("s1", is_user_speaking=True) is None
    # Silence shorter than the threshold still holds.
    assert await latch.get_next_message("s1", is_user_speaking=False) is None

    clock.advance(2.0)  # boundary is >= threshold
    assert await latch.get_next_message("s1", is_user_speaking=False) == payload


async def test_silence_just_below_threshold_holds(redis_client: Redis, clock) -> None:
    latch = AgentLatch(redis_client=redis_client, now=clock)
    await latch.enqueue(ResponsePayload(session_id="s1", text_to_speak="hi"))
    await latch.get_next_message("s1", is_user_speaking=True)  # mark speech at t0

    clock.advance(1.999)

    assert await latch.get_next_message("s1", is_user_speaking=False) is None


async def test_delivers_in_fifo_order(redis_client: Redis, clock) -> None:
    latch = AgentLatch(redis_client=redis_client, now=clock)
    first = ResponsePayload(session_id="s1", text_to_speak="A")
    second = ResponsePayload(session_id="s1", text_to_speak="B")
    await latch.enqueue(first)
    await latch.enqueue(second)
    await latch.get_next_message("s1", is_user_speaking=True)  # baseline at t0

    clock.advance(2.0)
    assert await latch.get_next_message("s1", is_user_speaking=False) == first
    clock.advance(2.0)
    assert await latch.get_next_message("s1", is_user_speaking=False) == second


@pytest.mark.parametrize("bad", [1, 0, "true", "false", None, 1.0])
async def test_non_bool_is_user_speaking_raises(redis_client: Redis, clock, bad) -> None:
    # StrictBool: no coercion — anything that is not a real bool is rejected.
    latch = AgentLatch(redis_client=redis_client, now=clock)
    with pytest.raises(ValidationError):
        await latch.get_next_message("s1", is_user_speaking=bad)


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
async def test_blank_session_id_raises(redis_client: Redis, clock, blank) -> None:
    latch = AgentLatch(redis_client=redis_client, now=clock)
    with pytest.raises(ValidationError):
        await latch.get_next_message(blank, is_user_speaking=False)


async def test_session_id_is_normalized_like_enqueue(redis_client: Redis, clock) -> None:
    # enqueue stores under the ResponsePayload-normalized "s1"; a padded lookup
    # must resolve to the SAME key, or delivery would silently miss the message.
    latch = AgentLatch(redis_client=redis_client, now=clock)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi")
    await latch.enqueue(payload)

    assert await latch.get_next_message(" s1 ", is_user_speaking=True) is None
    clock.advance(2.0)
    assert await latch.get_next_message(" s1 ", is_user_speaking=False) == payload
