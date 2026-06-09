# TODO — Slice 4: The End-to-End Sandbox

Status tracker for the `dev-loop`. Architecture, DAG, and design notes live in
[`plan.md`](./plan.md); intent + boundaries in [`SPEC.md`](../SPEC.md).

> **Slices 1–3 ✅ COMPLETE** — T0–T9 merged, CP-A/CP-B/CP-C/CP-D approved (PRs #1–#13); the core
> library (ingest, delivery, context injection) is built and live-smoke-verified. Full history in git.
> **This slice (4)** proves the whole path in a **live environment** (SPEC §8) — a dev Redis, a Pipecat
> WebRTC edge, a LangGraph backend, and the human **Interruption Test** — **without changing core**.
> Plan drafted 2026-06-08; **T10–T11 merged ([#16](https://github.com/DaithiMartin/AgentLatch/pull/16), [#18](https://github.com/DaithiMartin/AgentLatch/pull/18)); T12 PR-open ([#19](https://github.com/DaithiMartin/AgentLatch/pull/19)); next: T13**.

**Status legend:** `[ ]` pending · `[~]` PR open (link) · `[x]` merged.
A task flips to `[x]` only on **merge** (the next task's start flips it).
A `CP-*` checkpoint flips to `[x]` only on the user's **explicit approval**.

> **Slice-wide boundary (SPEC §5 / §9 Never) — every task re-asserts it:** `pipecat-ai`, `langchain`,
> `langgraph` live **only** under `/sandbox`, each app in its own venv / `requirements.txt`; they
> **never** enter core `pyproject.toml`. The only core edit in this slice is T10's `ruff` exclude.

---

### T10 — dev Redis + sandbox scaffold  `[x]` — [PR #16](https://github.com/DaithiMartin/AgentLatch/pull/16) (merged)
- **Depends on:** none (Slices 1–3 merged).
- **Do:** root `docker-compose.yml` running **only** `redis:alpine` on `6379` (with a `redis-cli ping`
  healthcheck); `sandbox/README.md` documenting the **isolation contract** (own venv per app; never add
  the three libs to core), how to bring Redis up, how to run each app, and the **Interruption Test
  runbook** (CP-E steps) — pointing to SPEC §8, not duplicating it. In core `pyproject.toml`, add
  `extend-exclude = ["sandbox"]` under `[tool.ruff]` (tooling scope only — **no dependency added**).
- **Acceptance:**
  - [x] `docker compose config` is valid; `docker compose up -d` makes Redis answer `PING` on `6379`;
        `docker compose down` cleans up. compose defines **only** the `redis` service (no app services).
        *(Redis bound to `127.0.0.1` per cross-check — unauthenticated dev datastore stays off the LAN.)*
  - [x] `sandbox/README.md` states the §5 isolation boundary, the per-app run steps, and the CP-E runbook.
  - [x] `uv run ruff check .` stays clean and does **not** lint `sandbox/`: prove it with an
        **ephemeral** `sandbox/_rufftest.py` carrying a deliberate unused import — created **and
        deleted inside the Verify step** (never committed) — that `ruff check .` does **not** flag.
        `uv run pytest` still passes **122** (no sandbox collection); `uv run mypy src` clean.
        *(Mutation-tested: removing the exclude flips ruff red, F401 — gate is load-bearing.)*
  - [x] **Core untouched:** `git diff --exit-code -- src/agentlatch` is empty; the only
        `pyproject.toml` change is the `[tool.ruff] extend-exclude` line (no dep added; deps stay
        `redis` + `pydantic`, `fastapi` extra only).
- **Verify:** `docker compose config && docker compose up -d && docker exec $(docker compose ps -q redis) redis-cli ping && docker compose down` ; ruff exclude proof with cleanup-on-failure: `( trap 'rm -f sandbox/_rufftest.py' EXIT; printf 'import os\n' > sandbox/_rufftest.py && uv run ruff check . )` ; `uv run pytest -q && uv run mypy src` ; **core frozen:** `git diff --exit-code -- src/agentlatch` **and** `git diff --exit-code -- uv.lock` (lock unchanged — the ruff edit isn't a dep) ; **boundary scan:** `grep -rniE 'pipecat|langgraph|langchain' pyproject.toml uv.lock .github src` returns **nothing**.

### T11 — edge receiver: AgentLatch + FastAPI router  `[x]` — [PR #18](https://github.com/DaithiMartin/AgentLatch/pull/18) (merged)
- **Depends on:** T10.
- **Do:** `sandbox/edge_pipecat/receiver.py` — construct `AgentLatch(redis_url="redis://localhost:6379")`
  and mount `create_router(latch)` (the Slice-1 router → `POST /api/v1/queue_response`), exposing
  `app` for `uvicorn receiver:app`. `sandbox/edge_pipecat/requirements.txt` installing the **local core
  package with the fastapi extra**, editable (`-e ../..[fastapi]`), plus `uvicorn`. Brief run notes in
  `sandbox/edge_pipecat/README.md` (or folded into the sandbox README). The receiver **only enqueues**.
- **Acceptance:**
  - [x] In a fresh `edge_pipecat` venv, `pip install -r requirements.txt` pulls `fastapi` via the
        **extra** (not core); core `pyproject` still lists only `redis` + `pydantic`.
  - [x] `uvicorn receiver:app` starts; `POST /api/v1/queue_response` with a valid `ResponsePayload`
        body (using the shared `SESSION_ID`) → **202** `{"status":"queued"}`; the item is in Redis with
        a **bounded TTL** (`0 < TTL agentlatch:queue:<SESSION_ID> ≤ 3600`); the stored value, **parsed
        as `ResponsePayload`** (core serializes the model — incl. `silent_context_update: null` — so
        compare *normalized fields*, not raw request bytes), has the `session_id`/`text_to_speak` posted.
  - [x] An invalid body (missing `text_to_speak` or an extra key) → **422** and the queue length is
        **unchanged** by that request (check the count before vs after — not "== 0", since a prior valid
        item may be queued).
  - [x] **Core frozen:** `git diff --exit-code -- src/agentlatch` and `git diff --exit-code -- uv.lock`
        both empty; core `pyproject` lists only `redis` + `pydantic` (`fastapi` extra only).
  - [x] **Enqueue-only (§3.4):** the receiver process **never polls** — `grep -q get_next_message
        sandbox/edge_pipecat/receiver.py` returns nothing. (Added from the T11 verify review: makes the
        §3.4 "receiver doesn't poll" property a mechanical gate, not inspection-only.)
- **Verify:** (Redis up) from `sandbox/edge_pipecat`: fresh venv + `pip install -r requirements.txt`; start `uvicorn receiver:app` with a **readiness wait** (poll `:8000` until up; **fail if 8000 already occupied**) and a `trap`-killed PID. Use a **unique sid per run** (or `redis-cli DEL agentlatch:queue:$SID` first) so stale state can't skew it: valid POST == **202**, `redis-cli TTL agentlatch:queue:$SID` in `(0,3600]`, and a **parse helper** asserts the stored value is a `ResponsePayload` — `python -c "import json,redis,agentlatch; r=redis.Redis(); raw=r.lindex(f'agentlatch:queue:{SID}',-1); p=agentlatch.ResponsePayload(**json.loads(raw)); assert p.text_to_speak==EXPECTED"`; record `LLEN`, then invalid POST == **422** with `LLEN` **unchanged**. **Boundary/freeze (from repo root, e.g. `git -C ../.. …`):** `grep -rniE 'pipecat|langgraph|langchain' ../../pyproject.toml ../../uv.lock ../../.github ../../src` empty; `git -C ../.. diff --exit-code -- src/agentlatch uv.lock pyproject.toml`. **Enqueue-only (§3.4):** `grep -q get_next_message receiver.py` returns nothing (the receiver must never poll).

### T12 — LangGraph backend: webhook fire after compute  `[~]` — [PR #19](https://github.com/DaithiMartin/AgentLatch/pull/19)
- **Depends on:** T11 (a receiver to POST to).
- **Do:** `sandbox/backend_langgraph/graph.py` — a `StateGraph` with **one node**: `await
  asyncio.sleep(SLEEP_S)` (default 10; env-overridable for the smoke check) then `httpx` POST a
  `ResponsePayload`-shaped body to `WEBHOOK_URL` (env, default
  `http://localhost:8000/api/v1/queue_response`). The body's `session_id` is the shared **`SESSION_ID`**
  env (default `sandbox-demo`) — **the same value the edge polls** (plan §4.10) — and the node **logs
  the sid it posted**. `sandbox/backend_langgraph/requirements.txt` =
  `langgraph` + `httpx`. The backend is a **black-box HTTP client** — it knows only the URL + JSON
  shape. It **must treat a non-202 as failure** (`raise_for_status()` / assert `status_code == 202`),
  so a 422 from a contract drift surfaces loudly rather than being swallowed.
- **Acceptance:**
  - [x] Invoking the compiled graph (with `SLEEP_S` small) sleeps, then POSTs a **valid**
        `ResponsePayload` JSON to `WEBHOOK_URL`; against the running T11 receiver it gets **202** and
        the **exact `session_id` it emitted** is the key that gains a message in Redis. A non-202
        response **raises** (no silent swallow). *(Non-202 raise now gated in Verify — see below.)*
  - [x] `requirements.txt` contains `langgraph` + `httpx` and **no** `agentlatch` internals; core
        `pyproject`/`uv.lock` carry no `langgraph`/`langchain` (lock byte-identical).
- **Verify:** (Redis + T11 receiver running) `backend_langgraph` venv + install; with a clean key (`redis-cli DEL agentlatch:queue:$SID`) record `LLEN`, then `SLEEP_S=0 SESSION_ID=$SID WEBHOOK_URL=http://localhost:8000/api/v1/queue_response python -c '...ainvoke...'` exits 0 (202 asserted via raise_for_status) and `LLEN agentlatch:queue:$SID` **increments by exactly one** (same sid the edge will poll). **Boundary/freeze (repo root):** `grep -rniE 'pipecat|langgraph|langchain' pyproject.toml uv.lock .github src` empty; `git diff --exit-code -- src/agentlatch uv.lock pyproject.toml`. **Non-202 raises (no swallow):** re-run with `WEBHOOK_URL=http://localhost:8000/api/v1/WRONG` (404) → `python graph.py` exits **non-zero** and `LLEN` unchanged (a contract drift / 422 must surface, never be swallowed).

### T13 — Pipecat edge delivery: poll → TextFrame  `[ ]`
- **Depends on:** T11 (shares the edge `AgentLatch`); T10 (Redis).
- **Do:** `sandbox/edge_pipecat/frame_processor.py` — a custom Pipecat `FrameProcessor` that
  **tracks** `is_user_speaking` *state* from VAD frames (`UserStartedSpeakingFrame`→True,
  `UserStoppedSpeakingFrame`→False) and **polls `latch.get_next_message(session_id, is_user_speaking)`
  continuously on the frame cadence** (NOT only on VAD events — see the timing trap below), pushing
  `TextFrame(text=payload.text_to_speak)` downstream on a returned payload (**never a raw string** —
  SPEC §9 Never), passing all other frames through. `sandbox/edge_pipecat/edge.py` — minimal pipeline
  wiring (transport → VAD → processor → TTS) on the same `AgentLatch(redis_url=…)`. `requirements.txt
  += pipecat-ai`. `sandbox/edge_pipecat/test_frame_processor.py` — sandbox-local unit test.
  **Build with `agent-skills:source-driven-development`** (Pipecat API is unfamiliar — don't guess it).
  Poll the shared **`SESSION_ID`** (plan §4.10 — the key the backend posts to).
  **TIMING TRAP (plan §3 T13 / §4.9):** the engine stamps `last_speech` only on `is_user_speaking=True`,
  so polling **only** on start/stop releases the moment the user stops (speech counted as silence). The
  processor **must re-poll while speaking** so `last_speech` tracks the *last* speech frame.
  **SILENCE CADENCE + ONE POLL PATH (plan §3 T13, §4.13):** **source-verify** whether Pipecat fires
  `process_frame` during silence; if it does **not**, drive polling with the edge's **own periodic poll**
  (poll cadence only — silence is still measured in core by timestamp diffing, never by the cadence
  sleep). The frame-driven poll and the timer poll must funnel through **one serialized poll path** (a
  single poll coroutine + in-flight guard, or the timer firing only when frames are absent) — **never
  two concurrent pollers** on one session (SPEC §3.4). Polls must continue **after**
  `UserStoppedSpeakingFrame` or a held message starves.
- **Acceptance:**
  - [ ] `test_frame_processor.py` (edge venv, **injected clock — no `sleep`**) proves the timing curve:
        start-speaking → several **continued-speech** polls (each re-stamps `last_speech`) → stop →
        at **1.999s** of silence **no `TextFrame`** is pushed → crossing **≥2.0s** pushes **exactly one**
        `TextFrame` whose text == `payload.text_to_speak`. (A start/stop-only implementation fails the
        1.999s case by releasing early.) A raw `str` is **never** pushed.
  - [ ] **Post-stop polling continues (automated):** a deterministic test (no real silence frames /
        injected ticks) proves the processor **keeps polling after `UserStoppedSpeakingFrame`** via the
        serialized poll path — and that the frame-poll and periodic-poll **never run concurrently** for
        one session (in-flight guard) — so a held message is neither starved nor double-polled (§3.4).
  - [ ] **Core frozen:** `uv run pytest` still **122** (sandbox test not collected); core
        `pyproject`/`uv.lock` gain **no** `pipecat-ai`; `git diff --exit-code -- src/agentlatch uv.lock
        pyproject.toml` empty.
- **Verify:** (edge venv) `pytest sandbox/edge_pipecat/test_frame_processor.py` green; from core: `uv run pytest -q` == 122; **boundary/freeze:** `grep -rniE 'pipecat|langgraph|langchain' pyproject.toml uv.lock .github src` empty; `git diff --exit-code -- src/agentlatch uv.lock pyproject.toml`. (Full WebRTC run is the CP-E human test.)

### CP-E — the Interruption Test · live, end-to-end (human gate)  `[ ]`
- **Depends on:** T10–T13 merged + `docker compose up`.
- **Evidence to present:** the SPEC §8 Interruption Test run live — start the Pipecat session, speak one
  long continuous sentence, trigger the LangGraph backend mid-sentence; **PASS** = the message is held
  in Redis and the TTS is injected **only** after the human pauses > 2.0s. Evidence is a
  **timestamped, sandbox-side log** (no core instrumentation — core is frozen): the **posted sid** and
  the **polled sid** (must match) → **enqueue** → **Redis queue length + TTL before/after each poll**
  (read directly) → VAD **start/stop** → the FrameProcessor's **own computed silence** → the poll
  **returning a payload vs None** → **`TextFrame` push** — showing the message sat in Redis through the
  utterance and surfaced only after the > 2.0s gap.
- **Boundary-integrity check (precise):** `[project.dependencies]` in core `pyproject.toml` is **only**
  `redis` + `pydantic`; `fastapi` is **optional-only**; `pipecat-ai`/`langgraph`/`langchain` appear
  **nowhere** outside `/sandbox` — scanned across `pyproject.toml`, **`uv.lock`**, `.github/`, `src/`
  (`grep -rniE 'pipecat|langgraph|langchain' pyproject.toml uv.lock .github src` empty). Each sandbox
  app carries its own `requirements.txt`. Also: **doc-freshness** audit + a sandbox **README/runbook**
  sanity (a fresh reader can run the test from the docs alone).
- **Approval:** await explicit user OK, then mark `[x]`. This closes Slice 4 — the SPEC §8/§10 build.

---

## Open decisions (signed off at the plan gate 2026-06-08 — see [`plan.md`](./plan.md) §5)
- [x] **`pyproject.toml` `[tool.ruff] extend-exclude = ["sandbox"]`** — **APPROVED** (tooling only, no
      dependency); the one core edit this slice, made in T10.
- [x] **Leave `pipecat-ai` / `langgraph` unpinned** in the throwaway sandbox `requirements.txt` —
      **APPROVED** (pin only if a breaking release bites).
