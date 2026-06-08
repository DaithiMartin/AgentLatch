# HANDOFF — AgentLatch

Durable state for resuming work. Pointers, not duplicates:

- Live task status → [`tasks/todo.md`](./tasks/todo.md)
- Intent, boundaries, resolved decisions → [`SPEC.md`](./SPEC.md)
- Architecture, dependency graph, workflow → [`tasks/plan.md`](./tasks/plan.md)

## Current position

**Slices 1 & 2 COMPLETE.** Slice 1 (Receiver + Holding Tank): T0–T4 merged,
CP-A & CP-B approved. Slice 2 (Delivery Engine): T5 ([PR #8](https://github.com/DaithiMartin/AgentLatch/pull/8))
+ T6 ([PR #9](https://github.com/DaithiMartin/AgentLatch/pull/9)) merged, **CP-C approved
2026-06-08**. The full library round-trip (ingest → hold → release on ≥ 2.0s silence,
FIFO) is built, unit-tested, and **live-smoke-verified against a real Redis** with the
real wall clock. **Slice 3 (Context Injector) is underway:** planned via `/slice-plan`
(cross-checked 3 rounds, see `tasks/plan.md` §7) and signed off; **T7 (`memory.py`) is in
review — [PR #10](https://github.com/DaithiMartin/AgentLatch/pull/10).** **Next: T8** (engine
inject-before-return under the lock), then T9, then CP-D. Per-task status in `tasks/todo.md`.

## What exists

- Spec-driven docs: `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`.
- Package skeleton `agentlatch` (uv + hatchling, `src/` layout), MIT-licensed,
  dev tooling (pytest / ruff / mypy), smoke test, CI matrix (3.11/3.12), and a
  Conventional-Commits `commit-msg` hook.
- `ResponsePayload` payload contract (`agentlatch.schemas`, pydantic v2,
  `extra="forbid"`, non-empty whitespace-stripped strings).
- `HoldingTank` per-session Redis FIFO queue (`agentlatch.queue`:
  `push`/`pop`/`length`, RPUSH/LPOP, TTL refreshed on write).
- `DeliveryEngine` VAD-timed release (`agentlatch.engine`, merged T5):
  records `last_speech` on speech, pops after `silence ≥ threshold` via
  timestamp diffing on an injectable clock (never `asyncio.sleep`); fails
  safe on clock skew / corrupt timestamps (reseed + hold, no early pop).
- `AgentLatch` facade (`agentlatch.core`): `enqueue`, exactly-one Redis source,
  ownership-aware `aclose`; `AgentLatch`/`ResponsePayload` exported at top level.
  Delivery surface `get_next_message` (strict `StrictBool` + normalized
  `session_id`, injectable `now` clock seam) — merged (T6,
  [PR #9](https://github.com/DaithiMartin/AgentLatch/pull/9)).
- Optional FastAPI receiver (`agentlatch.integrations.fastapi.create_router`):
  `POST /api/v1/queue_response` → 202/422, import-guarded behind the extra.
- Context Injector seam (`agentlatch.memory`, T7 — in review,
  [PR #10](https://github.com/DaithiMartin/AgentLatch/pull/10)): `ContextInjector`
  ABC (exported; a sync `inject_context` override is rejected at class-definition
  via `__init_subclass__`) + internal `SessionLocks`, a `WeakValueDictionary`
  per-session `asyncio.Lock` registry that self-cleans. Wired by T8/T9.

## Infra / repo facts

- GitHub: <https://github.com/DaithiMartin/AgentLatch> (public).
- Commit format enforced by `.githooks/commit-msg`; contributors enable it with
  `git config core.hooksPath .githooks` (see `CONTRIBUTING.md`).
- Redis key scheme (owned by SPEC §3.2 / §3.4): `agentlatch:queue:{session_id}`
  (list, live since Slice 1); `agentlatch:last_speech:{session_id}` (Delivery
  Engine — live since T5, [PR #8](https://github.com/DaithiMartin/AgentLatch/pull/8),
  merged).

## Deferred (carried-forward obligations)

- **Slice 3 — Context Injector:** add the `context_injector` param to
  `AgentLatch.__init__` (`ContextInjector | None`, keyword-only ⇒ non-breaking);
  deliberately omitted from Slice 1 to avoid freezing a placeholder type. See
  SPEC §3.3.
