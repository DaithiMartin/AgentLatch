# ADR-NNNN: <short decision title>

> Architecture Decision Record. Copy this file to `NNNN-<slug>.md` (next number), fill it in, and
> link it from any task that depends on the decision. Keep ADRs short and immutable — supersede with a
> new ADR rather than rewriting an old one. `dev-loop` step 1 reads "any ADRs a task cites".

**Status:** proposed | accepted | superseded by [ADR-XXXX](./XXXX-...md) · **Date:** YYYY-MM-DD

## Context
What forces are at play — the problem, the constraints (SPEC §9 Boundaries that bind it), what
prompted the decision now.

## Decision
The choice, stated plainly. What we will do.

## The argument (cross-check)
When the decision was stress-tested with `cross-check`, record the transcript here — the value is the
reasoning, not just the verdict:
- **Reviewed with:** `<model>` (effort) · **rounds:** `<n>` · **verdict:** CONVERGED | DEADLOCK
- **What the critic flagged → what changed:** the BLOCKER/MAJOR findings Claude accepted and the
  revision each produced.
- **Rejected, with reason:** findings Claude declined and why (Claude is the final arbiter).
- **Open disagreements (if DEADLOCK):** the critic's point vs. Claude's counter-position, for the human.

## Consequences
What becomes easier or harder. Follow-on obligations (e.g. a deferred parameter, a future hardening).
