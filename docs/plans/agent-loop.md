# Plan: Self-Contained Query Service + Agent Efficiency

## Context

The agent's `read_file` tool reads source files from disk at query time, meaning the target repo must be cloned locally. The other 3 tools (`search_code`, `resolve_symbol`, `read_map`) already work entirely from indexed data. Storing full file contents in SQLite during indexing makes the service self-contained after indexing.

Additionally, query traces (002-004) reveal agent inefficiencies: micro-reads waste iterations, search previews are too short forcing follow-up reads, `resolve_symbol` is used too late, and the iteration budget is too generous.

## Steps

### Step 1: Add `file_contents` table to SQLite store

**File:** `src/indiseek/storage/sqlite_store.py`

- [ ] Add `CREATE TABLE IF NOT EXISTS file_contents (file_path TEXT PRIMARY KEY, content TEXT NOT NULL, line_count INTEGER NOT NULL)` in `init_db()`
- [ ] Add `insert_file_content(file_path, content)` — uses `INSERT OR REPLACE`, computes `line_count` from content
- [ ] Add `get_file_content(file_path) -> str | None`
- [ ] Add `DELETE FROM file_contents` to `clear_index_data()`

**Test:** `tests/test_tools.py` — add round-trip test (insert, retrieve, verify) and missing-path-returns-None test.

---

### Step 2: Store file contents during indexing

**File:** `scripts/index.py`

- [ ] In the parse loop (~line 166), after chunks insertion, read file content and call `store.insert_file_content(relative, content)`
- [ ] Add a counter and print summary

---

### Step 3: Refactor `read_file` to serve from SQLite

**File:** `src/indiseek/agent/loop.py` (lines 295-339)

- [ ] Replace the disk-read block with `content = self._store.get_file_content(file_path)`
- [ ] If `None`, return error
- [ ] Remove path traversal/exists checks (SQLite is the source of truth)
- [ ] Keep `self._file_cache` as in-memory cache over SQLite reads

**Tests to update** in `tests/test_agent.py`:
- [ ] Insert content into store via `store.insert_file_content()` instead of relying on disk files
- [ ] Update caching tests to spy on `store.get_file_content` instead of `Path.read_text`

---

### Step 4: Enforce minimum read size

**File:** `src/indiseek/agent/loop.py`

- [ ] After extracting `start_line`/`end_line`, if range < 100 lines, expand to 150 centered on midpoint
- [ ] Log when expansion happens

**Tests:** `tests/test_agent.py`
- [ ] Update `test_execute_read_file_with_lines` to use range >= 100
- [ ] Add `test_read_file_min_range_expansion`

---

### Step 5: Fix line cap mismatch + richer search previews

**File:** `src/indiseek/agent/loop.py` line 160
- [ ] Change `"Output is capped at 200 lines"` to `"Output is capped at 500 lines"`

**File:** `src/indiseek/tools/search_code.py` `format_results()` (line 199)
- [ ] Top 3 results: 600 chars / 15 lines preview
- [ ] Results 4+: 300 chars / 8 lines (current behavior)

**Tests:** `tests/test_tools.py`
- [ ] Update `test_truncates_long_content` — increase test content to 700 chars
- [ ] Add `test_top_results_get_longer_previews`

---

### Step 6: Contextual `resolve_symbol` suggestion after search

**File:** `src/indiseek/agent/loop.py`

- [ ] After search_code formatting, extract top symbol names and append tip if resolve_symbol not yet used

**Test:** `tests/test_agent.py` — add `test_search_code_suggests_resolve_symbol`

---

### Step 7: Reduce iteration budget

**File:** `src/indiseek/agent/loop.py`
- [ ] `MAX_ITERATIONS = 15` → `12`
- [ ] `SYNTHESIS_PHASE = 13` → `10`
- [ ] Reflection hint: trigger at iteration 8 instead of 10
- [ ] System prompt: `"at most 8-10 iterations"` → `"at most 7-8 iterations"`

**Tests to update** in `tests/test_agent.py`:
- [ ] `test_max_iterations`: `== 15` → `== 12`
- [ ] `test_budget_injected_into_evidence`: `"Iteration 1/15"` → `"Iteration 1/12"`
- [ ] `test_system_prompt_includes_repo_map`: `"15 iterations"` → `"12 iterations"`

## Verification

After each step:
1. `pytest` — all tests pass
2. `ruff check src/` — no lint errors
