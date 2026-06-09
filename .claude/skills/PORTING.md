# Porting this skills pack to another project

This is the adaptation guide for the repo-local skills in `.claude/skills/`
(`dev-loop`, `cross-check`, `slice-plan`, `commit-message`, `doc-freshness`).
They were built and refined against **AgentLatch** (a `uv`-managed Python library
with a `tasks/plan.md` + `tasks/todo.md` tracker and a `SPEC.md` source of truth),
so they carry project-specific bindings alongside a portable methodology.

**Who runs this:** a session living **inside the destination repo** — that's the
right place to adapt, because the specialization needs the destination's real
structure, toolchain, test framework, and invariants as ground truth. This guide
carries the *rationale* a cold session wouldn't otherwise have, so you re-specialize
the bindings without stripping load-bearing subtleties.

**The mental model.** Each skill = **portable methodology** (keep verbatim) +
**project bindings** (re-specialize against the target) + **war-story rationale**
(genericize: keep the lesson, drop the AgentLatch anecdote). The methodology is the
point; the bindings are what give it teeth in *this* repo and must be re-pointed at
the destination's reality.

---

## Step 0 — Port the harness, not just the markdown

The skills lean on surrounding wiring. Markdown copied without it misbehaves
*silently*. Recreate, at the destination:

- [ ] **The `agent-skills:*` marketplace pack.** The repo-local skills invoke these
      10 — install/enable the same pack (or re-point the references):
      `build`, `code-reviewer`, `code-simplify`, `security-auditor`, `test-engineer`,
      `frontend-ui-engineering`, `api-and-interface-design`, `source-driven-development`,
      `doubt-driven-development`, `plan`. Without them, `dev-loop`'s build/verify steps
      and `cross-check`'s fallback have nothing to call.
- [ ] **`commit-msg` git hook** (`.githooks/commit-msg`) — enforces the 50/72
      Conventional-Commit format `commit-message` writes to. Install it at the
      destination (`git config core.hooksPath .githooks`, or your hook manager).
- [ ] **`.claude/settings.json` permissions.** Note AgentLatch's live in
      `settings.local.json` (personal, usually gitignored), so they **don't travel
      with a copy** — recreate them. The load-bearing one: **`gh pr merge` denied**
      (the human merges every PR; dev-loop hands off and stops). Plus any read-only
      Bash/`gh` allowlist you want to cut permission prompts.
- [ ] **The `cross-check` critic backend.** Needs the `codex` CLI (≥0.130) +
      ChatGPT/OpenAI auth, pinned to a non-`-codex` model slug (`gpt-5.5`). If the
      destination has no codex, `cross-check` still works via its **degradation path**
      (a fresh `agent-skills:code-reviewer` sub-agent) — but it's then cross-*context*,
      not cross-*model*; decide whether that's acceptable or wire a different critic.
- [ ] **A git repo with a GitHub remote + `gh` CLI** — `dev-loop`'s PR step needs it.

---

## Step 1 — Re-specialize the cross-cutting bindings

These appear in **multiple** skills; fix them once, consistently.

### The seam list (biggest re-specialization)
AgentLatch's critical invariants are quoted verbatim in **both** `dev-loop` (step 5
reviewer briefing) and `cross-check` (grounding): *"never `asyncio.sleep` to measure
silence — timestamp diffing only; real `redis.asyncio`, never a Python `dict`; the
clock stays an injectable callable; `fastapi` is an optional extra, never a hard core
dep; mutate memory only under the idle lock."*

This is what makes the reviewers *catch things* — a generic "review for quality" is
toothless by comparison. **Replace it with the destination's own hard invariants** —
derive them from the target's equivalent of a "Never" boundary list (its `SPEC`/ADRs/
README). Examples by domain: "all money is integer cents, never float"; "all DB
writes go through the repository layer, never raw SQL in handlers"; "no network calls
in the render path"; "tenant_id on every query." If the destination has no written
invariant list, **that's worth creating first** — the skills are far weaker without it.

### Tracker + source-of-truth paths
`tasks/todo.md`, `tasks/plan.md`, `SPEC.md`, `docs/adr/`, the `[ ]`→`[~]`→`[x]`
states, the single-plan/single-todo convention. Keep the *model* (a durable
source-of-truth doc + a live status tracker + ordered tasks with Acceptance/Verify);
re-point the *paths/format* to whatever the destination uses (could be the same files,
or GitHub issues/Projects, or a different layout). If you change the tracker shape,
update every skill that reads it (`dev-loop`, `slice-plan`).

### Toolchain commands
`uv run pytest`, `uv add`, `tests/README.md`, `pytest-asyncio`, `fakeredis`,
`from tests.conftest import` → the destination's package manager, test runner, and
test-double conventions (`poetry`/`npm`/`cargo`/`go test`, etc.).

### Critic model config
`gpt-5.5`, `effort=high`, the `-codex`-slug-400s-under-ChatGPT-auth warning, codex
version — keep if the destination uses codex; otherwise re-point to its critic.

---

## Step 2 — Per-skill adaptation

