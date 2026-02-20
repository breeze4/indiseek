# Plan: Test Suite Speed

## Problem

Full test suite: **395 tests in 159s (2:39)**. Almost all time is in fixture setup, not actual test logic.

### Root Cause

Every test creates a fresh `SqliteStore` at function scope and calls `init_db()`. That method:
1. Creates 10 tables + indexes (`executescript`)
2. Runs 12 `_migrate_add_column` calls (each does `PRAGMA table_info`)
3. Creates 9 `repo_id` indexes
4. Runs 4 `_migrate_composite_unique` calls (each reads `sqlite_master` DDL)
5. Runs `_ensure_legacy_repo`

Per the `--durations=0` output, setup is **0.4–1.2s per test** while actual test calls are **0.01–0.05s**. With ~395 tests that's ~200s of pure init overhead.

### Additional issues
- No shared `conftest.py` — `store(tmp_path)` fixture is copy-pasted across all 12 test files
- No pytest markers — can't run subsets selectively
- No test parallelism (`pytest-xdist` not installed)
- One test (`test_embed_batch_error_retries`) has a 2s `time.sleep` for retry backoff
- `test_cache.py` has ~0.35s of `time.sleep` calls for timestamp ordering

## Plan

### Phase 1: Shared conftest + module-scoped store (biggest win)

- [ ] 1. Create `tests/conftest.py` with a **module-scoped** `_module_db` fixture that creates one `SqliteStore` + `init_db()` per test module, plus a **function-scoped** `store` fixture that wraps it with transaction rollback for test isolation
- [ ] 2. The function-scoped `store` fixture: begins a savepoint before each test, rolls back after. This gives each test a clean slate without re-running `init_db()`. For tests that need a truly fresh DB (e.g., migration tests in `test_repos.py`), provide a `fresh_store` fixture that creates a new DB from scratch.
- [ ] 3. Remove duplicate `store(tmp_path)` fixtures from all 12 test files
- [ ] 4. Move shared helpers to conftest: `repo_dir`, `searcher`, `_make_text_response`, `_make_fn_call_response` (currently duplicated across test_agent.py, test_multi_agent.py, test_classic.py)
- [ ] 5. Verify all tests still pass after the migration

### Phase 2: Pytest markers for selective running

- [ ] 6. Register markers in `pyproject.toml`: `unit`, `integration`, `slow`
- [ ] 7. Mark test classes:
  - `unit` — pure logic tests (TestDataStructures, TestConstants, TestQuerySimilarity, TestStripFilePaths, etc.)
  - `integration` — tests that need SQLite + fixtures (TestAgentLoop, TestDashboard*, TestCache*, etc.)
  - `slow` — the embedding retry test, cache timestamp tests
- [ ] 8. Add pytest config to run `unit` by default or document `pytest -m "not slow"` usage

### Phase 3: pytest-xdist parallel execution

- [ ] 9. Add `pytest-xdist` to dev dependencies
- [ ] 10. Verify tests pass with `pytest -n auto` (module-scoped fixtures + tmp_path should isolate cleanly)
- [ ] 11. Add note in CLAUDE.md for parallel execution

### Phase 4: Mock the sleep calls

- [ ] 12. In `test_embedding.py::test_embed_batch_error_retries`, mock `time.sleep` to avoid the 2s wait
- [ ] 13. In `test_cache.py`, replace `time.sleep` calls with explicit timestamp manipulation where possible

## Expected Impact

- Phase 1 alone should cut the suite from ~160s to ~15-25s (10x improvement) by eliminating ~380 redundant `init_db()` calls
- Phase 2 enables `pytest -m unit` for instant feedback during development (~2-3s)
- Phase 3 could halve the remaining time on multi-core machines
- Phase 4 saves ~2.5s

## Pre-existing Test Failures

9 tests in `test_multi_agent.py` are failing because `multi.py` was refactored to return tuples instead of dataclass instances. These are out of scope for this plan but should be fixed separately.

## Results

| Metric | Before | After | Improvement |
|---|---|---|---|
| Full suite (`pytest`) | 159s | 22s | **7.2x faster** |
| Parallel (`pytest -n auto --dist loadfile`) | N/A | 14s | **11.4x faster** |

### What was implemented
- [x] Phase 1: `tests/conftest.py` with module-scoped DB template + `shutil.copy` per test
- [x] Phase 1: Removed duplicate `store`, `repo_dir`, `searcher` fixtures from all 12 test files
- [x] Phase 1: Moved `_make_text_response`/`_make_fn_call_response` to `tests/helpers.py`
- [x] Phase 3: Added `pytest-xdist` to dev deps
- [x] Phase 4: Mocked `time.sleep` in `test_embed_batch_error_retries` (saved 2s)
- [x] Registered `slow` marker in `pyproject.toml`
- [x] Updated CLAUDE.md with test running commands

### What was deferred
- Phase 2 marker annotations on individual tests (diminishing returns at 22s total)
- Mocking `time.sleep` in `test_cache.py` (only 0.35s total, not worth the complexity)

## Status: Archived (partial completion)

Goal achieved — 7.2x speedup (159s → 22s). Remaining checklist items in Phases 2/4 were explicitly deferred as diminishing returns. Original phase checklists left unchecked but the Results section above reflects actual work done.
