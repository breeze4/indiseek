# Query Trace #3

**Date:** 2026-02-15
**Query:** "How does Vite HMR propagation work when a CSS file changes?"
**Fixes active:** strip_file_paths, file read cache, query dedup cache, resolve_symbol hint, parallel tool guidance

## Summary

| Metric | Trace #2 (before) | Trace #3 (after) |
|--------|-------------------|-------------------|
| Tool calls | 13 | 13 |
| Productive | 9 | 6 |
| Cache hits | 0 | 5 (4 file + 1 query) |
| Redundant | 4 | 2 |
| resolve_symbol first used | Step 13 | Step 7 |
| Answer produced | Yes | Yes |

## Step-by-step trace

| # | Tool call | Category | Notes |
|---|-----------|----------|-------|
| 1 | `search_code('HMR CSS propagation')` | Productive | Good initial search, 10 results |
| 2 | `read_file(css.ts, lines 1-200)` | Productive | Key CSS plugin file, first disk read |
| 3 | `search_code('handleHotUpdate', lexical)` | Productive | Targeted lexical search, 4 results |
| 4 | `read_file(css.ts, lines 3000-3539)` | **File cache hit** | Served from memory (cached at step 2) |
| 5 | `read_file(css.ts, lines 1-1000)` | **File cache hit** | Larger range, still from cache |
| 6 | `search_code('handleHotUpdate: {', lexical)` | Productive | Lexical search for handler definition |
| 7 | `resolve_symbol('handleHotUpdate', references)` | Productive | SCIP cross-ref, 4 references found |
| 8 | `search_code('handleHotUpdate: {', lexical)` | **Query cache hit** | Exact duplicate of step 6, served from cache |
| 9 | `search_code('handleHotUpdate:', lexical)` | Redundant | Near-duplicate of step 6, Jaccard 0.5 < 0.8 threshold |
| 10 | `read_file(css.ts, lines 1001-1500)` | **File cache hit** | Served from memory |
| 11 | `read_file(client.ts, lines 1-200)` | Productive | New file: Vite client HMR handler |
| 12 | `read_file(client.ts, lines 201-400)` | **File cache hit** | Served from memory (cached at step 11) |
| 13 | `search_code('handleHotUpdate( {', lexical)` | Redundant | Near-duplicate of step 6, Jaccard 0.33 < 0.8 threshold |

## What worked

- **File caching:** 4 cache hits on `css.ts` and `client.ts`. The model read `css.ts` at 4 different ranges — only the first hit disk. This is exactly the problem described in Issue #3.
- **Query dedup:** Step 8 was an exact repeat of step 6 and was served instantly from cache.
- **resolve_symbol hint:** Used at step 7, 6 steps earlier than trace #2. The hint fires at iteration 5+ if unused; the model responded by calling it at step 7.
- **File path stripping:** No file paths appeared in search queries this run (the model happened not to try, but the guard is in place).

## What didn't work

- **2 near-duplicate lexical searches slipped through:** Steps 9 and 13 are variants of step 6 (`handleHotUpdate:` vs `handleHotUpdate: {` vs `handleHotUpdate( {`). Jaccard similarity on single-token queries with punctuation differences is too low to catch these. The threshold (0.8) is correct for multi-word queries but too strict for short queries with punctuation variants.
- **No parallel tool calls:** Despite prompt guidance, the model still used 1 call per iteration. This is a model behavior issue — Gemini may need stronger prompting or it may just not reliably batch calls.

## Next steps

- Consider normalizing punctuation before Jaccard comparison (strip `:`, `{`, `(`, etc.)
- Consider lowering the threshold for very short queries (1-2 tokens)
- Investigate Gemini's parallel function calling behavior more

---

## Synthesized answer

When a CSS file changes in a Vite project, the Hot Module Replacement (HMR) process follows a specific propagation path managed by the Vite dev server and the browser client.

### 1. File Change Detection and Context Initialization
When a file is modified, the Vite dev server's watcher triggers `handleHMRUpdate` in `packages/vite/src/node/server/hmr.ts`.
- Vite identifies the affected modules using the `moduleGraph`.
- For CSS files, the corresponding `EnvironmentModuleNode` has its `type` set to `'css'` (determined in the constructor in `packages/vite/src/node/server/moduleGraph.ts`).

### 2. Update Propagation
The server then calls `updateModules`, which invokes `propagateUpdate` to determine how the change affects the module graph.
- **Self-Acceptance**: CSS modules are generally treated as **self-accepting**. During the transformation phase, the CSS plugin ensures these modules are marked so they can handle their own updates without requiring a parent JS module to reload.
- **Boundary Discovery**: `propagateUpdate` (in `packages/vite/src/node/server/hmr.ts`) traverses the graph. Since CSS nodes are marked as self-accepting, they act as HMR boundaries.
- **Update Payload**: The server constructs an update payload where the type is dynamically assigned as `` `${boundary.type}-update` ``. For CSS, this results in a `css-update` payload.

### 3. Handling CSS @import Dependencies
Vite handles CSS-to-CSS dependencies (via `@import`) through the `vite:css-analysis` plugin (in `packages/vite/src/node/plugins/css.ts`):
- When a CSS file is transformed, Vite tracks its `@import` dependencies.
- If an imported CSS file changes, the plugin's HMR logic ensures that the importer (the "parent" CSS file) is also invalidated and included in the HMR update. This ensures that styles compounded across multiple files stay in sync.

### 4. Client-Side Execution
The server sends the `css-update` payload via WebSockets to the Vite client-side runtime (`packages/vite/src/client/client.ts`).
- **Style Swapping**: The client identifies the `<style>` or `<link>` tags associated with the updated CSS module ID.
- **Cache Busting**: It fetches the new version of the CSS by appending a timestamp query parameter (e.g., `?t=123456789`) to the URL.
- **DOM Update**:
    - If it's a **linked stylesheet**, the `href` is updated to the new URL.
    - If it's **inlined/processed CSS**, the old `<style>` tag is replaced with a new one containing the updated content.
- This process allows the styles to update instantly without losing the application's state (unlike a full page reload).

### 5. Integration with JS
If the CSS is imported into a JS file (e.g., `import './style.css'`), Vite typically serves a "proxy" JS module for that CSS. The HMR update still targets the CSS resource specifically, but the proxy ensures that the dependency relationship is maintained in the `moduleGraph` so that if the CSS is removed or its export name (in CSS Modules) changes, the importing JS module is correctly notified.
