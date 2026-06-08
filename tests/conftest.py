"""Shared fixtures + test doubles: a fake Redis, a HoldingTank, a manual clock,
and an injector/lock-registry spy for the delivery-injection tests.

No live Redis — unit tests use fakeredis (SPEC §7 test isolation).
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from redis.asyncio import Redis

from agentlatch.memory import ContextInjector, SessionLocks
from agentlatch.queue import HoldingTank


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = FakeRedis()
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def tank(redis_client: Redis) -> HoldingTank:
    return HoldingTank(redis_client, session_ttl=3600)


class FakeClock:
    """A manually-advanced clock for deterministic, sleep-free timing tests.

    The Delivery Engine measures silence by timestamp diffing against an
    injectable ``now`` callable (SPEC §3.4). Tests advance this clock by hand
    instead of sleeping, so timing edge-cases are exact and fast.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


class FakeInjector(ContextInjector):
    """A configurable spy ``ContextInjector`` for delivery-injection tests.

    Records each call. Given a ``locks`` registry it captures (and retains a
    strong ref to) the exact per-session lock and whether it was held *during*
    the call — the strong ref defeats the ``WeakValueDictionary`` so later
    assertions inspect the same object, not a freshly recreated one. Given a
    ``tank`` it records the queue length at injection entry (to prove the LPOP
    preceded injection). It can block on a ``gate`` event (to prove the payload
    is not returned until injection completes) and/or raise.
    """

    def __init__(
        self,
        *,
        locks: SessionLocks | None = None,
        tank: HoldingTank | None = None,
        gate: asyncio.Event | None = None,
        raises: bool = False,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.entered = asyncio.Event()
        self.captured_lock: asyncio.Lock | None = None
        self.lock_held_during_call: bool | None = None
        self.queue_len_at_entry: int | None = None
        self._locks = locks
        self._tank = tank
        self._gate = gate
        self._raises = raises

    async def inject_context(self, session_id: str, data: dict[str, Any]) -> None:
        if self._locks is not None:
            lock = self._locks.get(session_id)
            self.captured_lock = lock  # strong ref keeps the weak entry alive
            self.lock_held_during_call = lock.locked()
        if self._tank is not None:
            self.queue_len_at_entry = await self._tank.length(session_id)
        self.calls.append((session_id, data))
        self.entered.set()
        if self._gate is not None:
            await self._gate.wait()
        if self._raises:
            raise RuntimeError("injection failed")


class SpyLocks(SessionLocks):
    """A ``SessionLocks`` that records every ``get`` so a test can assert *zero*
    lock acquisitions on the non-injecting paths (the engine only asks for a lock
    inside the inject branch)."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[str] = []

    def get(self, session_id: str) -> asyncio.Lock:
        self.get_calls.append(session_id)
        return super().get(session_id)
