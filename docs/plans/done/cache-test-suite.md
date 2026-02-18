# Plan: Comprehensive Query Cache Test Suite

## Context

The query caching system uses Jaccard similarity on normalized token sets (threshold 0.8) to match prompts, auto-invalidates on reindex, and bypasses the agent loop on cache hits. Current test coverage is minimal — only basic similarity and in-memory QueryCache tests exist. There are **zero tests** for the persistent cache layer (SqliteStore query methods) or the dashboard endpoint cache integration logic. This plan adds a comprehensive `tests/test_cache.py` covering all layers.

## Files to Create

- `tests/test_cache.py` — New file, entire deliverable

## Files Under Test

- `src/indiseek/tools/search_code.py:53-107` — `compute_query_similarity()`, `QueryCache`
- `src/indiseek/storage/sqlite_store.py:441-530` — Query lifecycle methods
- `src/indiseek/api/dashboard.py:463-546` — `run_query_op()` cache logic
- `src/indiseek/api/task_manager.py` — `TaskManager` (reset between integration tests)

## Mocking Strategy for Dashboard Tests

`run_query_op()` creates its own `SqliteStore(config.SQLITE_PATH)` per request. To test with real SQLite but fake agent:

1. Patch `indiseek.config.SQLITE_PATH` → temp DB path
2. Patch `indiseek.config.GEMINI_API_KEY` → `"fake-key"`
3. Patch `indiseek.api.dashboard.create_agent_loop` (lazy import inside `_run()`)  — only needed for cache-miss tests where the background task actually starts
4. Reset `_task_manager._tasks` between tests to avoid 409 conflicts
5. Pre-populate store with `insert_query()` + `complete_query()` before HTTP requests
6. Use `FastAPI TestClient` for synchronous endpoint calls

## Checklist

- [ ] **Step 1: Create `tests/test_cache.py` with imports and fixtures**
  - Imports: pytest, json, time, unittest.mock
  - Imports from indiseek: `compute_query_similarity`, `QueryCache`, `SqliteStore`
  - `store` fixture: `SqliteStore(tmp_path / "test.db")` + `init_db()`
  - Verify: `pytest tests/test_cache.py` passes (0 tests, no import errors)

- [ ] **Step 2: `TestComputeQuerySimilarity` — normalization cases**
  - `test_identical` — `"hello world"` vs `"hello world"` → 1.0
  - `test_case_insensitive` — `"Hello World"` vs `"hello world"` → 1.0
  - `test_punctuation_stripped` — `"How does X work?"` vs `"How does X work"` → 1.0
  - `test_same_tokens_different_order` — `"foo bar baz"` vs `"baz foo bar"` → 1.0
  - `test_whitespace_variations` — `"  hello   world  "` vs `"hello world"` → 1.0
  - `test_underscore_preserved` — `"my_func works"` vs `"my_func works"` → 1.0

- [ ] **Step 3: `TestComputeQuerySimilarity` — threshold boundaries and edge cases**
  - `test_at_threshold_four_of_five` — 4 shared + 1 extra = 4/5 = 0.8
  - `test_below_threshold_four_of_six` — 4 shared + 2 extra = 4/6 ≈ 0.667
  - `test_completely_different` — no overlap → 0.0
  - `test_empty_both` — `""` vs `""` → 0.0
  - `test_empty_one` — `""` vs `"hello"` → 0.0
  - `test_single_token_exact` — `"hello"` vs `"hello"` → 1.0
  - `test_single_token_case` — `"Hello"` vs `"hello"` → 1.0
  - `test_single_token_mismatch` — `"hello"` vs `"world"` → 0.0

