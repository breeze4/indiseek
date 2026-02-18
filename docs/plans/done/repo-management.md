# Multi-Repo Management Implementation Plan

## Overview

Transform Indiseek from a single-hardcoded-repo service into one that manages multiple repositories. Users add repos via the dashboard, each repo has its own indexing state, and queries run against a specific repo. The existing indexed Vite data is migrated into the new schema as "repo 1."

## Current State

- One repo, hardcoded via `REPO_PATH` env var in `config.py`
- All SQLite tables (`symbols`, `chunks`, `scip_symbols`, `scip_occurrences`, `scip_relationships`, `file_summaries`, `queries`) have no `repo_id` concept
- One LanceDB table (`chunks`) at `DATA_DIR/lancedb`
- One Tantivy index directory at `DATA_DIR/tantivy`
- Dashboard pages assume a single repo: overview, file tree, search, query, operations all operate on the one repo
- `scripts/index.py` reads `REPO_PATH` from env
- Agent loop (`create_agent_loop`) reads `REPO_PATH` from config

## Key Design Decisions

1. **Single SQLite database, `repo_id` foreign key on data tables.** Not separate databases per repo. Keeps management simple, allows cross-repo queries later.
2. **Per-repo LanceDB tables.** LanceDB table name becomes `chunks_{repo_id}`. Each repo's vectors are isolated.
3. **Per-repo Tantivy index directories.** Tantivy index path becomes `DATA_DIR/tantivy_{repo_id}/`.
4. **Repos stored on local disk.** The service manages `git clone` into `DATA_DIR/repos/{repo_id}/`. `REPO_PATH` env var becomes optional — used only as a migration convenience for the existing Vite repo.
5. **`queries` table gets `repo_id`** so query history is per-repo.
6. **The `POST /query` endpoint gets a `repo_id` parameter.** The top-level `/query` endpoint (non-dashboard) also accepts it.
7. **Migration creates a "legacy" repo row** from the existing `REPO_PATH` if data exists, assigns `repo_id=1` to all existing rows.
8. **Freshness is tracked per-repo via two SHAs.** `repos.indexed_commit_sha` is the HEAD at last index completion. `repos.current_commit_sha` is updated whenever someone checks for changes (git fetch + rev-parse). The delta between them tells you if the index is stale and by how many commits. A "Check for updates" button in the UI triggers a git fetch and SHA comparison. A "Sync" button does git pull + incremental re-index of changed files.

---

## Phase 1: Database Schema + Migration

### Overview
Add the `repos` table and `repo_id` foreign key to all data tables. Migrate existing data into repo_id=1. After this phase, the app still works exactly as before — all existing queries default to repo_id=1.

- [ ] **1.1 Add `repos` table to `SqliteStore.init_db()`**
  - Add table:
    ```
    repos(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, url TEXT,
          local_path TEXT NOT NULL, created_at TEXT NOT NULL,
          last_indexed_at TEXT, indexed_commit_sha TEXT,
          current_commit_sha TEXT, commits_behind INTEGER DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'active')
    ```
  - `indexed_commit_sha` — HEAD when the last indexing run completed
  - `current_commit_sha` — HEAD after the most recent `git fetch` / freshness check
  - `commits_behind` — count of commits between indexed and current (0 = fresh)
  - Add `CREATE INDEX IF NOT EXISTS idx_repos_name ON repos(name)`
  - No changes to existing tables yet; this is purely additive
  - Run tests to confirm nothing breaks

- [ ] **1.2 Add `repo_id` column to `symbols`, `chunks`, `file_summaries` tables via migration**
  - In `init_db()`, after table creation, check if `repo_id` column exists on `symbols` (same pattern as the existing `source_query_id` migration)
  - If missing, run: `ALTER TABLE symbols ADD COLUMN repo_id INTEGER DEFAULT 1`, same for `chunks`, `file_summaries`
  - Add `CREATE INDEX IF NOT EXISTS idx_symbols_repo ON symbols(repo_id)`, same pattern for chunks and file_summaries
  - Default value of 1 means all existing data is automatically assigned to repo 1
  - Run tests to confirm nothing breaks

- [ ] **1.3 Add `repo_id` column to `scip_symbols`, `scip_occurrences`, `scip_relationships` tables via migration**
  - Same pattern: `ALTER TABLE ... ADD COLUMN repo_id INTEGER DEFAULT 1` for each SCIP table
  - Add indexes on `repo_id` for each
  - Run tests

