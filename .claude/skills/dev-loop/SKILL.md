---
name: dev-loop
description: >-
  Execute exactly ONE task from tasks/todo.md, end-to-end and supervised: orient against SPEC.md and
  tasks/plan.md, branch, build with TDD, simplify, verify in an INDEPENDENT sub-agent (reviewer ≠ author),
  open a GitHub PR, then update the tracker + HANDOFF.md and STOP at the next task or checkpoint. Use this
  whenever the user wants to advance the build — "do the next task", "run the loop", "start T3", "continue
  the build", "work the todo list", "pick up the next task" — even if they don't say "dev-loop". Any request
  to pick up and ship the next planned unit of work from tasks/todo.md should trigger this skill. Do NOT use
  it for one-off edits unrelated to the task list, or to implement a Checkpoint (those are human gates).
---

# dev-loop

A linear, **supervised** build loop: you build it yourself, but **Verify runs in an independent sub-agent
that didn't write the code** — reviewer ≠ author, so the review is genuinely cold. One task per run, then
stop. The point of the structure is to keep the tracker and `HANDOFF.md` honest, gate irreversible/outward
actions, and make verification fit infra/CI/data tasks, not just unit-test-shaped ones.

*(Adapted from the "Ralph v4" workflow. This repo uses a single `tasks/plan.md` + single `tasks/todo.md`,
so there are no per-slice task cards — a task's Acceptance/Verification/Dependencies live inline in
`tasks/todo.md`.)*

## Tracker convention
`tasks/todo.md` tasks move `[ ]` pending → `[~]` PR open (with link) → `[x]` merged. A task only becomes
`[x]` on **merge**; the *next* task's start flips it. A Checkpoint (`CP-*`) only becomes `[x]` on the
user's **explicit approval**. Keeping these accurate is what lets a resuming session trust the file.

## Before you start
- **Reconcile the tracker first.** In `tasks/todo.md`: flip any now-merged `[~]→[x]`, and mark any passed
  **Checkpoint** `[x]`. This prevents acting on stale state.
- **Name the todo item:** the topmost unchecked, dependency-ready task. **Skip Checkpoints** here — they're
  human gates handled separately (see **Checkpoints**).
- **Check readiness:** confirm the task's **Depends on** are merged and its inputs exist (an API key, a data
  file, a confirmed library call). Gather missing inputs now. If something's missing, **ask — don't guess.**
- **Remote check (first run):** the PR step needs a GitHub remote. If `git remote -v` is empty, surface it
  and ask the user to add one (`git remote add origin …`) or confirm a fallback before you start building.
- **No task-level plan cross-check (deliberately dropped).** Design was already audited by `slice-plan`
  (cross-model `mode=plan` at the slice level); test rigor is owned downstream by the **verify step**
  (mutation testing + `cross-check mode=diff`, step 5). A per-task `mode=plan` sat between the two
  re-auditing test rigor — measured over T5–T8 as mostly redundant with verify, and the most
  context-heavy step in the loop, so it's dropped for loop speed (a pre-1.0 trade). **Escape hatch:** if
  building a task surfaces a *genuinely new design or contract* the slice plan didn't settle, that's a
  signal the **slice plan has a gap** — stop and fix `tasks/plan.md` (re-run `slice-plan`), don't paper
  over it with a one-off task review.
- **The gate — present, then wait.** Show: (a) the task + dependency/inputs check, (b) your build
  plan, and (c) any **deviation** from the task as written or a decision needing input, each with
  rationale.
  **Then wait for the user's go-ahead before writing code.**  ← *supervised gate*

