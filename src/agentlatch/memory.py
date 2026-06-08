"""The Context Injector seam — silently update the live LLM's memory *before* a
held message is spoken (SPEC §3.3).

This module is a pure interface + primitive: the ``ContextInjector`` ABC the
developer overrides, and ``SessionLocks``, the per-session lock that guards
mutation. The Delivery Engine (T8) acquires the lock around ``inject_context``;
the facade (T9) exposes the same lock via ``AgentLatch.memory_lock`` so the
developer can guard their LLM's memory *reads* with it too.
"""

import abc
import asyncio
import weakref
from typing import Any


class ContextInjector(abc.ABC):
    """Developer-implemented hook to update the live LLM's memory.

    Subclass and override :meth:`inject_context` with an ``async`` method.
    AgentLatch awaits it **under the per-session lock**
    (``AgentLatch.memory_lock(session_id)``) **before** returning the held
    message, so the LLM's memory is updated prior to TTS. Guard your LLM's
    memory *reads* with the same lock to avoid mutating memory mid-read.
    """

    @abc.abstractmethod
    async def inject_context(self, session_id: str, data: dict[str, Any]) -> None:
        """Apply ``data`` to ``session_id``'s live memory. Must be ``async``."""
        raise NotImplementedError


class SessionLocks:
    """A registry of per-session ``asyncio.Lock``s, created on demand.

    Values are held **weakly**: a session's lock survives exactly while someone
    holds a strong reference — an in-flight injection's ``async with`` or a
    developer's ``memory_lock`` block — and is garbage-collected once unreferenced.
    So the registry self-cleans (no per-session leak) while preserving lock
    *identity* whenever mutual exclusion actually matters.

    Single-event-loop use only (``asyncio.Lock`` is loop-affine, and the lazy
    create below assumes no concurrent access from another thread/loop).
    """

    def __init__(self) -> None:
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def get(self, session_id: str) -> asyncio.Lock:
        """Return the lock for ``session_id``, creating it on first use."""
        lock = self._locks.get(session_id)
        if lock is None:
            # Safe without guarding: a single event loop never interleaves the
            # check and the insert (no ``await`` between them).
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock
