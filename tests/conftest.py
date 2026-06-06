"""Shared fixtures: an in-memory fake Redis and a HoldingTank bound to it.

No live Redis — unit tests use fakeredis (SPEC §7 test isolation).
"""

from collections.abc import AsyncIterator

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
