# AgentLatch Sandbox — live end-to-end proving ground

This directory exercises the **whole AgentLatch path in a live environment** (SPEC
[§8](../SPEC.md#8-end-to-end-sandbox-manual-verification)): a Pipecat WebRTC edge
that polls the Delivery Engine and speaks held messages, a LangGraph backend that
fires the webhook after simulated heavy compute, and a human-run **Interruption
Test**. It is **integration scaffolding only** — it consumes the published core
library and **adds nothing to it**.

> **Scaffold status:** Redis + this contract land in **T10**. The two apps below
> are filled in by later Slice-4 tasks — `edge_pipecat/receiver.py` (**T11**),
> `backend_langgraph/graph.py` (**T12**), `edge_pipecat/{frame_processor,edge}.py`
> (**T13**). The full Interruption Test (**CP-E**) needs T10–T13 merged.

---

## The isolation contract (SPEC §5 — non-negotiable)

`pipecat-ai`, `langchain`, and `langgraph` (and their transitive deps) live
**only** here, each app in **its own venv / `requirements.txt`**. They **never**
enter core `pyproject.toml` — not as a dependency, not as an extra. `fastapi`
stays an **optional extra** of core, pulled in here only via that extra.

- **One venv per app.** `edge_pipecat/` and `backend_langgraph/` each get their
  own virtualenv; never share one, never install the heavy libs into the repo's
  core `.venv`.
- **Core is consumed, never modified.** The sandbox installs AgentLatch as a
  package (editable, with the `[fastapi]` extra where needed). The single core
  edit this whole slice is allowed is T10's one `[tool.ruff] extend-exclude` line
  (tooling scope, no dependency). If the sandbox reveals a *core* bug or a missing
  seam, that is a **SPEC/plan finding** — fix core via `dev-loop`, do **not** patch
  around it here.
- **The boundary is verified, not just intended.** Each Slice-4 task re-asserts it
  with a freeze gate: `grep -rniE 'pipecat|langgraph|langchain' pyproject.toml
  uv.lock .github src` returns nothing, and `git diff --exit-code` on
  `src/agentlatch`, `uv.lock`, and `pyproject.toml` stays clean (the lock catches a
  *transitive* leak a name grep would miss).

---

## Bring up Redis (shared by both apps)

From the repo root, the single dev datastore (SPEC §8):

```bash
docker compose up -d                 # start redis:alpine on :6379
docker compose ps                    # redis should be "healthy" (redis-cli ping)
docker compose down                  # stop + remove when done
```

Both apps point at `redis://localhost:6379`. The compose file defines **only**
this Redis service — the apps run on the host, not in containers.

## The shared session id

The backend POSTs `session_id` and the edge polls `agentlatch:queue:<session_id>`
— they **must be the same string** or the message is enqueued under one key and
polled under another (silent non-delivery). Export one `SESSION_ID` in **every**
shell that runs an app, so the whole runbook uses one value:

```bash
export SESSION_ID=sandbox-demo
```

Both sides **log the sid they used** so a mismatch is obvious.

---

## Run each app

Each app has its own setup; create the venv **inside** its directory.

### `edge_pipecat/` — the Fast Edge (Zone 1) · T11 + T13
Absorbs webhooks (`receiver.py`, run under uvicorn) **and** delivers held messages
to TTS (`frame_processor.py` + `edge.py`). Run as **two processes** sharing one
Redis — only the delivery process polls, so the single-poll-loop contract (SPEC
§3.4) holds.

```bash
cd edge_pipecat
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # (T11) installs core via the [fastapi] extra, editable
uvicorn receiver:app                  # the webhook absorber → POST /api/v1/queue_response  (T11)
# in a second shell, same SESSION_ID:  python edge.py        # the polling delivery edge   (T13)
```

### `backend_langgraph/` — the Deep Backend (Zone 3) · T12
A LangGraph `StateGraph` whose one node sleeps (simulated heavy compute) then
`httpx`-POSTs a `ResponsePayload`-shaped body to the receiver's webhook. It is a
**black-box HTTP client** — it knows only the URL and the JSON shape, never
AgentLatch internals.

```bash
cd backend_langgraph
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # langgraph + httpx
SESSION_ID=$SESSION_ID python graph.py   # invoke the compiled graph → fires the webhook  (T12)
```

---

## The Interruption Test (CP-E) — the final human gate

The SPEC §8 final verification, run by a human on a real WebRTC client. **Do not
duplicate the spec here** — read [SPEC §8](../SPEC.md#8-end-to-end-sandbox-manual-verification)
for the authoritative definition. Operationally:

1. `docker compose up -d` (Redis healthy); `export SESSION_ID=sandbox-demo` in
   every shell.
2. Start the edge: `uvicorn receiver:app` (shell 1) and `python edge.py` (shell 2).
3. Join the WebRTC session and **speak one long continuous sentence**.
4. **Mid-sentence**, trigger the LangGraph backend (`python graph.py`, shell 3,
   same `SESSION_ID`) so the webhook fires.
5. **PASS** = AgentLatch **holds** the message in Redis and the TTS is injected
   **only after** the human pauses **> 2.0s** — never cutting into live speech.

**Evidence** is a timestamped, **sandbox-side** log (core stays frozen — it emits
no instrumentation this slice): the **posted sid** and the **polled sid** (must
match) → enqueue → **Redis queue length + TTL before/after each poll** (read
directly from Redis) → VAD **start/stop** → the FrameProcessor's **own computed
silence** → the poll **returning a payload vs `None`** → the **`TextFrame` push** —
showing the message sat in Redis through the whole utterance and surfaced only
after the > 2.0s gap. A screen recording may accompany the log but is not
sufficient alone.
