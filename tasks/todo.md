# TODO — Slice 2: The Delivery Engine

Status tracker for the `dev-loop`. Architecture, DAG, and design notes live in
[`plan.md`](./plan.md); intent + boundaries in [`SPEC.md`](../SPEC.md).

> **Slice 1 ✅ COMPLETE** — T0–T4 merged, CP-A/CP-B approved (PRs #1–#5); the ingest path
> is built and verified. Full history in git + [`HANDOFF.md`](../HANDOFF.md).
> **This slice (2)** builds the delivery path. Next after sign-off: `dev-loop` picks up **T5**.

**Status legend:** `[ ]` pending · `[~]` PR open (link) · `[x]` merged.
A task flips to `[x]` only on **merge** (the next task's start flips it).
A `CP-*` checkpoint flips to `[x]` only on the user's **explicit approval**.

---

### T5 — `engine.py` · `DeliveryEngine`  `[~]` — [PR #8](https://github.com/DaithiMartin/AgentLatch/pull/8)
- **Depends on:** T2 (HoldingTank) — merged.
- **Do:** `DeliveryEngine(redis, tank, *, silence_threshold, session_ttl, now)`; key
  `agentlatch:last_speech:{session_id}`. `get_next_message(session_id, is_user_speaking)`:
  speaking → atomic `SET last_speech now() ex=session_ttl`, return `None`; not speaking → cold start
  (no last_speech) seeds the baseline with the same atomic `SET now() ex=session_ttl` and returns
  `None`; else `silence = now() − last`, **clamp negative → hold**, `silence < threshold` → `None`,
  else `tank.pop()` (`None` if empty).
  `tests/test_engine.py` (DAMP, fakeredis + injected fake clock). Add a fake-clock fixture to
  `tests/conftest.py`.
- **Acceptance:** (all met — 15 DAMP tests; verified by an independent Claude reviewer + Codex `cross-check`)
  - [x] `is_user_speaking=True` → returns `None` **and** records last_speech (value + bounded TTL).
  - [x] Not speaking: `silence` at **1.999s → `None`**; at **2.000s → pops**; at **2.001s → pops**
        (boundary is `≥`), with a non-empty queue.
  - [x] Sufficient silence but **empty queue → `None`**; **cold start** (no last_speech) + not
        speaking → **seeds baseline, returns `None`**, then delivers only after ≥2s subsequent
        silence (§4.2).
  - [x] **Future last_speech** (negative silence) → **`None`** (skew guard, §4.5).
  - [x] An injected clock returning **`NaN`/`inf`** → **raises loudly** (finite-on-read; speaking + silent).
  - [x] A **corrupt/non-finite stored last_speech** → fails safe (reseed + hold, no early pop) — cross-check find.
  - [x] **Speech races a due delivery:** `is_user_speaking=True` when silence would otherwise pop →
        `None`, **no pop**, message stays queued (SPEC §7 DAMP).
  - [x] Two silent polls after enqueuing A,B release **A then B** (FIFO).
  - [x] No `asyncio.sleep` anywhere in `engine.py` or its tests (timestamp diffing only).
- **Verify:** `uv run pytest tests/test_engine.py && uv run ruff check . && uv run mypy src`

### T6 — `core.py` · wire `get_next_message` into the facade  `[ ]`
- **Depends on:** T5.
- **Do:** `AgentLatch.get_next_message(session_id, is_user_speaking)` — validate with Pydantic
  (SPEC §9): `is_user_speaking` via a `StrictBool` `TypeAdapter` (`ValidationError`, no coercion),
  `session_id` normalized via the same `NonEmptyStr` `schemas.py` uses (shared `TypeAdapter`) so
  lookup keys match enqueue keys; delegate to the engine. Harden `__init__` numerics
  (`silence_threshold` finite-positive **non-bool** — reject `True`/`False`/`NaN`/`inf`; `session_ttl`
  positive **non-bool** `int`). Add
  keyword-only `now: Callable[[], float] | None = None` (default UTC wall clock; if provided, validate
  callable + finite) and construct `self._engine`. **`now` on the constructor needs gate sign-off
  (SPEC §9 Ask-first).** Extend `tests/test_core.py` (or add `tests/test_delivery.py`).
- **Acceptance:**
  - [ ] Round-trip with an injected clock: `enqueue` → `None` while speaking / `silence < 2.0s` →
        `ResponsePayload` once `silence ≥ 2.0s`; FIFO across two deliveries.
  - [ ] Non-`bool` `is_user_speaking` (e.g. `1`, `"true"`, `None`) → `ValidationError` (StrictBool,
        no coercion); empty/blank `session_id` → raises.
  - [ ] `enqueue(ResponsePayload(session_id="s1", …))` then `get_next_message(" s1 ", …)` resolve to
        the **same** key and the message is delivered (normalization).
  - [ ] `silence_threshold` ∈ {`True`, `NaN`, `inf`, `0`, `-1`} → `ValueError`; `session_ttl` ∈
        {`True`, `False`, `1.5`, `NaN`, `inf`, `0`, `-1`} → `ValueError` (strict finite-positive non-bool).
  - [ ] The single-loop-per-session contract is stated in the `get_next_message` docstring (normative;
        SPEC §3.4) — no lock.
  - [ ] Default construction (no `now`) uses a UTC wall clock; public exports unchanged.
- **Verify:** `uv run pytest && uv run ruff check . && uv run mypy src`

### CP-C — Slice 2 complete · delivery path proven (human gate)  `[ ]`
- **Depends on:** T6 merged.
- **Evidence to present:** full suite + `ruff` + `mypy src` green; SPEC §7 gate 2 holds (None when
  speaking; None when `silence < 2.0s`; payload only when `silence ≥ 2.0s` **and** queue non-empty);
  FIFO on delivery; no-`sleep` respected; last_speech key set with TTL. Optional: a live real-Redis
  round-trip smoke (enqueue → wait > 2s → release).
- **Approval:** await explicit user OK, then mark `[x]`.

---

## Open decisions (for the human gate — cross-check ran 3 rounds + an intent-grounded re-run)
- [x] **Clock seam:** **APPROVED** — keyword-only `now` on `AgentLatch.__init__` (default UTC wall
      clock; finite-validated). Signed off at the gate 2026-06-08.
- [x] **Concurrency:** **document** the single-loop-per-session contract normatively (no lock) —
      Claude *and* the intent-grounded re-run agree. Confirm at the gate.

*Cross-check resolved: cold-start holds; atomic `SET ex=`; skew clamp; `session_id` normalized;
speech-races-pop + clock-`NaN` tests; strict non-bool numerics. Full record in [`plan.md`](./plan.md) §7.*
