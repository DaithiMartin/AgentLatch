"""LangGraph backend — the webhook fire (sandbox, T12).

A one-node ``StateGraph`` that simulates heavy Zone-3 compute (a sleep) then POSTs
a finished message to the edge receiver's webhook (T11). It is a **black-box HTTP
client**: it knows only the webhook URL and the ``ResponsePayload`` JSON shape,
never AgentLatch internals (requirements.txt is just ``langgraph`` + ``httpx``).

The sleep here is **simulated compute** (SPEC §8), NOT silence measurement — the
no-``asyncio.sleep`` rule (SPEC §9) governs the delivery *engine*, not this backend.

Run (Redis up + the T11 receiver running; from ``sandbox/backend_langgraph`` with
the venv active)::

    SESSION_ID=sandbox-demo python graph.py

Env knobs: ``SLEEP_S`` (default 10; set 0 for the smoke check), ``WEBHOOK_URL``
(default ``http://localhost:8000/api/v1/queue_response``), ``SESSION_ID`` (default
``sandbox-demo`` — the SAME value the edge polls, plan §4.10), ``TEXT_TO_SPEAK``.
"""

import asyncio
import logging
import os
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backend_langgraph")

SLEEP_S = float(os.environ.get("SLEEP_S", "10"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "http://localhost:8000/api/v1/queue_response")
SESSION_ID = os.environ.get("SESSION_ID", "sandbox-demo")
TEXT_TO_SPEAK = os.environ.get("TEXT_TO_SPEAK", "Your order #1234 shipped — it arrives Thursday.")


class BackendState(TypedDict, total=False):
    """The graph's state: the message to send, plus the HTTP result for inspection."""

    session_id: str
    text_to_speak: str
    status_code: int


async def fire_webhook(state: BackendState) -> dict[str, int]:
    """Simulate heavy compute, then POST a ``ResponsePayload``-shaped body to the edge."""
    # Simulated Zone-3 work standing in for a slow backend agent. This is NOT the
    # silence window — that measurement (and the no-sleep ban, SPEC §9) lives in
    # the delivery engine; here a sleep is just latency.
    await asyncio.sleep(SLEEP_S)

    sid = state["session_id"]
    # Only the fields the core contract owns — extra keys would be rejected (422).
    body = {"session_id": sid, "text_to_speak": state["text_to_speak"]}
    async with httpx.AsyncClient() as client:
        response = await client.post(WEBHOOK_URL, json=body)

    # Log the sid we posted: CP-E needs posted-sid == polled-sid (plan §4.10).
    logger.info("posted session_id=%s to %s -> HTTP %s", sid, WEBHOOK_URL, response.status_code)

    # A non-202 means the contract drifted (e.g. a 422 from an extra/missing field).
    # Surface it loudly — never swallow. raise_for_status catches 4xx/5xx; the
    # explicit check catches any other non-202 (202 is the receiver's only success).
    response.raise_for_status()
    if response.status_code != 202:
        raise RuntimeError(f"expected 202 from {WEBHOOK_URL}, got {response.status_code}")
    return {"status_code": response.status_code}


def build_graph():
    """Compile the one-node webhook-fire graph (START -> fire_webhook -> END)."""
    builder = StateGraph(BackendState)
    builder.add_node("fire_webhook", fire_webhook)
    builder.add_edge(START, "fire_webhook")
    builder.add_edge("fire_webhook", END)
    return builder.compile()


graph = build_graph()


async def main() -> None:
    result = await graph.ainvoke({"session_id": SESSION_ID, "text_to_speak": TEXT_TO_SPEAK})
    logger.info("graph complete: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
