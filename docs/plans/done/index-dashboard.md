# Index Inspector Dashboard — Implementation Plan

## Context

The indiseek indexing pipeline writes to 3 stores (SQLite, LanceDB, Tantivy) across 5 steps, but there's no way to see what's actually been indexed, find coverage gaps, or trigger operations without the CLI. The user suspects only part of Vite got indexed (possibly just `packages/vite/src/node/`). This dashboard provides full visibility into the pipeline state and the ability to run/re-run indexing operations with live progress.

## Architecture

- **Backend:** FastAPI `APIRouter` in `src/indiseek/api/dashboard.py`, mounted on existing app at `/dashboard/api`
- **Frontend:** React SPA in `frontend/` (Vite + shadcn/ui + React Router + TanStack Query), served at `/dashboard`
- **Indexing:** Background thread pool with SSE progress streaming
- **Scope:** All 5 pipeline steps triggerable, with optional directory prefix for tree-sitter/embed/summarize

## Key Design Decisions

1. **Dashboard stores init separate from agent loop** — Dashboard lazily opens SqliteStore, VectorStore, LexicalIndexer directly from config paths. Does not trigger agent loop init (which needs GEMINI_API_KEY for the full agent).

2. **Background indexing via threading** — Indexing operations run in `concurrent.futures.ThreadPoolExecutor(max_workers=1)` to prevent concurrent mutation. A `TaskManager` tracks the current operation (status, progress events, errors). Only one operation runs at a time.

3. **SSE for progress** — `GET /dashboard/api/tasks/{task_id}/stream` returns an SSE stream. The indexer functions get a `progress_callback(event_dict)` that the TaskManager routes to SSE subscribers. Frontend uses `EventSource` to display live logs.

4. **Progress callbacks on existing indexers** — Embedder and Summarizer already print progress to stdout. We add an optional `on_progress: Callable[[dict], None]` parameter to `embed_all_chunks()` and `summarize_repo()`. When set, progress events go to the callback instead of (or in addition to) stdout. Same pattern for tree-sitter parsing and SCIP loading — wrap the loop in `scripts/index.py` into callable functions with callbacks.

5. **Directory scoping** — Tree-sitter: add `clear_index_data_for_prefix(prefix)` to SqliteStore (deletes chunks/symbols for files matching prefix, not SCIP data). Embed: filter chunks by file_path prefix before embedding. Summarize: already supports `path_filter`. SCIP and Lexical: always full (SCIP loads one index.scip, Lexical rebuilds from all chunks).

6. **SPA serving** — FastAPI mounts `StaticFiles` from `frontend/dist/` at `/dashboard` if the directory exists. Catch-all route serves `index.html` for client-side routing. In dev mode, Vite dev server proxies API calls to FastAPI.

## Spec Update

Step 0 of implementation: update `docs/SPEC-dashboard.md` to add the indexing operations section (replacing the "read-only" constraint).

---

## Implementation Checklist

### Phase A: Backend — Store Methods

#### Step 1: Add query methods to SqliteStore
**Files:** `src/indiseek/storage/sqlite_store.py`, `tests/test_tools.py`

Add:
- `get_chunk_by_id(chunk_id: int) -> dict | None` — single row lookup on chunks PK
- `get_all_file_paths_from_chunks() -> set[str]` — `SELECT DISTINCT file_path FROM chunks`
- `get_all_file_paths_from_summaries() -> set[str]` — `SELECT DISTINCT file_path FROM file_summaries`
- `get_file_summary(file_path: str) -> dict | None` — single row by exact path
- `clear_index_data_for_prefix(prefix: str)` — deletes from chunks and symbols WHERE file_path LIKE 'prefix%' (does not touch SCIP or file_summaries)

Add tests for each. Existing tests pass unchanged.

#### Step 2: Add doc_count to LexicalIndexer
**Files:** `src/indiseek/indexer/lexical.py`

Add `doc_count() -> int` — calls `self._searcher.num_docs` if index is open, returns 0 otherwise. The tantivy Python bindings expose this on the Searcher object.

