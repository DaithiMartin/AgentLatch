"""Spec for the Context Injector seam: the ``ContextInjector`` ABC and the
per-session ``SessionLocks`` registry that guards memory mutation.

T7 is foundational — it defines the contract and the lock primitive; the engine
(T8) and facade (T9) wire them. No Redis here.
"""

import gc
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


def test_sync_inject_context_override_is_rejected() -> None:
    # AgentLatch awaits inject_context; a sync override would fail only after a
    # message was dequeued, so it must be impossible to even define.
    with pytest.raises(TypeError):

        class SyncInjector(ContextInjector):
            def inject_context(self, session_id: str, data: dict) -> None:  # type: ignore[override]
                return None


def test_sync_inject_context_inherited_via_mixin_is_rejected() -> None:
    # A sync inject_context inherited from a non-ContextInjector mixin is not in
    # the subclass __dict__, but it IS what AgentLatch would await. The ABC must
    # resolve via the MRO and reject it at class-definition — otherwise this is an
    # instantiable ContextInjector with a sync method that fails only post-LPOP.
    class SyncMixin:
        def inject_context(self, session_id: str, data: dict) -> None:
            return None

    with pytest.raises(TypeError):

        class Bad(SyncMixin, ContextInjector):  # type: ignore[misc]
            pass


def test_non_callable_inject_context_is_rejected() -> None:
    # inject_context shadowed by a non-callable attribute is not awaitable; the
    # MRO-resolving check rejects it at class-definition rather than at await.
    with pytest.raises(TypeError):

        class Bad(ContextInjector):
            inject_context = None  # type: ignore[assignment]


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


def test_session_locks_releases_unreferenced_lock() -> None:
    # The behavioural half of the weak design: once no one holds the lock it is
    # collected, so the registry self-cleans rather than leaking per session.
    locks = SessionLocks()
    lock = locks.get("s1")
    assert "s1" in locks._locks

    del lock
    gc.collect()

    assert "s1" not in locks._locks


def test_context_injector_exported_session_locks_internal() -> None:
    assert agentlatch.ContextInjector is ContextInjector
    # Public surface is exact: precisely these names are exported (so a future
    # accidental export is caught too), and the internal lock registry is not.
    assert set(agentlatch.__all__) == {
        "AgentLatch",
        "ContextInjector",
        "ResponsePayload",
        "__version__",
    }
    assert not hasattr(agentlatch, "SessionLocks")
    # The pre-existing exports still resolve.
    assert agentlatch.AgentLatch is AgentLatch
    assert agentlatch.ResponsePayload is ResponsePayload
