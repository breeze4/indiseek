# Query Caching

## Context

The agent loop is expensive — each query makes multiple Gemini API calls and tool executions (up to 15 iterations). When a user asks the same or very similar question, the full loop reruns unnecessarily. The `queries` table already stores completed results, so we have the data — we just need a lookup mechanism.

This adds fuzzy prompt-level caching using the existing Jaccard similarity function, with automatic cache invalidation when indexing operations complete. Cache hits bypass the agent loop entirely and return instantly. The cache hit is visible in the history sidebar with a "cached" badge, and a "Re-run" button lets users bypass the cache.

## Design Decisions

**Cache check before TaskManager.** On cache hit, we skip TaskManager entirely and return the cached result directly from the API endpoint. This means cache hits work even when another task is running (no 409 conflict), and the response is instant. The frontend handles this as a separate response shape.

**Fuzzy matching via Jaccard similarity.** Reuses `compute_query_similarity()` from `search_code.py` (threshold 0.8). This matches prompts like "How does HMR work?" with "how does hmr work" while rejecting genuinely different queries.

**Auto-invalidation on reindex.** A `metadata` table stores `last_index_at` timestamp, updated by every indexing endpoint on completion. Cache only returns queries completed after this timestamp.

**Cached queries get their own row.** On cache hit, a new `queries` row is inserted with `status='cached'` and `source_query_id` pointing to the original. This preserves full history — every query appears in the sidebar.

## Files to modify

**Modified:**
- `src/indiseek/storage/sqlite_store.py` — `metadata` table, `source_query_id` column, cache lookup methods
- `src/indiseek/api/dashboard.py` — cache check in `run_query_op`, `last_index_at` updates in indexing endpoints
- `frontend/src/api/client.ts` — `QueryCachedResponse` type, `force` param on `runQuery`
- `frontend/src/api/hooks.ts` — update `useRunQuery` for `{prompt, force}` shape
- `frontend/src/pages/Query.tsx` — cache hit display, "cached" badge, "Re-run" button
- `docs/SPEC-dashboard.md` — document caching behavior

## Implementation Checklist

- [x] **Step 1: Add `metadata` table to SQLite.** In `init_db()`, add:
  ```
  metadata(key TEXT PRIMARY KEY, value TEXT)
  ```
  Add methods: `set_metadata(key, value)` (INSERT OR REPLACE), `get_metadata(key) -> str | None`. App works unchanged.

- [x] **Step 2: Add `source_query_id` to `queries` table.** Add `source_query_id INTEGER` column to the `queries` DDL (nullable, no FK constraint — keeps it simple). No code uses it yet, purely additive.

- [x] **Step 3: Add cache lookup methods to SqliteStore.** Add `get_completed_queries_since(after: str | None) -> list[dict]` — returns `id, prompt, answer, evidence, duration_secs` for completed queries where `completed_at > after` (or all completed if `after` is None). Add `insert_cached_query(prompt, answer, evidence_json, source_query_id, duration_secs) -> int` — inserts with `status='cached'`, `created_at=now`, `completed_at=now`, copies the passed answer/evidence/duration. App works unchanged.

- [x] **Step 4: Update indexing endpoints to set `last_index_at`.** In each of the 5 indexing `_run` functions (treesitter, scip, embed, summarize, lexical), after the operation succeeds, call `store.set_metadata("last_index_at", datetime.now(timezone.utc).isoformat())`. Each already has a `store` variable. Import `datetime`/`timezone` where needed. Existing behavior unchanged — just adds one metadata write at the end.

- [x] **Step 5: Add cache check to `run_query_op`.** Before the `has_running_task()` check, add cache lookup:
  1. Open store, read `last_index_at` from metadata
  2. Call `get_completed_queries_since(last_index_at)`
  3. Import `compute_query_similarity` from `indiseek.tools.search_code`
  4. Find best match with similarity >= 0.8
  5. On hit: call `insert_cached_query(...)`, log it, return `{"cached": true, "query_id": new_id, "source_query_id": orig_id, "answer": "...", "evidence": [...]}`
  6. On miss: fall through to existing flow

- [x] **Step 6: Add `force` parameter to bypass cache.** Add `force: bool = False` to `QueryRequest`. In `run_query_op`, skip the cache check if `req.force` is True.

- [x] **Step 7: Update frontend types and API client.** In `client.ts`:
  - Add `QueryCachedResponse` interface: `{cached: true, query_id: number, source_query_id: number, answer: string, evidence: QueryEvidence[]}`
  - Change `runQuery(prompt, force?)` to accept optional `force` boolean, include it in the POST body
  - Return type becomes `RunResponse | QueryCachedResponse`

- [x] **Step 8: Update hooks.** In `hooks.ts`, change `useRunQuery` mutation to accept `{prompt: string, force?: boolean}` instead of just `string`.

- [x] **Step 9: Update Query.tsx.** Three changes:
  1. **Handle cache hit in `handleSubmit`:** Check if response has `cached: true`. If so, set `displayResult` directly from the response (answer + evidence), show a cache hit indicator, refresh history. No SSE stream.
  2. **History sidebar:** Handle `status='cached'` with a distinct badge style (e.g., purple/indigo). Cached queries don't show duration (or show source query's duration).
  3. **Re-run button:** When viewing a cached result (from live cache hit or from history where status='cached'), show a "Re-run" button that calls `runQuery` with `force: true`.

- [x] **Step 10: Update spec and verify.** Update `docs/SPEC-dashboard.md` Query section with caching behavior. `ruff check` clean. `pytest` passes. `npm run build` succeeds.

## Verification

1. `pytest` — all existing tests pass
2. `ruff check src/indiseek/storage/sqlite_store.py src/indiseek/api/dashboard.py` — clean
3. `cd frontend && npm run build` — compiles
4. Manual: submit a query, see it complete. Submit the same query again — should return instantly with "cached" badge. Run an indexing op, submit the same query — should re-execute (cache invalidated). Use "Re-run" button — should re-execute regardless.