### `cross-check`
- **Keep verbatim (the methodology):** fresh `codex exec` session **per round** (never
  `resume`); the **target-lock** (review only the files in the diff); the
  **in-target-only verdict rule** (out-of-target findings are non-gating observations);
  the three honest verdicts (**CONVERGED / ROUNDS_EXHAUSTED / DEADLOCK**); always close
  stdin (`</dev/null`); model pinning + surfacing `reviewed with:`; the degradation
  fallback. **These are hard-won — see "Do not prune" below.**
- **Re-specialize:** the grounding's seam list; the `tasks/plan.md` / `SPEC §1–§2/§9` /
  `docs/adr/` pointers; the model/auth config.
- **Genericize:** "observed on T10", "this bit us on T7" → state the lesson
  ("a resumed critic re-flags already-fixed code — anchoring") without the anecdote.

### `dev-loop`
- **Keep verbatim:** the supervised loop shape (orient → branch → build → simplify →
  **verify in an INDEPENDENT sub-agent, reviewer ≠ author** → PR → mark → stop); the
  build gate (present, then wait); the **mutation-testing mandate** for
  ordering/atomicity/concurrency/identity/timing guarantees; **Acceptance↔Verify
  coverage** (no inspection-only guarantees); confirm-before-irreversible; the
  Checkpoint halt + the doc-freshness/slice-audit batched at each `CP-*`.
- **Re-specialize:** the seam list in the step-5 briefing; `uv` commands; tracker
  paths; the build-phase skill table (keep the *idea* — pick the right build skill per
  task type — re-point the rows/examples to the destination's stack); `tests/README.md`.
- **Genericize:** "Ralph v4", "T5–T8 measured", `Slice N`/`CP-*` naming, the
  no-task-level-plan-cross-check rationale (keep the principle: slice-level plan review
  + per-task verify, don't double-audit).

### `slice-plan`
- **Keep verbatim:** vertical slicing (each task a complete testable capability); the
  two-doc output (architecture doc + ordered task tracker); **Acceptance↔Verify
  coverage** + the red-flags/checklist; **CP-* checkpoints**; the **`cross-check
  mode=plan` gate before the human sees the plan**; task sizing (S/M, ~5 files).
- **Re-specialize:** `SPEC §9/§10` references; the `engine.py`/`queue.py` ordering
  example; `uv`/`tests` mechanics; the `agent-skills:plan` lineage note.

### `commit-message`
- **Mostly portable:** Conventional Commits, 50/72, atomicity, the *why*-capture, the
  `Co-Authored-By` trailer. Just ensure the destination's `commit-msg` hook matches
  (Step 0), and adjust scope examples to the destination's modules.

### `doc-freshness`
- Audits stateful markdown for stale restatements of facts owned elsewhere. Keep the
  method; re-point the doc set it scans (`SPEC.md`/`README.md`/`docs/`/`tasks/`) to the
  destination's docs.

---

## Step 3 — Do NOT prune these (load-bearing subtleties)

A cold session often "tidies away" these as noise. Each was added to fix a real
failure — keep them unless the destination genuinely doesn't apply:

- **`cross-check`: fresh session per round, never `resume`.** A resumed critic anchors
  on its own prior findings and re-flags code you already fixed (even after re-reading
  it). Continuity is carried by the grounding, not the thread.
- **`cross-check`: target-lock + in-target-only verdict.** Without it the critic drifts
  to coupled adjacent files and a valid-but-out-of-scope finding can stall the loop.
- **`cross-check`: close stdin (`</dev/null`).** Otherwise a backgrounded `codex exec`
  hangs forever waiting on stdin (looks "slow" at ~0% CPU).
- **`cross-check`: model pinning / no `-codex` slug under ChatGPT auth** (those 400).
- **`dev-loop`: reviewer ≠ author** (independent fresh-context sub-agent) and the
  **mutation-testing mandate** — these are the test-rigor backbone, not optional polish.
- **`dev-loop`/`slice-plan`: Acceptance↔Verify coverage** — every Acceptance bullet
  needs a Verify step that goes red if it breaks (inspection-only guarantees shipped
  twice here before this rule).
- **The human-merges discipline** (`gh pr merge` denied; dev-loop hands off and stops).

---

## Step 4 — Smoke-test the adaptation

After re-specializing, sanity-check at the destination:
- [ ] `commit-message` produces a commit the `commit-msg` hook accepts (format wired).
- [ ] `cross-check mode=diff` runs the critic against a trivial diff and returns a
      verdict with a `reviewed with: <model>` line (critic backend + auth wired); if
      codex is absent, it degrades to the Claude reviewer and *says so*.
- [ ] `dev-loop`'s orient step finds the tracker + source-of-truth docs (paths wired)
      and the reviewer sub-agent can run the destination's test command.
- [ ] The seam list in `dev-loop`/`cross-check` names the destination's *real*
      invariants — not AgentLatch's.

If all four hold, the pack is adapted. If the seam list still mentions Redis/VAD/
`asyncio.sleep`, you've copied the methodology but not yet given it teeth.
