---
name: cross-check
description: >-
  Adversarial CROSS-MODEL review of a plan or a diff. Claude builds; a second model (OpenAI
  Codex / GPT-5.5, read-only) argues against the work to find what breaks BEFORE it ships. A
  bounded loop: the critic returns severity-tagged findings, Claude arbitrates each (accept and
  revise, or reject with a logged reason), and re-submits to a FRESH critic session each round (no
  resume — a resumed thread anchors on its own earlier findings and re-flags issues already fixed)
  until no BLOCKER/MAJOR is open or MAX_ROUNDS is hit. Use this for a second-model sanity check on a
  high-stakes PLAN (auth, schema, concurrency, timing, migrations, irreversible work) or on a
  finished DIFF. Invoked by `slice-plan` (plan review before human sign-off) and by `dev-loop`
  (build-gate plan review + step-5 diff review), or directly: "cross-check this plan",
  "have Codex review the diff", "argue this plan with a second model", "adversarial review".
  NOT a substitute for the independent Claude reviewer in dev-loop — it runs ALONGSIDE it
  (cross-MODEL ≠ cross-context). NOT for trivial/cheap changes.
---

# cross-check — adversarial cross-model critic

One target, a second model, a bounded argument. **Claude is the builder and the final arbiter.
The critic is read-only** — it reads the target and the repo but never writes a file. This is the
cross-*model* complement to dev-loop's cross-*context* Claude reviewer: a different model family
has different blind spots, so running both is genuine defense in depth.

Reach for it on expensive-to-get-wrong work — auth, data models, concurrency, the no-`sleep`
timing path, Redis key schema, migrations, public-API changes. Skip it for cheap/obvious changes.

## Modes

| Mode | Target (default) | What the critic reviews |
|------|------------------|-------------------------|
| `plan` | `tasks/plan.md` | A plan/design *before* code — the cheapest place to catch flaws. |
| `diff` | `main...HEAD`    | A finished diff — alongside (not instead of) the Claude reviewer. |

## Tunable args (read from skill args, else default)

| Var | Default | Meaning |
|-----|---------|---------|
| `mode` | `plan` | `plan` or `diff`. |
| `target` | mode default above | A file path (plan) or a git ref/range (diff). |
| `rounds` | `3` | Hard cap on argument rounds. The loop ALWAYS terminates here. |
| `model` | `gpt-5.5` | The critic model. **Pinned and surfaced every run** — see below. |
| `effort` | `high` | `model_reasoning_effort` for the critic. |

Echo the resolved values back before starting (e.g. `cross-check: mode=plan target=tasks/plan.md
rounds=3 model=gpt-5.5 effort=high`).

## Prerequisites (verify once, fast)

- `codex --version` ≥ 0.130 (this repo verified on 0.137). If absent → **degrade** (see below).
- Authenticated (`codex login status` → "Logged in"). On an auth/model error, **surface it and
  degrade — never silently retry.**
- **Model pinning is deliberate, not default-trusting.** We pin `model` + `effort` explicitly so we
  always *know and control* which model reviewed. `gpt-5.5` is OpenAI's strongest agentic coding
  model **and** is ChatGPT-auth-safe. **Do NOT use a `-codex` slug** (e.g. `gpt-5.5-codex`): those
  400 under ChatGPT-account auth. If `model` is overridden, keep it a non-`-codex` slug.
- **Every round is a fresh `codex exec -s read-only`** — no `resume` (see Step 3 for why). One flag
  makes the critic reliably read-only, with **no `sandbox_mode` footgun**. (Historical note: `codex
  exec resume` rejected `-s` and needed `-c sandbox_mode="read-only"` to avoid inheriting a
  `danger-full-access` `config.toml` and writing files mid-loop. We no longer resume, so that trap is
  gone — but if you ever reintroduce resume, that override is mandatory.)

## Flow

### Step 1 — Assemble the grounding

Read the target and build a **grounding block** the critic gets every round. This is what makes the
critique find *our* bugs, not textbook ones.

