# Test Conventions

## Shared Fixtures (conftest.py)

All common fixtures live in `tests/conftest.py`. Do NOT redefine them in test files.

### `store` — Pre-initialized SqliteStore
A module-scoped template DB is created once with `init_db()`, then `shutil.copy`'d for each test. This avoids running `init_db()` (which does ~25 SQL operations including table rebuilds) on every test.

**Use `store`** — don't create your own `SqliteStore` + `init_db()` unless you're specifically testing init/migration behavior.

```python
# GOOD — uses the shared fixture
def test_something(self, store):
    store.insert_chunks([...])

# BAD — creates a new DB from scratch (slow)
def test_something(self, tmp_path):
    db = SqliteStore(tmp_path / "test.db")
    db.init_db()
```

### `db_path` — Path to pre-initialized DB
If you need the raw path (e.g., for a `TestClient` that creates its own store connections), use `db_path`. It points to an already-initialized DB copy — any `SqliteStore(db_path)` will work without calling `init_db()`.

### `repo_dir` — Empty repo directory
Creates `tmp_path / "repo"`. Override in your test file if you need files on disk (see `test_tools.py` for an example that writes `src/main.ts` etc.).

### `searcher` — CodeSearcher with no backends
For tests that mock the Gemini client and don't need real search.

## Mock Response Helpers (helpers.py)

Shared Gemini mock response factories live in `tests/helpers.py`:

```python
from tests.helpers import _make_text_response, _make_fn_call_response

mock_client.models.generate_content.return_value = _make_text_response("answer")
mock_client.models.generate_content.return_value = _make_fn_call_response("read_map", {})
```

Do NOT duplicate these in test files.

## Performance Rules

1. **Never call `init_db()` in tests** unless testing migration behavior. The `store` fixture handles it.
2. **Mock `time.sleep`** when testing retry/backoff logic. Use `@patch("module.path.time.sleep")`.
3. **Keep file-specific fixtures local** (e.g., `vector_store`, `populated_store`, `client`). Only move to conftest if 3+ files need it.
4. **Use `TYPE_CHECKING` imports** for type annotations that reference removed imports (e.g., `SqliteStore` in type hints).

## Running Tests

```
pytest                          # full suite (~22s)
pytest -n auto --dist loadfile  # parallel via xdist (~14s)
pytest tests/test_tools.py      # single file
pytest -k "TestReadMap"         # single class/test
```