#### Step 3: Add get_indexed_file_paths to LexicalIndexer
**Files:** `src/indiseek/indexer/lexical.py`

Add `get_indexed_file_paths() -> set[str]` — searches all docs and collects unique file_path values. Alternative: since Tantivy is always rebuilt from SQLite chunks, we can just check if the index exists and treat all chunked files as lexical-indexed. Decide during implementation which is simpler.

---

### Phase B: Backend — Indexing Pipeline as Library Functions

#### Step 4: Extract tree-sitter parsing into a callable function
**Files:** `src/indiseek/indexer/pipeline.py` (new)

Extract the tree-sitter parsing loop from `scripts/index.py:154-183` into:
```
def run_treesitter(store, repo_path, path_filter=None, on_progress=None) -> dict
```
Returns `{"files_parsed": N, "symbols": N, "chunks": N, "errors": N}`.
Calls `on_progress({"step": "treesitter", "current": i, "total": N, "file": relative})` per file.
If `path_filter` is set, uses `clear_index_data_for_prefix` instead of `clear_index_data`.

`scripts/index.py` is refactored to call this function instead of having inline logic. Existing CLI behavior unchanged.

#### Step 5: Extract SCIP loading into a callable function
**Files:** `src/indiseek/indexer/pipeline.py`

Extract SCIP loading into:
```
def run_scip(store, scip_path, on_progress=None) -> dict
```
Returns `{"symbols": N, "occurrences": N, "relationships": N}`.
No directory scoping — always full load.

Refactor `scripts/index.py` to call this.

#### Step 6: Add progress callbacks to Embedder.embed_all_chunks
**Files:** `src/indiseek/indexer/embedder.py`

Add `on_progress: Callable[[dict], None] | None = None` parameter to `embed_all_chunks()`. Add `path_filter: str | None = None` parameter to filter chunks by file_path prefix before embedding.

When set, calls `on_progress({"step": "embed", "current": embedded, "total": total, "batch": batch_num, "total_batches": N})` after each batch.

Existing print statements remain for CLI usage. Tests unchanged.

#### Step 7: Add progress callbacks to Summarizer.summarize_repo
**Files:** `src/indiseek/indexer/summarizer.py`

Add `on_progress: Callable[[dict], None] | None = None` parameter to `summarize_repo()`. Calls `on_progress({"step": "summarize", "current": i, "total": total, "file": relative})` per file.

`path_filter` already exists. Tests unchanged.

#### Step 8: Extract lexical build into pipeline module
**Files:** `src/indiseek/indexer/pipeline.py`

Add:
```
def run_lexical(store, tantivy_path, on_progress=None) -> dict
```
Returns `{"documents_indexed": N}`. Wraps `LexicalIndexer.build_index()`.

Refactor `scripts/index.py` to call this. No directory scoping — always full rebuild.

#### Step 9: Refactor scripts/index.py to use pipeline functions
**Files:** `scripts/index.py`

Replace inline parsing/loading logic with calls to `run_treesitter()`, `run_scip()`, embedder methods, summarizer methods, and `run_lexical()`. The script becomes a thin CLI wrapper. Verify `python scripts/index.py --dry-run` still works correctly.

---

### Phase C: Backend — Dashboard API & Task Management

#### Step 10: Create TaskManager for background operations
**Files:** `src/indiseek/api/task_manager.py` (new)

Simple task manager:
- `TaskManager` class with `ThreadPoolExecutor(max_workers=1)`
- `submit(name, fn, **kwargs) -> task_id` — submits a function, returns a UUID task ID
- `get_status(task_id) -> {"id", "name", "status", "progress_events", "result", "error"}`
- `list_tasks() -> list[dict]` — all tasks (running, completed, failed)
- `subscribe(task_id) -> queue.Queue` — returns a Queue that receives progress events for SSE streaming
- Status transitions: pending → running → completed | failed
- Prevents submitting if a task is already running (returns 409)

Progress events are dicts pushed to all subscriber queues.

Tests for TaskManager in isolation.

#### Step 11: Create dashboard API router with stats endpoint
**Files:** `src/indiseek/api/dashboard.py` (new), `src/indiseek/api/server.py`

