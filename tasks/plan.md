# Plan — Slice 2: The Delivery Engine

> Source of truth: [`SPEC.md`](../SPEC.md) (esp. §3.4, §4, §7, §9). Per-task status,
> Acceptance, and Verify steps live in [`todo.md`](./todo.md) (the dev-loop tracker).
> This file owns the **architecture, dependency graph, and design notes** for this slice.
> Workflow conventions are the repo's [`dev-loop`](../.claude/skills/dev-loop/SKILL.md) skill —
> not restated here.
>
> **Previous:** Slice 1 (Receiver + Holding Tank) — COMPLETE, merged in PRs #1–#5 (T0–T4,
> CP-A/CP-B). See [`HANDOFF.md`](../HANDOFF.md) for what exists.

**Created:** 2026-06-08 · **Status:** drafted — pending cross-check + human sign-off

---

## 1. Goal of this slice

Release a held message **only during a genuine pause**. Implement `engine.py` (VAD-timed delivery)
and wire `get_next_message` into the `AgentLatch` facade so the full library round-trip works:

```python
latch = AgentLatch(redis_url="redis://localhost:6379", silence_threshold=2.0)
await latch.enqueue(ResponsePayload(session_id="s1", text_to_speak="hi"))

await latch.get_next_message("s1", is_user_speaking=True)   # -> None  (records speech)
await latch.get_next_message("s1", is_user_speaking=False)  # -> None  (silence < 2.0s)
# …≥ 2.0s of continuous silence later…
await latch.get_next_message("s1", is_user_speaking=False)  # -> ResponsePayload (FIFO release)
```

**Out of scope (later slices):**
- **The Context Injector** (`memory.py`, `ContextInjector`, the idle `asyncio.Lock`, the
  inject-before-return step) — **Slice 3**. In Slice 2 a popped payload carrying
  `silent_context_update` is returned **as-is**: there is no injector yet to violate the
  "inject before TTS" guarantee, so direct return is correct for this slice. Slice 3 inserts the
  injection step before the return.
- `/sandbox` (Pipecat edge, LangGraph backend) — **Slice 4**.

---

## 2. Dependency graph

```
queue.py · HoldingTank   (Slice 1 ✅ — pop = LPOP → FIFO; key agentlatch:queue:{session_id})
 │
 ▼
T5  engine.py · DeliveryEngine.get_next_message(session_id, is_user_speaking)
 │     · last_speech store: SET/GET agentlatch:last_speech:{session_id} (+TTL), via the injectable clock
 │     · is_user_speaking True  → record now, return None
 │     · is_user_speaking False → silence = now() − last_speech
 │          silence < threshold → None ; silence ≥ threshold → tank.pop() (None if empty)
 │     + DAMP timing tests (test_engine.py: fakeredis + injected fake clock, never sleep)
 ▼
T6  core.py · AgentLatch.get_next_message  — strict bool validation, owns a DeliveryEngine
 │     (clock seam, default UTC wall clock), delegates. + facade/round-trip tests.
 │
 ╚══ CP-C ── full library delivery path proven (enqueue → silence ≥ 2.0s → FIFO release); human gate
```

Linear: the engine consumes the Holding Tank; the facade wires the engine. SPEC §9 already fixes the
order ("do not start `engine.py` until `queue.py` has full coverage" — it does, Slice 1).

---

## 3. Design notes (per task)

Acceptance + Verify commands are in [`todo.md`](./todo.md). These are the decisions behind them.

### T5 — `engine.py` · `DeliveryEngine`
- **Constructor:** `DeliveryEngine(redis, tank, *, silence_threshold, session_ttl, now)` where
  `now: Callable[[], float]` returns epoch seconds. The facade passes its existing `self._redis`,
  `self._tank`, `self.silence_threshold`, `self.session_ttl` + the clock.
- **last_speech store:** key `agentlatch:last_speech:{session_id}`. Writes use a **single atomic**
  `SET key str(now()) ex=session_ttl` (not SET-then-EXPIRE, which can leave an immortal key if the
  process dies between commands). Reads: `GET key` → `float | None`.
- **`get_next_message(session_id, is_user_speaking)`** (SPEC §3.4 Option A):
  - `is_user_speaking` → write `now()` to last_speech; return `None`.
  - else → `last = GET last_speech`.
    - **Cold start** (`last is None`): no pause has been observed yet — **seed last_speech = now()
      (same atomic `SET … ex=session_ttl`) and return `None`**, so delivery requires ≥ threshold of
      *subsequent observed* silence and never releases on 0s. [decision §4.2]
    - else `silence = now() − last`. **Skew guard:** a future/negative `silence` (cross-process
      clock skew) is clamped to hold → return `None`, never deliver on a negative delta. If
      `silence < silence_threshold` → `None`; else `return await tank.pop(session_id)` (`None` if
      the queue is empty).
