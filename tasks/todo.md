# TODO — Slice 3: The Context Injector

Status tracker for the `dev-loop`. Architecture, DAG, and design notes live in
[`plan.md`](./plan.md); intent + boundaries in [`SPEC.md`](../SPEC.md).

> **Slices 1 & 2 ✅ COMPLETE** — T0–T6 merged, CP-A/CP-B/CP-C approved (PRs #1–#9); the
> ingest **and** delivery paths are built and live-smoke-verified. Full history in git.
> **This slice (3)** injects silent context into the live LLM's memory **before** a held
> message is spoken. Plan signed off 2026-06-08; **T7–T8 merged** (PRs #10–#11); **T9 in review**
> (PR #12) — per-task status below.

**Status legend:** `[ ]` pending · `[~]` PR open (link) · `[x]` merged.
A task flips to `[x]` only on **merge** (the next task's start flips it).
A `CP-*` checkpoint flips to `[x]` only on the user's **explicit approval**.

---

### T7 — `memory.py` · `ContextInjector` ABC + `SessionLocks`  `[x]` — [PR #10](https://github.com/DaithiMartin/AgentLatch/pull/10) (merged)
- **Depends on:** none (Slices 1 & 2 merged).
- **Do:** new `src/agentlatch/memory.py` — (1) `class ContextInjector(abc.ABC)` with one abstract
  coroutine `async def inject_context(self, session_id: str, data: dict[str, Any]) -> None` (SPEC §3.3;
  docstring states the override must be `async`); (2) `class SessionLocks` — a registry whose
  `get(self, session_id: str) -> asyncio.Lock` lazily creates and caches one in-process `asyncio.Lock`
  per session in a `weakref.WeakValueDictionary`, so a session's lock self-cleans once unreferenced
  while keeping identity whenever it's held (single-event-loop use, plan §4.6/§4.7). Export
  `ContextInjector` from `src/agentlatch/__init__.py` `__all__` (`SessionLocks` stays internal). Add
  `tests/test_memory.py`.
- **Acceptance:** (all met — 9 tests; independent Claude reviewer **PASS** + Codex `cross-check
  mode=diff` **0 BLOCKER/0 MAJOR**)
  - [x] Instantiating `ContextInjector()` directly raises `TypeError`; a subclass that doesn't
        implement `inject_context` also raises `TypeError`. A concrete `async` subclass instantiates
        and its `inject_context` is awaitable, returning `None`. **(+ a *sync* override is rejected at
        class-definition via `__init_subclass__` — folded from the diff cross-check.)**
  - [x] `SessionLocks().get(sid)` returns an `asyncio.Lock`; the **same** object on repeated calls for
        one `sid` (while a reference is held), and **distinct** objects for distinct `sid`s; the weak
        backing is pinned **and** an unreferenced lock is GC-released (self-cleaning proven).
  - [x] `ContextInjector` imports from the top-level package and is in `__all__` (asserted by **exact**
        set equality); existing `AgentLatch`/`ResponsePayload` exports still resolve. `SessionLocks`
        is **not** exported.
- **Verify:** `uv run pytest tests/test_memory.py && uv run ruff check . && uv run mypy src`

### T8 — `engine.py` · inject-before-return under the per-session lock  `[x]` — [PR #11](https://github.com/DaithiMartin/AgentLatch/pull/11) (merged)
- **Depends on:** T7 — merged.
- **Do:** `DeliveryEngine.__init__` gains keyword-only `injector: ContextInjector | None = None` and
  `locks: SessionLocks | None = None` (engine creates its own if not supplied; the facade passes its
  shared registry — keeps existing engine tests green). Replace the final `return await self._tank.pop(...)`
  with: pop → if `payload.silent_context_update is not None` **and** `injector is not None`,
  `async with self._locks.get(session_id): await self._injector.inject_context(session_id,
  payload.silent_context_update)`; then `return payload`. Guard is `is not None` (empty `{}` still
  injects). At-most-once on failure (pop precedes inject — plan §4.4). Add `tests/test_inject.py` +
  a `FakeInjector` spy in `tests/conftest.py`.
- **Acceptance:** (all met — 11 inject tests; independent Claude reviewer **PASS** (mutation-tested) +
  Codex `cross-check mode=diff` **0 BLOCKER/0 MAJOR**; proofs hardened via cross-check — plan §7)
  - [x] **Inject-before-return is gated** (not a race): with a **blocking** injector, the
        `get_next_message` task is **not done** while injection blocks; after release it returns the
        payload. (`entered.wait()` is wrapped in `asyncio.wait_for` so a non-injecting bug *fails* fast.)
  - [x] The per-session lock is **held during** inject (also **while suspended**) and **released
        after** — asserted on the **captured lock object** (retained past the `WeakValueDictionary`),
        after both **success and a raising** inject; `engine._locks.get(sid)` **is** that same object.
  - [x] An **empty `{}`** `silent_context_update` still injects (`is not None`, not truthy).
  - [x] **No injection AND no lock acquisition** when there's no `silent_context_update`, no injector,
        or an empty queue — a `SessionLocks` get-spy records **zero** acquisitions on those paths.
  - [x] **At-most-once:** a raising `inject_context` records `queue.length == 0` **at inject entry**
        (LPOP preceded inject) **and** `queue.length == 0` **after** the exception propagates (not
        re-queued).
  - [x] The lock wraps **only** inject: a **pop-spy** sees the session lock **unlocked during the pop**.
  - [x] Two ctx payloads release **FIFO** with an injection between each. No `asyncio.sleep`; in-process
        `asyncio.Lock` only, never a Redis lock.
  - [x] Direct `DeliveryEngine` construction with a **sync** `inject_context` duck object raises
        `AssertionError` (reaches the precondition), so a bad injector can't fail after an `LPOP`.
- **Verify:** `uv run pytest tests/test_inject.py tests/test_engine.py && uv run ruff check . && uv run mypy src`

### T9 — `core.py` · wire `context_injector` + expose `memory_lock`  `[~]` — [PR #12](https://github.com/DaithiMartin/AgentLatch/pull/12)
- **Depends on:** T8 — merged.
- **Do:** add keyword-only `context_injector: ContextInjector | None = None` to `AgentLatch.__init__`;
  validate it is `None` **or** a `ContextInjector` instance, else raise **`ValueError`** (async-ness is
  already guaranteed by the ABC's `__init_subclass__` from T7 — plan §4.8). Own a `SessionLocks`; pass
  `injector` + `locks` to the `DeliveryEngine`. Add `memory_lock(self, session_id: str) ->
  asyncio.Lock` returning the lock for the **normalized** id (same `NonEmptyStr` adapter as
  `get_next_message`), with a docstring stating the read-cooperation contract (plan §4.5/§4.6). Extend
  `tests/test_inject.py` + `tests/test_core.py`. **`context_injector` + `memory_lock` are public-surface
  changes → gate sign-off (SPEC §9 Ask-first; APPROVED 2026-06-08).**
- **Bundled `fix(memory)` (surfaced by the T9 diff cross-check):** T9's facade trusts the ABC's
  async guarantee, but `ContextInjector.__init_subclass__` checked only `cls.__dict__`, so a sync method
  inherited from a mixin (`class Bad(SyncMixin, ContextInjector)`) bypassed it → an instantiable
  `ContextInjector` with a sync method, awaited only **after** the `LPOP` under `python -O`. Hardened to
  resolve via the MRO (`getattr`); +2 regression tests in `tests/test_memory.py`.
- **Acceptance:** (all met — 12 facade tests + 2 ABC-regression tests; independent Claude reviewer
  **PASS** ×2 (facade + the memory fix, both mutation-tested) + Codex `cross-check mode=diff`
  **CONVERGED** — R1 1 BLOCKER (the `__dict__` bypass) accepted & fixed, R2 **0 BLOCKER/0 MAJOR**)
  - [x] Round-trip: `enqueue(ResponsePayload(…, silent_context_update={…}))` then, after the injected
        clock advances ≥ 2.0s, `get_next_message` awaits the injector's `inject_context` **then**
        returns the `ResponsePayload`.
  - [x] Default construction (no `context_injector`) returns the payload **un-injected** — Slice 2
        behavior intact; exports unchanged except the new `ContextInjector` (T7).
  - [x] A non-`ContextInjector` (e.g. `object()`, a bare function) → `ValueError` at construction. (A
        **sync** `inject_context` subclass can no longer be *defined* — the ABC rejects it, now even via
        mixin inheritance after the bundled fix.)
  - [x] `memory_lock(" s1 ")` returns the **same** lock object the injection for `"s1"` acquires
        (normalized id) — the developer's read-lock matches the injection lock.
  - [x] The **documented safe order** round-trips: acquire+release `memory_lock("s1")`, then
        `get_next_message` injects fine. (`memory_lock` is a non-reentrant `asyncio.Lock`: holding it
        across a `get_next_message` that injects would deadlock — documented on the method, plan §4.9.)
- **Verify:** `uv run pytest && uv run ruff check . && uv run mypy src`

### CP-D — Slice 3 complete · inject-before-TTS proven (human gate)  `[ ]`
- **Depends on:** T9 merged.
- **Evidence to present:** full suite + `ruff` + `mypy src` green; SPEC §7 gate 4 holds
  (`inject_context` awaited **under `memory_lock`**, **before** the payload is returned, **only** when
  `silent_context_update` is present **and** an injector is configured); the no-injector path is
  unchanged; injection ordering proven (memory updated before the text surfaces); `memory_lock` returns
  the same lock the injection uses. Optional: a live real-Redis round-trip with a real injector.
- **Approval:** await explicit user OK, then mark `[x]`.

---

## Open decisions (for the human gate — cross-checked 3 rounds, see [`plan.md`](./plan.md) §7)
**Both public-surface items signed off 2026-06-08, before T9 is implemented.**
- [x] **`context_injector` constructor param** (Ask-first): **APPROVED 2026-06-08** — keyword-only
      ⇒ non-breaking, pre-planned in SPEC §3.3/§4.
- [x] **Expose `memory_lock(session_id)`** (Ask-first public addition): **APPROVED 2026-06-08** — the
      cross-check showed a deferred accessor leaves the lock decorative and §3.3's memory-safety
      contract unsatisfiable (plan §4.5). Non-reentrant `asyncio.Lock` (deadlock contract documented,
      plan §4.9).
*Resolved by cross-check (3 rounds, CONVERGED): inject-before-return uses `is not None` (empty `{}`
injects); injector validated as a coroutine fn at construction (facade `ValueError` + engine precondition
`assert`); lock released-after asserted (success + exception); at-most-once documented publicly;
one-event-loop-per-instance documented; `memory_lock` deadlock (non-reentrancy) contract documented;
lock registry uses a `WeakValueDictionary` so it self-cleans with no leak (the bounded-LRU idea was
rejected as unsafe once locks are public). One logged rejection: the engine precondition stays an
`assert` (the facade does the authoritative `ValueError`).*
