# Master Implementation Plan

Consolidated execution plan for all open implementation work across Indiseek. Each `## Phase` is one autonomous iteration. Phases are ordered to respect dependencies.

## Notes

- Original detailed plans archived in `docs/plans/done/` (agent-loop.md, hierarchical-directory-summaries.md, repo-management.md, todo.md, query-history.md)
- Query history was already fully implemented — archived without changes
- `todo.md` Phases 3-6 manual verification items (API key dependent) are not included here

---

## Phase 1: File Contents Storage Layer

Add a `file_contents` table to SQLite so the query service can serve file contents from the database instead of requiring the repo on disk at query time.

**Implementation:**

In `src/indiseek/storage/sqlite_store.py`:
- [x] Add `CREATE TABLE IF NOT EXISTS file_contents (file_path TEXT PRIMARY KEY, content TEXT NOT NULL, line_count INTEGER NOT NULL)` in `init_db()`
- [x] Add `insert_file_content(file_path, content)` — `INSERT OR REPLACE`, compute `line_count` from content
- [x] Add `get_file_content(file_path) -> str | None`
- [x] Add `DELETE FROM file_contents` to `clear_index_data()`

In `scripts/index.py`:
- [x] In the parse loop, after chunks insertion, read file content and call `store.insert_file_content(relative, content)`
- [x] Add a counter and print summary line for files stored

Tests in `tests/test_tools.py`:
- [x] Add round-trip test: insert content, retrieve, verify match
- [x] Add test: missing path returns None

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 2: Self-Contained read_file + Minimum Read Size

Refactor the agent's `read_file` tool to serve from SQLite instead of disk. Enforce minimum read window to prevent micro-reads.

**Implementation:**

In `src/indiseek/agent/loop.py` (read_file handler, ~lines 295-339):
- [x] Replace disk-read block with `content = self._store.get_file_content(file_path)`
- [x] Return error if `None` (file not in index)
- [x] Remove path traversal / exists checks (SQLite is source of truth)
- [x] Keep `self._file_cache` as in-memory cache over SQLite reads
- [x] After extracting `start_line`/`end_line`, if range < 100 lines, expand to 150 lines centered on midpoint
- [x] Log when expansion happens

Tests in `tests/test_agent.py`:
- [x] Update read_file tests to insert content via `store.insert_file_content()` instead of relying on disk files
- [x] Update caching tests to spy on `store.get_file_content` instead of `Path.read_text`
- [x] Update `test_execute_read_file_with_lines` to use range >= 100
- [x] Add `test_read_file_min_range_expansion` — verify small ranges get expanded

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 3: Search Previews + Iteration Budget

Fix the line cap documentation mismatch, give top search results longer previews, and tighten the iteration budget.

**Implementation:**

In `src/indiseek/agent/loop.py`:
- [x] Change `"Output is capped at 200 lines"` → `"Output is capped at 500 lines"` (line ~160)
- [x] `MAX_ITERATIONS = 15` → `12`
- [x] `SYNTHESIS_PHASE = 13` → `10`
- [x] Reflection hint: trigger at iteration 8 instead of 10
- [x] System prompt: `"at most 8-10 iterations"` → `"at most 7-8 iterations"`

In `src/indiseek/tools/search_code.py` `format_results()`:
- [x] Top 3 results: 600 chars / 15 lines preview
- [x] Results 4+: 300 chars / 8 lines (current behavior)

Tests:
- [x] `test_truncates_long_content` — increase test content to 700 chars
- [x] Add `test_top_results_get_longer_previews`
- [x] `test_max_iterations`: `== 15` → `== 12`
- [x] `test_budget_injected_into_evidence`: `"Iteration 1/15"` → `"Iteration 1/12"`
- [x] `test_system_prompt_includes_repo_map`: `"15 iterations"` → `"12 iterations"`

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 4: Directory Summaries — Backend

Add bottom-up LLM directory summaries. This phase covers the storage layer, summarizer method, and pipeline integration.

**Implementation:**

