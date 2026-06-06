# AgentLatch

Stateful queueing middleware that holds a background agent's message until the
user stops talking, then releases it into a live voice session. AgentLatch
absorbs webhooks from long-running background agents, parks them in Redis, and
waits for voice-activity-detection (VAD) silence before delivering — so async
results never interrupt the human mid-sentence.

> **Status:** early development, built spec-first. See [`SPEC.md`](./SPEC.md) for
> the full design and [`tasks/`](./tasks) for progress.

## Install

```bash
pip install agentlatch              # core (redis + pydantic)
pip install "agentlatch[fastapi]"  # + ready-made webhook router
```

## Documentation

- [`SPEC.md`](./SPEC.md) — objective, architecture, boundaries.
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — dev setup and conventions.

## License

[MIT](./LICENSE)