**Point, don't transcribe.** The critic has full repo access — it can read `SPEC.md`, `tasks/todo.md`,
and the diff file itself. So the grounding is mostly *pointers* ("read SPEC §9; the task is `T8` in
`tasks/todo.md`; the diff is `/tmp/cross-check-diff.patch`") plus only what it *can't* infer: the
**settled-decision deltas** it must not re-litigate, and the task-specific framing. Don't paste large
slabs of boundary/acceptance text it can read itself — that's wasted effort and context, every round.
Include:

- **The target lock — state it FIRST, every round.** Name the **exact files under review**: for
  `diff` mode, the files in `/tmp/cross-check-diff.patch` (list them by name); for `plan` mode, the
  plan file. Say it plainly: *review ONLY these files; the SPEC / plan / todo / future tasks are
  constraints to honor, NOT the review surface — do not raise findings about anything outside the
  target.* With a fresh, memory-less session each round (Step 3), this grounding is the **only** thing
  steering the critic onto the right surface — a thin diff paired with fat pointers to the plan
  otherwise drifts into reviewing the plan (observed on T10 round 1).
- **Intent / the "why" — so the critic argues *with* the settled rationale, not against it.** Give
  it what is already decided and why: the project's objective and **scope discipline** (**SPEC §1–§2**
  — the primary directive and the *Resolved design decisions* table), any carried-forward obligations
  (deferral notes in **`SPEC.md`**), and the rationale of any **ADRs** (`docs/adr/`) the work cites. Summarize the
  settled decisions so the critic does not burn rounds re-litigating intent that is already
  reasoned-through (e.g. "this is deliberately single-loop / boring-solution by design"). **But do NOT
  paste your private reasoning or this conversation** — the value of a cross-*model* check is an
  *independent* read; over-feeding your framing anchors the critic into agreeing with you. Give it the
  *what-and-why-decided*, then let it judge independently whether the plan executes correctly within
  that intent.
- **SPEC §9 Boundaries** — point the critic at §9 (Always / Ask-first / Never); don't transcribe the lists.
- **The real seams** for this repo: never `asyncio.sleep()` to measure silence (timestamp diffing
  only); real `redis.asyncio` calls, never a Python `dict`; the clock is an injectable callable;
  `fastapi` is an optional extra, never a hard core dep; one module at a time; mutate LLM memory
  only under the idle lock.
- For `plan` mode: the slice's **Goal / Acceptance** from `tasks/todo.md`.
- For `diff` mode: **commit your fixes first, then** write the diff —
  `git diff <target> > /tmp/cross-check-diff.patch` — and name the touched files. `<target>` defaults to
  `main...HEAD`, which is **committed-only**: an uncommitted fix won't be in the patch, so **regenerate it
  after every commit** or the critic reviews stale code and burns a round flagging "the patch still has
  the old code" (this bit us on T7).

### Step 2 — The critic prompt (sent each round)

