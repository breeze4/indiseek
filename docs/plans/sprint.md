# Sprint Plan

Rolling plan for incremental fixes and features. Each `## Phase` is one autonomous Ralph iteration — small enough to complete in a single loop, verified before marking done.

## Notes

- Phases are independent unless explicitly marked with dependencies
- Each phase follows the atomic/incremental/always-functional checklist pattern
- Add new phases at the bottom as work is identified

---

## Phase 1: Fix Sync — Re-embed and Re-summarize Changed Files

The `/repos/{id}/sync` endpoint only re-runs tree-sitter and lexical index. Changed files don't get re-embedded or re-summarized, leaving vector search and file summaries stale after sync.

**Implementation:**

In `src/indiseek/api/dashboard.py` (`_run` inside `sync_repo`):
- [x] After re-parsing changed files, re-embed their chunks: instantiate `Embedder`, call `embed_chunks()` for the changed file paths only (filter chunks by file_path)
- [x] After re-parsing, re-summarize changed files: instantiate `Summarizer`, call it for each changed file that exists on disk
- [x] Update directory summaries for parent directories of changed files
- [x] Import the necessary classes at the top of `_run`

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean
- [ ] Manual: sync a repo with known changes, confirm embeddings and summaries update

---

## Phase 2: Fix Sync — Handle Non-TS Files and Deleted File Cleanup

The sync loop only processes `.ts`/`.tsx` files, skipping `.js`, `.json`, `.md`, etc. Also has a redundant double-delete of changed files.

**Implementation:**

