# Test conventions

Mechanical conventions for this suite. The testing **strategy** (DAMP over DRY,
`fakeredis` not live Redis, never `asyncio.sleep` — advance the injected clock instead)
is owned by [`SPEC.md` §7](../SPEC.md); this note only covers the repo-specific mechanics
that aren't there.

## `tests/` is a package
There is a `tests/__init__.py`, so import shared test doubles with the **package path**:

```python
from tests.conftest import FakeInjector, SpyLocks   # ✅
from conftest import FakeInjector                    # ❌ ModuleNotFoundError
```

## Shared fixtures + doubles live in `conftest.py`
- **Fixtures:** `redis_client` (a `fakeredis` async client), `tank` (a `HoldingTank` on it),
  `clock` (a `FakeClock` instance — a manually-advanced, sleep-free clock).
- **Doubles:** `FakeClock`, `FakeInjector` (a configurable `ContextInjector` spy — records calls,
  can observe the held lock / queue length, can block on a gate or raise), and `SpyLocks` (a
  `SessionLocks` that records `get()` calls so a test can assert zero lock acquisitions).

Add a new reusable double to `conftest.py` so every test module can import it.

## Async tests need no decorator
`pytest-asyncio` runs in `mode=auto` (see `pyproject.toml`), so an `async def test_...` is collected
and run automatically — don't add `@pytest.mark.asyncio`.

## Run
```
uv run pytest                    # whole suite
uv run pytest tests/test_x.py    # one module
uv run ruff check . && uv run mypy src
```