> You are an adversarial reviewer. Be skeptical and specific — your job is to find what breaks, not
> to be agreeable. You are READ-ONLY: read the target and any repo files you need, but do NOT modify
> any file. **Target — review ONLY these files:** `<plan path | the files in
> /tmp/cross-check-diff.patch, listed by name>`. Anything not in the target — the SPEC, the plan, the
> todo, future tasks — is **context to honor, not a review surface**; do not raise findings about it.
> **Grounding (treat as hard constraints):** `<grounding block from Step 1>`. Find concrete flaws: boundary violations, broken
> seams, race conditions, timing/`sleep` mistakes, Redis key/TTL/FIFO errors, missing edge cases,
> wrong assumptions, observability gaps, simpler alternatives, public-API churn. For EACH flaw emit
> one line, exactly:
> `- [BLOCKER|MAJOR|MINOR] <area>: <the flaw> — Fix: <one-line fix>`
> (BLOCKER = must fix or it's wrong/unsafe; MAJOR = materially deficient; MINOR = nit.) Then end with
> exactly one line: `OPEN_BLOCKERS: <n>  OPEN_MAJORS: <n>`.

### Step 3 — The loop

Maintain `ROUND` (start 1). **Every round is a FRESH `codex exec` session** — a brand-new thread that
reads the *current* target cold, with **no memory of prior rounds**. This is deliberate. A resumed
thread accumulates its own earlier critiques and **anchors** on them: it re-flags an issue you already
fixed even after re-reading the corrected file (observed on Slice-4 T10 — twice, the critic's own
`grep` returned the fixed line yet it re-raised the old defect from thread memory). A fresh session
can't anchor on what it never saw. **Continuity is carried by you, not the thread:** the regenerated
target plus the **target-lock + settled-decisions grounding** (Step 1) is now what keeps the critic on
the right surface and prevents re-litigation — so send that grounding, in full, every round.

**Every round** (fresh session — pins the model; `-s read-only` is reliable on `codex exec`, so there
is no `sandbox_mode` footgun):
```bash
codex exec -s read-only \
  -c model="$model" -c model_reasoning_effort="$effort" \
  --json -o /tmp/cross-check.txt \
  "$REVIEW_PROMPT" \
  </dev/null 2>/dev/null | grep '"type":"thread.started"'
```
`$REVIEW_PROMPT` is the **same cold Step-2 prompt every round** — grounding + the *current* target.
**Do NOT send a "I revised it, here's what I changed, re-review" recap:** naming the old defects
re-injects their text and rebuilds the very anchor you went fresh to avoid. Point the fresh critic at
the current target and let it read cold. The critique lands in `/tmp/cross-check.txt`; the
`thread.started` line confirms the call ran and the model pin took (a wrong/unauthorized slug fails
with no such line). If neither the line nor the file appears, the run failed (auth/model) →
**degrade** (Step 5).

> **Always close stdin (`</dev/null`) on every `codex exec` call.** `codex exec` reads stdin *in
> addition to* the prompt argument; if stdin is left open (e.g. when the call is backgrounded) it
> blocks **forever** on `Reading additional input from stdin...` — the process sits at ~0% CPU with no
> output and looks "slow" when it is actually hung. Verified the hard way: a backgrounded review hung
> 28 minutes using 0.1s of CPU before this was added.

**Each round, after the critic returns:**
1. Read `/tmp/cross-check.txt`. Record the findings (you'll surface them in Step 4).
2. **Arbitrate every BLOCKER and MAJOR** — Claude has final say; the critic advises, it does not
   command. For each: **accept** → revise the plan/code and note what changed; or **reject** → log a
   one-line reason. MINORs are recorded, not gating.
   - **CONVERGED** — this round returned a **clean round** (`OPEN_BLOCKERS: 0  OPEN_MAJORS: 0`): an
     **independent, memory-less** second-model read of the *current* target found nothing material.
     Go to Step 4. (The meaning shifted from the old resume loop — see Step 4.)
   - Else (BLOCKER/MAJOR found this round) **and `ROUND < rounds`** → fold the accepted findings (the
     rejected ones stay open), revise the target, **(diff mode) regenerate `/tmp/cross-check-diff.patch`
     so the next round reads the corrected code**, `ROUND += 1`, and run another **fresh** round (Step
     3). Folded findings are **unverified** until a later fresh round reads the corrected target and
     stays silent on them.
3. **At the cap** — a round found BLOCKER/MAJOR **and `ROUND == rounds`** (no clean round was reached
   within budget). Do NOT fake convergence; pick the honest terminal verdict:
   - If **any BLOCKER/MAJOR is open (rejected)** → **DEADLOCK** — a genuine Claude-vs-critic
     disagreement the human must break.
   - Else (the final round's findings were **all accepted and folded**, but **no further fresh round
     confirmed them**) → **ROUNDS_EXHAUSTED** — the round budget ran out with the last fixes
     **unverified by a clean round**. This is **not** CONVERGED: "we ran out of tries" ≠ "a cold read
     signed off". Record the unverified final-round folds so the human/implementer knows what carries
     no critic pass. (If those folds were high-stakes, the caller may **raise `rounds` and re-run** to
     push for a confirming round.)
   Go to Step 4.

### Step 4 — Return

Return a compact report to the caller (`slice-plan`, `dev-loop`, or the user):

```
## cross-check — mode=<mode> · reviewed with: <model> (effort=<effort>) · rounds used: <n>/<rounds>
Verdict: CONVERGED | ROUNDS_EXHAUSTED | DEADLOCK

Findings:
- [SEV] area: finding — Fix: … → accepted (changed X) | rejected (reason) | recorded (minor)

Unverified final-round folds (ROUNDS_EXHAUSTED only): <findings accepted+folded in the last round that the critic never re-reviewed — carry no critic pass>
Open disagreements (DEADLOCK only): <critic's point vs. Claude's counter-position>
```

The three verdicts are **not** interchangeable:
- **CONVERGED** — a clean round from a **fresh, memory-less** critic: an independent second-model read
  of the *current* target found nothing material. Read it as *"an independent cold reviewer signed off
  on the final state"* — strong, but a **different claim** from the old resume loop's "the critic that
  objected is now satisfied". Each round is a cold read, so "convergence" is not a trajectory across
  rounds; the final clean round is what counts.
- **ROUNDS_EXHAUSTED** — the `rounds` budget ran out while still folding accepted findings; the final
  fixes are sound to Claude but **never read clean by a fresh round**. Raising `rounds` and re-running
  may earn a confirming (CONVERGED) round.
- **DEADLOCK** — the cap was hit with an open BLOCKER/MAJOR Claude **rejected**: a real disagreement.

`reviewed with: <model>` is mandatory — a review's provenance is never ambiguous. The `--json` stream
does NOT echo the served model, so surface the **pinned** value: a wrong or unauthorized slug fails the
call with no `thread.started` line, so a successful run *is* confirmation the pin took. In `dev-loop`
this report goes in the PR body; in `slice-plan` it's shown at the human gate. On **DEADLOCK** the human
breaks the tie; on **ROUNDS_EXHAUSTED** the human decides whether the unverified folds are acceptable or
warrant another run.

### Step 5 — Degradation (never hard-fail)

If `codex` is missing or the first call errors (auth/model), fall back to a **fresh-context Claude
critic**: spawn an `agent-skills:code-reviewer` sub-agent (Agent tool) with the *same* grounding and
the *same* severity-tagged output contract; pin a strong model via the Agent `model` param and
**surface which model reviewed** (`reviewed with: claude-<…> (fallback)`). Same loop, same verdicts.
This keeps the workflow moving when the second model is unavailable — note in the report that it was
a same-model fallback (cross-context, not cross-model).

## Hard rules

- The critic is read-only EVERY round — every round is a fresh `codex exec -s read-only`; never
  `resume` (so no `sandbox_mode` override is needed). It never writes. If you're tempted to give it
  write access, stop.
- **Fresh session every round; never resume.** A resumed thread anchors on its own prior findings and
  re-flags issues you already fixed (even after re-reading the corrected file). The target-lock +
  settled-decisions grounding, sent in full every round, carries continuity instead — and the
  per-round prompt must NOT recap what changed (recapping re-injects the anchor).
- **Pin and surface the model every run.** Never trust `<default>`; never use a `-codex` slug under
  ChatGPT auth.
- The loop ALWAYS terminates at `rounds`. No unbounded recursion.
- Claude is the final arbiter on every finding — incorporate good critiques, reject bad ones *with a
  logged reason*. Don't cave on everything (defeats the cross-model check) and don't ignore it
  (defeats the point).
- **`CONVERGED` is reserved for a clean critic round — a fresh, memory-less critic must have read the
  *current* target and returned `0/0`.** Never label a cap-hit run CONVERGED: if the last round's
  findings were folded without a confirming fresh round it's **`ROUNDS_EXHAUSTED`** (fixes unverified);
  if a BLOCKER/MAJOR is open/rejected at the cap it's **`DEADLOCK`**. Surface both honestly — "ran out
  of tries" is not "a cold read signed off".

## What NOT to do

- Don't let the critic edit files. Read-only, always.
- Don't `resume` the critic across rounds — fresh session each round, or it anchors on (and re-raises)
  findings you already fixed. Don't recap "what I changed" in the per-round prompt for the same reason.
- Don't pin a `-codex` model variant on ChatGPT-account auth — it 400s.
- Don't run it on trivial/cheap changes — proportionality (mirror dev-loop's high-stakes trigger).
- Don't treat it as a replacement for dev-loop's independent Claude reviewer — it runs alongside it.
