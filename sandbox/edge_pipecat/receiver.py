"""Edge receiver — the webhook absorber half of the Fast Edge (sandbox, T11).

Mounts AgentLatch's optional FastAPI router so a background agent (the LangGraph
backend, T12) can POST a finished message to ``/api/v1/queue_response`` and have
it land in Redis. This process **only enqueues** — it never polls — so it cannot
violate the single-poll-loop contract (SPEC §3.4); the polling delivery edge is a
separate process (``edge.py``, T13).

Consumes the published core package only (``agentlatch[fastapi]``, installed
editable via requirements.txt). Adds nothing to core.

Run (Redis up, from ``sandbox/edge_pipecat`` with the venv active)::

    uvicorn receiver:app

Configure the datastore with ``REDIS_URL`` (default ``redis://localhost:6379``).
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agentlatch import AgentLatch
from agentlatch.integrations.fastapi import create_router

# One AgentLatch per process, over the shared dev Redis. redis_url is the default
# source (AgentLatch creates and owns the client), so the lifespan below closes it.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
latch = AgentLatch(redis_url=REDIS_URL)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # AgentLatch owns the Redis client it built from REDIS_URL; close it on
    # shutdown so the connection pool drains cleanly.
    yield
    await latch.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(create_router(latch))