In `src/indiseek/storage/sqlite_store.py`:
- [x] Add `CREATE TABLE IF NOT EXISTS directory_summaries (id INTEGER PRIMARY KEY, dir_path TEXT UNIQUE, summary TEXT NOT NULL)`
- [x] Add `insert_directory_summary(dir_path, summary)` — INSERT OR REPLACE
- [x] Add `insert_directory_summaries(summaries: list[tuple[str, str]])` — batch insert
- [x] Add `get_directory_summary(dir_path) -> dict | None`
- [x] Add `get_directory_summaries(paths: list[str]) -> dict[str, str]` — batch lookup
- [x] Add `get_all_directory_paths_from_summaries() -> set[str]`

In `src/indiseek/indexer/summarizer.py`:
- [x] Add `summarize_directories()` method to `Summarizer` class
- [x] Walk directories bottom-up (deepest first) using paths from `file_summaries`
- [x] For each directory: collect child file summaries + child directory summaries (already computed), send to Gemini Flash
- [x] Skip directories already in `directory_summaries` (resume-safe)
- [x] Support `on_progress` callback
- [x] 0.5s delay between API calls

In `src/indiseek/indexer/pipeline.py`:
- [x] Add call to `summarize_directories()` after file summarization step
- [x] Only runs when `--summarize` flag is set
- [x] Add progress reporting

Tests:
- [x] Add tests for directory_summaries CRUD in sqlite_store
- [x] Add test for summarize_directories with mocked LLM calls

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 5: Directory Summaries — API + read_map

Expose directory summaries through the dashboard API and enhance the `read_map` agent tool.

**Implementation:**

In `src/indiseek/api/dashboard.py`:
- [x] Add `POST /dashboard/api/run/summarize-dirs` endpoint (or extend existing summarize endpoint)
- [x] Follows same pattern as `/run/summarize` — creates summarizer, calls `summarize_directories()`

In `src/indiseek/api/dashboard.py` (`/tree` endpoint):
- [x] After building children dict, batch-fetch file summaries for file children
- [x] Batch-fetch directory summaries for directory children
- [x] Add `summary` field (string or null) to each child in the response

In `src/indiseek/tools/read_map.py`:
- [x] Look up directory summaries via `store.get_directory_summaries()` (batch)
- [x] Modify `_format_tree` so directory lines render as `dirname/ — summary` instead of just `dirname/`
- [x] Fall back gracefully if no directory summaries exist

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 6: Directory Summaries — Frontend

Render directory and file summaries in the FileTree UI.

**Implementation:**

In `frontend/src/api/client.ts`:
- [x] Add `summary?: string` to `TreeChild` interface

In `frontend/src/pages/FileTree.tsx`:
- [x] File rows: add summary text between filename and badges — `flex-1 min-w-0 truncate text-xs text-gray-500`
- [x] Directory rows: add summary between dir name and stats — same truncated style
- [x] Use `title={child.summary}` for hover to see full text
- [x] Graceful degradation if no summary

**Verify:**
- [x] `cd frontend && npm run build` succeeds
- [x] `pytest` passes (no Python changes expected, but confirm nothing broke)

---

## Phase 7: Multi-Repo — Schema + Migration

*Original detail: docs/plans/done/repo-management.md, Phase1

Add the `repos` table and `repo_id` column to all data tables. Migrate existing data to repo_id=1. App still works identically after this phase.

**Migration strategy:** Zero-downtime, backwards-compatible. Use `ALTER TABLE ... ADD COLUMN repo_id INTEGER DEFAULT 1` — the DEFAULT 1 automatically assigns all existing rows to the legacy repo. No data copying or table reconstruction needed. Use the same migration pattern as the existing `source_query_id` migration in `init_db()`: check if column exists, ALTER if missing.

**Implementation:**