- The engine reads time via a small `_now()` helper that **validates the clock returned a finite
  number** (raises loudly on `NaN`/`inf`), so a misbehaving *injected* clock fails fast instead of
  poisoning `last_speech` or popping immediately. `is_user_speaking` is a **`bool` precondition**
  (asserted) — the real Pydantic `StrictBool` validation is at the public facade (T6); validating at
  both layers would be redundant.
- **Tests** (`test_engine.py`, DAMP — one readable test per case): `fakeredis` + a controllable fake
  clock; no `asyncio.sleep` anywhere. Cover the boundary (1.999 / 2.000 / 2.001s), empty queue,
  **cold-start seeds the baseline and holds**, **future-timestamp holds**, **a clock returning
  `NaN`/`inf` raises**, FIFO, and **speech racing a due delivery** (`is_user_speaking=True` when
  silence would otherwise pop → `None`, no pop, the message stays queued).

### T6 — `core.py` · facade wiring
- **`async def get_next_message(self, session_id, is_user_speaking) -> ResponsePayload | None`:**
  validate both public inputs **with Pydantic** (SPEC §9): `is_user_speaking` via a `StrictBool`
  `TypeAdapter` (rejects `int`/`str`/other with `ValidationError`, no coercion — `bool` is an `int`
  subclass), and **`session_id` normalized via the SAME `NonEmptyStr` `schemas.py` uses** (strip +
  non-empty `TypeAdapter`) so a lookup key always matches what `enqueue` wrote — a raw check would let
  `" s1 "` miss the stored `s1`. Then delegate to `self._engine.get_next_message(...)`.
- **Constructor hardening:** validate `silence_threshold` is a **finite positive non-bool** float
  (reject `True`/`False`/`NaN`/`inf`) and `session_ttl` a **positive non-bool** `int` — `bool` is an
  `int`/`float` subclass, and a `NaN` threshold makes `silence < threshold` always false and pops
  immediately. (Tightens the T3 numeric checks.)
- **Clock seam (SPEC §3.4 mandates an injectable clock):** add keyword-only
  `now: Callable[[], float] | None = None` to `__init__` (default `datetime.now(UTC).timestamp()`).
  Keyword-only ⇒ **non-breaking**, mirroring the deferred `context_injector`. If provided, validate
  it is callable and returns a finite number. **This touches the public constructor → needs human
  sign-off at the gate (SPEC §9 Ask-first); see §4.1.** Build `self._engine` in `__init__`.
- **Tests:** facade round-trip with an injected fake clock — `None` while speaking / short silence;
  payload after enqueue + sufficient silence; non-`bool` raises; a `" s1 "`-vs-`s1` key-match test;
  FIFO end-to-end.

### CP-C — Slice 2 complete (human gate)
Evidence: full suite + `ruff` + `mypy src` green; SPEC §7 gate 2 holds (None when speaking, None when
`silence < 2.0s`, payload only when `silence ≥ 2.0s` and the queue is non-empty); FIFO on delivery;
no-`sleep` respected (injected clock); last_speech key set with TTL. Optional: a live real-Redis
round-trip smoke. Then await explicit approval.

---

## 4. Decisions (revised after cross-check — see §7)

1. **Clock seam exposure — APPROVED at the gate (2026-06-08): keyword-only `now` on
   `AgentLatch.__init__`** (default UTC wall clock; non-breaking, mirrors the deferred
   `context_injector`). SPEC §3.4 mandates an injectable clock; the intent-grounded re-run did not
   object, only requiring it be finite-validated (done). Honors SPEC §9 Ask-first via this sign-off.
2. **Cold-start last_speech ⇒ seed baseline + hold (revised).** No last_speech + not speaking →
   **seed last_speech = now and return `None`**; delivery then needs ≥ threshold of subsequent
   observed silence, so a message never releases on 0s. *(Was "deliver immediately"; changed per the
   cross-check BLOCKER.)*
3. **Silence boundary is `≥` threshold:** exactly `2.000s` of silence **delivers**.
4. **last_speech write is atomic** `SET … ex=session_ttl` (one command, not SET-then-EXPIRE).
5. **Negative/future silence is clamped to hold:** a last_speech timestamp in the future
   (cross-process skew) yields `silence ≤ 0` → `None`, never deliver on a negative delta.
6. **`silent_context_update` returned un-injected in Slice 2** (injector is Slice 3) — a deliberate
   seam, documented in code.
7. **Both public inputs validated with Pydantic (SPEC §9):** `is_user_speaking` via `StrictBool`
   (`ValidationError`, no coercion), `session_id` normalized via the schemas `NonEmptyStr` so delivery
   keys match enqueue keys.
8. **Strict finite-numeric constructor validation:** `silence_threshold` finite-positive **non-bool**
   (reject `True`/`False`/`NaN`/`inf`); `session_ttl` positive **non-bool** `int` (reject
   `True`/`False`/`1.5`/`NaN`/`inf`). (`bool` is an `int`/`float` subclass.) Manual strict checks are
   fine — a full Pydantic model for two scalars is over-engineering; both params get the invalid-type
   tests.

