# AgentLatch — Specification

> Stateful queueing middleware that holds a background agent's message until the
> human stops talking, then releases it into a live voice session.

**Status:** active — spec-driven; build progress in [`tasks/todo.md`](./tasks/todo.md).

---

## 1. Objective

AgentLatch is a small, **framework-agnostic Python library** that solves one
problem: a long-running background agent (Zone 3) finishes its work *whenever it
finishes* and wants to say something to a user who is in a real-time voice
conversation (Zone 1). You cannot just blurt the message out — the user might be
mid-sentence. AgentLatch:

1. **Absorbs** the message via a webhook (the *Receiver*).
2. **Holds** it in a Redis list keyed by session (the *Holding Tank*).
3. **Releases** it only after Voice Activity Detection (VAD) reports the user has
been silent for ≥ 2.0 seconds (the *Delivery Engine*).
4. Optionally **injects silent context** into the live LLM's memory before the
message is spoken (the *Context Injector*).

### Target users
Python developers building real-time voice agents who need to merge asynchronous
backend results into a synchronous conversation without interrupting the user.
They are assumed to already run their own web server and voice/LLM stack —
AgentLatch is glue, not a platform.

### Primary directive — scope discipline
**Build only the stateful queueing middleware.** Do **not** build a voice
framework, an LLM router, or a message broker. If a feature smells like one of
those, it belongs in the sandbox or in the user's app, not in core.

### Glossary
- **VAD (Voice Activity Detection):** the component of a voice stack that reports,
moment to moment, whether a human is currently speaking. AgentLatch consumes
this signal; it does not implement it.
- **Zone 1 / Zone 3:** the real-time voice session vs. the slow background agent.
- **Silence threshold:** the continuous quiet duration (default **2.0s**) that
must elapse before a held message is released.

---

## 2. Resolved design decisions

These four forks were debated and settled before writing this spec. Recorded here
so the rationale survives.

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | Web framework coupling | **Optional `[fastapi]` extra** | A *library* must not pin its consumer's web framework. Core depends only on `redis-py` + `pydantic`; FastAPI users add `pip install agentlatch[fastapi]` and get a ready-made router. No loss of convenience, no dependency conflicts. |
| 2 | VAD timing API | **Single method** `get_next_message(session_id, is_user_speaking)` | The speech timestamp updates as a side effect of asking for a message. Matches the natural Pipecat `FrameProcessor` poll loop; one public method to keep stable. |
| 3 | Tooling / Python | **uv + hatchling, Python 3.11+** | Modern, fast contributor DX. 3.11 floor gives `datetime.UTC` and mature asyncio — relevant because timing is core. |
| 4 | License | **MIT** | No patent concern; maximize adoption and contributor familiarity. MIT→Apache-2.0 relicensing stays possible later if a corporate adopter needs it. |

---

## 3. Architecture & module boundaries

Four conceptual responsibilities. Note the divergence from the original draft's
flat `api.py`: the HTTP receiver is an **optional adapter**, so the
*core* receiver responsibility is "validate a payload and enqueue it," and the
HTTP binding lives under `integrations/`.

### 3.1 The Receiver
- **Core responsibility:** validate an incoming payload (Pydantic) and hand it to
the Holding Tank. Lives in `schemas.py` + `core.AgentLatch.enqueue()`.
- **Optional HTTP binding:** `integrations/fastapi.py` exposes
`POST /api/v1/queue_response` via an `APIRouter` the user mounts into their app.
- **Payload schema (`ResponsePayload`):**
- `session_id: str` — required, non-empty.
- `text_to_speak: str` — required, non-empty.
- `silent_context_update: dict | None` — optional.
- **Success criteria:** invalid body → **HTTP 422**; valid body → **HTTP 202**
with body `{"status": "queued"}`, returning in well under 50 ms (enqueue is one
async Redis `RPUSH`).