In `src/indiseek/api/dashboard.py` (`_run` inside `sync_repo`):
- [x] Widen the file extension filter to match what the full indexing pipeline handles (`.ts`, `.tsx`, `.js`, `.jsx`)
- [x] Update `file_contents` for non-parseable changed files that exist on disk (`.json`, `.md`, `.yaml`) — they don't get symbols/chunks but should have current content and summaries
- [x] Remove the redundant second pass that re-clears deleted files (line ~283-286) — already cleared in the first pass
- [x] For deleted files, separate them from modified files before the parse loop: split `changed` into `modified` (exists on disk) and `deleted` (doesn't exist), clear deleted once, parse modified

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 3: Fix /check — Honest Response for Never-Indexed Repos

When `indexed_sha` is null, `/check` returns `commits_behind: 0` which is misleading. The caller can't distinguish "up to date" from "never indexed."

**Implementation:**

In `src/indiseek/api/dashboard.py` (`check_repo_freshness`):
- [x] When `indexed_sha` is null, set `commits_behind` to `-1` (or a sentinel) and add a `"status": "not_indexed"` field to the response
- [x] When indexed and up to date, add `"status": "current"`
- [x] When stale, add `"status": "stale"`
- [x] Update frontend Repos page to show appropriate badge/message for each status

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean
- [ ] Manual: check a never-indexed repo, confirm response has `status: not_indexed`

---

## Phase 4: Sync — SCIP Reload for Changed Files

SCIP cross-reference data is not updated during sync. After a sync, go-to-definition and find-references return stale results.

**Implementation:**

- [x] After tree-sitter re-parse, check if a SCIP index file exists at the repo's expected path
- [x] If it does, re-run `run_scip()` scoped to the repo — SCIP is all-or-nothing (the protobuf contains the whole index), so this is a full SCIP reload, not incremental
- [x] If no SCIP index exists, skip silently
- [x] Add progress callback for SCIP step

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 5: Add Tests for Sync Endpoint

The sync endpoint has zero test coverage. Add tests that exercise the main code paths.

**Implementation:**

In `tests/test_dashboard.py` (or new `tests/test_sync.py`):
- [x] Test sync with no changes (indexed_sha == HEAD after pull) returns up_to_date
- [x] Test sync with changed files: mock git operations, verify tree-sitter re-parse is called for changed files, verify old data is cleared
- [x] Test sync with deleted files: verify rows are removed
- [x] Test sync with null indexed_sha: verify full re-index path is taken
- [x] Test sync rejects when another task is running (409)

Also fixed: task_id closure race condition across all dashboard endpoints (pre-generate task_id before defining `_run`).

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 6: Add Tests for /check Endpoint

The check endpoint also has zero test coverage.

**Implementation:**

In `tests/test_dashboard.py`:
- [x] Test check with valid indexed_sha and matching remote: commits_behind=0, status=current
- [x] Test check with valid indexed_sha and diverged remote: correct commits_behind and changed_files
- [x] Test check with null indexed_sha: status=not_indexed (after Phase 3)
- [x] Test check with repo not found: 404
- [x] Test check with missing local path: 400

**Verify:**
- [x] `pytest` passes
- [x] `ruff check src/` clean

---

## Phase 7: Directory Summaries — Fix Population and Visibility

Directory summaries aren't actually being populated or shown for repos. The summarize-dirs pipeline step exists but isn't being triggered properly, and the dashboard doesn't surface their status.

**Implementation:**

In `src/indiseek/api/dashboard.py`:
- [x] Verify the `/run/summarize-dirs` endpoint actually works end-to-end — trace from API call through `Summarizer.summarize_directories()` and confirm rows land in `directory_summaries`
- [x] ~~If the endpoint is broken, fix whatever is preventing population~~ — endpoint is wired up correctly, repo_id flows through all layers
- [x] Add directory summary count to the `/stats` endpoint response (total dirs, dirs with summaries)
- [x] The `/tree` endpoint already includes directory summaries — verified, uses `store.get_directory_summaries()` and attaches to each dir child

In frontend:
- [x] FileTree already renders directory summaries — verified, `child.summary && <span>...</span>` at TreeNode
- [x] Added `directory_summaries` to Stats type and Overview page

**Verify:**
- [x] `pytest` passes (404 tests)
- [x] `ruff check src/` clean
- [ ] Manual: run summarize-dirs for a repo, confirm summaries appear in `/tree` response and FileTree UI

---

## Phase 8: Incremental Summary Generation

Add the ability to generate only missing summaries (files and directories) rather than re-running the full summarization. Should be able to see what needs creation or updates based on repo changes.

**Implementation:**

In `src/indiseek/api/dashboard.py`:
- [x] Add `GET /api/repos/{repo_id}/summary-status` endpoint — returns files_total, files_summarized, files_missing, files_missing_paths (first 100), dirs_total, dirs_summarized, dirs_missing
- [x] The existing summarizer already skips files that have summaries (resume-safe) — verified for both file and directory summarization

In `src/indiseek/api/dashboard.py`:
- [x] Add `POST /api/run/summarize-missing` — runs file summarization (skips existing) then directory summarization (skips existing) via `RunRequest` body
- [x] After a sync (Phase 1-2), changed files already have their old summaries deleted so re-summarization picks them up as missing
- [x] Progress reporting via SSE (uses `_make_progress_callback`)

In `src/indiseek/storage/sqlite_store.py`:
- [x] Add `get_all_file_paths_from_file_contents()` method for summary-status counts

In frontend:
- [x] Add `SummaryStatus` type, `fetchSummaryStatus`, `runSummarizeMissing` to `client.ts`
- [x] Add `useSummaryStatus`, `useRunSummarizeMissing` hooks
- [x] Add `SummaryStatusBar` component to Repos page — shows file/dir summary counts and "Generate Missing" button
- [x] Progress shown via existing task stream panel

**Verify:**
- [x] `pytest` passes (404 tests)
- [x] `ruff check src/` clean
- [x] TypeScript type check clean
- [ ] Manual: index a repo without `--summarize`, confirm summary-status shows all missing, run summarize-missing, confirm counts update

---

## Phase 9: Fix Test Suite Issues

Two issues observed when running the full test suite (`pytest`, 341 tests, ~2min):

### 9a: Researcher tool error in tests

A `tool error: resolve_symbol: 'symbol_name'` log line appeared during tests. Root cause: bare `args['symbol_name']` and `args['action']` key access in summary building (loop.py:387, classic.py:337) — `KeyError` when the LLM returns incomplete args.

- [x] Found: `loop.py:387` and `classic.py:337` use bare dict key access `args['symbol_name']`
- [x] Fixed: changed to `args.get('symbol_name', '?')` and `args.get('action', '?')` in both files. Also fixed `args['path']` -> `args.get('path', '?')` for `read_file` summary.
- [x] Confirmed: no `resolve_symbol` or `KeyError` log lines during clean test run

### 9b: TaskManager KeyError race condition

```
Task e4ec5656-... (query) failed
KeyError: 'e4ec5656-...'
```

Root cause: `test_cache.py` called `_task_manager._tasks.clear()` without acquiring `_lock`, racing with background threads from previous tests that still held references to the module-level `_task_manager`.

- [x] Read `src/indiseek/api/task_manager.py` — traced full lifecycle
- [x] Fixed `TaskManager._run`: replaced `self._tasks[task_id]` dict subscript with `self._tasks.get(task_id)` + None guard (defensive against external clear)
- [x] Fixed `test_cache.py`: replaced bare `_tasks.clear()` with fresh `TaskManager()` per test (matching `test_dashboard.py` pattern) — eliminates the thread pool race entirely

**Verify:**
- [x] `pytest` passes (404 tests)
- [x] `ruff check src/` clean
- [x] No `KeyError` or unexpected error log lines during test run
