---
name: cross-check
description: >-
  Adversarial CROSS-MODEL review of a plan or a diff. Claude builds; a second model (OpenAI
  Codex / GPT-5.5, read-only) argues against the work to find what breaks BEFORE it ships. A
  bounded loop: the critic returns severity-tagged findings, Claude arbitrates each (accept and
  revise, or reject with a logged reason), and re-submits to the SAME critic session until no
  BLOCKER/MAJOR is open or MAX_ROUNDS is hit. Use this for a second-model sanity check on a
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
- **Sandbox flag differs between the two commands.** `codex exec` accepts `-s read-only`. `codex
  exec resume` does NOT (it rejects `-s` as "unexpected argument"); on resume you MUST force
  read-only via `-c sandbox_mode="read-only"`, or Codex inherits `config.toml` (possibly
  `danger-full-access` + `approval_policy="never"`) and could **write files mid-loop**. This is the
  single most important safety detail in this skill.

## Flow

### Step 1 — Assemble the grounding

Read the target and build a **grounding block** the critic gets every round. This is what makes the
critique find *our* bugs, not textbook ones. Include:

- **Intent / the "why" — so the critic argues *with* the settled rationale, not against it.** Give
  it what is already decided and why: the project's objective and **scope discipline** (**SPEC §1–§2**
  — the primary directive and the *Resolved design decisions* table), any carried-forward obligations
  in **`HANDOFF.md`**, and the rationale of any **ADRs** (`docs/adr/`) the work cites. Summarize the
  settled decisions so the critic does not burn rounds re-litigating intent that is already
  reasoned-through (e.g. "this is deliberately single-loop / boring-solution by design"). **But do NOT
  paste your private reasoning or this conversation** — the value of a cross-*model* check is an
  *independent* read; over-feeding your framing anchors the critic into agreeing with you. Give it the
  *what-and-why-decided*, then let it judge independently whether the plan executes correctly within
  that intent.
- **SPEC §9 Boundaries** — the Always / Ask-first / Never lists, verbatim or tightly summarized.
- **The real seams** for this repo: never `asyncio.sleep()` to measure silence (timestamp diffing
  only); real `redis.asyncio` calls, never a Python `dict`; the clock is an injectable callable;
  `fastapi` is an optional extra, never a hard core dep; one module at a time; mutate LLM memory
  only under the idle lock.
- For `plan` mode: the slice's **Goal / Acceptance** from `tasks/todo.md`.
- For `diff` mode: write the diff to a file the critic can read —
  `git diff <target> > /tmp/cross-check-diff.patch` — and name the touched files.

### Step 2 — The critic prompt (sent each round)