## 5. Open questions (for the human gate)
- **Concurrency — RESOLVED to document (Claude + the intent-grounded cross-check re-run agree).**
  Concurrent `get_next_message` polls for one session could both pass the silence gate and both
  `LPOP`. With the single-loop intent foregrounded, the re-run **withdrew its earlier lock proposal**
  and said to commit to documentation. **Decision:** no Redis lock / Lua (it would contradict SPEC §1
  scope discipline and §6 "boring solution") — make the one-audio-loop-per-session-per-process
  contract **normative** in SPEC §3.4 **and** the `get_next_message` docstring, asserted as a **T6
  acceptance item**. Revisit only if multi-loop ever enters scope.
- Does delivery need to refresh the **queue key TTL** on read? (Likely no — set-on-write suffices.)

## 6. Next step
`dev-loop` always picks up the topmost unchecked, dependency-ready task in
[`todo.md`](./todo.md) — the live status tracker. (This file owns the architecture,
not the cursor.)

## 7. Cross-check record
Reviewed with **gpt-5.5** (effort=high) via `cross-check mode=plan`. **Verdict: CONVERGED in 3 rounds**
(1 BLOCKER + 6 MAJOR → 1 + 4 → 0 + 1; every finding accepted-and-folded except the concurrency lock,
which Claude rejected with a logged reason and routed to the gate).

**Round 1:**
- **BLOCKER — cold start delivers on 0s observed silence** → **accepted**: seed baseline + hold (§4.2).
- **MAJOR — public `now` is an Ask-first signature change** → **accepted**: routed to the human gate (§4.1).
- **MAJOR — SET-then-EXPIRE is non-atomic** → **accepted**: atomic `SET … ex=` (§4.4).
- **MAJOR — cross-process clock skew / negative delta** → **accepted**: clamp to hold + test (§4.5).
- **MAJOR — `session_id` not normalized like `ResponsePayload`** → **accepted**: reuse `NonEmptyStr` (§4.7, T6).
- **MAJOR — missing SPEC §7 "speech races the pop" DAMP test** → **accepted**: added to T5/T6 tests.
- **MAJOR — silence-check + LPOP not atomic (add a lock)** → **rejected, with reason**: single-loop
  contract; a lock contradicts SPEC §1/§6 (§5).
- **MINOR — validate an injected `now` is callable/finite** → **recorded**: folded into T6.

**Round 2 (re-review of the revisions):**
- **BLOCKER — `NaN`/`inf` `silence_threshold` passes validation, pops immediately** → **accepted**:
  finite-numeric constructor validation (§4.8).
- **MAJOR — cold-start seed lacked an explicit TTL** → **accepted**: every last_speech write
  (incl. cold-start) is atomic `SET … ex=session_ttl`.
- **MAJOR — `enqueue("s1", …)` test shorthand implied a signature change** → **accepted**: test uses
  `enqueue(ResponsePayload(session_id="s1", …))`.
- **MAJOR — `is_user_speaking` validated manually, not via Pydantic (§9)** → **accepted**: `StrictBool`
  TypeAdapter (§4.7).
- **MAJOR — concurrency only documented** → **accepted (strengthened)**: make it normative in
  SPEC §3.4 + the public docstring; lock still rejected (single-loop contract, §5).

**Round 3 (verification):**
- **MAJOR — make `silence_threshold`/`session_ttl` strictly non-bool, with `True`/`False`/`NaN`/`inf`
  tests** → **accepted**: strict finite-numeric validation (§4.8). **0 BLOCKERs — loop converged.**

**Re-run (fresh session, with *intent grounding* — SPEC §1–§2 + §3.4 caveat + §6 + HANDOFF):**
- **Concurrency** → with the single-loop intent foregrounded, the critic **withdrew its lock proposal**
  and endorsed documenting the contract → **accepted**, resolved to document (§5). *This validates that
  intent grounding ends re-litigation of settled rationale.*
- **MAJOR — clock validated only at construction; later `NaN`/`inf` poisons state** → **accepted**:
  finite-on-read `_now()` helper (T5).
- **MAJOR — `session_ttl` invalid-type tests missing** → **accepted** (coverage); the "use a Pydantic
  model" framing **rejected** (manual strict checks suffice, §4.8).
- **BLOCKER — engine trusts `is_user_speaking` before T6 validates** → **downgraded/rejected**: the
  engine is not a public surface until T6 wires it; bool precondition + StrictBool at the facade
  (double-validation rejected, T5).
- **MINOR — `session_id` adapter coerces `bytes`** → **rejected, with reason**: must match `enqueue`'s
  existing `NonEmptyStr` handling, or ingest and delivery would diverge.
- The intent grounding **did not induce sycophancy** — the re-run still surfaced new correctness gaps
  (clock-on-read, test coverage); it only stopped re-arguing settled intent.
