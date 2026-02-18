# Query History

## Context

The Query page currently shows only the active query. When a new query is submitted, the previous result disappears. When the server restarts, all query results are lost (TaskManager is in-memory). There's no way to review past questions or compare answers.

This adds persistent query history stored in SQLite, a list API endpoint, and a history sidebar in the Query page UI.

## Design Decisions

**Storage: SQLite `queries` table.** Queries are stored alongside the existing indexing data. This is the simplest option — no new database, no new connection logic. The `queries` table stores the prompt, answer, evidence (JSON), status, timestamps, and duration.

**No separate history page.** History lives in the Query page itself as a sidebar/list. Clicking a past query loads its result into the main view. This keeps navigation simple — one page for all query interaction.

**Evidence stored as JSON text.** The evidence trail is a list of `{tool, args, summary}` dicts. Stored as a JSON string column rather than a normalized table — it's write-once read-many, and the structure may evolve.

## Files to modify/create

**Modified:**
- `src/indiseek/storage/sqlite_store.py` — add `queries` table DDL and CRUD methods
- `src/indiseek/api/dashboard.py` — persist query results, add `GET /queries` and `GET /queries/{id}` endpoints
- `frontend/src/api/client.ts` — add query history types and fetch functions
- `frontend/src/api/hooks.ts` — add `useQueryHistory` and `useQueryDetail` hooks
- `frontend/src/pages/Query.tsx` — add history sidebar, load past queries on click
- `docs/SPEC-dashboard.md` — update Query page spec with history

## Implementation Checklist

- [ ] **Step 1: Add `queries` table to SQLite.** In `sqlite_store.py`, add a `queries` table to `init_db()`:
  ```
  queries(id INTEGER PRIMARY KEY, prompt TEXT, answer TEXT, evidence TEXT,
          status TEXT, error TEXT, created_at TEXT, completed_at TEXT,
          duration_secs REAL)
  ```
  Add methods: `insert_query(prompt) -> int` (inserts with status='running', created_at=now), `complete_query(id, answer, evidence_json, duration)`, `fail_query(id, error)`, `list_queries() -> list[Row]` (returns id, prompt, status, created_at, duration — no answer/evidence for list view), `get_query(id) -> Row | None` (full row). App still works unchanged.

- [ ] **Step 2: Persist queries from the `/run/query` endpoint.** In `dashboard.py`, modify `run_query_op` to: (1) call `store.insert_query(prompt)` before submitting to TaskManager, (2) pass the query row ID into the task function, (3) in the task function, call `store.complete_query(...)` on success or `store.fail_query(...)` on error, (4) include `query_id` in the returned task result dict alongside `answer` and `evidence`. Existing behavior unchanged — SSE streaming still works, task result still has the same shape plus a new `query_id` field.

- [ ] **Step 3: Add query history API endpoints.** In `dashboard.py`, add `GET /queries` (returns list of `{id, prompt, status, created_at, duration_secs}` ordered by created_at desc, limited to 50) and `GET /queries/{id}` (returns full query including answer and evidence JSON). These read directly from SQLite, independent of TaskManager.

- [ ] **Step 4: Add query history types and API functions to frontend client.** In `client.ts`, add `QueryHistoryItem` interface (id, prompt, status, created_at, duration_secs) and `QueryHistoryDetail` interface (adds answer, evidence, error). Add `fetchQueryHistory()` and `fetchQueryDetail(id)` functions.

- [ ] **Step 5: Add query history hooks.** In `hooks.ts`, add `useQueryHistory()` (`useQuery` wrapping `fetchQueryHistory`, refetches on window focus) and `useQueryDetail(id)` (`useQuery` wrapping `fetchQueryDetail`, enabled when id > 0).

- [ ] **Step 6: Add history sidebar to Query page.** In `Query.tsx`, add a left sidebar (or top section) showing the query history list. Each entry shows the prompt (truncated), status badge (running/completed/failed), and relative timestamp. Clicking an entry loads the full result into the main view (fetched via `useQueryDetail`). The active/in-progress query appears at the top of the list. When a new query completes, invalidate the history query to refresh the list. Layout: history list on the left (~250px), query input + result on the right.

- [ ] **Step 7: Update spec and verify.** Update `docs/SPEC-dashboard.md` Query section to mention history. `cd frontend && npm run build` succeeds. `pytest` passes. `ruff check` clean on modified Python files.

## Verification

1. `pytest` — all existing tests pass
2. `ruff check` on modified Python files — clean
3. `cd frontend && npm run build` — compiles
4. Manual: submit a query, see it appear in history after completion. Restart server, history persists. Click a past query, see its answer and evidence load.