- [ ] **1.4 Add `repo_id` column to `queries` table via migration**
  - `ALTER TABLE queries ADD COLUMN repo_id INTEGER DEFAULT 1`
  - Add index on `queries(repo_id)`
  - `metadata` table stays global (app-level settings). Per-repo timestamps go in the `repos` table's `last_indexed_at` column.
  - Run tests

- [ ] **1.5 Auto-create legacy repo row on migration**
  - In `init_db()`, after migrations, check if `repos` table is empty AND `symbols` table has rows
  - If so, insert a row: `INSERT INTO repos (id, name, url, local_path, created_at, status) VALUES (1, <name_from_REPO_PATH_or_'legacy'>, '', <REPO_PATH>, <now>, 'active')`
  - Read `REPO_PATH` from config (may be empty string if not set — that's fine)
  - Ensures the existing Vite data is associated with repo_id=1
  - Run tests

- [ ] **1.6 Add `SqliteStore` helper methods for repos**
  - `insert_repo(name, url, local_path) -> int` — returns repo id
  - `get_repo(repo_id) -> dict | None`
  - `get_repo_by_name(name) -> dict | None`
  - `list_repos() -> list[dict]`
  - `update_repo(repo_id, **fields)` — update last_indexed_at, commit_sha, status, etc.
  - `delete_repo(repo_id)` — soft delete (set status='deleted') or hard delete with cascade
  - Run tests for these new methods

## Phase 2: Scope All Store Operations by `repo_id`

### Overview
Update all `SqliteStore` query methods and the insert methods to accept and filter by `repo_id`. Methods that currently operate globally now require `repo_id`. Callers are updated to pass `repo_id=1` to maintain current behavior.

- [ ] **2.1 Scope `insert_symbols` and `insert_chunks` by `repo_id`**
  - Add `repo_id` parameter to `insert_symbols()` and `insert_chunks()`
  - Include `repo_id` in the INSERT statement for each
  - Update `Symbol` and `Chunk` dataclasses to include optional `repo_id: int | None = None`
  - All existing callers (pipeline.py, tests) pass `repo_id=1` for now
  - Run tests

- [ ] **2.2 Scope `insert_file_summary`, `insert_file_summaries`, and read methods by `repo_id`**
  - Add `repo_id` parameter to `insert_file_summary()`, `insert_file_summaries()`
  - Add `repo_id` parameter to `get_file_summaries()`, `get_file_summary()`, `get_directory_tree()`
  - All WHERE clauses add `AND repo_id = ?`
  - Update callers to pass `repo_id=1`
  - Run tests

- [ ] **2.3 Scope SCIP operations by `repo_id`**
  - Add `repo_id` parameter to `insert_scip_symbol()`, `insert_scip_occurrences()`, `insert_scip_relationship()`
  - SCIP symbols currently use `UNIQUE(symbol)`. With multi-repo, change to `UNIQUE(symbol, repo_id)`. Since SCIP data is wiped on re-index (`clear_index_data()`), change the CREATE TABLE DDL and let the next re-index handle it. For migration: if the old constraint exists, the ALTER TABLE ADD COLUMN already happened in Phase 1 — the constraint change takes effect on table recreation (next full index).
  - Add `repo_id` parameter to `get_definition()`, `get_references()`, `get_scip_occurrences_by_symbol_id()` — filter by `repo_id`
  - Update callers to pass `repo_id=1`
  - Run tests

- [ ] **2.4 Scope `get_symbols_*` and `get_chunks_*` methods by `repo_id`**
  - Add `repo_id` parameter to `get_symbols_by_name()`, `get_symbols_by_file()`, `get_symbols_in_range()`, `get_chunks_by_file()`, `get_chunk_by_id()`
  - All WHERE clauses add `AND repo_id = ?`
  - Update callers to pass `repo_id=1`
  - Run tests

- [ ] **2.5 Scope `clear_index_data` and `clear_index_data_for_prefix` by `repo_id`**
  - Add `repo_id` parameter to both methods
  - DELETE WHERE clauses add `AND repo_id = ?`
  - Update callers
  - Run tests

- [ ] **2.6 Scope dashboard query methods by `repo_id`**
  - `get_all_file_paths_from_chunks(repo_id)`, `get_all_file_paths_from_summaries(repo_id)` — add repo_id filter
  - `count(table, repo_id=None)` — optionally filter by repo_id
  - Update callers
  - Run tests

- [ ] **2.7 Scope query history by `repo_id`**
  - Add `repo_id` parameter to `insert_query()`, `insert_cached_query()`
  - Add `repo_id` filter to `list_queries()`, `get_completed_queries_since()`
  - `get_query()` fetches by id — no repo_id filter needed (row already has it)
  - Update callers to pass `repo_id=1`
  - Run tests

## Phase 3: Repo-Scoped Storage Backends (LanceDB + Tantivy)

### Overview
LanceDB and Tantivy need per-repo isolation. LanceDB uses per-repo table names. Tantivy uses per-repo index directories.

- [ ] **3.1 Make `VectorStore` repo-aware**
  - Change `TABLE_NAME` from class constant `"chunks"` to instance variable set in `__init__`
  - Constructor accepts `table_name: str = "chunks"` parameter
  - All methods use `self._table_name` instead of `self.TABLE_NAME`
  - Convention: table name is `chunks_{repo_id}` (e.g., `chunks_1`)
  - For backwards compatibility: if `table_name` is `"chunks"` and a legacy `"chunks"` table exists, keep using it
  - Run tests

- [ ] **3.2 Confirm `LexicalIndexer` is already repo-aware**
  - Constructor already accepts `index_path: Path`
  - Convention: callers pass `DATA_DIR/tantivy_{repo_id}/` as the path
  - No changes to `LexicalIndexer` itself — it's already parameterized by path
  - Run tests to verify

- [ ] **3.3 Add config helpers for per-repo paths**
  - In `config.py`, add `REPOS_DIR: Path = DATA_DIR / "repos"` (where cloned repos live)
  - Add helper functions (or in a new `src/indiseek/repo_manager.py`):
    - `get_repo_path(repo_id) -> Path` returns `REPOS_DIR / str(repo_id)`
    - `get_lancedb_table_name(repo_id) -> str` returns `f"chunks_{repo_id}"`
    - `get_tantivy_path(repo_id) -> Path` returns `DATA_DIR / f"tantivy_{repo_id}"`
  - For the legacy repo (repo_id=1): if `REPO_PATH` is set and exists, use that path instead of `REPOS_DIR/1`
  - Run tests

## Phase 4: Repo-Scoped Indexing Pipeline

### Overview
Update the indexing pipeline functions to operate on a specific repo, using repo_id to scope all storage operations and per-repo paths for LanceDB/Tantivy.

- [ ] **4.1 Update `run_treesitter()` to accept `repo_id`**
  - Add `repo_id: int` parameter
  - Pass `repo_id` to `store.insert_symbols()`, `store.insert_chunks()`, `store.clear_index_data()`
  - Update callers (dashboard.py, scripts/index.py) to pass `repo_id=1`
  - Run tests

- [ ] **4.2 Update `run_scip()` to accept `repo_id`**
  - Add `repo_id: int` parameter
  - Pass `repo_id` to `ScipLoader` (which passes to store methods)
  - Update `ScipLoader.load()` to accept and pass `repo_id`
  - Update callers to pass `repo_id=1`
  - Run tests

- [ ] **4.3 Update `Embedder.embed_all_chunks()` to accept `repo_id`**
  - Add `repo_id: int` parameter
  - Scope the SQLite chunk query by `repo_id`
  - Construct `VectorStore` with repo-specific table name
  - Update callers to pass `repo_id=1`
  - Run tests

- [ ] **4.4 Update `Summarizer.summarize_repo()` to accept `repo_id`**
  - Add `repo_id: int` parameter
  - Pass `repo_id` to `store.insert_file_summary()`
  - Scope `_get_summarized_paths()` by `repo_id`
  - Update callers to pass `repo_id=1`
  - Run tests

- [ ] **4.5 Update `run_lexical()` to accept `repo_id`**
  - Add `repo_id: int` parameter
  - Construct `LexicalIndexer` with repo-specific path (`get_tantivy_path(repo_id)`)
  - Add `repo_id` to the chunk query in `LexicalIndexer.build_index()`
  - Update callers to pass `repo_id=1`
  - Run tests

- [ ] **4.6 Update `scripts/index.py` CLI to accept `--repo` argument**
  - Add `--repo` argument (integer repo_id or string repo name)
  - Look up the repo in the database to get its `local_path`
  - If `--repo` not specified and `REPO_PATH` is set, use repo_id=1 (legacy behavior)
  - Pass `repo_id` to all pipeline functions
  - On completion: run `git rev-parse HEAD` in the repo dir, write result to `repos.indexed_commit_sha`, also set `repos.current_commit_sha` to the same value, set `commits_behind=0`, update `last_indexed_at`
  - Run tests

- [ ] **4.7 Add `git_utils.py` helper module**
  - New file: `src/indiseek/git_utils.py`
  - `get_head_sha(repo_path) -> str` — runs `git rev-parse HEAD`, returns the SHA
  - `fetch_remote(repo_path) -> None` — runs `git fetch origin`
  - `pull_remote(repo_path) -> None` — runs `git pull origin` (on current branch)
  - `count_commits_between(repo_path, old_sha, new_sha) -> int` — runs `git rev-list --count {old_sha}..{new_sha}`
  - `get_changed_files(repo_path, old_sha, new_sha) -> list[str]` — runs `git diff --name-only {old_sha}..{new_sha}`, returns list of file paths
  - `clone_repo(url, dest_path) -> None` — runs `git clone {url} {dest_path}`
  - All functions raise clear exceptions on failure (repo not found, not a git dir, etc.)
  - Run tests

## Phase 5: Repo-Scoped Agent + Tools

### Overview
The agent loop and tools need to operate within the context of a specific repo.

- [ ] **5.1 Update `create_agent_loop()` to accept `repo_id`**
  - Add `repo_id: int` parameter
  - Look up the repo's `local_path` from the database
  - Construct `VectorStore` with repo-specific table name
  - Construct `LexicalIndexer` with repo-specific path
  - `AgentLoop` stores `self._repo_id` and passes it to all store queries

- [ ] **5.2 Update `AgentLoop._execute_tool()` to pass `repo_id` to store methods**
  - `read_map` → `read_map(self._store, path=..., repo_id=self._repo_id)`
  - `resolve_symbol` → `resolve_symbol(self._store, ..., repo_id=self._repo_id)`
  - `read_file` already uses `self._repo_path` which is now repo-specific
  - `search_code` uses the repo-scoped `CodeSearcher` (already constructed with repo-specific backends)
  - `get_symbols_in_range` call in read_file handler: pass `repo_id`

- [ ] **5.3 Update tool functions to accept `repo_id`**
  - `read_map(store, path=None, repo_id=1)` — pass to `get_file_summaries()` and `get_directory_tree()`
  - `resolve_symbol(store, symbol_name, action, repo_id=1)` — pass to all store query methods
  - `read_file` already takes `repo_path` as a parameter, no change needed
  - Run tests

## Phase 6: Repo Management API

### Overview
Add backend API endpoints for CRUD operations on repos, and update all existing dashboard endpoints to be repo-scoped.

- [ ] **6.1 Add repo CRUD endpoints to `dashboard.py`**
  - `GET /dashboard/api/repos` — list all repos (id, name, url, status, last_indexed_at, indexed_commit_sha, current_commit_sha, commits_behind)
  - `POST /dashboard/api/repos` — add a new repo. Body: `{name, url}`. Triggers `git clone` into `REPOS_DIR/{new_id}/` via TaskManager. Repo row created immediately with `status='cloning'`, updated to `status='active'` when clone finishes. On clone complete, set `current_commit_sha` to HEAD of the clone.
  - `GET /dashboard/api/repos/{repo_id}` — full repo detail including pipeline status counts and freshness info
  - `DELETE /dashboard/api/repos/{repo_id}` — remove repo, delete all data, remove clone directory
  - Run tests

- [ ] **6.1b Add freshness check endpoint**
  - `POST /dashboard/api/repos/{repo_id}/check` — "Check for updates"
  - Runs `git fetch origin` in the repo's local clone
  - Runs `git rev-parse origin/HEAD` (or `origin/main` / `origin/master` — detect default branch) to get latest remote SHA
  - Compares to `repos.indexed_commit_sha`
  - Updates `repos.current_commit_sha` and `repos.commits_behind`
  - Returns: `{indexed_sha, current_sha, commits_behind, changed_files: [...]}`
  - `changed_files` comes from `git diff --name-only {indexed_sha}..{current_sha}` — shows what would be re-indexed
  - This is a fast operation (fetch + diff), not a TaskManager task — returns directly
  - Run tests

- [ ] **6.1c Add sync endpoint**
  - `POST /dashboard/api/repos/{repo_id}/sync` — "Pull + re-index changed files"
  - Runs via TaskManager (long-running)
  - Step 1: `git pull origin` to update the local clone
  - Step 2: `git diff --name-only {indexed_sha}..HEAD` to get changed files
  - Step 3: For changed/added files — re-run tree-sitter parse, re-embed chunks, re-summarize
  - Step 4: For deleted files — remove their rows from all tables (symbols, chunks, summaries, SCIP occurrences)
  - Step 5: Rebuild lexical index (Tantivy doesn't support incremental updates well — full rebuild is fast)
  - Step 6: Update `repos.indexed_commit_sha = HEAD`, `repos.current_commit_sha = HEAD`, `commits_behind = 0`, `last_indexed_at = now`
  - SSE progress streaming like existing indexing operations
  - If no changes detected (commits_behind = 0), return immediately with "already up to date"
  - Run tests

- [ ] **6.2 Add `repo_id` to existing dashboard stats/tree/file/chunk endpoints**
  - `GET /dashboard/api/repos/{repo_id}/stats`
  - `GET /dashboard/api/repos/{repo_id}/tree?path=`
  - `GET /dashboard/api/repos/{repo_id}/files/{file_path:path}`
  - `GET /dashboard/api/repos/{repo_id}/chunks/{chunk_id}`
  - Keep old un-scoped endpoints as aliases for repo_id=1
  - Run tests

- [ ] **6.3 Add `repo_id` to search and query endpoints**
  - `GET /dashboard/api/repos/{repo_id}/search?q=&mode=&limit=`
  - `POST /dashboard/api/repos/{repo_id}/run/query`
  - `GET /dashboard/api/repos/{repo_id}/queries`
  - `GET /dashboard/api/repos/{repo_id}/queries/{query_id}`
  - Update top-level `POST /query` in `server.py` to accept optional `repo_id`, default 1
  - Run tests

- [ ] **6.4 Add `repo_id` to indexing operation endpoints**
  - `POST /dashboard/api/repos/{repo_id}/run/treesitter`
  - `POST /dashboard/api/repos/{repo_id}/run/scip`
  - `POST /dashboard/api/repos/{repo_id}/run/embed`
  - `POST /dashboard/api/repos/{repo_id}/run/summarize`
  - `POST /dashboard/api/repos/{repo_id}/run/lexical`
  - Each looks up the repo's `local_path`, passes `repo_id` to pipeline functions
  - Keep old un-scoped endpoints as aliases for repo_id=1
  - Run tests

## Phase 7: Frontend — Repo Management Page

### Overview
Add a new Repos page for listing, adding, and managing repos. Independent of existing pages.

- [ ] **7.1 Add repo API types and functions to `client.ts`**
  - `Repo` interface: `{id, name, url, local_path, created_at, last_indexed_at, indexed_commit_sha, current_commit_sha, commits_behind, status}`
  - `FreshnessCheck` interface: `{indexed_sha, current_sha, commits_behind, changed_files: string[]}`
  - `fetchRepos()`, `fetchRepo(repoId)`, `createRepo(name, url)`, `deleteRepo(repoId)`
  - `checkRepoFreshness(repoId) -> FreshnessCheck` — POST to `/repos/{id}/check`
  - `syncRepo(repoId)` — POST to `/repos/{id}/sync`, returns SSE stream
  - Repo-scoped versions of existing functions: `fetchRepoStats(repoId)`, `fetchRepoTree(repoId, path)`, etc.

- [ ] **7.2 Add repo hooks to `hooks.ts`**
  - `useRepos()`, `useRepo(repoId)`, `useCreateRepo()`, `useDeleteRepo()`
  - `useCheckFreshness()` — mutation that calls `checkRepoFreshness`, invalidates repo query on success
  - `useSyncRepo()` — mutation that calls `syncRepo`, handles SSE streaming
  - Repo-scoped versions of existing hooks

- [ ] **7.3 Create `Repos.tsx` page**
  - List all repos as cards showing:
    - Name, URL, status
    - Freshness indicator: green "Fresh" badge when `commits_behind == 0`, yellow "N commits behind" badge when stale, gray "Unknown" when never checked
    - Indexed SHA (short, e.g., `a1b2c3d`) and last indexed time (relative, e.g., "2 hours ago")
  - Each card has three action buttons:
    - **"Check for updates"** — calls `checkRepoFreshness`, updates the badge. While running, shows spinner on the button. On completion, if stale, shows a summary: "5 commits behind, 12 files changed"
    - **"Sync"** — calls `syncRepo`, shows progress (SSE). Disabled when already fresh. On completion, badge flips to green.
    - **"View"** — navigates to the repo's overview
  - "Add Repo" form: name input, URL input, submit
  - Delete button with confirmation
  - Cloning status indicator (pulsing dot for `status='cloning'`)

- [ ] **7.4 Add Repos page to App.tsx routing and navigation**
  - Add to `navItems`: `{ to: '/repos', icon: GitBranch, label: 'Repos' }`
  - Add route: `<Route path="/repos" element={<Repos />} />`
  - Position first in nav

## Phase 8: Frontend — Repo-Scoped Pages

### Overview
All existing pages become repo-scoped via a repo selector in the nav.

- [ ] **8.1 Add repo context to the app**
  - React context `RepoContext` with `currentRepoId` and `setCurrentRepoId`
  - Initialize from localStorage, default to repo_id=1
  - Repo selector dropdown in nav sidebar

- [ ] **8.2 Update Overview page to be repo-scoped**
  - Use `currentRepoId` from context, call `useRepoStats(currentRepoId)`
  - Add freshness card at the top: shows indexed SHA, current SHA, commits behind, last indexed time
  - "Check for updates" and "Sync" buttons inline (same behavior as Repos page)
  - If stale, show a warning banner: "Index is N commits behind — Sync to update"

- [ ] **8.3 Update FileTree page to be repo-scoped**
  - Use `currentRepoId`, call `useRepoTree(currentRepoId, path)`

- [ ] **8.4 Update FileDetail and ChunkDetail pages to be repo-scoped**
  - Use `currentRepoId`, call repo-scoped endpoints

- [ ] **8.5 Update Search page to be repo-scoped**
  - Use `currentRepoId`, call repo-scoped search endpoint

- [ ] **8.6 Update Query page to be repo-scoped**
  - Use `currentRepoId`, pass `repoId` to `runQuery`
  - History sidebar shows only queries for current repo

- [ ] **8.7 Update Operations page to be repo-scoped**
  - Use `currentRepoId`, all "Run" buttons call repo-scoped endpoints

## Phase 9: Cleanup + Tests

- [ ] **9.1 Update all existing tests to use `repo_id`**
  - All store/tool tests pass `repo_id=1` (or test-specific id)
  - New `tests/test_repos.py` for repo CRUD
  - All tests pass

- [ ] **9.2 Make `REPO_PATH` optional in config**
  - Remove the hard requirement from `scripts/index.py`
  - Keep in `.env.example` as optional with comment
  - Update `CLAUDE.md` with new multi-repo workflow

- [ ] **9.3 Keep un-scoped endpoints as aliases**
  - Old endpoints alias to repo_id=1 for backwards compatibility
  - Remove in a future cleanup pass

- [ ] **9.4 Update SPEC.md**
  - Multi-repo section, `repos` table schema, per-repo storage layout

- [ ] **9.5 Update CLAUDE.md**
  - `scripts/index.py --repo <name-or-id>` documentation
  - Dashboard repo management workflow

---

## Migration Strategy

Zero-downtime, backwards-compatible:

1. **Schema migration** happens in `init_db()` via `ALTER TABLE ... ADD COLUMN ... DEFAULT 1`
2. **Legacy repo row** auto-created if data exists and no repos row exists
3. **`REPO_PATH`** continues working for legacy repo (repo_id=1)
4. **Old API endpoints** default to repo_id=1
5. **LanceDB**: existing `chunks` table used as-is for repo_id=1. New repos get `chunks_{repo_id}`
6. **Tantivy**: existing `DATA_DIR/tantivy` used as-is for repo_id=1. New repos get `DATA_DIR/tantivy_{repo_id}/`

No data copying or table reconstruction needed.

## Risk Areas

- **SCIP `UNIQUE(symbol)` constraint**: changing to `UNIQUE(symbol, repo_id)` requires table recreation. Since SCIP data is always fully reloaded on re-index, this is manageable — next SCIP load uses the new schema.
- **LanceDB table naming**: existing `chunks` table has no `repo_id` column. Leave as legacy table for repo_id=1, use `chunks_{id}` for new repos.
- **Concurrent indexing**: TaskManager currently allows one task globally. Keep this constraint for now — safe and simple. Per-repo task queues can come later.