- [x] **1.1** Add `repos` table to `init_db()`: `repos(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, url TEXT, local_path TEXT NOT NULL, created_at TEXT NOT NULL, last_indexed_at TEXT, indexed_commit_sha TEXT, current_commit_sha TEXT, commits_behind INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'active')` with index on `name`
- [x] **1.2** Add `repo_id INTEGER DEFAULT 1` column to `symbols`, `chunks`, `file_summaries` tables via ALTER TABLE migration (same pattern as existing `source_query_id` migration). Add indexes.
- [x] **1.3** Add `repo_id INTEGER DEFAULT 1` column to `scip_symbols`, `scip_occurrences`, `scip_relationships` tables via migration. Add indexes.
- [x] **1.4** Add `repo_id INTEGER DEFAULT 1` column to `queries` table via migration. Add index.
- [x] **1.4b** Add `repo_id INTEGER DEFAULT 1` column to `file_contents` and `directory_summaries` tables via migration. Add indexes. *(Note: these tables were added by Phases 1 and 4 of this master plan; not in the original repo-management plan.)*
- [x] **1.5** Auto-create legacy repo row: if `repos` table empty AND `symbols` has rows, insert `(id=1, name=<from REPO_PATH or 'legacy'>, local_path=<REPO_PATH>, created_at=now, status='active')`
- [x] **1.6** Add `SqliteStore` helper methods: `insert_repo()`, `get_repo()`, `get_repo_by_name()`, `list_repos()`, `update_repo()`, `delete_repo()`

Tests:
- [x] Add tests for repo CRUD methods
- [x] All existing tests still pass

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 8: Multi-Repo — Scope Store Operations

*Original detail: docs/plans/done/repo-management.md, Phase2

Update all SqliteStore methods to accept and filter by `repo_id`. All callers pass `repo_id=1` to maintain current behavior.

**Implementation:**

- [x] **2.1** Scope `insert_symbols` and `insert_chunks` by `repo_id`. Update `Symbol`/`Chunk` dataclasses with optional `repo_id`.
- [x] **2.2** Scope `insert_file_summary`, `insert_file_summaries`, `get_file_summaries`, `get_file_summary`, `get_directory_tree` by `repo_id`.
- [x] **2.2b** Scope `insert_directory_summary`, `insert_directory_summaries`, `get_directory_summary`, `get_directory_summaries` by `repo_id`. *(Not in original plan.)*
- [x] **2.2c** Scope `insert_file_content`, `get_file_content` by `repo_id`. *(Not in original plan.)*
- [x] **2.3** Scope SCIP operations by `repo_id`. Change UNIQUE constraint on `scip_symbols` from `UNIQUE(symbol)` to `UNIQUE(symbol, repo_id)`. **Risk:** changing the constraint requires table recreation. Since SCIP data is always fully reloaded on re-index (`clear_index_data()`), change the CREATE TABLE DDL — the new constraint takes effect on next full index. Existing data is fine until then.
- [x] **2.4** Scope `get_symbols_*` and `get_chunks_*` methods by `repo_id`.
- [x] **2.5** Scope `clear_index_data` and `clear_index_data_for_prefix` by `repo_id`.
- [x] **2.6** Scope dashboard query methods: `get_all_file_paths_from_chunks`, `get_all_file_paths_from_summaries`, `count()`.
- [x] **2.7** Scope query history methods: `insert_query`, `insert_cached_query`, `list_queries`, `get_completed_queries_since`.
- [x] Update ALL callers (pipeline.py, dashboard.py, agent/loop.py, tools, tests) to pass `repo_id=1`.

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 9: Multi-Repo — Per-Repo Storage Backends

*Original detail: docs/plans/done/repo-management.md, Phase3

Make LanceDB and Tantivy repo-aware with per-repo table names and index directories.

**Implementation:**

- [x] **3.1** Make `VectorStore` repo-aware: change `TABLE_NAME` from class constant to instance variable, constructor accepts `table_name` parameter. Convention: `chunks_{repo_id}` for new repos. **Legacy handling:** existing `"chunks"` table has no `repo_id` column — leave it as-is for repo_id=1. New repos get `chunks_{id}`. If `table_name` is `"chunks"` and a legacy table exists, keep using it.
- [x] **3.2** Confirm `LexicalIndexer` is already repo-aware (constructor accepts `index_path`). Convention: callers pass `DATA_DIR/tantivy_{repo_id}/`.
- [x] **3.3** Add config helpers: `REPOS_DIR = DATA_DIR / "repos"`, helper functions `get_repo_path(repo_id)`, `get_lancedb_table_name(repo_id)`, `get_tantivy_path(repo_id)`. Legacy repo (id=1) uses `REPO_PATH` if set.

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 10: Multi-Repo — Scoped Indexing Pipeline

