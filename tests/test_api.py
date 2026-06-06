"""Spec for the FastAPI receiver — POST /api/v1/queue_response.

DAMP: each status-code case is explicit. The app is driven in-process via
httpx.ASGITransport so it shares the test event loop with the fakeredis-backed
latch (FastAPI's TestClient would use its own loop and cross-wire the client).
"""

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from agentlatch import AgentLatch
from agentlatch.integrations.fastapi import create_router
from agentlatch.queue import HoldingTank

ENDPOINT = "/api/v1/queue_response"
VALID = {"session_id": "s1", "text_to_speak": "hi"}


@pytest_asyncio.fixture
async def client(redis_client: Redis) -> AsyncIterator[AsyncClient]:
    latch = AgentLatch(redis_client=redis_client)
    app = FastAPI()
    app.include_router(create_router(latch))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def test_valid_payload_returns_202_and_enqueues(
    client: AsyncClient, redis_client: Redis
) -> None:
    resp = await client.post(ENDPOINT, json=VALID)
    assert resp.status_code == 202
    tank = HoldingTank(redis_client, session_ttl=3600)
    assert await tank.length("s1") == 1
    popped = await tank.pop("s1")
    assert popped is not None
    assert popped.text_to_speak == "hi"


async def test_missing_text_to_speak_returns_422(client: AsyncClient) -> None:
    resp = await client.post(ENDPOINT, json={"session_id": "s1"})
    assert resp.status_code == 422


async def test_empty_session_id_returns_422(client: AsyncClient) -> None:
    resp = await client.post(ENDPOINT, json={"session_id": "", "text_to_speak": "hi"})
    assert resp.status_code == 422


async def test_unknown_field_returns_422(client: AsyncClient) -> None:
    resp = await client.post(ENDPOINT, json={**VALID, "priority": "high"})
    assert resp.status_code == 422


async def test_non_json_body_returns_422(client: AsyncClient) -> None:
    resp = await client.post(
        ENDPOINT, content="not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 422


async def test_router_mounts_into_bare_app(redis_client: Redis) -> None:
    latch = AgentLatch(redis_client=redis_client)
    app = FastAPI()
    app.include_router(create_router(latch))
    assert ENDPOINT in {route.path for route in app.routes}