Create `dashboard.py` with `APIRouter`. Lazy-init function opens SqliteStore, VectorStore, LexicalIndexer from config paths (each in try/except for graceful degradation).

`GET /stats` returns:
```json
{
  "sqlite": {"files_parsed": N, "chunks": N, "symbols": N, "scip_symbols": N, "scip_occurrences": N, "file_summaries": N},
  "lancedb": {"embedded_chunks": N, "available": true},
  "tantivy": {"indexed_docs": N, "available": true}
}
```

In `server.py`: `app.include_router(dashboard_router, prefix="/dashboard/api")`.

Add CORS middleware for dev (React dev server on different port).

#### Step 12: Implement tree endpoint
**Files:** `src/indiseek/api/dashboard.py`

`GET /tree?path=` — runs `git ls-files` in REPO_PATH, queries SQLite for file_paths with chunks and summaries, queries VectorStore for embedded chunk_ids (mapped to file_paths), returns one level of children with aggregated coverage counts.

Response shape:
```json
{
  "path": "packages/vite/src",
  "children": [
    {"name": "node", "type": "directory", "total_files": 42, "parsed": 42, "summarized": 38, "embedded": 40},
    {"name": "index.ts", "type": "file", "parsed": true, "summarized": true, "embedded": true}
  ]
}
```

#### Step 13: Implement file detail endpoint
**Files:** `src/indiseek/api/dashboard.py`

`GET /files/{path:path}` — returns file summary, chunks list with per-chunk pipeline status (embedded yes/no), symbols.

#### Step 14: Implement chunk detail endpoint
**Files:** `src/indiseek/api/dashboard.py`

`GET /chunks/{chunk_id}` — returns full chunk data including content, pipeline presence indicators. 404 if not found.

#### Step 15: Implement search endpoint
**Files:** `src/indiseek/api/dashboard.py`

`GET /search?q=&mode=hybrid&limit=10` — instantiates CodeSearcher with available backends. If GEMINI_API_KEY is set, creates embed_fn for semantic mode. Returns ranked results. Returns 400 if requested mode is unavailable.

#### Step 16: Implement indexing trigger endpoints
**Files:** `src/indiseek/api/dashboard.py`

Five POST endpoints, each submits to TaskManager:
- `POST /run/treesitter` — body: `{"path_filter": "..." | null}`
- `POST /run/scip` — body: `{"scip_path": "..." | null}` (defaults to REPO_PATH/index.scip)
- `POST /run/embed` — body: `{"path_filter": "..." | null}`
- `POST /run/summarize` — body: `{"path_filter": "..." | null}`
- `POST /run/lexical` — no body

Each returns `{"task_id": "uuid", "name": "treesitter", "status": "running"}` or 409 if a task is already running.

Also:
- `GET /tasks` — list all tasks with status
- `GET /tasks/{task_id}` — task detail with latest progress

#### Step 17: Implement SSE streaming endpoint
**Files:** `src/indiseek/api/dashboard.py`

`GET /tasks/{task_id}/stream` — returns `text/event-stream` response. Uses `StreamingResponse` from Starlette. Subscribes to the TaskManager's progress queue for the given task. Sends each progress event as an SSE message. Sends a final "done" or "error" event when the task completes.

#### Step 18: Add aiofiles dependency and SPA static mount
**Files:** `pyproject.toml`, `src/indiseek/api/server.py`

Add `aiofiles` to dependencies. In `server.py`, after all API routes, conditionally mount `StaticFiles(directory="frontend/dist", html=True)` at `/dashboard` if the directory exists. Add catch-all for client-side routing.

#### Step 19: Update .gitignore and CLAUDE.md
**Files:** `.gitignore`, `CLAUDE.md`

Add `frontend/node_modules/` and `frontend/dist/` to .gitignore. Update CLAUDE.md with dashboard commands.

---

### Phase D: Frontend Scaffolding

#### Step 20: Initialize React project
**Files:** `frontend/` directory (new)

Create React + TypeScript project with Vite. Configure `vite.config.ts` with `base: "/dashboard/"` and proxy `/dashboard/api` to `http://localhost:8000`.