*Original detail: docs/plans/done/repo-management.md, Phase4

Update all pipeline functions to accept `repo_id` and scope their storage operations.

**Implementation:**

- [x] **4.1** Update `run_treesitter()` to accept `repo_id`, pass to insert methods. Update callers.
- [x] **4.2** Update `run_scip()` to accept `repo_id`, pass through `ScipLoader`. Update callers.
- [x] **4.3** Update `Embedder.embed_all_chunks()` to accept `repo_id`, scope chunk query, use repo-specific VectorStore table. Update callers.
- [x] **4.4** Update `Summarizer.summarize_repo()` and `summarize_directories()` to accept `repo_id`. Update callers.
- [x] **4.5** Update `run_lexical()` to accept `repo_id`, use repo-specific Tantivy path. Update callers.
- [x] **4.6** Update `scripts/index.py` CLI: add `--repo` argument (id or name). Look up repo's `local_path`. Default to repo_id=1 when `REPO_PATH` set. On completion, write HEAD SHA to `repos.indexed_commit_sha`, update `last_indexed_at`.
- [x] **4.7** Add `src/indiseek/git_utils.py`: `get_head_sha()`, `fetch_remote()`, `pull_remote()`, `count_commits_between()`, `get_changed_files()`, `clone_repo()`. All raise clear exceptions on failure.

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 11: Multi-Repo — Scoped Agent + Tools

*Original detail: docs/plans/done/repo-management.md, Phase5

The agent loop and tools operate within a specific repo's context.

**Implementation:**

- [x] **5.1** Update `create_agent_loop()` to accept `repo_id`. Look up repo's `local_path`. Construct repo-specific VectorStore and LexicalIndexer. `AgentLoop` stores `self._repo_id`.
- [x] **5.2** Update `AgentLoop._execute_tool()` to pass `repo_id` to all store methods: `read_map`, `resolve_symbol`, `get_symbols_in_range`, `get_file_content`.
- [x] **5.3** Update tool functions: `read_map(store, path=None, repo_id=1)`, `resolve_symbol(store, symbol_name, action, repo_id=1)`. `read_file` already takes `repo_path` as parameter.

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 12: Multi-Repo — Management API

*Original detail: docs/plans/done/repo-management.md, Phase6

Add backend API endpoints for repo CRUD, freshness checks, sync, and scope all existing endpoints by repo_id.

**Implementation:**

