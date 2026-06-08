# HANDOFF — AgentLatch

Durable state for resuming work. Pointers, not duplicates:

- Live task status → [`tasks/todo.md`](./tasks/todo.md)
- Intent, boundaries, resolved decisions → [`SPEC.md`](./SPEC.md)
- Architecture, dependency graph, workflow → [`tasks/plan.md`](./tasks/plan.md)

## Current position

**Slice 1 COMPLETE** — T0–T4 merged, CP-A & CP-B approved; the library + HTTP
ingest path is built, tested, and live-smoke-verified. **Slice 2 (Delivery Engine)
is underway:** planned via `/slice-plan` (cross-checked, see `tasks/plan.md` §7), and
**T5 (`engine.py`) is in review — [PR #8](https://github.com/DaithiMartin/AgentLatch/pull/8).**
**Next: T6** (wire `get_next_message` into the facade), then CP-C. Per-task status in
`tasks/todo.md`.

## What exists

- Spec-driven docs: `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`.
- Package skeleton `agentlatch` (uv + hatchling, `src/` layout), MIT-licensed,
  dev tooling (pytest / ruff / mypy), smoke test, CI matrix (3.11/3.12), and a
  Conventional-Commits `commit-msg` hook.
- `ResponsePayload` payload contract (`agentlatch.schemas`, pydantic v2,
  `extra="forbid"`, non-empty whitespace-stripped strings).
- `HoldingTank` per-session Redis FIFO queue (`agentlatch.queue`:
  `push`/`pop`/`length`, RPUSH/LPOP, TTL refreshed on write).
- `AgentLatch` facade (`agentlatch.core`): `enqueue`, exactly-one Redis source,
  ownership-aware `aclose`; `AgentLatch`/`ResponsePayload` exported at top level.
- Optional FastAPI receiver (`agentlatch.integrations.fastapi.create_router`):
  `POST /api/v1/queue_response` → 202/422, import-guarded behind the extra.

## Infra / repo facts

- GitHub: <https://github.com/DaithiMartin/AgentLatch> (public).
- Commit format enforced by `.githooks/commit-msg`; contributors enable it with
  `git config core.hooksPath .githooks` (see `CONTRIBUTING.md`).
- Redis key scheme (owned by SPEC §3.2 / §3.4): `agentlatch:queue:{session_id}`
  (list, live since Slice 1); `agentlatch:last_speech:{session_id}` (Delivery
  Engine — T5, in review · [PR #8](https://github.com/DaithiMartin/AgentLatch/pull/8)).

## Deferred (carried-forward obligations)

- **Slice 3 — Context Injector:** add the `context_injector` param to
  `AgentLatch.__init__` (`ContextInjector | None`, keyword-only ⇒ non-breaking);
  deliberately omitted from Slice 1 to avoid freezing a placeholder type. See
  SPEC §3.3.
