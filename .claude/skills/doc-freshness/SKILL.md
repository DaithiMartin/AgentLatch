---
name: doc-freshness
description: Audit the repo's markdown docs for stale restatements of volatile state — docs that duplicate a fact owned elsewhere instead of pointing to its source. Use before merging doc changes, after a decision/ADR lands, or periodically to keep the docs honest.
---

# Doc Freshness

## Principle

**Markdown docs are pointers, not duplicates of volatile state.** A doc should restate a fact only
if it is the *home* (single source of truth) for that fact. When a stable doc hard-codes a value
that lives — and changes — somewhere else, it silently goes stale: nobody updates it, and it starts
contradicting reality. (We have already hit this: ADR ranges frozen at `0008`, a "now implementing
Batch 1" line, and a SPEC tech-stack row that still said "GCS + Cloud CDN" three ADRs after the
hosting decision changed.)

This skill finds those restatements so they can be turned back into pointers.

## Doc-ownership map (the single home for each fact)

A fact is "stale-prone" only when it appears **outside** its home. Homes for this repo:

| Fact | Home (source of truth) |
|------|------------------------|
| Product decisions, data model, contracts, Glossary, Boundaries | `SPEC.md` + `docs/adr/` |
| The list/count of ADRs | `docs/adr/README.md` (the index) |
| Architecture, slice DAG, risks | `docs/PLAN.md` |
| Per-task detail (acceptance criteria, verify steps) | `tasks/slice-N-*.md` |
| Task status (done / in-progress) | `tasks/todo.md` (single status tracker) |
| Current position, project state, infra facts (SAs, buckets, URLs), git state | `HANDOFF.md` |
| Per-task implementation procedure | the session prompt |
| Durable conventions + pointers (must carry **no** volatile state) | `CLAUDE.md`, `README.md`, dir `README.md`s |

A doc carrying its *own* state is fine (that's its job): `todo.md` checkboxes, `HANDOFF.md`'s current
position, the `docs/adr/README.md` index. The bug is a *different* doc restating it.

## Volatile-fact checklist (scan every doc for these)

Flag any of these when they appear outside their home above:

1. **Batch/slice/task currency** — "currently/now implementing Batch N", "Current batch: …", "we are on Slice N".
2. **Counts & ranges that grow** — ADR ranges (`ADR-0001 … ADR-000N`), PR numbers/ranges (`PRs #1–#N`), file/task/box counts ("38 boxes").
3. **Status words / checkboxes** outside `tasks/todo.md` (per-card status banners are OK).
4. **Dates / "last updated" stamps** — except `HANDOFF.md`, where currency is the point.
5. **Decisions superseded by a later ADR** — wording that still states the pre-decision choice (e.g. a doc citing "Cloud CDN" after ADR-0009/0010 dropped it).
6. **Hardcoded values that live in code/config** — SA emails, bucket names, project IDs, service URLs — restated outside `HANDOFF.md`/`infra/`.

## Process

1. **Enumerate:** `git ls-files '*.md'` (skip `node_modules`).
2. **Scan each doc** against the ownership map + checklist. For each hit, decide: *is this doc the home for this fact?* If not, it's a stale restatement.
3. **Decision-currency cross-check:** for each accepted ADR, grep the docs for the wording it superseded (e.g. after a hosting ADR, grep for the old hosting terms). A grep starter for the mechanical cases:
   ```bash
   grep -rniE "currently implementing|now implementing|ADR-000[0-9] ?(…|\.\.\.|-) ?ADR-00|PRs? #[0-9]" --include="*.md" . | grep -v node_modules
   ```
   (Grep only finds the obvious patterns — also read for *semantic* drift, like a superseded decision, which no regex catches.)
4. **Report**, don't silently fix. Output a table: `file:line` · stale content · owning doc · suggested **pointer** fix.
5. **Apply only on request.** Treat `SPEC.md` and ADRs (source of truth) as sensitive — confirm before editing; a fix there should align the doc with the already-accepted decision, never invent a new one.

## Output format

```
## Doc-freshness audit

| File:line | Stale content | Owned by | Suggested fix |
|-----------|---------------|----------|---------------|
| README.md:56 | "Currently implementing Batch 1" | HANDOFF/todo | defer to HANDOFF.md + tasks/todo.md |
| SPEC.md:80 | "GCS bucket + Cloud CDN" | ADR-0009 | "Cloud Run static (ADR-0009)" |

Clean: <docs with no findings>
```

## What is NOT a finding

- A doc carrying the state it *owns* (todo.md status, HANDOFF current position/facts, the ADR index).
- **Durable** facts digested into a pointer doc (the five slices, the tech choices, the Glossary) — stable, not volatile.
- Forward-looking plans that name future ADRs/tasks that don't exist yet (that's intent, not drift).

The test in one line: *would this line need editing when nothing about it conceptually changed — only the count, batch, or date moved?* If yes, it should be a pointer.
