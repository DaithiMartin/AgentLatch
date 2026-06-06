# Contributing to AgentLatch

## Dev setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync --extra fastapi              # install core + FastAPI extra + dev tools
git config core.hooksPath .githooks # enable the commit-msg hook (one-time)
```

## Quality gates

```bash
uv run pytest          # tests — no live Redis needed (uses fakeredis)
uv run ruff check .    # lint
uv run ruff format     # format
uv run mypy src        # types
```

## Commits

[Conventional Commits](https://www.conventionalcommits.org/), 50/72 style:
`type(scope): description` where `type` is one of `feat, fix, docs, style,
refactor, perf, test, build, ci, chore, revert`. The subject targets ≤50 chars
(hard cap 72). The `commit-msg` hook (enabled above) enforces this.
