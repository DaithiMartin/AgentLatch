# ADR-0001: Adopt a cross-model adversarial critic (`cross-check`) in the skills workflow

**Status:** accepted · **Date:** 2026-06-08

## Context
Our `dev-loop` already enforces **reviewer ≠ author** at step 5, but that reviewer is a fresh-context
*Claude* agent — cross-*context*, not cross-*model*, and it only runs *after* code exists. A flawed
design therefore isn't independently challenged until implementation. Two axes were going unused: a
**different model family** (different blind spots) and review at the **plan stage** (the cheapest place
to catch a flaw). The `chaseai-yt/grill-me-codex` `codex-review` skill demonstrated the pattern; `codex`
0.137 is installed here (GPT-5.5, ChatGPT-account auth), so a second model is genuinely available.

## Decision
Add a repo-local skill **`cross-check`** — a model-agnostic adversarial critic seam (Codex/GPT-5.5
backend, read-only, with a fresh-context Claude `code-reviewer` fallback) — and wire it into the
workflow at three points:
1. **`slice-plan`** (the repo-local fork of `agent-skills:plan`) runs `cross-check mode=plan` on the
   slice plan before human sign-off.
2. **`dev-loop` build gate** runs `cross-check mode=plan` on the build plan for high-stakes tasks.
3. **`dev-loop` step-5 verify** runs `cross-check mode=diff` on the diff, alongside the Claude reviewer.

The critic returns **severity-tagged findings** (BLOCKER/MAJOR/MINOR + a one-line fix); Claude is the
**final arbiter** (accept-and-revise, or reject-with-logged-reason); the loop is **bounded** (`rounds`,
default 3) and surfaces an honest **DEADLOCK** rather than faking convergence. The reviewing model is
**pinned and surfaced** every run (`gpt-5.5`, `model_reasoning_effort=high`) — never a `-codex` slug,
which 400s under ChatGPT auth.

## The argument (cross-check)
This decision pre-dates the skill being runnable, so it was reviewed by a human (the gate that approved
the implementation plan) rather than by `cross-check` itself. Future ADRs should record a real
`cross-check` transcript here per the template.

## Consequences
- Defense in depth across two diversity axes (cross-model at plan-time + cross-context Claude at
  code-time) — proportional to stakes (always for slice plans; high-stakes-by-default for the gate and
  diff pass; skip cheap work).
- Establishes the `docs/adr/` practice (`dev-loop` step 1 already reads cited ADRs).
- Adds latency/cost: one read-only Codex call per round, capped at `rounds`. Mitigated by the
  high-stakes trigger and the cap.
- Follow-on: if Codex auth/CLI changes, only `cross-check`'s isolated mechanics need updating; the
  fallback keeps the workflow moving meanwhile.
