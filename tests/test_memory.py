"""Spec for the Context Injector seam: the ``ContextInjector`` ABC and the
per-session ``SessionLocks`` registry that guards memory mutation.

T7 is foundational — it defines the contract and the lock primitive; the engine
(T8) and facade (T9) wire them. No Redis here.
"""

import weakref
from asyncio import Lock

import pytest

import agentlatch
from agentlatch import AgentLatch, ContextInjector, ResponsePayload
from agentlatch.memory import SessionLocks


def test_context_injector_is_abstract() -> None:
    # The ABC itself is not instantiable — it is a contract to subclass.
    with pytest.raises(TypeError):
        ContextInjector()  # type: ignore[abstract]


def test_subclass_without_inject_context_is_abstract() -> None:
    class Incomplete(ContextInjector):
        pass

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


async def test_concrete_subclass_injects() -> None:
    class Spy(ContextInjector):
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def inject_context(self, session_id: str, data: dict) -> None:
            self.calls.append((session_id, data))

    spy = Spy()
    result = await spy.inject_context("s1", {"k": "v"})

    assert result is None
    assert spy.calls == [("s1", {"k": "v"})]


def test_session_locks_same_lock_per_session() -> None:
    locks = SessionLocks()

    first = locks.get("s1")
    second = locks.get("s1")  # `first` holds a strong ref, so identity is stable

    assert isinstance(first, Lock)
    assert first is second


def test_session_locks_distinct_lock_per_session() -> None:
    locks = SessionLocks()

    s1 = locks.get("s1")  # bind both so neither weak value is collected mid-test
    s2 = locks.get("s2")

    assert s1 is not s2


def test_session_locks_registry_is_weak() -> None:
    # The weak backing is the anti-leak design decision (a plain dict would pass
    # every identity test above): pin the mechanism so a swap to dict is caught.
    locks = SessionLocks()
    assert isinstance(locks._locks, weakref.WeakValueDictionary)


def test_context_injector_exported_session_locks_internal() -> None:
    assert agentlatch.ContextInjector is ContextInjector
    # Public surface is exact: the three names + version are exported, and the
    # internal lock registry is not.
    assert {"AgentLatch", "ResponsePayload", "ContextInjector", "__version__"} <= set(
        agentlatch.__all__
    )
    assert "SessionLocks" not in agentlatch.__all__
    assert not hasattr(agentlatch, "SessionLocks")
    # The pre-existing exports still resolve.
    assert agentlatch.AgentLatch is AgentLatch
    assert agentlatch.ResponsePayload is ResponsePayload