> You are an adversarial reviewer. Be skeptical and specific — your job is to find what breaks, not
> to be agreeable. You are READ-ONLY: read the target and any repo files you need, but do NOT modify
> any file. **Target:** `<plan path | /tmp/cross-check-diff.patch>`. **Grounding (treat as hard
> constraints):** `<grounding block from Step 1>`. Find concrete flaws: boundary violations, broken
> seams, race conditions, timing/`sleep` mistakes, Redis key/TTL/FIFO errors, missing edge cases,
> wrong assumptions, observability gaps, simpler alternatives, public-API churn. For EACH flaw emit
> one line, exactly:
> `- [BLOCKER|MAJOR|MINOR] <area>: <the flaw> — Fix: <one-line fix>`
> (BLOCKER = must fix or it's wrong/unsafe; MAJOR = materially deficient; MINOR = nit.) Then end with
> exactly one line: `OPEN_BLOCKERS: <n>  OPEN_MAJORS: <n>`.

### Step 3 — The loop

Maintain `ROUND` (start 1) and `THREAD_ID` (empty until round 1 returns).

**Round 1** (creates the session — pins the model — captures `thread_id`):
```bash
codex exec -s read-only \
  -c model="$model" -c model_reasoning_effort="$effort" \
  --json -o /tmp/cross-check.txt \
  "$REVIEW_PROMPT" \
  </dev/null 2>/dev/null | grep '"type":"thread.started"'
```
Parse `thread_id` from the `{"type":"thread.started","thread_id":"..."}` line → `THREAD_ID`. The
critique lands in `/tmp/cross-check.txt`. If neither the `thread.started` line nor the file appears,
the run failed (auth/model) → **degrade** (Step 5).

> **Always close stdin (`</dev/null`) on every `codex exec` call.** `codex exec` reads stdin *in
> addition to* the prompt argument; if stdin is left open (e.g. when the call is backgrounded) it
> blocks **forever** on `Reading additional input from stdin...` — the process sits at ~0% CPU with no
> output and looks "slow" when it is actually hung. Verified the hard way: a backgrounded review hung
> 28 minutes using 0.1s of CPU before this was added.

**Rounds 2..rounds** (resume the SAME session — it remembers its earlier critiques; force read-only
*and* re-pin the model because `resume` rejects `-s`):
```bash
codex exec resume "$THREAD_ID" \
  -c sandbox_mode="read-only" \
  -c model="$model" -c model_reasoning_effort="$effort" \
  --json -o /tmp/cross-check.txt \
  "I revised the $mode. Re-review the same target. Same rules and same output format." \
  </dev/null 2>/dev/null >/dev/null
```

**Each round, after the critic returns:**
1. Read `/tmp/cross-check.txt`. Record the findings (you'll surface them in Step 4).
2. **Arbitrate every BLOCKER and MAJOR** — Claude has final say; the critic advises, it does not
   command. For each: **accept** → revise the plan/code and note what changed; or **reject** → log a
   one-line reason. MINORs are recorded, not gating.
   - If, after arbitration, **no BLOCKER or MAJOR remains open** → **CONVERGED**, go to Step 4.
   - Else revise the target, increment `ROUND`, and resume (Step 3) — unless `ROUND > rounds`.
3. If `ROUND > rounds` with an open BLOCKER/MAJOR → **DEADLOCK**, go to Step 4. Do NOT fake
   convergence — a surfaced disagreement beats a false "approved".

### Step 4 — Return

Return a compact report to the caller (`slice-plan`, `dev-loop`, or the user):

```
## cross-check — mode=<mode> · reviewed with: <model> (effort=<effort>) · rounds used: <n>
Verdict: CONVERGED | DEADLOCK

Findings:
- [SEV] area: finding — Fix: … → accepted (changed X) | rejected (reason) | recorded (minor)

Open disagreements (DEADLOCK only): <critic's point vs. Claude's counter-position>
```

`reviewed with: <model>` is mandatory — a review's provenance is never ambiguous. The `--json` stream
does NOT echo the served model, so surface the **pinned** value: a wrong or unauthorized slug fails the
call with no `thread.started` line, so a successful run *is* confirmation the pin took. In `dev-loop`
this report goes in the PR body; in `slice-plan` it's shown at the human gate; on DEADLOCK the human
breaks the tie.

### Step 5 — Degradation (never hard-fail)

If `codex` is missing or the first call errors (auth/model), fall back to a **fresh-context Claude
critic**: spawn an `agent-skills:code-reviewer` sub-agent (Agent tool) with the *same* grounding and
the *same* severity-tagged output contract; pin a strong model via the Agent `model` param and
**surface which model reviewed** (`reviewed with: claude-<…> (fallback)`). Same loop, same verdicts.
This keeps the workflow moving when the second model is unavailable — note in the report that it was
a same-model fallback (cross-context, not cross-model).

## Hard rules

- The critic is read-only EVERY round — `-s read-only` first call, `-c sandbox_mode="read-only"` on
  every resume (resume has no `-s`). It never writes. If you're tempted to give it write access, stop.
- **Pin and surface the model every run.** Never trust `<default>`; never use a `-codex` slug under
  ChatGPT auth.
- The loop ALWAYS terminates at `rounds`. No unbounded recursion.
- Claude is the final arbiter on every finding — incorporate good critiques, reject bad ones *with a
  logged reason*. Don't cave on everything (defeats the cross-model check) and don't ignore it
  (defeats the point).
- Surface DEADLOCK honestly. Never report CONVERGED with an open BLOCKER/MAJOR.

## What NOT to do

- Don't let the critic edit files. Read-only, always.
- Don't pin a `-codex` model variant on ChatGPT-account auth — it 400s.
- Don't run it on trivial/cheap changes — proportionality (mirror dev-loop's high-stakes trigger).
- Don't treat it as a replacement for dev-loop's independent Claude reviewer — it runs alongside it.