#### Step 21: Install and configure UI dependencies
**Files:** `frontend/`

Install: Tailwind CSS, shadcn/ui, react-router-dom, @tanstack/react-query. Configure providers in `main.tsx`.

#### Step 22: Set up routing shell and layout
**Files:** `frontend/src/`

Routes: `/` (Overview), `/files` (FileTree), `/files/*` (FileDetail), `/chunks/:id` (ChunkDetail), `/search` (Search), `/operations` (IndexingOperations). Layout with sidebar nav.

#### Step 23: Create API client and hooks
**Files:** `frontend/src/api/`

TypeScript interfaces for all API responses. Fetch wrapper functions. TanStack Query hooks: `useStats()`, `useTree(path)`, `useFileDetail(path)`, `useChunkDetail(id)`, `useSearch(q, mode, limit)`, `useTasks()`, `useRunOperation(name)`.

SSE hook: `useTaskStream(taskId)` — connects to EventSource, returns array of progress events.

---

### Phase E: Frontend Pages

#### Step 24: Overview page
**Files:** `frontend/src/pages/Overview.tsx`

Stats cards per store. Coverage percentage bars. Links to file tree.

#### Step 25: File Tree page
**Files:** `frontend/src/pages/FileTree.tsx`

Collapsible directory tree via `useTree(path)`. Coverage indicators per node. Non-indexable files grayed out. Click file → file detail.

#### Step 26: File Detail page
**Files:** `frontend/src/pages/FileDetail.tsx`

File metadata, summary, chunk list with pipeline status badges. Click chunk → chunk detail.

#### Step 27: Chunk Detail page
**Files:** `frontend/src/pages/ChunkDetail.tsx`

Full chunk data, source code in `<pre>` block, pipeline status badges.

#### Step 28: Search page
**Files:** `frontend/src/pages/Search.tsx`

Query input, mode selector, results list with scores. Results link to chunk detail.

#### Step 29: Operations page
**Files:** `frontend/src/pages/Operations.tsx`

Cards for each pipeline step with:
- "Run" button (with optional path_filter input)
- Status indicator (idle/running/completed/failed)
- Live progress log via SSE (shows file-by-file progress, batch counts, etc.)
- Task history (previous runs with results)

Disable the Run button while any operation is in progress.

#### Step 30: Integration test
Build frontend, start FastAPI, verify end-to-end: stats load, tree navigable, search works, operations trigger and stream progress.

---

## Files Modified/Created Summary

**Modified:**
- `src/indiseek/storage/sqlite_store.py` — new query methods
- `src/indiseek/indexer/lexical.py` — doc_count method
- `src/indiseek/indexer/embedder.py` — progress callback + path_filter
- `src/indiseek/indexer/summarizer.py` — progress callback
- `src/indiseek/api/server.py` — include dashboard router, static mount, CORS
- `scripts/index.py` — refactor to use pipeline functions
- `pyproject.toml` — add aiofiles
- `.gitignore` — add frontend/node_modules, frontend/dist
- `CLAUDE.md` — dashboard commands
- `docs/SPEC-dashboard.md` — add indexing operations section

**Created:**
- `src/indiseek/indexer/pipeline.py` — extracted pipeline step functions with progress callbacks
- `src/indiseek/api/dashboard.py` — dashboard API router (stats, tree, files, chunks, search, operations, SSE)
- `src/indiseek/api/task_manager.py` — background task execution + progress routing
- `frontend/` — entire React SPA
- `tests/test_dashboard_api.py` — API endpoint tests

## Verification

1. `pytest` — all existing + new tests pass
2. `ruff check src/` — no lint errors
3. `python scripts/index.py --dry-run` — CLI still works after refactor
4. `cd frontend && npm run build` — frontend builds
5. `uvicorn indiseek.api.server:app` then browse `http://localhost:8000/dashboard`:
   - Overview shows real store stats
   - File tree navigable with coverage indicators
   - Search works in lexical mode
   - Trigger "summarize" on a small directory, watch SSE progress stream
   - Verify stats update after operation completes
