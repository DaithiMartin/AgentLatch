# Plan — Slice 3: The Context Injector

> Source of truth: [`SPEC.md`](../SPEC.md) (esp. §3.3, §3.4, §4, §7 gate 4, §9).
> Per-task status, Acceptance, and Verify steps live in [`todo.md`](./todo.md) (the
> dev-loop tracker). This file owns the **architecture, dependency graph, and design
> notes** for this slice. Workflow conventions are the repo's
> [`dev-loop`](../.claude/skills/dev-loop/SKILL.md) skill — not restated here.
>
> **Previous:** Slice 1 (Receiver + Holding Tank, PRs #1–#5) and Slice 2 (Delivery
> Engine, PRs #8–#9, CP-C approved) — COMPLETE. Status in [`todo.md`](./todo.md); git for history.

**Created:** 2026-06-08 · **Status:** drafted — cross-checked (see §7), pending human sign-off

---

## 1. Goal of this slice

Silently update the live LLM's memory **before** a held message is spoken, so the agent
can speak naturally about a backend result. Implement `memory.py` (the `ContextInjector`
ABC **and** the per-session lock registry that guards mutation), wire the inject-before-
return step into the Delivery Engine, and expose the cooperation lock on the facade:

```python
class MyInjector(ContextInjector):
    async def inject_context(self, session_id: str, data: dict) -> None:
        async with my_memory_guard:          # the developer's own mutation is already
            await my_llm.update_memory(session_id, data)   # called UNDER latch.memory_lock(sid)

latch = AgentLatch(redis_url="redis://…", silence_threshold=2.0, context_injector=MyInjector())
await latch.enqueue(ResponsePayload(
    session_id="s1", text_to_speak="your order shipped",
    silent_context_update={"order_status": "shipped"},
))
# In the voice loop, the developer guards their LLM memory READS with the SAME lock:
async with latch.memory_lock("s1"):
    ... read memory while generating ...
# …≥ 2.0s of silence later…
msg = await latch.get_next_message("s1", is_user_speaking=False)
# inject_context(...) is awaited UNDER latch.memory_lock("s1") BEFORE msg is returned →
# the LLM's memory is updated before the text reaches TTS, with no read/write interleave.
```

**Out of scope (later slices):**
- **`/sandbox`** (Pipecat edge, LangGraph backend, the live Interruption Test) — **Slice 4**.
- A persistent/cross-process lock or lock garbage-collection — see §4.7 (v1 limitation).

---

## 2. Dependency graph

```
schemas.ResponsePayload (silent_context_update field — Slice 1 ✅)
queue.HoldingTank (LPOP/FIFO — Slice 1 ✅) · engine.DeliveryEngine (Slice 2 ✅)
 │
 ▼
T7  memory.py · ContextInjector ABC: async inject_context(session_id, data) -> None
 │     + SessionLocks registry: get(session_id) -> asyncio.Lock (lazy, per-session, in-process)
 │     + top-level export `from agentlatch import ContextInjector` (SPEC §4)
 ▼
T8  engine.py · inject-before-return under the per-session lock
 │     pop → if payload.silent_context_update is not None AND injector is not None:
 │              async with locks.get(session_id): await injector.inject_context(session_id, data)
 │           return payload                       (un-injected when no injector / no ctx)
 │     + DAMP tests (FakeInjector spy): inject ran UNDER the lock, BEFORE return; lock RELEASED
 │       after success AND after a raising injector; empty `{}` still injects; no-injector regression
 ▼
T9  core.py · AgentLatch(context_injector=…) keyword-only; validate it's a ContextInjector whose
 │     inject_context is a coroutine fn; own the SessionLocks, pass injector+locks to the engine;
 │     expose `memory_lock(session_id) -> asyncio.Lock` (normalized id); facade round-trip tests
 │
 ╚══ CP-D ── inject-before-TTS proven (memory updated before the payload surfaces); human gate
```

Bottom-up: define the contract + lock (T7), enforce the guarantee at the engine (T8),
expose it on the facade (T9). SPEC §9 "one module at a time" is honored.

---

## 3. Design notes (per task)

Acceptance + Verify commands are in [`todo.md`](./todo.md). These are the decisions behind them.

### T7 — `memory.py` · `ContextInjector` ABC + `SessionLocks`
- **ABC:** `class ContextInjector(abc.ABC)` with one abstract coroutine
  `async def inject_context(self, session_id: str, data: dict[str, Any]) -> None` (SPEC §3.3).
  `@abstractmethod` alone only enforces the name is overridden — **not** that the override is a
  coroutine — so the ABC adds an `__init_subclass__` that **rejects a non-coroutine (sync or
  non-callable) override at class-definition time** (`inspect.iscoroutinefunction`). This is the
  strongest, unbypassable layer (you cannot even define a sync injector), folded from the T7 diff
  cross-check; it supersedes the facade-only check that was planned for T9 (§4.8).
- **`SessionLocks`:** a tiny registry — `get(self, session_id: str) -> asyncio.Lock`, lazily
  creating and caching one `asyncio.Lock` per session id in a **`weakref.WeakValueDictionary`** (§4.7).
  A session's lock survives exactly while someone holds a strong ref — an in-flight injection (the
  engine's `async with`) or the developer's `memory_lock` block — and is GC'd once nobody does, so the
  registry **self-cleans** with no lifetime leak while preserving lock **identity** whenever mutual
  exclusion actually matters. Lazy creation is safe because all access is from a single event loop
  (§4.6); `asyncio.Lock()` is loop-unbound at construction and weakly-referenceable on 3.11.
- **Export:** add `ContextInjector` to `agentlatch/__init__.py` `__all__` (SPEC §4). (`SessionLocks`
  stays internal — exposed only through `AgentLatch.memory_lock`.)

### T8 — `engine.py` · inject-before-return under the per-session lock
- **Engine gains** keyword-only `injector: ContextInjector | None = None` and
  `locks: SessionLocks | None = None` (defaults to a fresh internal `SessionLocks()` if not supplied —
  keeps the existing `test_engine.py` constructions green; the facade passes its **shared** registry so
  `memory_lock` returns the same lock the engine acquires — §4.5). `__init__` asserts the precondition
  `injector is None or inspect.iscoroutinefunction(injector.inject_context)` — mirroring the existing
  `is_user_speaking` bool-assert, this catches a bad injector passed via **direct** engine construction
  at construction time (before any `LPOP`); the user-facing `ValueError` lives at the facade (§4.8).
- **Wiring** (replaces the final `return await self._tank.pop(session_id)`):
  ```
  payload = await self._tank.pop(session_id)
  if payload is not None and payload.silent_context_update is not None and self._injector is not None:
      async with self._locks.get(session_id):
          await self._injector.inject_context(session_id, payload.silent_context_update)
  return payload
  ```
  Realises SPEC §3.4 (*LPOP → if `silent_context_update` run the injector under the lock → return*).
  The guard is **`is not None`**, not truthiness, so an **empty `{}`** context update still injects
  (it is "present"). The lock wraps **only** the `inject_context` await.
- **At-most-once on failure** (§4.4): LPOP precedes inject, so a raising `inject_context` propagates
  and the popped message is **not** returned or re-queued. `async with` releases the lock on both the
  success and exception paths.
- **Tests** (`test_inject.py`, DAMP — fakeredis + injected clock + a configurable `FakeInjector` spy in
  `conftest.py`). The cross-check hardened the *proofs* (§7), so they are precise about ordering and the
  `WeakValueDictionary`/lock-identity interaction:
  - **Inject-before-return is gated:** a **blocking** injector (`asyncio.Event`) lets the test assert the
    `get_next_message` task is **not done** while injection blocks, then returns the payload after
    release. `entered.wait()` is wrapped in `asyncio.wait_for` (test-hang guard, not silence timing).
  - **Lock held/released on the captured object:** the `FakeInjector` retains a **strong ref** to the
    exact lock during injection; the test asserts `lock_held_during_call is True`, then that *same*
    object is unlocked after **success and a raise**, and `engine._locks.get(sid)` **is** it (so the
    release assertion can't be fooled by a freshly-GC-recreated lock).
  - **At-most-once:** the raising injector records `queue.length` **at entry** (== 0 ⇒ LPOP-before-inject)
    and the test asserts `queue.length == 0` **after** the exception (not re-queued).
  - **Lock scope:** a pop-spy asserts the lock is **unlocked during the pop** (wraps only inject); a
    `SessionLocks` get-spy asserts **zero** lock acquisitions on the no-ctx / no-injector / empty-queue
    paths.
  - Plus: **empty `{}`** still injects; the no-injector path is the **Slice 2 regression** (un-injected
    return); two ctx payloads release **FIFO** with injection between; direct construction with a **sync**
    `inject_context` duck raises `AssertionError`. No `asyncio.sleep`, no Redis lock.

### T9 — `core.py` · facade wiring + the cooperation lock
- **Constructor:** add keyword-only `context_injector: ContextInjector | None = None`. Validate it is
  `None` **or** an instance of `ContextInjector`, else **`ValueError`** (§4.8). The `async`-ness of
  `inject_context` is already guaranteed by the ABC's `__init_subclass__` (T7), so no
  `iscoroutinefunction` check is needed here. Own a `SessionLocks`; pass
  `injector=context_injector, locks=self._locks` to the `DeliveryEngine`. Keyword-only ⇒ non-breaking.
  **Public constructor change → Ask-first / gate sign-off (§4.1, APPROVED).**
- **Expose the cooperation lock** (§4.5): `def memory_lock(self, session_id: str) -> asyncio.Lock`
  returning `self._locks.get(<normalized id>)` — normalized through the **same** `NonEmptyStr`
  adapter `get_next_message`/`enqueue` use, so the developer's lock is the **same object** the
  injection acquires. Its docstring states the contract: *guard your LLM memory reads with
  `async with latch.memory_lock(session_id)`; AgentLatch calls `inject_context` under this lock.*
  **Deadlock footgun (§4.9):** the lock is a plain non-reentrant `asyncio.Lock`, so the docstring also
  warns — **never call `get_next_message` for a session while already holding its `memory_lock`**: a due
  injection re-acquires the same lock and the task deadlocks. Acquire `memory_lock` around reads only;
  release it before polling.
- **Tests** (extend `test_inject.py` + `test_core.py`): round-trip — enqueue a payload with
  `silent_context_update`, advance the clock ≥ 2.0s → the injector's `inject_context` is awaited
  (spy) **then** the `ResponsePayload` is returned; default construction (no injector) returns the
  payload **un-injected**; a non-`ContextInjector` (e.g. `object()`) **or** a `ContextInjector`
  subclass whose `inject_context` is **sync**/non-callable → `ValueError` at construction;
  `memory_lock(" s1 ")` returns the **same** lock object the injection for `"s1"` acquires; and the
  **documented safe order** round-trips — acquire+release `memory_lock("s1")`, then `get_next_message`
  injects fine (proving the contract, not the deadlock).

### CP-D — Slice 3 complete (human gate)
Evidence: full suite + `ruff` + `mypy src` green; SPEC §7 gate 4 holds (`inject_context` awaited
**under `memory_lock`**, **before** the payload is returned, **only** when `silent_context_update`
is present **and** an injector is configured); the no-injector path is unchanged (un-injected return);
injection ordering proven (memory updated before the text surfaces); `memory_lock` returns the same
lock the injection uses. Optional: a live real-Redis round-trip with a real injector. Then await
explicit approval.

---

## 4. Decisions

1. **`context_injector` keyword-only on `__init__`** (`ContextInjector | None`, default `None`).
   Pre-planned in SPEC §3.3/§4; non-breaking. Ask-first public signature → **human sign-off at the
   gate** (mirrors the `now` seam in Slice 2).
2. **Injection lives in the ENGINE, not the facade.** SPEC §3.4 explicitly describes inject-before-
   return as the Delivery Engine's behavior. The new engine→`memory` dependency is internal/downward,
   not public API. (Considered facade-orchestration; rejected — §3.4 is the source of truth.)
3. **Per-session `asyncio.Lock`, in-process, guarding only the `inject_context` await.** It protects
   **in-process LLM memory**, and the single-loop-per-session contract (SPEC §3.4, normative since
   Slice 2) means one process owns a session's loop. A Redis/distributed lock would be the wrong
   layer **and** broker-scope creep (§1/§6).
4. **At-most-once on injection failure.** Per §3.4 ordering (LPOP → inject → return), the message is
   already popped when injection runs; a raising `inject_context` propagates and the message is not
   returned or re-queued. Documented in the **public `get_next_message` docstring** so users know a
   failing injector drops the dequeued message (M7).
5. **Expose `memory_lock(session_id)` THIS slice (revised — was "defer").** The cross-check showed a
   deferred accessor makes the lock **decorative**: without it, consumers cannot put their LLM memory
   **reads** under AgentLatch's lock, so §3.3's read/write safety contract is unsatisfiable and §9
   "never mutate memory without the guard" cannot hold end-to-end. So the `SessionLocks` registry
   lives in `memory.py` (shared), the facade **owns and exposes** it, and the cooperation contract is
   documented. Public-surface addition → routed to the gate (§5).
6. **One event loop per `AgentLatch` instance (normative).** `asyncio.Lock` is loop-affine and the
   registry's lazy creation assumes single-event-loop access. This extends the single-loop-per-session
   contract; documented on `memory_lock` and the class. (No cross-thread locking — that would be
   over-engineering for an async-first library.)
7. **Lock-registry self-cleans via `weakref.WeakValueDictionary` (no leak).** The registry holds locks
   by **weak** reference, so a session's `asyncio.Lock` lives exactly as long as a strong reference
   exists — an in-flight injection's `async with`, or a developer's `memory_lock` block — and is
   garbage-collected once unreferenced. This preserves lock **identity** at every instant mutual
   exclusion matters (both parties are in an `async with`, so both hold strong refs to the *same* live
   object) while bounding memory to *referenced* locks, not *all sessions ever seen*. A bounded-LRU
   that evicts unlocked locks was **rejected**: once `memory_lock` exposes raw lock identity, a
   developer can cache a reference to an evicted lock and later acquire a *different* object than the
   injection uses, silently breaking mutual exclusion — weak-value storage avoids this because a
   referenced lock is never collected.
8. **`inject_context` is enforced `async` at the ABC (revised in T7).** The `ContextInjector`
   `__init_subclass__` rejects a non-coroutine override at **class-definition** time, so any
   `ContextInjector` *instance* is guaranteed to have an `async inject_context` — the strongest,
   unbypassable layer (B1/M9, folded from the T7 diff cross-check). Therefore **T9's facade check
   simplifies to `isinstance(context_injector, ContextInjector)` (or `None`)**, raising `ValueError`
   for anything else (e.g. `object()`); the `iscoroutinefunction` check is no longer needed at the
   facade. The `DeliveryEngine` keeps a lightweight precondition `assert` for direct-construction
   misuse (§3 T8).
9. **`memory_lock` is a non-reentrant `asyncio.Lock` (deadlock contract).** SPEC §3.3 specifies a plain
   `asyncio.Lock`, which is not reentrant, so holding `memory_lock(sid)` across a `get_next_message(sid)`
   that injects would deadlock (the injection re-acquires the same lock). The contract — read under the
   lock, release, then poll — is documented on `memory_lock` and proven by a safe-order test (§3 T9). A
   reentrant lock would deviate from §3.3 and is out of scope.

## 5. Open questions (for the human gate) — RESOLVED
Both public-surface items were **signed off at the plan gate 2026-06-08**, before T7 starts:
- **`context_injector` constructor addition** — **APPROVED** (Ask-first, pre-planned in SPEC §3.3/§4).
- **Expose `memory_lock(session_id)`** — **APPROVED** (the cross-check established a deferred accessor
  leaves the lock decorative and the memory-safety contract unsatisfiable, §4.5; non-reentrancy
  deadlock contract documented, §4.9).
- _(Lock-registry growth is no longer an open question — solved in v1 by the `WeakValueDictionary`
  registry, §4.7.)_

## 6. Next step
`dev-loop` always picks up the topmost unchecked, dependency-ready task in
[`todo.md`](./todo.md) — the live status tracker. (This file owns the architecture, not the cursor.)

## 7. Cross-check record
Reviewed with **gpt-5.5** (effort=high) via `cross-check mode=plan`.

**Round 1 — 2 BLOCKER + 5 MAJOR + 2 MINOR (all accepted/folded):**
- **BLOCKER — `@abstractmethod` doesn't enforce `async`/callable** → coroutine-fn validation at
  construction (§4.8).
- **BLOCKER — deferring `memory_lock` makes the lock decorative** → expose it now; registry moved to
  `memory.py`; read-cooperation contract documented (§4.5).
- MAJOR — truthy guard skips empty `{}` → `is not None` everywhere (§3 T8).
- MAJOR — lock-registry growth → documented limitation (§4.7); _build-GC rejected, then re-framed R2._
- MAJOR — lazy-lock single-loop assumption → "one event loop per instance" normative (§4.6).
- MAJOR — `.locked()` only proves an instant → assert lock released after success **and** exception (§3 T8).
- MAJOR — at-most-once undocumented for users → public `get_next_message` docstring note (§4.4).
- MINOR — §1 "+ lock" vs T7 → reconciled (registry now in `memory.py`). MINOR — pick one exception → `ValueError`.

**Round 2 — 0 BLOCKER + 3 MAJOR + 1 MINOR (all accepted/folded):**
- MAJOR — engine bypasses injector validation on direct construction → precondition `assert` in
  `DeliveryEngine.__init__` (§3 T8, §4.8).
- MAJOR — `memory_lock` non-reentrant ⇒ deadlock if held across `get_next_message` → documented
  acquisition-order contract + safe-order test (§4.9, §3 T9).
- MAJOR — §4.7 understated the leak → corrected to *total distinct sessions over instance lifetime*;
  bounded-LRU recorded as the future fix; build-vs-defer routed to the gate (§4.7, §5).
- MINOR — sign-off timing → stated as required **before** implementing T9 (§5).

**Round 3 (verification) — 0 BLOCKER + 2 MAJOR. Verdict: CONVERGED at the rounds cap.**
- MAJOR — recorded bounded-LRU future fix is unsound once locks are public (cached ref to an evicted
  lock ≠ injection's new lock) → **accepted**: replaced with a **`weakref.WeakValueDictionary`** registry
  that self-cleans while preserving identity (§4.7) — this also *supersedes* the round-2 leak limitation.
- MAJOR — engine precondition `assert` is stripped under `python -O` → **rejected, with reason**: the
  facade (the only public surface) validates with a real `raise ValueError`, not an assert; the engine
  assert is an internal dev-time precondition matching the merged Slice-2 `is_user_speaking` bool-assert,
  and the unguarded case is direct-engine-construction + `-O` + a bad injector (knowing internal misuse).

**Verdict: CONVERGED** (rounds used: 3). One logged rejection (engine `-O` assert); the WeakValueDictionary
fix landed in the final round, so it carries no further critic pass — flagged for the implementer's tests.
