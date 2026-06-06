# Plan — Slice 1: The Receiver + The Holding Tank

> Source of truth: [`SPEC.md`](../SPEC.md). Per-task status, Acceptance, and Verify
> steps live in [`todo.md`](./todo.md) (the dev-loop tracker). This file owns the
> **architecture, dependency graph, design notes, and workflow conventions**.

**Created:** 2026-06-06 · **Status:** awaiting human review

---

## 1. Goal of this slice

Deliver the complete **ingest path** — from an HTTP webhook (or a direct library
call) to a payload durably parked in a Redis list, with FIFO ordering and a TTL:

```python
latch = AgentLatch(redis_url="redis://localhost:6379")
await latch.enqueue(ResponsePayload(session_id="s1", text_to_speak="hi"))
# …and POST /api/v1/queue_response does the same → 202 valid / 422 invalid
```

**Out of scope (later slices):** `engine.py` (`get_next_message`, `last_speech`
timing), `memory.py` (`ContextInjector`), `/sandbox`. The `AgentLatch` constructor
accepts `silence_threshold` / `context_injector` now — kept inert — so the public
signature does not churn when Slice 2/3 land.

---

## 2. Dependency graph

```
T0  Scaffolding (uv + hatchling, MIT, src/ layout, tooling, HANDOFF.md)
 │
 ▼
T1  schemas.py  ── ResponsePayload (Pydantic v2)
 │
 ▼
T2  queue.py    ── HoldingTank: push / pop / length  (RPUSH/LPOP + TTL)
 │                 (imports ResponsePayload for (de)serialization)
 ▼
T3  core.py     ── AgentLatch facade: __init__ + enqueue()
 │
 ╠══ CP-A ── library-only ingest path proven; freeze public API (human gate)
 │
 ▼
T4  integrations/fastapi.py ── create_router(latch): POST /queue_response (202/422)
 │
 ╚══ CP-B ── Slice 1 complete; HTTP ingest path proven (human gate)
```

Linear by nature — each module consumes the one above it. `schemas.py` is the
shared contract; `queue.py` (de)serializes it; `core.py` wires the tank; the
FastAPI router calls `core.enqueue`. No meaningful intra-slice parallelism.

**Vertical-slice note:** modules layer, but each task ships *with its tests* and
proves a complete capability as far as its layer reaches — T3 proves the full
library ingest path (CP-A); T4 proves the full HTTP path (CP-B). No "code now,
test later" horizontal phase.

---

## 3. Design notes (per module)

Acceptance criteria and Verify commands for each task are in [`todo.md`](./todo.md).
These are the design decisions behind them.

### T0 — scaffolding
- `pyproject.toml`: PEP 621, `build-backend = hatchling`, `requires-python = ">=3.11"`;
  `dependencies = ["redis>=5.0", "pydantic>=2.6"]`; `[project.optional-dependencies]
  fastapi = ["fastapi>=0.110"]`; dev group (uv) `pytest pytest-asyncio fakeredis>=2.20
  httpx ruff mypy`; `[tool.pytest.ini_options] asyncio_mode = "auto"`;
  `[tool.hatch.build.targets.wheel] packages = ["src/agentlatch"]`; ruff + mypy config.
- `HANDOFF.md`: seed the durable-state doc the dev-loop reads/writes each run.

### T1 — `ResponsePayload` (`schemas.py`)
- Pydantic v2, `model_config = ConfigDict(extra="forbid")` (free-form data belongs
  in `silent_context_update`, so unknown top-level keys are a contract violation).
- `session_id` / `text_to_speak`:
  `Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]`.
- `silent_context_update: dict[str, Any] | None = None`.

### T2 — Holding Tank (`queue.py`)
- `HoldingTank(redis, session_ttl)`; key `agentlatch:queue:{session_id}`.
- `push`: pipeline `RPUSH key model_dump_json()` then `EXPIRE key ttl` (TTL refreshed
  every write). `pop`: `LPOP` → `model_validate_json` | `None`. `length`: `LLEN`.
- Tests use `fakeredis.aioredis.FakeRedis` — **no live Redis** (SPEC §7 isolation).

### T3 — `AgentLatch` facade (`core.py`)
- `__init__(self, *, redis_url=None, redis_client=None, silence_threshold=2.0,
  session_ttl=3600, context_injector=None)`. Exactly one of `redis_url`/`redis_client`
  (the client seam injects fakeredis in tests); build via `redis.asyncio.from_url`;
  validate positive numerics — else `ValueError`.
- `enqueue(payload: ResponsePayload)` → `tank.push` (model-only, no `dict` overload).
- `aclose()` closes the client. `__init__.py` exports `AgentLatch`, `ResponsePayload`
  (`ContextInjector` deferred to Slice 3).

### T4 — FastAPI receiver (`integrations/fastapi.py`)
- `create_router(latch) -> APIRouter`, `POST /api/v1/queue_response`, body
  `ResponsePayload` (FastAPI auto-422 on validation failure); handler awaits
  `latch.enqueue` and returns **202**. Import-guarded with a clear
  `pip install agentlatch[fastapi]` message.

---

## 4. Workflow conventions (dev-loop)

We follow the repo's `dev-loop` skill: **one task per run, then stop; halt at
checkpoints.** Per task:

1. **Gate** — present the task + dependency/inputs check + build plan + any
   deviation, then **wait** for go-ahead before writing code.
2. **Branch** off `main`, descriptive name (`feat/scaffold`, `feat/schemas`,
   `feat/holding-tank`, `feat/latch-facade`, `feat/fastapi-receiver`).
3. **Build (TDD)** with `agent-skills:build`; pair the build-phase skill that fits —
   `agent-skills:api-and-interface-design` for the public contracts (T1–T4),
   `agent-skills:source-driven-development` where exact library APIs matter
   (Pydantic v2 constraints in T1; redis async pipeline/TTL in T2). Stay in scope
   (~5 files). Commit via the **`commit-message`** skill (Conventional Commits,
   50/72, `Co-Authored-By` trailer).
4. **Simplify** the diff with `agent-skills:code-simplify`; commit.
5. **Verify (cold)** — spawn an independent `agent-skills:code-reviewer` sub-agent
   (reviewer ≠ author): run the task's Verify block, five-axis review, confirm
   SPEC §9 Boundaries hold. Fail → fix root cause, re-verify; surface after ~2 rounds.
6. **PR** — `git push -u origin <branch>`, `gh pr create` with the verdict in the
   body. **Never merge** — the user merges every PR.
7. **Track + docs** — set the task `[~]` + PR link in `todo.md`; update `HANDOFF.md`
   if durable state changed; run `doc-freshness`; commit; hand off the PR link and stop.

**Tracker states** (`todo.md`): `[ ]`→`[~]` (PR open)→`[x]` (merged; flipped at next
task start). `CP-*` → `[x]` only on explicit approval. **Confirm before any
irreversible/outward action beyond the expected PR.**

---

## 5. Decisions
**Resolved:** `extra="forbid"` on `ResponsePayload` (strict) · `enqueue` accepts
`ResponsePayload` only · MIT holder = Daithi Martin · uv + hatchling, Py 3.11+ · MIT.

**Open (see [`todo.md`](./todo.md)):** whether T0 installs a tracked
`.githooks/commit-msg` + `core.hooksPath`, or we rely on the `commit-message`
skill to write compliant messages without an enforcing hook.

## 6. Next step
Resolve the `commit-msg` hook question, then run `dev-loop` on **T0** (it presents
the T0 gate and waits for go-ahead before writing code).