### 3.2 The Holding Tank — `queue.py`
- **Goal:** persist payloads until the user is ready.
- **Backend:** `redis.asyncio` (real calls — never a Python `dict`).
- **Data structure:** a Redis **List** at `agentlatch:queue:{session_id}`.
- Enqueue: `RPUSH` (append right).
- Deliver: `LPOP` (take left) → **FIFO** ordering.
- **TTL:** every session key gets a configurable TTL (default **3600s**) refreshed
on write, so abandoned sessions self-clean and Redis doesn't leak keys.

### 3.3 The Context Injector — `memory.py`
- **Goal:** silently update the live LLM's memory *before* the held message is
spoken (e.g. "the order lookup returned X" so the agent can speak naturally).
- **API:** abstract base class `ContextInjector` with
`async def inject_context(self, session_id: str, data: dict) -> None`, which the
developer overrides.
- **Wiring:** an injector is optional. When `get_next_message` pops a payload that
carries a `silent_context_update`, the engine `await`s `inject_context(...)`
**before** returning the text, guaranteeing memory is updated prior to TTS.
- **Constructor wiring (TO ADD in Slice 3 — deferred from Slice 1/T3):** add the
`context_injector` parameter to `AgentLatch.__init__`, typed `ContextInjector |
None` (default `None`), and store it for the engine to use. It is keyword-only, so
adding it is non-breaking. It was intentionally omitted from Slice 1 to avoid
freezing a placeholder type before `ContextInjector` existed.
- **CRITICAL CONSTRAINT — thread safety:** memory mutation must happen only while
the conversational LLM is idle, to avoid "dict changed size during iteration"
style failures. AgentLatch provides a per-session `asyncio.Lock` that guards the
injection call; the documented integration contract is that the developer's
`inject_context` (and their LLM's read of memory) cooperate via this lock /
an idle flag.

### 3.4 The Delivery Engine — `engine.py`
- **Goal:** release a held message only during a genuine pause.
- **API:** `async def get_next_message(session_id, is_user_speaking: bool) -> ResponsePayload | None`.
- **Behavior (Option A):**
- If `is_user_speaking is True`: write `last_speech = now`, return `None`.
- If `is_user_speaking is False`: compute `silence = now - last_speech`.
- If `silence < silence_threshold` (default 2.0s): return `None`.
- Else: `LPOP` the next payload. If none, return `None`. If one exists and it
  has `silent_context_update`, run the injector under the lock, then return it.
- **State key:** `agentlatch:last_speech:{session_id}` in Redis, so timing is
stateless and works across workers.
- **Concurrency contract (normative):** a given `session_id` **must** have at most
**one active poll loop at a time** (one real-time voice stream ⇒ one poll loop).
**Any** concurrent same-session polling — speaking or silent — is unsupported in
v1: a second poller could `LPOP` a message another already gated, or write
`last_speech` between a poll's silence check and its `LPOP`, releasing a message
into fresh speech. The silence check and the `LPOP` are intentionally **not**
atomic and AgentLatch ships **no** per-session lock — serializing pollers would
drift toward message-broker scope (§1) and away from the boring solution (§6).
Sequential handoff (a session moving to another worker **after** its previous loop
stops — never concurrently) is permitted, **subject to the wall-clock caveat
below**: across-process timing is skew-safe only insofar as the workers' clocks
agree; Redis-server-time as the canonical clock is the noted future hardening.
Revisit only if multi-loop-per-session enters scope.
- **CRITICAL CONSTRAINT — the state trap:** **never** `asyncio.sleep()` to measure
silence; it blocks the audio event loop. Use timestamp diffing only.
- **Clock seam:** the "now" source is an **injectable callable** (defaults to UTC
wall clock) so timing edge-cases are unit-testable deterministically without
sleeping. (v1 caveat: timestamps use the calling process's wall clock; for a
single session the audio loop runs in one process, so skew is a non-issue.
Redis-server-time as the canonical clock is noted as a future hardening.)

---

## 4. Public API (the "commands" surface)

This is a Hyrum's-Law surface — treat every signature below as a stable contract.
It describes the complete intended surface; each method/parameter lands in the
slice that builds it (e.g. `get_next_message` in Slice 2; `context_injector` /
`ContextInjector` in Slice 3) — see [`tasks/todo.md`](./tasks/todo.md).

```python
from agentlatch import AgentLatch, ResponsePayload, ContextInjector

latch = AgentLatch(
redis_url="redis://localhost:6379",
silence_threshold=2.0,        # seconds
session_ttl=3600,             # seconds
context_injector=None,        # optional ContextInjector instance
)

# Receiver side (called by the webhook handler):
await latch.enqueue(payload: ResponsePayload) -> None

# Delivery side (called by the voice pipeline poll loop):
msg: ResponsePayload | None = await latch.get_next_message(
session_id: str, is_user_speaking: bool
)
```

Optional FastAPI adapter:

```python
from agentlatch.integrations.fastapi import create_router
app.include_router(create_router(latch))   # adds POST /api/v1/queue_response
```

Strict typing rule: an invalid `is_user_speaking` (non-`bool`) or a malformed
payload must raise/validate loudly — no silent coercion.

---

## 5. Project structure

```
AgentLatch/
├── pyproject.toml              # uv + hatchling, PEP 621, optional [fastapi] extra
├── README.md
├── LICENSE                     # MIT
├── docker-compose.yml          # redis:alpine on :6379 (dev + sandbox only)
├── .github/workflows/ci.yml    # lint + test on push/PR
├── src/
│   └── agentlatch/
│       ├── __init__.py         # public exports
│       ├── schemas.py          # ResponsePayload (Pydantic)
│       ├── queue.py            # Holding Tank — async Redis list ops
│       ├── memory.py           # ContextInjector ABC + per-session lock
│       ├── engine.py           # Delivery Engine — VAD timing + pop
│       ├── core.py             # AgentLatch facade
│       └── integrations/
│           ├── __init__.py
│           └── fastapi.py      # optional router (import-guarded)
├── tests/
│   ├── conftest.py             # fakeredis + clock fixtures
│   ├── test_schemas.py
│   ├── test_queue.py
│   ├── test_engine.py          # DAMP timing edge-cases
│   └── test_api.py             # FastAPI TestClient (needs [fastapi] extra)
└── sandbox/                    # ISOLATED — own venv & requirements, NOT packaged
├── README.md
├── edge_pipecat/           # Pipecat WebRTC app + FrameProcessor
│   └── requirements.txt
└── backend_langgraph/      # LangGraph StateGraph that fires the webhook
    └── requirements.txt
```

**Architectural boundary:** `pipecat-ai`, `langchain`, `langgraph` appear **only**
under `/sandbox`, each with its own environment. They never touch the core
`pyproject.toml`. `fastapi` appears in core only as an **optional extra**.

---

## 6. Code style

- **uv-managed**, `src/` layout, PEP 621 `pyproject.toml`.
- **Async-first**: all I/O is `async`; use `redis.asyncio`.
- **Fully type-hinted**; public inputs validated with Pydantic v2.
- **Ruff** for lint + format; **mypy** (or pyright) clean on `src/`.
- **Prefer the boring solution.** Redis lists, not Kafka/RabbitMQ. Standard
library `datetime`/`time`, not a scheduling library.
- Small, single-responsibility modules; the facade (`core.py`) is the only thing
that wires modules together.
- Comments explain *why* (especially the no-sleep timing constraint), not *what*.

---

## 7. Testing strategy

- **The Beyoncé Rule:** no module merges without tests.
- **Test isolation:** unit tests must **not** require a live Redis. Use
`fakeredis` (async) for queue/engine; the FastAPI tests use `TestClient`.
- **Deterministic time:** timing tests inject the clock callable and advance it
manually — **never `sleep`** in tests either. (`fakeredis` + injected `now`.)
- **DAMP over DRY in tests:** `test_engine.py` duplicates setup so each timing
edge-case reads like a spec to a human (e.g. *user starts speaking exactly as
the queue would pop*, *silence at 1.999s vs 2.001s*, *empty queue during
silence*).

### Mandatory quality gates (verify before proposing a commit)
1. **Receiver:** invalid JSON / missing field → **HTTP 422**; valid payload →
**HTTP 202**.
2. **Delivery Engine:** `get_next_message` returns **`None`** when
`is_user_speaking is True`, **and** when `now - last_speech < 2.0s`. It returns
a payload only when silence ≥ threshold *and* the queue is non-empty.
3. **Holding Tank:** FIFO ordering preserved; TTL set on write.
4. **Context Injector:** `inject_context` is awaited under the lock before the
payload is returned, and only when `silent_context_update` is present.

---

## 8. End-to-end sandbox (manual verification)

The sandbox proves VAD timing + Redis queueing in a live environment. It is
**strictly isolated** from core.

- `docker-compose.yml` at root runs `redis:alpine` on `6379`.
- **Sandbox 1 — Fast Edge (`/sandbox/edge_pipecat/`):** a Pipecat WebRTC app
(`pipecat init`). A custom `FrameProcessor.process_frame` loop polls
`latch.get_next_message(...)`; when a payload is returned it yields a
`TextFrame(text=payload.text_to_speak)` downstream. **Never** feed a raw string
into the pipeline.
- **Sandbox 2 — Deep Backend (`/sandbox/backend_langgraph/`):** a LangGraph
`StateGraph` with one node that does `asyncio.sleep(10)` (simulated heavy
compute) then `httpx` POSTs the webhook to the Receiver.
- **Final verification gate — the Interruption Test (human-run):**
1. Start the Pipecat session; speak one long continuous sentence.
2. Trigger the LangGraph backend mid-sentence so the webhook fires.
3. **Pass:** AgentLatch holds the message in Redis and injects the TTS audio
 **only** after the human pauses for > 2.0s.

---

## 9. Boundaries

### Always
- Write tests before merging a module (fakeredis, no live Redis in unit tests).
- Use real `redis.asyncio` calls for the queue and timestamp.
- Use timestamp diffing for silence; keep the clock injectable for tests.
- Strongly type and validate every public input with Pydantic.
- Keep core dependencies to `redis-py` + `pydantic` (+ `fastapi` only as extra).
- Implement and verify **one module at a time**; do not start `engine.py` until
`queue.py` has full coverage.

### Ask first
- Adding **any** new runtime dependency to core `pyproject.toml`.
- Changing a public signature: `ResponsePayload` fields, `get_next_message`,
`enqueue`, the route path, or return types.
- Changing the 2.0s default, the Redis key naming scheme, or FIFO semantics.
- Anything resembling a message broker beyond Redis lists.

### Never
- Add `pipecat-ai` / `langchain` / `langgraph` to core deps (sandbox only);
never make `fastapi` a hard core dependency.
- Mock the Redis queue with a Python `dict`.
- Use `asyncio.sleep()` to evaluate the silence window.
- Feed a raw string into the Pipecat pipeline (always wrap in `TextFrame`).
- Mutate LLM memory without the idle lock/guard.
- Build a voice framework or an LLM router (scope discipline).

---

## 10. Execution plan

Build in vertical slices, verifying each before the next:

1. **Slice 1 — Receiver + Holding Tank:** `schemas.py`, `queue.py`, `core.enqueue`,
and the optional FastAPI router, with tests for the 202/422 gates and FIFO.
2. **Slice 2 — Delivery Engine:** `engine.py` timestamp logic + DAMP timing tests.
3. **Slice 3 — Context Injector:** `memory.py` ABC + lock + injection ordering.
4. **Slice 4 — Sandbox:** docker-compose, Pipecat edge, LangGraph backend,
manual Interruption Test.

Each slice is broken into ordered, acceptance-tested tasks with `/slice-plan` when
it is reached (the repo-local planner — it cross-checks the plan with a second
model before human sign-off); current position and the next step live in
[`tasks/todo.md`](./tasks/todo.md) and [`HANDOFF.md`](./HANDOFF.md).