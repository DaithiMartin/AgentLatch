# HANDOFF — AgentLatch

Durable state for resuming work. Pointers, not duplicates:

- Live task status → [`tasks/todo.md`](./tasks/todo.md)
- Intent, boundaries, resolved decisions → [`SPEC.md`](./SPEC.md)
- Architecture, dependency graph, workflow → [`tasks/plan.md`](./tasks/plan.md)

## Current position

Slice 1 (Receiver + Holding Tank). T0 merged; **T1 in review** —
[PR #2](https://github.com/DaithiMartin/AgentLatch/pull/2).
Per-task status in `tasks/todo.md`.

## What exists

- Spec-driven docs: `SPEC.md`, `tasks/plan.md`, `tasks/todo.md`.
- Package skeleton `agentlatch` (uv + hatchling, `src/` layout), MIT-licensed,
  dev tooling (pytest / ruff / mypy), smoke test, CI matrix (3.11/3.12), and a
  Conventional-Commits `commit-msg` hook.
- `ResponsePayload` payload contract (`agentlatch.schemas`, pydantic v2,
  `extra="forbid"`, non-empty whitespace-stripped strings).

## Infra / repo facts

- GitHub: <https://github.com/DaithiMartin/AgentLatch> (public).
- Commit format enforced by `.githooks/commit-msg`; contributors enable it with
  `git config core.hooksPath .githooks` (see `CONTRIBUTING.md`).
- Planned Redis keys: `agentlatch:queue:{session_id}` (list);
  `agentlatch:last_speech:{session_id}` (Slice 2).
