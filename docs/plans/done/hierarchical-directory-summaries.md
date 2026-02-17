# Hierarchical Directory Summaries

## Problem

File summaries exist per-file in `file_summaries` but there's no rollup. You can't glance at `packages/vite/src/node/server/` and immediately know "this is the dev server — HTTP handling, HMR, module graph, plugin pipeline." You have to click into individual files. The tree UI also wastes horizontal space — file/dir names sit on the left, PSE badges on the far right, and the entire middle is empty.

## Approach

### Backend: Bottom-up LLM rollup

After file summarization completes, walk the directory tree bottom-up. For each directory, collect its children's summaries (both files and already-summarized subdirectories), send them to Gemini Flash with a prompt like "Given these contents, summarize this directory's purpose in one sentence." Store results in a new `directory_summaries` table.

The rollup is bottom-up so leaf directories get summarized first from file summaries, then parent directories get summarized from child directory summaries + direct file summaries. This means a top-level directory like `packages/vite/src/` gets a high-level architectural summary, not a dump of 200 file descriptions.

**New table:**
```
directory_summaries(dir_path TEXT UNIQUE, summary TEXT)
```

**Cost:** One Gemini Flash call per directory. For Vite (~300 directories), this is roughly ~100k tokens input → ~$0.01. Trivial compared to file summarization.

### Backend: Serve summaries in `/tree` API

The `/tree` endpoint already fetches coverage sets. Add a batch fetch of summaries for the current level's children:
- For file children: look up `file_summaries` by path
- For directory children: look up `directory_summaries` by path
- Add `summary?: string` to the response

This is one SQL query with an `IN` clause, not N+1. Payload stays small because we only fetch summaries for the ~20-50 items at the current tree level.

### Frontend: Fill the middle column

Current layout per row: `[icon] [name] ........empty space........ [PSE badges]`

Target layout: `[icon] [name] [summary in gray, truncated] [PSE badges]`

The summary text sits in a `flex-1 truncate` span between name and badges. For files it's the file summary; for directories it's the rolled-up directory summary. Truncation with CSS `text-overflow: ellipsis` keeps rows single-line. Full summary visible on hover via `title` attribute.

## Files to modify

- `src/indiseek/storage/sqlite_store.py` — new table + insert/read methods
- `src/indiseek/indexer/summarizer.py` — new `summarize_directories()` method
- `src/indiseek/indexer/pipeline.py` — wire directory summarization as post-step
- `src/indiseek/api/dashboard.py` — `/tree` returns summaries, new run endpoint
- `src/indiseek/tools/read_map.py` — annotate directory lines with directory summaries
- `frontend/src/api/client.ts` — add `summary` to `TreeChild`
- `frontend/src/pages/FileTree.tsx` — render summaries in rows

## Steps

- [ ] **1. Add `directory_summaries` table to SQLite store**
  - New table: `directory_summaries(id INTEGER PRIMARY KEY, dir_path TEXT UNIQUE, summary TEXT)`
  - `insert_directory_summary(dir_path, summary)` — INSERT OR REPLACE
  - `insert_directory_summaries(summaries: list[tuple[str, str]])` — batch
  - `get_directory_summary(dir_path) -> dict | None`
  - `get_directory_summaries(paths: list[str]) -> dict[str, str]` — batch lookup, returns `{path: summary}`
  - `get_all_directory_paths_from_summaries() -> set[str]`
  - Add `CREATE TABLE` to `_ensure_tables()`

- [ ] **2. Add `summarize_directories()` to summarizer**
  - New method on `Summarizer` class
  - Walk all directories that contain summarized files (derive from `file_summaries` paths)
  - Sort directories by depth descending (deepest first = bottom-up)
  - For each directory:
    - Collect child file summaries (direct children only, not recursive)
    - Collect child directory summaries (already computed since we go bottom-up)
    - Format as: `"Directory: {path}\n\nContents:\n- subdir/ — {dir_summary}\n- file.ts — {file_summary}\n..."`
    - Send to Gemini Flash with system prompt asking for a 1-sentence directory summary
    - Store result
  - Skip directories already in `directory_summaries` (resume-safe)
  - Support `on_progress` callback like file summarization does
  - 0.5s delay between calls to avoid rate limiting

- [ ] **3. Wire directory summarization into the pipeline**
  - In `pipeline.py`, add a call to `summarize_directories()` after file summarization
  - Only runs if `--summarize` flag is set (same gate as file summarization)
  - Add progress reporting consistent with existing steps

- [ ] **4. Add dashboard endpoint to trigger directory summarization**
  - New `POST /dashboard/api/run/summarize-dirs` endpoint (or extend existing summarize endpoint)
  - Follows same pattern as `/run/summarize` — creates summarizer, calls `summarize_directories()`
  - Returns count of directories summarized

- [ ] **5. Return summaries from `/tree` endpoint**
  - After building the children dict, collect all file paths and dir paths at this level
  - Batch-fetch file summaries: query `file_summaries` for file children paths → extract `summary` field
  - Batch-fetch dir summaries: query `directory_summaries` for dir children paths
  - Add `summary` string (or null) to each child in the response
  - Keep response lean: summary is just a string, not the full summary record

- [ ] **6. Add directory summaries to `read_map` tool**
  - In `read_map.py`, after building the nested tree dict, look up directory summaries for each directory node
  - Modify `_format_tree` so directory lines render as `dirname/ — summary` instead of just `dirname/`
  - Fetch directory summaries via `store.get_directory_summaries()` (batch), pass into formatter
  - Falls back gracefully if no directory summaries exist (just shows `dirname/` like today)

- [ ] **7. Update `TreeChild` type and render summaries in FileTree**
  - Add `summary?: string` to `TreeChild` interface in `client.ts`
  - In `TreeNode` file rows: add summary text between filename and badges — `flex-1 min-w-0 truncate text-xs text-gray-500`
  - In `TreeNode` directory rows: add summary between dir name and stats — same truncated style
  - Summary always visible on every row (truncated with ellipsis)
  - Use `title={child.summary}` for hover to see full text
  - If no summary, show nothing (graceful degradation)

## Cost estimate

For Vite (~300 directories): ~300 Gemini Flash calls, ~100k input tokens → ~$0.01 on paid tier. Runs in ~2.5 min with 0.5s delay.

## Decisions

- **`read_map` shows directory summaries** — directory lines render as `dirname/ — summary` (step 6)
- **Summaries always visible** — truncated on every row, not just when collapsed