- [ ] **Step 4: `TestQueryCacheInMemory` — full in-memory cache coverage**
  - `test_empty_returns_none`
  - `test_exact_match`
  - `test_fuzzy_above_threshold`
  - `test_fuzzy_below_threshold_returns_none`
  - `test_returns_first_hit_not_best` — insert entry A (lower similarity), then entry B (higher similarity). Query matches both. Assert returns A's result (first match wins).
  - `test_clear`
  - `test_custom_threshold` — threshold=0.5, query that would fail at 0.8 succeeds

- [ ] **Step 5: `TestSqliteQueryLifecycle` — insert, complete, fail, get**
  - `test_insert_creates_running` — insert, get → status='running', answer is None
  - `test_complete_sets_answer` — insert, complete, get → status='completed', answer set, duration set
  - `test_fail_sets_error` — insert, fail, get → status='failed', error set
  - `test_get_nonexistent_returns_none` — `get_query(9999)` → None

- [ ] **Step 6: `TestSqliteQueryLifecycle` — cached queries and evidence parsing**
  - `test_insert_cached_query` — complete a source query, insert_cached_query pointing to it. get_query shows status='cached', source_query_id matches.
  - `test_get_query_parses_evidence` — complete with valid evidence JSON. get_query returns evidence as parsed list.
  - `test_get_query_malformed_evidence` — complete with `"not json"`. get_query doesn't crash (evidence stays as raw string).

- [ ] **Step 7: `TestSqliteCompletedQueries` — filtering and ordering**
  - `test_since_none_returns_all_completed` — 1 completed, 1 failed, 1 running → returns 1
  - `test_since_timestamp_filters` — 2 completed queries with sleep gap. Timestamp between them → returns only the later one.
  - `test_excludes_failed_running_cached` — 4 queries of each status. Returns only completed.
  - `test_list_queries_order` — 3 queries, list_queries returns newest first
  - `test_list_queries_limit` — 5 queries, limit=2 → returns 2

- [ ] **Step 8: `TestDashboardCacheIntegration` — setup and cache miss**
  - Fixture: patches config.SQLITE_PATH, config.GEMINI_API_KEY, resets _task_manager
  - `test_cache_miss_first_query` — no completed queries exist. POST /dashboard/api/run/query → response has `task_id` and `status`, no `cached` key.

- [ ] **Step 9: Dashboard cache hit tests**
  - `test_cache_hit_identical` — pre-populate completed query. POST with same prompt → `cached: True`, correct `source_query_id` and `answer`.
  - `test_cache_hit_similar` — pre-populate "How does HMR work in Vite". POST "how does hmr work in vite" → cache hit.
  - `test_cache_miss_dissimilar` — pre-populate HMR query. POST about plugin API → cache miss.

- [ ] **Step 10: Dashboard force, failed-source, and reindex tests**
  - `test_force_bypasses_cache` — pre-populate completed query. POST with `force: true` → cache miss (has `task_id`).
  - `test_failed_query_not_cache_source` — insert + fail query. POST with same prompt → cache miss.
  - `test_reindex_invalidates` — pre-populate completed query. Set `last_index_at` to after its `completed_at`. POST → cache miss.

- [ ] **Step 11: Dashboard best-match and response-shape tests**
  - `test_best_match_wins` — two completed queries, POST a prompt closer to one. Assert `source_query_id` is the better match.
  - `test_cache_response_shape` — verify response keys: `cached`, `query_id`, `source_query_id`, `answer`, `evidence`. `evidence` is a list.
  - `test_cache_hit_bypasses_running_task_check` — patch `_task_manager.has_running_task` → True. POST a cacheable query → still returns cached (no 409).

- [ ] **Step 12: Dashboard edge cases**
  - `test_empty_answer_cached` — completed query with answer=`""`. POST → cache hit with empty answer.
  - `test_malformed_evidence_in_source` — completed query with evidence=`"not json"`. POST → cache hit with `evidence: []`.

## Verification

```bash
pytest tests/test_cache.py -v
# All tests pass

pytest tests/ -v
# Full suite still passes (no regressions)

ruff check src/ tests/
# No lint errors
```
