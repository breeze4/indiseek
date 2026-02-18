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
- [ ] After re-parsing changed files, re-embed their chunks: instantiate `Embedder`, call `embed_chunks()` for the changed file paths only (filter chunks by file_path)
- [ ] After re-parsing, re-summarize changed files: instantiate `Summarizer`, call it for each changed file that exists on disk
- [ ] Update directory summaries for parent directories of changed files
- [ ] Import the necessary classes at the top of `_run`

**Verify:**
- [ ] `pytest` passes
- [ ] `ruff check src/` clean
- [ ] Manual: sync a repo with known changes, confirm embeddings and summaries update

---

## Phase 2: Fix Sync — Handle Non-TS Files and Deleted File Cleanup

The sync loop only processes `.ts`/`.tsx` files, skipping `.js`, `.json`, `.md`, etc. Also has a redundant double-delete of changed files.

**Implementation:**

In `src/indiseek/api/dashboard.py` (`_run` inside `sync_repo`):
- [ ] Widen the file extension filter to match what the full indexing pipeline handles (`.ts`, `.tsx`, `.js`, `.jsx`)
- [ ] Update `file_contents` for non-parseable changed files that exist on disk (`.json`, `.md`, `.yaml`) — they don't get symbols/chunks but should have current content and summaries
- [ ] Remove the redundant second pass that re-clears deleted files (line ~283-286) — already cleared in the first pass
- [ ] For deleted files, separate them from modified files before the parse loop: split `changed` into `modified` (exists on disk) and `deleted` (doesn't exist), clear deleted once, parse modified

**Verify:**
- [ ] `pytest` passes
- [ ] `ruff check src/` clean

---

## Phase 3: Fix /check — Honest Response for Never-Indexed Repos

When `indexed_sha` is null, `/check` returns `commits_behind: 0` which is misleading. The caller can't distinguish "up to date" from "never indexed."

**Implementation:**

In `src/indiseek/api/dashboard.py` (`check_repo_freshness`):
- [ ] When `indexed_sha` is null, set `commits_behind` to `-1` (or a sentinel) and add a `"status": "not_indexed"` field to the response
- [ ] When indexed and up to date, add `"status": "current"`
- [ ] When stale, add `"status": "stale"`
- [ ] Update frontend Repos page to show appropriate badge/message for each status

**Verify:**
- [ ] `pytest` passes
- [ ] `ruff check src/` clean
- [ ] Manual: check a never-indexed repo, confirm response has `status: not_indexed`

---

## Phase 4: Sync — SCIP Reload for Changed Files

SCIP cross-reference data is not updated during sync. After a sync, go-to-definition and find-references return stale results.

**Implementation:**

- [ ] After tree-sitter re-parse, check if a SCIP index file exists at the repo's expected path
- [ ] If it does, re-run `run_scip()` scoped to the repo — SCIP is all-or-nothing (the protobuf contains the whole index), so this is a full SCIP reload, not incremental
- [ ] If no SCIP index exists, skip silently
- [ ] Add progress callback for SCIP step

**Verify:**
- [ ] `pytest` passes
- [ ] `ruff check src/` clean

---

## Phase 5: Add Tests for Sync Endpoint

The sync endpoint has zero test coverage. Add tests that exercise the main code paths.

**Implementation:**

In `tests/test_dashboard.py` (or new `tests/test_sync.py`):
- [ ] Test sync with no changes (indexed_sha == HEAD after pull) returns up_to_date
- [ ] Test sync with changed files: mock git operations, verify tree-sitter re-parse is called for changed files, verify old data is cleared
- [ ] Test sync with deleted files: verify rows are removed
- [ ] Test sync with null indexed_sha: verify full re-index path is taken
- [ ] Test sync rejects when another task is running (409)

**Verify:**
- [ ] `pytest` passes
- [ ] `ruff check src/` clean

---

## Phase 6: Add Tests for /check Endpoint

The check endpoint also has zero test coverage.

**Implementation:**

In `tests/test_dashboard.py`:
- [ ] Test check with valid indexed_sha and matching remote: commits_behind=0, status=current
- [ ] Test check with valid indexed_sha and diverged remote: correct commits_behind and changed_files
- [ ] Test check with null indexed_sha: status=not_indexed (after Phase 3)
- [ ] Test check with repo not found: 404
- [ ] Test check with missing local path: 400

**Verify:**
- [ ] `pytest` passes
- [ ] `ruff check src/` clean

---

## Phase 7: Directory Summaries — Fix Population and Visibility

Directory summaries aren't actually being populated or shown for repos. The summarize-dirs pipeline step exists but isn't being triggered properly, and the dashboard doesn't surface their status.

**Implementation:**

In `src/indiseek/api/dashboard.py`:
- [ ] Verify the `/run/summarize-dirs` endpoint actually works end-to-end — trace from API call through `Summarizer.summarize_directories()` and confirm rows land in `directory_summaries`
- [ ] If the endpoint is broken, fix whatever is preventing population (likely a missing repo_id passthrough or the endpoint not being wired up correctly)
- [ ] Add directory summary count to the `/stats` endpoint response (total dirs, dirs with summaries)
- [ ] The `/tree` endpoint should already include directory summaries per Phase 5-6 of master plan — verify this works and summaries actually appear in the tree response

In frontend:
- [ ] Verify FileTree renders directory summaries (was implemented in Phase 6 of master plan) — if the data isn't there, the UI won't show anything even if the rendering code exists

**Verify:**
- [ ] `pytest` passes
- [ ] `ruff check src/` clean
- [ ] Manual: run summarize-dirs for a repo, confirm summaries appear in `/tree` response and FileTree UI

---

## Phase 8: Incremental Summary Generation

Add the ability to generate only missing summaries (files and directories) rather than re-running the full summarization. Should be able to see what needs creation or updates based on repo changes.

**Implementation:**

In `src/indiseek/api/dashboard.py`:
- [ ] Add `GET /dashboard/api/repos/{repo_id}/summary-status` endpoint that returns: total files, files with summaries, files missing summaries, total directories, directories with summaries, directories missing summaries, list of unsummarized file paths (or first N)
- [ ] The existing summarizer already skips files that have summaries (resume-safe) — verify this works correctly for both file and directory summarization

In `src/indiseek/api/dashboard.py` (or extend existing endpoints):
- [ ] Add `POST /dashboard/api/repos/{repo_id}/run/summarize-missing` that runs file summarization (skips existing) then directory summarization (skips existing) — just the resume behavior but as an explicit "fill gaps" action
- [ ] After a sync (Phase 1-2), changed files should have their old summaries deleted so re-summarization picks them up as missing
- [ ] Progress reporting via SSE for the missing-summary run

In frontend:
- [ ] Add summary status to the repo overview or operations page — show counts and a "Generate Missing Summaries" button
- [ ] Show progress during generation

**Verify:**
- [ ] `pytest` passes
- [ ] `ruff check src/` clean
- [ ] Manual: index a repo without `--summarize`, confirm summary-status shows all missing, run summarize-missing, confirm counts update