## 1. Orient (read-only, in order)
1. `SPEC.md` — source of truth; honor its **Boundaries** (Always / Ask-first / Never).
2. `tasks/plan.md` — the **phase** this task belongs to and its checkpoint.
3. The task's **entry in `tasks/todo.md`** — its **Acceptance**, **Verify** steps, **Depends on**, and **Do** scope.
4. Any **ADRs** it cites and the **code** it touches.
5. `HANDOFF.md` — current durable project state (what exists, what's decided).

## 2. Branch
Branch off `main` with a descriptive name drawn from the task — e.g. `feat/scaffold`,
`feat/chat-endpoint`, `feat/memory-seam`.

## 3. Build (TDD)
- Use `agent-skills:build` (incremental + TDD). **Add the build-phase skill that fits the task type:**

  | Task involves… | Add skill |
  |---|---|
  | UI / the chat window | `agent-skills:frontend-ui-engineering` |
  | endpoints / module interfaces / the seams | `agent-skills:api-and-interface-design` |
  | an unfamiliar library (e.g. `google-genai`) | `agent-skills:source-driven-development` |
  | high-stakes (security, auth, irreversible, the crisis path) | `agent-skills:doubt-driven-development` |

- Implement to the **Acceptance criteria**, stay in scope (~5 files). Use `uv` for everything
  (`uv run pytest`, `uv add …`). Stage the change and **commit with the `commit-message` skill** — it
  writes a Conventional-Commit (50/72) from the staged diff and makes the commit. Use it at *every* commit
  step in this loop. Keep the `Co-Authored-By` trailer crediting the assistant.
- **Confirm before any irreversible or outward-facing action** mid-build — a `docker push`, a deploy, a
  bucket upload, anything that leaves the machine. Show the plan/diff first and wait. (The PR in step 6 is
  the *expected* outward action; this rule is about surprises beyond it.)

## 4. Simplify
Run `agent-skills:code-simplify` on the diff — clarity only, behavior-preserving; commit. A no-op on a
trivial change is fine.

## 5. Verify — delegate to an INDEPENDENT sub-agent
Spawn a fresh-context review agent with the **Agent** tool, `subagent_type: agent-skills:code-reviewer`
(use `agent-skills:security-auditor` for auth / secrets / the crisis-guardrail task). The value is the cold
read: **don't leak your intended solution or conclusions — but DO give it the factual context it needs**
(what tools/auth it has, what already exists, and that it must NOT re-run irreversible steps). Briefing:

> "Task `<TX>` is committed on branch `<branch>`. Verify it against its entry in `tasks/todo.md` (`<TX>`):
> run that task's **Verify** block (`uv run pytest …` + any manual check), do a five-axis review
> (correctness, readability, architecture, security, performance), and confirm the SPEC §9 Boundaries hold
> (especially the seams: no `asyncio.sleep` to measure silence — timestamp diffing only; real
> `redis.asyncio`, never a Python `dict`; the clock stayed an injectable callable; `fastapi` stayed an
> optional extra, never a hard core dep). Return a verdict: **pass**, or **fail** with specific findings."

**Mutation-test subtle tasks (non-negotiable).** With no task-level `mode=plan`, mutation testing is now
the **primary** test-rigor check, not a supplement — so on any task with an ordering / atomicity /
concurrency / identity / timing guarantee, **require the reviewer to mutation-test**: mutate the
implementation to plausible-wrong variants (fire-and-forget instead of await; `truthy` instead of
`is not None`; lock the wrong region) and confirm a test **fails** for each. This is the check that
replaced the dropped plan review — do **not** let loop-speed pressure skip it.

**For infra / packaging tasks** (e.g. the Dockerfile) the Verify block may not be fully runnable cold or
pre-merge. Tell the agent what's checkable **now** (a local `docker build`, inspecting produced artifacts)
and record in the PR what only completes **after merge**. Don't have it re-run a push/deploy.

**Cross-model pass (defense in depth).** Alongside the independent Claude reviewer, run
**`cross-check mode=diff`** on the branch diff — a *different model family* catches what cross-context
alone can't (cross-MODEL ≠ cross-context). Default-on for high-stakes tasks; opt-in for routine ones
(the Claude reviewer is always-on). Put **both** verdicts — each with its `reviewed with: <model>` line —
in the PR body. A BLOCKER/MAJOR from *either* reviewer is a fail.

- **Pass** (both reviewers clear) → step 6.
- **Fail** → fix the **root cause** yourself (never skip or delete a failing test), re-commit, re-run the
  reviewer(s) that failed. Bounded: after ~2 failed rounds, **stop and surface** to the user.

## 6. PR
`git push -u origin <branch>`, then `gh pr create`. Put the verify agent's verdict in the body, plus any
post-merge checks still pending. Do **not** merge it yourself — the human merges (`gh pr merge` is denied
in `.claude/settings.json`). Hand-off-and-stop happens after step 7.

## 7. Mark progress → doc-freshness → stop
- `tasks/todo.md`: set the task to **`[~]` + PR link** (not `[x]` — that's on merge; the next task's start
  flips it). Tick the **Acceptance** items this PR satisfies; leave any that only complete post-merge, and say so.
- **Update `HANDOFF.md`** if the task changed durable state — a working endpoint, the memory format on disk,
  a settled decision. Keep HANDOFF a *pointer to durable facts* (defer live status to `todo.md`). Skipping
  this is what makes a resuming session start stale.
- **Run the `doc-freshness` skill** over the stateful markdown (`HANDOFF.md`, `CLAUDE.md`, `README.md`,
  `SPEC.md`, `docs/`, `tasks/`). It flags docs that now hard-code a fact owned elsewhere instead of pointing
  to its source — the silent-staleness trap. Apply its fixes. This is the end-of-loop honesty check on our
  stateful artifacts.
- Commit the tracker/doc updates (via `commit-message`) and push. Then **hand off the PR link and stop** —
  the user merges.

## Checkpoints (human gates)
Never implement a Checkpoint as a task. When the topmost item is a `CP-*`: gather its evidence — it may
require the prior PR to **merge** first (e.g. green CI, a live round-trip) — **present it**, await the
user's **explicit approval**, then mark it `[x]`. Then continue to the next task.

**Slice-completion test audit (run at each `CP-*`).** Because the loop has no per-task plan review, every
slice-closing checkpoint runs a dedicated **integration + mutation audit** over the slice's *whole* test
suite — distinct from, and bigger than, the per-task verify. Spawn a fresh `agent-skills:test-engineer`
(or `code-reviewer`) to check: does the suite cover the **cross-task** flow end-to-end (e.g.
enqueue → silence → inject → release as one path), and do mutations **across the slice's modules** (not
one task at a time) each fail a test? Fold any gap into a test **before** the checkpoint is approved.
This is where slice-level test-rigor debt is caught — per-task reviews never see the seams between tasks.

## Loop rules
- **One task, then stop. Halt at Checkpoints.**
- **Confirm before irreversible / outward-facing actions** beyond the expected PR.
- **Blocked or unsure → stop and surface.** Don't work around a missing input or an ambiguous card.

---

**Autonomous mode (opt-in, later):** drop the "wait for go-ahead" build gate and auto-pick the topmost
unchecked, dependency-ready, non-checkpoint task. **Keep** the irreversible-action confirmation, the SPEC
Boundaries, the Checkpoint halt, and the loop rules. Only switch to this when the user explicitly asks.