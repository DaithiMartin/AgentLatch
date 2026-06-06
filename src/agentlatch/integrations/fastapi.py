"""FastAPI integration: a ready-made webhook router for the Receiver.

Optional — requires ``pip install 'agentlatch[fastapi]'``. The core library never
depends on FastAPI; importing this module without it raises a clear error.
"""

try:
    from fastapi import APIRouter
except ModuleNotFoundError as exc:  # pragma: no cover - only hit without the extra
    raise ImportError(
        "AgentLatch's FastAPI integration requires the optional extra: "
        "install it with `pip install 'agentlatch[fastapi]'`."
    ) from exc

from agentlatch.core import AgentLatch
from agentlatch.schemas import ResponsePayload


def create_router(latch: AgentLatch) -> APIRouter:
    """Build an ``APIRouter`` exposing ``POST /api/v1/queue_response``.

    Mount it into an app with ``app.include_router(create_router(latch))``.
    FastAPI validates the body against ``ResponsePayload`` (returning 422 on any
    violation); a valid payload is enqueued and acknowledged with 202.
    """
    router = APIRouter()

    @router.post("/api/v1/queue_response", status_code=202)
    async def queue_response(payload: ResponsePayload) -> dict[str, str]:
        await latch.enqueue(payload)
        return {"status": "queued"}

    return router
