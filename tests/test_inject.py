"""DAMP tests for inject-before-return in the Delivery Engine (T8).

The engine awaits the configured injector's ``inject_context`` **under the
per-session lock, before** returning a popped payload that carries a
``silent_context_update`` (SPEC §3.3/§3.4). Proofs are deliberately precise about
ordering and the ``WeakValueDictionary`` lock-identity interaction. fakeredis +
the injected ``clock`` fixture; never ``asyncio.sleep``.
"""

import asyncio

import pytest

from agentlatch.engine import DeliveryEngine
from agentlatch.memory import SessionLocks
from agentlatch.schemas import ResponsePayload
from tests.conftest import FakeInjector, SpyLocks


def make_engine(redis_client, tank, now, *, injector=None, locks=None, threshold=2.0, ttl=3600):
    return DeliveryEngine(
        redis_client,
        tank,
        silence_threshold=threshold,
        session_ttl=ttl,
        now=now,
        injector=injector,
        locks=locks,
    )


async def _seed_and_wait(engine, tank, clock, payload):
    """Enqueue a payload, mark speech at t0, then advance past the threshold."""
    await tank.push(payload.session_id, payload)
    await engine.get_next_message(payload.session_id, is_user_speaking=True)
    clock.advance(2.0)


async def test_payload_not_returned_until_injection_completes(redis_client, tank, clock):
    # Gated: the payload must not surface until inject_context has finished.
    gate = asyncio.Event()
    locks = SessionLocks()
    injector = FakeInjector(locks=locks, gate=gate)
    engine = make_engine(redis_client, tank, clock, injector=injector, locks=locks)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi", silent_context_update={"k": "v"})
    await _seed_and_wait(engine, tank, clock, payload)

    task = asyncio.create_task(engine.get_next_message("s1", is_user_speaking=False))
    await asyncio.wait_for(injector.entered.wait(), 1.0)  # injection has started…

    assert not task.done()  # …and the payload is NOT returned while it blocks

    gate.set()
    assert await task == payload
    assert injector.calls == [("s1", {"k": "v"})]


async def test_lock_held_during_injection_and_released_after(redis_client, tank, clock):
    locks = SessionLocks()
    injector = FakeInjector(locks=locks)
    engine = make_engine(redis_client, tank, clock, injector=injector, locks=locks)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi", silent_context_update={"k": "v"})
    await _seed_and_wait(engine, tank, clock, payload)

    assert await engine.get_next_message("s1", is_user_speaking=False) == payload
    assert injector.lock_held_during_call is True  # held during the call
    assert injector.captured_lock is not None
    assert injector.captured_lock.locked() is False  # released after
    # the captured object IS the registry's lock — not a freshly recreated one
    assert engine._locks.get("s1") is injector.captured_lock


async def test_lock_released_after_raising_injection(redis_client, tank, clock):
    locks = SessionLocks()
    injector = FakeInjector(locks=locks, raises=True)
    engine = make_engine(redis_client, tank, clock, injector=injector, locks=locks)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi", silent_context_update={"k": "v"})
    await _seed_and_wait(engine, tank, clock, payload)

    with pytest.raises(RuntimeError):
        await engine.get_next_message("s1", is_user_speaking=False)

    assert injector.captured_lock is not None
    assert injector.captured_lock.locked() is False  # released on the exception path too


async def test_failed_injection_is_at_most_once(redis_client, tank, clock):
    locks = SessionLocks()
    injector = FakeInjector(locks=locks, tank=tank, raises=True)
    engine = make_engine(redis_client, tank, clock, injector=injector, locks=locks)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi", silent_context_update={"k": "v"})
    await _seed_and_wait(engine, tank, clock, payload)

    with pytest.raises(RuntimeError):
        await engine.get_next_message("s1", is_user_speaking=False)

    assert injector.queue_len_at_entry == 0  # the LPOP happened BEFORE inject ran
    assert await tank.length("s1") == 0  # …and the message was not re-queued


async def test_empty_context_update_still_injects(redis_client, tank, clock):
    locks = SessionLocks()
    injector = FakeInjector(locks=locks)
    engine = make_engine(redis_client, tank, clock, injector=injector, locks=locks)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi", silent_context_update={})
    await _seed_and_wait(engine, tank, clock, payload)

    assert await engine.get_next_message("s1", is_user_speaking=False) == payload
    assert injector.calls == [("s1", {})]  # an empty dict is "present" (is not None)


async def test_no_injection_or_lock_without_context(redis_client, tank, clock):
    locks = SpyLocks()
    injector = FakeInjector(locks=locks)
    engine = make_engine(redis_client, tank, clock, injector=injector, locks=locks)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi")  # no silent_context_update
    await _seed_and_wait(engine, tank, clock, payload)

    assert await engine.get_next_message("s1", is_user_speaking=False) == payload
    assert injector.calls == []  # not injected
    assert locks.get_calls == []  # …and no lock was ever acquired


async def test_no_injection_or_lock_when_injector_is_none(redis_client, tank, clock):
    # Slice 2 regression: with no injector the payload returns un-injected.
    locks = SpyLocks()
    engine = make_engine(redis_client, tank, clock, injector=None, locks=locks)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi", silent_context_update={"k": "v"})
    await _seed_and_wait(engine, tank, clock, payload)

    assert await engine.get_next_message("s1", is_user_speaking=False) == payload
    assert locks.get_calls == []  # no injector ⇒ no lock acquired


async def test_lock_wraps_only_injection_not_the_pop(redis_client, tank, clock, monkeypatch):
    locks = SessionLocks()
    injector = FakeInjector(locks=locks)
    engine = make_engine(redis_client, tank, clock, injector=injector, locks=locks)
    payload = ResponsePayload(session_id="s1", text_to_speak="hi", silent_context_update={"k": "v"})
    await _seed_and_wait(engine, tank, clock, payload)

    locked_at_pop: list[bool] = []
    real_pop = engine._tank.pop

    async def spy_pop(session_id):
        locked_at_pop.append(locks.get(session_id).locked())
        return await real_pop(session_id)

    monkeypatch.setattr(engine._tank, "pop", spy_pop)

    await engine.get_next_message("s1", is_user_speaking=False)
    assert locked_at_pop == [False]  # lock NOT held during the pop…
    assert injector.lock_held_during_call is True  # …only during injection


async def test_two_context_payloads_release_fifo_with_injection(redis_client, tank, clock):
    locks = SessionLocks()
    injector = FakeInjector(locks=locks)
    engine = make_engine(redis_client, tank, clock, injector=injector, locks=locks)
    first = ResponsePayload(session_id="s1", text_to_speak="A", silent_context_update={"n": 1})
    second = ResponsePayload(session_id="s1", text_to_speak="B", silent_context_update={"n": 2})
    await tank.push("s1", first)
    await tank.push("s1", second)
    await engine.get_next_message("s1", is_user_speaking=True)

    clock.advance(2.0)
    assert await engine.get_next_message("s1", is_user_speaking=False) == first
    clock.advance(2.0)
    assert await engine.get_next_message("s1", is_user_speaking=False) == second
    assert injector.calls == [("s1", {"n": 1}), ("s1", {"n": 2})]  # injected each, in order


async def test_direct_construction_rejects_sync_injector(redis_client, tank, clock):
    # A duck object whose inject_context is sync reaches the engine precondition.
    class SyncDuck:
        def inject_context(self, session_id, data):
            return None

    with pytest.raises(AssertionError):
        make_engine(redis_client, tank, clock, injector=SyncDuck())
