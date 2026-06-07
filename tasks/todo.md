# TODO — Slice 1: Receiver + Holding Tank

Status tracker for the `dev-loop`. Architecture, DAG, and design notes live in
[`plan.md`](./plan.md); intent + boundaries in [`SPEC.md`](../SPEC.md).

> **Slice 1 is COMPLETE ✅** — all tasks (T0–T4) merged and both checkpoints
> (CP-A, CP-B) approved. Next: `/plan` Slice 2 — the Delivery Engine.

**Status legend:** `[ ]` pending · `[~]` PR open (link) · `[x]` merged.
A task flips to `[x]` only on **merge** (the next task's start flips it).
A `CP-*` checkpoint flips to `[x]` only on the user's **explicit approval**.

---

### T0 — Scaffolding & tooling  `[x]` — [PR #1](https://github.com/DaithiMartin/AgentLatch/pull/1) (merged)
- **Depends on:** —
- **Do:** `pyproject.toml` (uv + hatchling, `requires-python >=3.11`, deps `redis>=5.0`
  + `pydantic>=2.6`, `[fastapi]` extra, dev group), `LICENSE` (MIT 2026 Daithi Martin),
  `README.md` stub, `.gitignore`, `src/agentlatch/__init__.py`,
  `src/agentlatch/integrations/__init__.py`, `tests/__init__.py`, seed `HANDOFF.md`;
  `.githooks/commit-msg` + `core.hooksPath`; CI matrix (`.github/workflows/ci.yml`, 3.11/3.12).
- **Acceptance:** `uv sync --extra fastapi` resolves; `import agentlatch` works;
  `ruff` + `pytest` run clean (0 tests OK); `LICENSE` is valid MIT (2026, Daithi Martin).
- **Verify:** `uv sync --extra fastapi && uv run python -c "import agentlatch" && uv run ruff check . && uv run pytest`

### T1 — `schemas.py` · `ResponsePayload`  `[x]` — [PR #2](https://github.com/DaithiMartin/AgentLatch/pull/2) (merged)
- **Depends on:** T0
- **Do:** Pydantic v2 model, `extra="forbid"`; `session_id` + `text_to_speak`
  (non-empty, whitespace-stripped); `silent_context_update: dict[str, Any] | None`.
  Tests in `tests/test_schemas.py`.
- **Acceptance:** valid (±optional) constructs; `ValidationError` for missing field,
  empty/whitespace string, wrong type, and unknown extra key; JSON round-trip is lossless.
- **Verify:** `uv run pytest tests/test_schemas.py`

### T2 — `queue.py` · HoldingTank  `[x]` — [PR #3](https://github.com/DaithiMartin/AgentLatch/pull/3) (merged)
- **Depends on:** T1
- **Do:** `HoldingTank(redis, session_ttl)` with `push` (RPUSH+EXPIRE pipeline),
  `pop` (LPOP→`ResponsePayload`|None), `length` (LLEN); key `agentlatch:queue:{session_id}`.
  `tests/conftest.py` (fakeredis async + `tank` fixtures), `tests/test_queue.py`.
- **Acceptance:** FIFO (A,B,C→A,B,C); empty pop→`None`; TTL set + refreshed on write
  (`0 < TTL ≤ session_ttl`); `length` correct; `silent_context_update` survives round-trip.
- **Verify:** `uv run pytest tests/test_queue.py`

### T3 — `core.py` · `AgentLatch` facade  `[x]` — [PR #4](https://github.com/DaithiMartin/AgentLatch/pull/4) (merged)
- **Depends on:** T2
- **Do:** `AgentLatch(*, redis_url|redis_client, silence_threshold=2.0, session_ttl=3600)`
  (exactly-one client source; positive numerics); `enqueue(payload)` → tank.push
  (ResponsePayload only); ownership-aware `aclose()`; export `AgentLatch`, `ResponsePayload`
  from `__init__.py`. (`context_injector` deferred to Slice 3.) `tests/test_core.py`.
- **Acceptance:** `enqueue` persists (fakeredis length 1, pop returns it);
  `ValueError` on both/neither client source and on non-positive `silence_threshold`/`session_ttl`;
  package exports resolve.
- **Verify:** `uv run pytest tests/test_core.py`

### CP-A — library ingest path (human gate)  `[x]` — approved (ingest API frozen)
- **Depends on:** T3 merged
- **Evidence to present:** `uv run ruff check . && uv run mypy src && uv run pytest` all green;
  public API shape (`AgentLatch`, `ResponsePayload`) reads well before it's frozen behind HTTP.
- **Approval:** await explicit user OK, then mark `[x]`.

### T4 — `integrations/fastapi.py` · receiver  `[x]` — [PR #5](https://github.com/DaithiMartin/AgentLatch/pull/5) (merged)
- **Depends on:** T3 merged, CP-A approved
- **Do:** `create_router(latch)` → `POST /api/v1/queue_response` (body `ResponsePayload`),
  202 on valid; import-guarded with a friendly `pip install agentlatch[fastapi]` error.
  `tests/test_api.py`.
- **Acceptance:** valid→202 + enqueued (fakeredis length 1); 422 for missing field,
  empty `session_id`, unknown extra key, non-JSON; router mounts into a bare `FastAPI()` app.
- **Verify:** `uv run pytest tests/test_api.py`

### CP-B — Slice 1 complete (human gate)  `[x]` — approved (live real-Redis smoke passed)
- **Depends on:** T4 merged
- **Evidence to present:** full suite + `ruff` + `mypy src` green; both ingest paths proven;
  mandatory gates hold (422 invalid / 202 valid · FIFO · TTL on write). Optional: manual
  `curl` smoke against a local Redis.
- **Approval:** await explicit user OK, then mark `[x]`.

---

## Open decisions
- [x] **`commit-msg` hook:** added tracked `.githooks/commit-msg` + `core.hooksPath .githooks` (option A).
- [x] `extra="forbid"` on `ResponsePayload` — **yes** (strict).
- [x] `enqueue` accepts `ResponsePayload` only (no `dict` overload) — **yes**.
- [x] MIT holder = **Daithi Martin**.