- [x] **6.1** Add repo CRUD endpoints: `GET /repos`, `POST /repos` (triggers git clone via TaskManager), `GET /repos/{repo_id}`, `DELETE /repos/{repo_id}`.
- [x] **6.1b** Add freshness check: `POST /repos/{repo_id}/check` — runs `git fetch origin`, then `git rev-parse` on the default remote branch (detect origin/main vs origin/master) to get latest remote SHA. Compares to `repos.indexed_commit_sha`. Updates `repos.current_commit_sha` and `repos.commits_behind`. Returns `{indexed_sha, current_sha, commits_behind, changed_files}`. This is a fast synchronous operation (no TaskManager needed).
- [x] **6.1c** Add sync endpoint: `POST /repos/{repo_id}/sync` — runs via TaskManager with SSE progress streaming. Steps: (1) `git pull origin` to update clone, (2) `git diff --name-only {indexed_sha}..HEAD` to get changed files, (3) for changed/added files: re-run tree-sitter parse, re-embed chunks, re-summarize, (4) for deleted files: remove their rows from all tables (symbols, chunks, summaries, SCIP occurrences, file_contents), (5) rebuild lexical index (full rebuild — Tantivy doesn't support incremental well), (6) update `repos.indexed_commit_sha = HEAD`, `current_commit_sha = HEAD`, `commits_behind = 0`, `last_indexed_at = now`. If no changes detected, return immediately with "already up to date".
- [x] **6.2** Add `repo_id` to existing dashboard endpoints: `stats`, `tree`, `files`, `chunks`. Keep old un-scoped endpoints as aliases for repo_id=1.
- [x] **6.3** Add `repo_id` to search and query endpoints. Update top-level `POST /query` to accept optional `repo_id`, default 1.
- [x] **6.4** Add `repo_id` to indexing operation endpoints (`run/treesitter`, `run/scip`, `run/embed`, `run/summarize`, `run/lexical`). Keep old endpoints as aliases.

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 13: Multi-Repo — Frontend Repos Page

*Original detail: docs/plans/done/repo-management.md, Phase7

New Repos page for listing, adding, and managing repos.

**Implementation:**

- [x] **7.1** Add repo API types and functions to `frontend/src/api/client.ts`: `Repo`, `FreshnessCheck` interfaces; `fetchRepos()`, `fetchRepo()`, `createRepo()`, `deleteRepo()`, `checkRepoFreshness()`, `syncRepo()`.
- [x] **7.2** Add repo hooks to `frontend/src/api/hooks.ts`: `useRepos()`, `useRepo()`, `useCreateRepo()`, `useDeleteRepo()`, `useCheckFreshness()`, `useSyncRepo()`.
- [x] **7.3** Create `frontend/src/pages/Repos.tsx`: repo cards with name/URL/status, freshness badge, action buttons (Check/Sync/View), "Add Repo" form, delete with confirmation.
- [x] **7.4** Add to `App.tsx`: route `/repos`, nav item with GitBranch icon, positioned first in nav.

**Verify:**
- [x] `cd frontend && npm run build` succeeds
- [x] `pytest` passes

---

## Phase 14: Multi-Repo — Repo-Scoped Frontend Pages

*Original detail: docs/plans/done/repo-management.md, Phase8

All existing dashboard pages become repo-scoped via a repo selector.

**Implementation:**

- [x] **8.1** Add `RepoContext` (React context) with `currentRepoId` + `setCurrentRepoId`. Initialize from localStorage, default repo_id=1. Repo selector dropdown in nav sidebar.
- [x] **8.2** Update Overview page: use `currentRepoId`, add freshness card with Check/Sync buttons, stale warning banner.
- [x] **8.3** Update FileTree page: use `currentRepoId` for tree API calls.
- [x] **8.4** Update FileDetail and ChunkDetail pages: use `currentRepoId`.
- [x] **8.5** Update Search page: use `currentRepoId` for search endpoint.
- [x] **8.6** Update Query page: use `currentRepoId`, pass `repoId` to `runQuery`, filter history by repo.
- [x] **8.7** Update Operations page: use `currentRepoId` for all "Run" buttons.

**Verify:**
- [x] `cd frontend && npm run build` succeeds
- [x] `pytest` passes

---

## Phase 15: Multi-Repo — Cleanup + Tests

*Original detail: docs/plans/done/repo-management.md, Phase9

Final cleanup, test coverage, documentation updates.

**Implementation:**

- [ ] **9.1** Update all existing tests to use `repo_id` parameter explicitly. Add `tests/test_repos.py` for repo CRUD. All tests pass.
- [ ] **9.2** Make `REPO_PATH` optional in config. Update `.env.example` with comment. Update `scripts/index.py` to work without it when `--repo` is specified.
- [ ] **9.3** Verify un-scoped API endpoints still work as aliases for repo_id=1.
- [ ] **9.4** Update `docs/SPEC.md`: multi-repo section, `repos` table schema, per-repo storage layout.
- [ ] **9.5** Update `CLAUDE.md`: `scripts/index.py --repo <name-or-id>` documentation, dashboard repo management workflow.

**Verify:**
- [ ] `pytest` passes
- [ ] `ruff check src/` clean
- [ ] `cd frontend && npm run build` succeeds
