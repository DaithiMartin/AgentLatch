# Plan — Slice 4: The End-to-End Sandbox

> Source of truth: [`SPEC.md`](../SPEC.md) (esp. §5 structure + **Architectural boundary**, §8 the
> sandbox + Interruption Test, §9 Boundaries, §10 item 4). Per-task status, Acceptance, and Verify
> live in [`todo.md`](./todo.md) (the dev-loop tracker). This file owns the **architecture, dependency
> graph, and design notes**. Workflow conventions are the repo's
> [`dev-loop`](../.claude/skills/dev-loop/SKILL.md) skill — not restated here.
>
> **Previous:** Slices 1–3 COMPLETE — Receiver + Holding Tank (PRs #1–#5), Delivery Engine (PRs #8–#9),
> Context Injector (PRs #10–#13); CP-A–CP-D approved. The full **core library** (`schemas`, `queue`,
> `engine`, `memory`, `core`, `integrations/fastapi`) is built, tested, and live-smoke-verified.
> Status in [`todo.md`](./todo.md); git for history.

**Created:** 2026-06-08 · **Status:** drafted — pending cross-check (§7) + human sign-off

---

## 1. Goal of this slice

Prove the whole AgentLatch path **in a live environment** — VAD timing + Redis queueing end-to-end —
without adding one line to the core library. Slice 4 is **integration scaffolding only**: a dev Redis,
a Pipecat WebRTC edge that polls the Delivery Engine and speaks held messages, a LangGraph backend that
fires the webhook after simulated heavy compute, and the human-run **Interruption Test** (SPEC §8) as
the final gate.

The capability proven: a background result that arrives **mid-sentence** is **held** in Redis and
released into TTS **only after the human pauses > 2.0s** — never interrupting speech.

**The hard constraint that shapes every task (SPEC §5 boundary, §9 Never):** `pipecat-ai`, `langchain`,
`langgraph` (and their transitive deps) live **only** under `/sandbox`, each app in its **own venv /
`requirements.txt`**. They **never** touch core `pyproject.toml`. `fastapi` stays an optional extra.
Each task's Verify re-asserts this with the **full freeze gate** (§4.8/§4.12): a
`pipecat|langgraph|langchain` scan across `pyproject.toml`/`uv.lock`/`.github`/`src` **plus**
`git diff --exit-code` on `src/agentlatch`, `uv.lock`, and `pyproject.toml` (the last changes **only**
in T10, for the one `ruff` line) — name-grep alone misses transitive lock leaks.

```
                 docker-compose (redis:alpine :6379)   ← T10
                          ▲                 ▲
        enqueue (RPUSH)   │                 │  poll get_next_message / LPOP
                          │                 │
   backend_langgraph ──POST /api/v1/queue_response──►  edge_pipecat
   (sleep → httpx)        │   receiver (create_router)   ├─ receiver.py  ← T11
        T12               │   + delivery (FrameProcessor)└─ edge.py / frame_processor.py ← T13
                          ▼                 ▼
                     one Redis; one AgentLatch per process; shared via Redis keys
                          │
                          ╚══ CP-E ── the Interruption Test (human, live WebRTC)
```

**Out of scope:** any change to the core library or its public API (Slice 4 is consumer-only); a
production deployment topology; auth on the webhook; persisting sandbox state. If the sandbox reveals a
*core* bug or a missing seam, that is a **SPEC/plan finding** — stop and fix the core via `dev-loop`,
don't patch around it in the sandbox.

---

## 2. Dependency graph

```
core library (Slices 1–3 ✅) — consumed as an installed package, never modified
 │
 ▼
T10  docker-compose.yml (redis:alpine :6379) + /sandbox/README.md (isolation contract + runbook)
 │      + core tooling: exclude /sandbox from `ruff check .` (keep core lint off sandbox code)
 ▼
T11  sandbox/edge_pipecat/receiver.py — AgentLatch(redis_url) + create_router (the Slice-1 router);
 │      runnable via uvicorn; requirements.txt = agentlatch[fastapi] (editable, local) + uvicorn
 ▼
T12  sandbox/backend_langgraph/graph.py — StateGraph: one node sleeps then httpx-POSTs the webhook;
 │      requirements.txt = langgraph + httpx.  (Depends on T11: a receiver to POST to)
 ▼
T13  sandbox/edge_pipecat/{frame_processor.py, edge.py} — Pipecat FrameProcessor polls
 │      get_next_message, wraps a returned payload in TextFrame; minimal pipeline wiring; a
 │      sandbox-local FrameProcessor unit test. requirements += pipecat-ai.  (Depends on T11)
 │
 ╚══ CP-E ── the Interruption Test (SPEC §8), human-run on live WebRTC; needs T10–T13 merged + redis up
```

Bottom-up and each task leaves a **provable** increment: Redis up (T10) → webhook→Redis over HTTP
(T11) → backend→webhook→Redis (T12) → held message → TextFrame under silence (T13) → full human loop
(CP-E). Risk is concentrated in T13 (the unfamiliar Pipecat API); its core logic is unit-tested in
isolation so the WebRTC-only parts are all that remain for the human gate.

---

## 3. Design notes (per task)

Acceptance + Verify commands are in [`todo.md`](./todo.md). These are the decisions behind them.

### T10 — dev Redis + sandbox scaffold
- **`docker-compose.yml` at root** runs **only** `redis:alpine` on `6379` (SPEC §8 — "dev + sandbox
  only"). The sandbox apps run on the host in their own venvs; compose is just the shared datastore. A
  named service `redis` with `ports: ["6379:6379"]` and a healthcheck (`redis-cli ping`).
- **`sandbox/README.md`** is the home for: the **isolation contract** (own venv per app; never add
  pipecat/langchain/langgraph to core), how to bring up Redis, how to run each app, and the
  **Interruption Test runbook** (the CP-E steps). It points to SPEC §8, doesn't duplicate it.
- **Core tooling — exclude `/sandbox` from the core lint.** `uv run ruff check .` runs from the repo
  root and would otherwise lint sandbox code (which imports uninstalled-in-core libs and follows its
  own conventions). Add `extend-exclude = ["sandbox"]` under `[tool.ruff]`. **This is the one core
  `pyproject.toml` edit in the slice** — it is *tooling scope only*, adds **no** dependency, and keeps
  the §5 boundary clean (core checks never reach into sandbox). `mypy` already scopes to `src` only;
  `pytest` already scopes to `tests` only — confirm both still hold (no sandbox collection).
- **Decision — compose runs Redis only, not the apps.** Faithful to SPEC §8 ("docker-compose.yml … runs
  redis:alpine"). Containerizing pipecat (audio/WebRTC) is out of scope and fights the "own venv" model.

### T11 — edge receiver (the webhook absorber)
- **`sandbox/edge_pipecat/receiver.py`:** construct `AgentLatch(redis_url="redis://localhost:6379")`
  and mount `create_router(latch)` (SPEC §3.1 / §4 — the Slice-1 optional router exposing
  `POST /api/v1/queue_response`). Runnable: `uvicorn receiver:app`. The receiver **only enqueues**
  (RPUSH) — it never polls, so it cannot violate the single-poll-loop contract (SPEC §3.4).
- **Co-location decision:** the receiver lives **inside `edge_pipecat/`** (not a third sandbox dir),
  matching SPEC §5's two-app layout. Edge = Zone-1; it both **absorbs** webhooks (receiver.py) and
  **delivers** (edge.py, T13). They run as **two processes** sharing one Redis (separate processes
  keep uvicorn and the Pipecat runtime from fighting over one event loop, and only the delivery process
  polls — so the concurrency contract holds). Production might co-locate them in one process; the
  sandbox splits them for simplicity. Both use the same `redis_url`.
- **`requirements.txt`:** install the **local core package with the fastapi extra**, editable, plus
  `uvicorn` — e.g. `-e ../..[fastapi]` (or `pip install -e '../..[fastapi]'`). This pulls `fastapi`
  via the *extra*, never into core. No core dep added.

### T12 — LangGraph backend (the webhook fire)
- **`sandbox/backend_langgraph/graph.py`:** a `StateGraph` with **one node** that does
  `await asyncio.sleep(10)` (simulated heavy Zone-3 compute) then `await httpx.AsyncClient().post(
  WEBHOOK_URL, json=<ResponsePayload-shaped body>)`. `WEBHOOK_URL` from env (default
  `http://localhost:8000/api/v1/queue_response`). The POST body matches `ResponsePayload`
  (`session_id`, `text_to_speak`, optional `silent_context_update`) — **the contract is owned by core
  `schemas.py`**; the backend must not invent fields (extra keys → 422).
- **`requirements.txt`:** `langgraph` + `httpx`. **Never** `agentlatch` core internals — the backend
  is a black-box HTTP client; it knows only the URL + the JSON shape.
- **Verify** drives the graph against the **running T11 receiver** and asserts `202 {"status":
  "queued"}` + the item landed in Redis. The 10s sleep is configurable (env) so the smoke check
  doesn't wait the full duration.

### T13 — Pipecat edge delivery (poll → TextFrame)
- **`sandbox/edge_pipecat/frame_processor.py`:** a custom `FrameProcessor` whose `process_frame`
  (1) **tracks** the live `is_user_speaking` *state* from Pipecat's VAD frames
  (`UserStartedSpeakingFrame` → True, `UserStoppedSpeakingFrame` → False) and (2) **polls
  `latch.get_next_message(session_id, is_user_speaking)` continuously on the frame cadence** — not only
  on the VAD events — then on a returned payload **pushes a `TextFrame(text=payload.text_to_speak)`
  downstream** (**never a raw string** — SPEC §9 Never). All other frames pass through untouched.
- **TIMING TRAP (cross-check BLOCKER, §4.9):** the engine writes `last_speech` **only** when polled
  with `is_user_speaking=True` (SPEC §3.4). If the processor polled **only** on VAD start/stop, a long
  utterance would mark `last_speech` at speech-*start*, then the stop-poll would compute
  `silence = now − start` ≥ 2.0s and **release immediately at speech-end with zero real silence**. So
  the processor **must keep polling while the user speaks** (each `True` poll re-stamps `last_speech` to
  ~now), making `last_speech` track the *most recent* speech moment; only then does a genuine ≥2.0s gap
  after the *last* speech frame release the message. This is the "natural Pipecat poll loop" SPEC §2
  decision 2 assumes — poll every frame, not every VAD event.
- **`sandbox/edge_pipecat/edge.py`:** minimal pipeline wiring (transport → VAD → our FrameProcessor →
  TTS) constructing the **same** `AgentLatch(redis_url=…)` the receiver uses, polling the shared
  `SESSION_ID` (§4.10). Pipecat specifics are **source-driven** (build with
  `agent-skills:source-driven-development` — do **not** guess the API); the plan fixes only the
  *contract*, not the Pipecat call signatures.
- **SILENCE CADENCE (cross-check R2-MAJOR).** The continuous-poll design needs `process_frame` to keep
  firing **during silence** — but if Pipecat emits no frames while the user is quiet, polling starves
  and the message never releases. **Source-verify** Pipecat's silence behavior; if it does **not** tick
  during silence, drive polling with the edge's **own periodic poll** (a small-interval task). This is
  the consumer's *poll cadence*, **not** silence measurement — silence is still computed in core by
  timestamp diffing (SPEC §3.4/§9); the cadence sleep never evaluates the window. Settle the exact
  mechanism at build time against Pipecat docs; an **automated** edge test must show polls continue after
  `UserStoppedSpeakingFrame`.
- **ONE serialized poll path (cross-check R3-BLOCKER, §3.4).** The frame-driven poll **and** any periodic
  timer poll target the *same* `SESSION_ID` — running both concurrently is **two pollers on one session**,
  exactly what §3.4 forbids (one could `LPOP` a message the other gated, or write `last_speech` between a
  poll's silence check and its `LPOP`). So they must funnel through **one serialized poll path**: a single
  poll coroutine with an **in-flight guard** (no overlapping `get_next_message` for a session), or the
  timer fires **only when** the frame cadence is absent. Never two live poll loops per session.
- **`requirements.txt` += `pipecat-ai`** (its own venv). **Unit-testable core:** the FrameProcessor's
  poll→TextFrame logic is provable **without** WebRTC — a `sandbox/edge_pipecat/test_frame_processor.py`
  (its own venv) drives the processor with an `AgentLatch` on a real/`fakeredis` Redis + **injected
  clock**, asserting the full timing curve deterministically (no `sleep`): start-speaking → several
  continued-speech polls (each re-stamps `last_speech`) → stop-speaking → at **1.999s** of silence
  **no `TextFrame`** is pushed → crossing **≥2.0s** pushes **exactly one** `TextFrame` whose text ==
  `payload.text_to_speak`. This both proves "only after >2.0s silence" *and* guards the stale-timestamp
  trap above (a start-only-poll implementation fails the 1.999s case by releasing early). A raw `str`
  is **never** pushed. This sandbox test is **not** collected by core `uv run pytest`
  (`testpaths=["tests"]`).
- **Single poll loop:** exactly one FrameProcessor polls a given session (one WebRTC stream ⇒ one loop),
  honoring SPEC §3.4. The receiver process never polls.

### CP-E — the Interruption Test (human gate, live)
The SPEC §8 final gate, run by a human on a real WebRTC client: (1) start the Pipecat session and speak
one long continuous sentence; (2) trigger the LangGraph backend mid-sentence so the webhook fires;
(3) **PASS** = AgentLatch holds the message in Redis and the TTS is injected **only** after the human
pauses > 2.0s. Needs T10–T13 **merged** + `docker compose up`.
- **Evidence — timestamped, sandbox-side log of the held-then-released path** (so the timing is
  *provable* and diagnosable, **without** touching frozen core, §4.11): structured lines for the
  webhook **enqueue** (and the **sid** posted), the edge's **polled sid**, **Redis queue length + TTL
  before and after** each poll (read directly from Redis), the FrameProcessor's **own computed
  silence**, whether the poll **returned a payload vs None**, and the **`TextFrame` push** — showing
  the message sat in Redis through the whole utterance and surfaced only after the > 2.0s gap. Posted
  sid == polled sid (§4.10). (A screen recording may accompany it but is not sufficient alone.)
- **Boundary-integrity check** (precise): `[project.dependencies]` in core `pyproject.toml` is **only**
  `redis` + `pydantic`; `fastapi` appears **only** as the optional extra; `pipecat-ai`/`langgraph`/
  `langchain` appear **nowhere** outside `/sandbox` — scanned across `pyproject.toml`, **`uv.lock`**,
  `.github/`, and `src/` (not pyproject alone). Each sandbox app carries its own `requirements.txt`.
- **Doc-freshness** + a sandbox-README/runbook sanity (a fresh reader can run the test from the docs
  alone) run here too, per dev-loop. Then explicit approval.

---

## 4. Decisions

1. **Sandbox is consumer-only; zero core changes** except the one **ruff exclude** in `pyproject.toml`
   (tooling scope, no dep). Any core defect the sandbox surfaces is fixed in core via `dev-loop`, not
   worked around — SPEC §1 scope discipline.
2. **The three heavy libs never enter core.** `pipecat-ai`/`langgraph`/`langchain` live in per-app
   `requirements.txt` under `/sandbox`; each task's Verify runs the full freeze gate — name-scan +
   `git diff --exit-code` on `src/agentlatch`/`uv.lock`/`pyproject.toml` — to prove it (SPEC §9 Never).
3. **Receiver co-located in `edge_pipecat/`, run as a separate process** from the delivery edge (SPEC §5
   two-app layout; avoids a uvicorn-vs-Pipecat single-loop clash; only delivery polls, so §3.4 holds).
4. **compose runs Redis only** (SPEC §8); apps run on the host in their own venvs.
5. **The webhook body is `ResponsePayload`, owned by core `schemas.py`.** The backend is a black-box
   HTTP client; it must not invent fields (`extra="forbid"` ⇒ 422). No new contract is introduced.
6. **Pipecat/LangGraph specifics are source-driven, not planned.** The plan fixes the *contracts*
   (poll → TextFrame; sleep → POST `ResponsePayload`); the unfamiliar API calls are settled at build
   time against official docs (`agent-skills:source-driven-development`) to avoid inventing signatures.
7. **FrameProcessor logic is unit-tested in isolation** so the human gate (CP-E) covers only the
   genuinely manual WebRTC/audio path — de-risking the one unfamiliar-API task.
8. **Core-freeze is *verified*, not just intended (cross-check B1).** Every sandbox task's Verify runs
   `git diff --exit-code -- src/agentlatch` (no core source touched) and restricts the only allowed
   `pyproject.toml` change to T10's single `ruff` exclude line. The boundary scan covers
   `pyproject.toml` + **`uv.lock`** + `.github/` + `src/` for the three forbidden libs — a sandbox
   `uv add` would surface in `uv.lock` even if `pyproject` looked clean.
9. **Continuous-poll timing contract (cross-check B2).** The FrameProcessor polls `get_next_message`
   on the frame cadence and re-stamps `last_speech` throughout speech; polling only on VAD start/stop
   would release a held message the instant the user stops (elapsed speech counted as silence). Proven
   by the injected-clock 1.999s-no-push / ≥2.0s-one-push test (§3 T13).
10. **Shared `SESSION_ID` contract (cross-check R2-B1).** The edge polls `agentlatch:queue:<sid>` and
    the backend POSTs `session_id` — they **must be the same string** or the message is enqueued under
    one key and polled under another (silent non-delivery). A single `SESSION_ID` env (default e.g.
    `sandbox-demo`) is read by **both** the backend (in the POST body) and the edge (passed to
    `get_next_message`); the runbook sets it once. Both sides **log the sid they used** so a mismatch is
    obvious. (T11/T12/T13 + CP-E.)
11. **Observability is sandbox-side only — core stays frozen (cross-check R2-B2).** CP-E's timing proof
    must **not** require core to emit "computed silence"/"LPOP" events (core exposes only
    `get_next_message()` and is frozen this slice). The FrameProcessor derives and logs its **own**
    silence (it owns the clock + speaking state) and reads **Redis qlen/TTL before+after** the poll
    directly; "the poll returned a payload vs None" stands in for the LPOP. No core instrumentation.
12. **`uv.lock` is frozen across the slice (cross-check R2-MAJOR).** No core dependency changes, so the
    lock must be byte-identical — each task Verifies `git diff --exit-code -- uv.lock`. This catches a
    **transitive** leak (a sandbox `uv add` pulling pipecat/langgraph deps into the core lock) that a
    name-only grep would miss. (The T10 `[tool.ruff]` edit is under tooling, not deps, so it doesn't
    touch the lock.) Each task also `git diff --exit-code -- pyproject.toml` (unchanged after T10's one
    line) so a hard-dep can't drift in behind a stale lock; boundary/freeze checks run from the **repo
    root** (not a sandbox cwd).
13. **One serialized poll path per session (cross-check R3-BLOCKER, §3.4).** The frame-driven poll and
    any periodic-poll fallback (§3 T13) must funnel through a **single** poll coroutine with an in-flight
    guard, or the timer fires only when frames are absent — never two concurrent pollers on one
    `SESSION_ID`. Proven by an automated T13 test (post-stop polling continues, no overlap).

## 5. Open questions (for the human gate) — RESOLVED
Both signed off at the plan gate **2026-06-08**, before T10 starts:
- **`pyproject.toml` ruff `extend-exclude = ["sandbox"]`** — **APPROVED**. The one core-file edit this
  slice (tooling scope, no dependency); made in T10. (Alternative `sandbox/.ruff.toml` declined — one
  exclude line is clearer.)
- **Sandbox lib versions** — **APPROVED unpinned**. `pipecat-ai` / `langgraph` stay unpinned (latest) in
  the throwaway sandbox `requirements.txt`; pin only if a breaking release bites.

## 6. Next step
`dev-loop` picks up the topmost unchecked, dependency-ready task in [`todo.md`](./todo.md) — **T10**.

## 7. Cross-check record
Reviewed with **gpt-5.5** (effort=high) via `cross-check mode=plan`. **Verdict: CONVERGED** (rounds
used: 3 — the cap). Every BLOCKER/MAJOR was **accepted and folded**; zero rejections.

**Round 1 — 2 BLOCKER + 6 MAJOR + 2 MINOR (all folded):**
- BLOCKER — core-freeze never verified → per-task `git diff --exit-code -- src/agentlatch` + pyproject
  restricted to T10's ruff line (§4.8).
- BLOCKER — T13 stale-timestamp early release (poll only on VAD start/stop ⇒ release at speech-end) →
  continuous-poll, re-stamp `last_speech` throughout speech; 1.999s/≥2.0s injected-clock test (§4.9).
- MAJOR ×6 — T13 just-before-threshold test; CP-E observability vague; T11 TTL+payload-shape; T12
  fail-on-non-202; boundary scan widened to uv.lock/.github/src; racey uvicorn smoke → readiness+trap.
- MINOR ×2 — CP-E "redis+pydantic only" wording vs the fastapi extra; ephemeral ruff placeholder.

**Round 2 — 2 BLOCKER + 4 MAJOR + 1 MINOR (all folded):**
- BLOCKER — edge/​backend `session_id` never shared ⇒ enqueue-vs-poll key mismatch → shared `SESSION_ID`
  contract, both sides logged (§4.10).
- BLOCKER — CP-E "engine computed silence/LPOP" logs would force core instrumentation (frozen) →
  observability moved fully sandbox-side (§4.11).
- MAJOR — T11 invalid-body `LLEN==0` false; LRANGE≠raw bytes (model serializes defaults) → parse as
  `ResponsePayload`; uv.lock transitive leak → `git diff --exit-code -- uv.lock` (§4.12); Pipecat may
  not tick during silence → source-verify + periodic-poll fallback. MINOR — trap-cleanup the ruff file.

**Round 3 (cap) — 1 BLOCKER + 4 MAJOR + 2 MINOR (all folded; no further critic pass):**
- BLOCKER — the periodic-poll fallback could run **alongside** frame-poll ⇒ two concurrent same-session
  pollers (§3.4 violation) → **one serialized poll path** + in-flight guard (§4.13).
- MAJOR — T11 boundary cmds used repo-root paths from a sandbox cwd → run freeze checks from repo root;
  pyproject not frozen in T11–T13 → add `git diff --exit-code -- pyproject.toml`; fixed-key `LLEN==1`
  flaky on rerun → unique sid / DEL / increment-by-one; T13 post-stop polling proof made an **automated**
  test (not "or runbook"). MINOR — concrete `ResponsePayload`-parse helper; §1/§4 wording synced to the
  stricter freeze gate.

**Note:** the round-3 folds (one-poll-path, pyproject freeze, automated post-stop test, Redis isolation)
landed in the final round, so — like the T7 WeakValueDictionary fix — they carry **no further critic
pass**; flagged here for the implementer to honor in T13's tests and each task's Verify.
