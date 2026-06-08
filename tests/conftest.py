"""Shared fixtures: an in-memory fake Redis and a HoldingTank bound to it.

No live Redis — unit tests use fakeredis (SPEC §7 test isolation).
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from redis.asyncio import Redis

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
