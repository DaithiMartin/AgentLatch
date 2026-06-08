"""DAMP timing tests for the Delivery Engine.

Each test sets up its own scenario explicitly (DAMP over DRY, SPEC §7) and drives
time with the injected ``clock`` fixture — never ``asyncio.sleep``. The engine
holds a payload until the user has been silent for >= ``silence_threshold``.
"""

import pytest

from agentlatch.engine import DeliveryEngine
from agentlatch.schemas import ResponsePayload

LAST_SPEECH_KEY = "agentlatch:last_speech:s1"


def make_engine(redis_client, tank, now, *, threshold=2.0, ttl=3600):
    return DeliveryEngine(
        redis_client, tank, silence_threshold=threshold, session_ttl=ttl, now=now
    )


async def test_user_speaking_returns_none_and_records_speech(redis_client, tank, clock):
    engine = make_engine(redis_client, tank, clock, ttl=3600)
    await tank.push("s1", ResponsePayload(session_id="s1", text_to_speak="hi"))

    result = await engine.get_next_message("s1", is_user_speaking=True)

    assert result is None
    assert await redis_client.get(LAST_SPEECH_KEY) == str(clock()).encode()  # exact time
    assert 0 < await redis_client.ttl(LAST_SPEECH_KEY) <= 3600  # TTL set and bounded


async def test_silence_just_below_threshold_holds(redis_client, tank, clock):
    engine = make_engine(redis_client, tank, clock)
    await tank.push("s1", ResponsePayload(session_id="s1", text_to_speak="hi"))
    await engine.get_next_message("s1", is_user_speaking=True)  # mark speech at t0

    clock.advance(1.999)

    assert await engine.get_next_message("s1", is_user_speaking=False) is None


async def test_silence_exactly_at_threshold_delivers(redis_client, tank, clock):
    engine = make_engine(redis_client, tank, clock)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi")
    await tank.push("s1", payload)
    await engine.get_next_message("s1", is_user_speaking=True)

    clock.advance(2.0)  # boundary is >= threshold

    assert await engine.get_next_message("s1", is_user_speaking=False) == payload


async def test_silence_above_threshold_delivers(redis_client, tank, clock):
    engine = make_engine(redis_client, tank, clock)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi")
    await tank.push("s1", payload)
    await engine.get_next_message("s1", is_user_speaking=True)

    clock.advance(2.001)

    assert await engine.get_next_message("s1", is_user_speaking=False) == payload


async def test_sufficient_silence_but_empty_queue_returns_none(redis_client, tank, clock):
    engine = make_engine(redis_client, tank, clock)
    await engine.get_next_message("s1", is_user_speaking=True)  # baseline, empty queue

    clock.advance(5.0)

    assert await engine.get_next_message("s1", is_user_speaking=False) is None


async def test_cold_start_seeds_baseline_then_delivers(redis_client, tank, clock):
    engine = make_engine(redis_client, tank, clock)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi")
    await tank.push("s1", payload)

    # No speech ever observed: the first silent poll must hold and seed a baseline,
    # so delivery needs >= threshold of *subsequent* silence (never 0s).
    assert await engine.get_next_message("s1", is_user_speaking=False) is None
    assert await redis_client.get(LAST_SPEECH_KEY) is not None

    clock.advance(2.0)
    assert await engine.get_next_message("s1", is_user_speaking=False) == payload


async def test_future_last_speech_holds(redis_client, tank, clock):
    engine = make_engine(redis_client, tank, clock)
    await tank.push("s1", ResponsePayload(session_id="s1", text_to_speak="hi"))
    # A last_speech in the future (cross-process clock skew) => negative silence.
    await redis_client.set(LAST_SPEECH_KEY, str(clock() + 100.0))

    assert await engine.get_next_message("s1", is_user_speaking=False) is None


async def test_nonfinite_clock_raises(redis_client, tank):
    nan_engine = make_engine(redis_client, tank, lambda: float("nan"))
    with pytest.raises(ValueError):
        await nan_engine.get_next_message("s1", is_user_speaking=True)

    inf_engine = make_engine(redis_client, tank, lambda: float("inf"))
    with pytest.raises(ValueError):
        await inf_engine.get_next_message("s1", is_user_speaking=True)


async def test_nonfinite_clock_raises_on_silent_poll(redis_client, tank, clock):
    # A valid baseline exists, but the clock goes bad during a silent poll.
    good = make_engine(redis_client, tank, clock)
    await tank.push("s1", ResponsePayload(session_id="s1", text_to_speak="hi"))
    await good.get_next_message("s1", is_user_speaking=True)  # baseline at t0

    bad = make_engine(redis_client, tank, lambda: float("nan"))
    with pytest.raises(ValueError):
        await bad.get_next_message("s1", is_user_speaking=False)
    assert await tank.length("s1") == 1  # nothing popped


@pytest.mark.parametrize("corrupt", ["nan", "inf", "-inf", "abc"])
async def test_corrupt_stored_last_speech_holds_and_reseeds(redis_client, tank, clock, corrupt):
    # A poisoned last_speech value (non-finite or unparseable) must never pop.
    engine = make_engine(redis_client, tank, clock, ttl=3600)
    await tank.push("s1", ResponsePayload(session_id="s1", text_to_speak="hi"))
    await redis_client.set(LAST_SPEECH_KEY, corrupt)

    assert await engine.get_next_message("s1", is_user_speaking=False) is None
    assert await tank.length("s1") == 1  # held, not popped
    # self-heal: reseeded to a finite baseline with a bounded TTL
    assert await redis_client.get(LAST_SPEECH_KEY) == str(clock()).encode()
    assert 0 < await redis_client.ttl(LAST_SPEECH_KEY) <= 3600
    clock.advance(2.0)
    assert await engine.get_next_message("s1", is_user_speaking=False) is not None


async def test_speech_during_due_delivery_holds_message(redis_client, tank, clock):
    engine = make_engine(redis_client, tank, clock)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi")
    await tank.push("s1", payload)
    await engine.get_next_message("s1", is_user_speaking=True)
    clock.advance(3.0)  # silence would otherwise be enough to deliver

    # ...but the user is speaking again right as it would pop.
    result = await engine.get_next_message("s1", is_user_speaking=True)

    assert result is None
    assert await tank.length("s1") == 1  # message stays queued, not popped

    clock.advance(2.0)
    assert await engine.get_next_message("s1", is_user_speaking=False) == payload


async def test_delivers_in_fifo_order(redis_client, tank, clock):
    engine = make_engine(redis_client, tank, clock)
    first = ResponsePayload(session_id="s1", text_to_speak="A")
    second = ResponsePayload(session_id="s1", text_to_speak="B")
    await tank.push("s1", first)
    await tank.push("s1", second)
    await engine.get_next_message("s1", is_user_speaking=True)  # baseline

    clock.advance(2.0)
    assert await engine.get_next_message("s1", is_user_speaking=False) == first
    clock.advance(2.0)
    assert await engine.get_next_message("s1", is_user_speaking=False) == second
