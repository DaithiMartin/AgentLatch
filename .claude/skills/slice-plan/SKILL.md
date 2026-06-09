---
name: slice-plan
description: >-
  Break the next vertical slice into small, verifiable tasks for THIS repo's dev-loop — and
  adversarially cross-check the plan before a human sees it. Reads SPEC.md (esp. §9 Boundaries and
  §10 execution plan) + the prior slices, maps the dependency graph, slices work vertically, writes
  the architecture to tasks/plan.md and the dependency-ordered tasks (each with Acceptance / Verify /
  Depends on / Do) to tasks/todo.md, adds CP-* checkpoints, then runs `cross-check mode=plan` so a
  second model argues the plan before human sign-off. Use this to plan a slice — "plan Slice 2",
  "/slice-plan", "break the delivery engine into tasks", "what are the next tasks". This is the
  repo-local fork of agent-skills:plan with the cross-check hook wired in; prefer it over
  /agent-skills:plan here. NOT for implementing a task (that's dev-loop) or one-off edits.
---

# slice-plan — vertical-slice planning with a built-in cross-check

Decompose a slice into tasks small enough to implement, test, and verify in one focused dev-loop
run, then **stress-test the plan with a second model before the human reviews it**. The output is two
documents the dev-loop consumes — `tasks/plan.md` (architecture, dependency graph, design notes) and
`tasks/todo.md` (the dependency-ordered task tracker) — not code.

*(Repo-local fork of `agent-skills:plan` / `planning-and-task-breakdown`, adapted to this repo's
single-plan/single-todo convention, uv tooling, SPEC §9 Boundaries, and dev-loop tracker states —
with an explicit `cross-check` gate added at the end of planning.)*

## When to use
- A slice from SPEC §10 is reached and needs breaking into tasks (e.g. Slice 2 — Delivery Engine).
- Work feels too large or the implementation order isn't obvious.

**When NOT to use:** implementing a task (use `dev-loop`), or a single obvious edit.

## The process (read-only until the plan docs are written)

### 1. Orient
- `SPEC.md` — source of truth. Read **§9 Boundaries** (Always / Ask-first / Never), **§10 execution
  plan** (which slice this is, its checkpoint), and the relevant architecture (§3) + public API (§4).
- `tasks/plan.md` + `tasks/todo.md` — how tasks are written here; `todo.md` checkboxes + `git`/PRs for
  what prior slices already built.
- **Carried-forward obligations** (deferred params, "TO ADD in Slice N" notes) — read them from `SPEC.md`,
  the durable home (the per-slice `plan.md` is rewritten each slice, so deferrals live in SPEC, not a status doc).
- The code the slice will touch and any ADRs (`docs/adr/`) it depends on.

**Do NOT write code.** The output is a plan.

### 2. Map the dependency graph
Map what depends on what; implementation order follows it bottom-up (build foundations first). Note
where the spec already fixes the order — e.g. SPEC §9 "do not start `engine.py` until `queue.py` has
full coverage."

### 3. Slice vertically
Each task delivers one complete, testable capability as far as its layer reaches — not a horizontal
"all the schema, then all the API" phase. Ship each task *with its tests* (the Beyoncé Rule, §7).

### 4. Write the two documents
- **`tasks/todo.md`** — one entry per task, in dependency order, each with:
  - **Acceptance** — specific, testable conditions.
  - **Verify** — the exact commands (`uv run pytest …` + any manual check) dev-loop will run cold.
    **Every Acceptance bullet must map to a Verify step that *exercises* it — not just asserts it.**
    A guarantee that holds only "by inspection" (e.g. "the receiver never polls", "a non-202 raises")
    needs a Verify command that would go **red** if it broke — a `grep` gate, an error-path assertion,
    a mutation the test catches — or the task ships an unchecked promise the reviewer has to catch
    after the fact (this gap shipped in Slice-4 T11 and T12). Before finalizing, walk each Acceptance
    bullet and point at the Verify line that fails when that bullet is violated.
  - **Depends on** — task IDs (or "none").
  - **Do** — the in-scope files (~5 max).
  - Tracker state `[ ]` pending → `[~]` PR open → `[x]` merged (dev-loop owns the transitions).
- **`tasks/plan.md`** — the slice Goal, the dependency graph, per-module design notes (the decisions
  behind the Acceptance criteria), and any resolved/open decisions. Out-of-scope items explicit.

### 5. Order and checkpoint
Order so dependencies are satisfied, each task leaves the system working, and high-risk tasks come
early. Insert **`CP-*` checkpoints** (human gates) after a capability is provable — note when a
checkpoint needs the prior PR *merged* first (green CI, a live round-trip).

### 6. Cross-check the plan ← the hook
Before any human sees the plan, run the second-model critic:

> **`cross-check mode=plan target=tasks/plan.md rounds=<rounds>`**

`<rounds>` is the critic's round cap — **default `3`**, overridable from this skill's args: invoke
`/slice-plan … rounds=5` (or pass `rounds=N` in the args) and slice-plan feeds that straight through to
`cross-check`. Raise it for a **wide or high-stakes slice** where you want the second model to keep
pushing past three rounds toward a confirming (CONVERGED) round; leave it at 3 for routine slices. Read
`rounds` from this skill's args, else default 3.

Pass it the grounding (SPEC §9 Boundaries + the repo seams + the slice's Acceptance from
`tasks/todo.md`). Then **fold every surviving BLOCKER/MAJOR into `tasks/plan.md` / `tasks/todo.md`**
(or, for any you reject, keep the critic's logged reason). Carry the cross-check report's verdict —
**`CONVERGED`, `ROUNDS_EXHAUSTED`, or `DEADLOCK`** — and the `reviewed with: <model>` line forward to
the human gate. If it **DEADLOCKs** (open disagreement) **or ends `ROUNDS_EXHAUSTED`** (the round budget
ran out with the final fixes unverified by the critic — *not* the same as agreement), surface that to
the human rather than burying it; they may want to raise `rounds` and re-run before signing off.

### 7. Present for human review (gate)
Show the human: the slice goal, the task list, the checkpoints, and the **cross-check verdict** (what
the argument changed, what was rejected and why, the model used). Plans are not "approved" until the
human signs off. dev-loop then picks up the topmost task.

## Task sizing
Prefer **S** (1–2 files) and **M** (3–5 files / one feature slice). Break anything **L+** down. Signs
a task is two tasks: you write "and" in the title; you can't state Acceptance in ≤3 bullets; it
touches two independent subsystems.

## Red flags
- Tasks without Acceptance or Verify steps.
- An Acceptance bullet with **no Verify step that exercises it** — an inspection-only guarantee.
- No CP-* checkpoints between capabilities.
- Dependency order ignored (e.g. starting `engine.py` before `queue.py` is covered).
- A task touching far more than ~5 files.
- The plan presented to the human **without** a cross-check pass.

## Verification (before handing the plan to the human)
- [ ] Every task has Acceptance + a runnable Verify block, and **every Acceptance bullet maps to a
      Verify step that exercises it** (no inspection-only guarantees).
- [ ] Dependencies identified and ordered; no task > ~5 files.
- [ ] CP-* checkpoints exist between capabilities.
- [ ] SPEC §9 Boundaries are respected by the plan.
- [ ] `cross-check mode=plan` ran (at the `rounds` cap — default 3, or the value passed in); surviving
      BLOCKER/MAJOR folded in; verdict (`CONVERGED` / `ROUNDS_EXHAUSTED` / `DEADLOCK`) + model recorded,
      and a non-CONVERGED verdict surfaced to the human (not relabelled as agreement).
