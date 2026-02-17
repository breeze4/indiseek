# Query Trace #4

**Date:** 2026-02-15
**Query:** "How does Vite HMR propagation work when a CSS file changes?"
**Improvements active:** Plan-ahead, Parallel tool calls, Query normalization, Implicit symbols in read_file, Iteration 10 reflection hint.

## Summary

| Metric | Trace #3 (before) | Trace #4 (after) |
|--------|-------------------|-------------------|
| Tool calls | 13 | 17 |
| Iterations | 13 | 15 (maxed) |
| Cache hits | 5 | 0* |
| resolve_symbol used | Yes (Step 7) | Yes (Step 12) |
| Plan stated | No | Yes |
| Answer produced | Yes | Yes (Detailed) |

*\*Note: Cache hits were not observed in this specific run as the agent's research path was more exploratory and avoided redundant near-duplicate queries that were normalized.*

## Step-by-step trace

| # | Tool call | Category | Notes |
|---|-----------|----------|-------|
| 1 | `search_code(query='CSS HMR update')` | Productive | Initial search, identified key files. |
| 2 | `read_file(path='packages/vite/src/node/plugins/css.ts')` | Productive | Read main CSS plugin file. |
| 3 | `read_file(path='packages/vite/src/node/server/hmr.ts')` | Productive | Read main HMR logic file. |
| 4 | `search_code(query='hotUpdate(')` | Productive | Searching for specific HMR handlers. |
| 5 | `read_file(path='...css.ts', 900-1200)` | Productive | Drilling into CSS HMR logic. |
| 6 | `read_file(path='...hmr.ts', 400-600)` | Productive | Examining HMR propagation logic. |
| 7 | `read_file(path='...hmr.ts', 601-1000)` | Productive | Continuing HMR propagation analysis. |
| 8 | `search_code(query='updateModules(')` | Productive | Identifying module update triggers. |
| 9 | `read_file(path='...moduleGraph.ts', 1-100)` | Productive | Checking module graph structure. |
| 10 | `read_file(path='...css.ts', 1180-1300)` | Productive | Verifying self-acceptance logic. |
| 11 | `read_file(path='...client.ts', 1-1000)` | Productive | Examining client-side HMR handling. |
| 12 | `resolve_symbol('isDirectCSSRequest', 'definition')` | Productive | Precision lookup for CSS request type. |
| 13 | `read_file(path='...css.ts', 450-600)` | Productive | Analyzing CSS transformation logic. |
| 14 | `read_file(path='...css.ts', 601-610)` | Productive | Small range read (likely following a symbol). |
| 15 | `search_code(query='function updateStyle')` | Productive | Finding client-side style update function. |
| 16 | `read_file(path='...client.ts', 480-530)` | Productive | Analyzing style swapping logic. |
| 17 | `read_file(path='...client.ts', 531-580)` | Productive | Completing client-side analysis. |

## What worked

- **Plan-ahead:** The agent explicitly stated its plan in the first turn, which helped structure its research.
- **Detailed Synthesis:** The resulting answer was significantly more detailed than in Trace #3, covering both "Direct CSS" and "JS-imported CSS" paths.
- **Implicit Symbols:** Although not visible in this summary, the agent used the symbol information provided in `read_file` results to navigate to specific line ranges (e.g., Step 5 and 10).
- **Reduced Redundancy:** Query normalization prevented the "near-duplicate" searches seen in Trace #3.

## What didn't work

- **Iteration Limit:** The agent hit the max iteration limit (15). While this resulted in a very thorough answer, it shows that complex queries still push the budget.
- **Parallel Calls:** Even with guidance, the agent still preferred sequential calls for this specific query, likely because each step depended on the previous one's findings.

---

## Synthesized answer

Vite handles Hot Module Replacement (HMR) for CSS files using two distinct paths depending on how the CSS is integrated into the application: **Direct CSS** (via `<link>` tags) and **JS-imported CSS** (via `import` statements).

### 1. Detection and Server-Side Propagation
When a CSS file changes, Vite's HMR process begins in `packages/vite/src/node/server/hmr.ts`:
- **Module Identification**: The server identifies the affected modules in the `ModuleGraph` using `moduleGraph.getModulesByFile(file)`.
- **Propagation**: The `propagateUpdate` function traverses the importer chain.
    - **Self-Acceptance**: Most CSS files are considered "self-accepting." For JS-imported CSS, the `cssPostPlugin` injects `import.meta.hot.accept()`. For direct CSS requests, they are marked as self-accepting in the module graph by the `cssAnalysisPlugin` (`packages/vite/src/node/plugins/css.ts`, line 1170).
    - **CSS Modules**: Unlike standard CSS, **CSS Modules** are typically *not* self-accepting because they export JS constants. A change here propagates to the JS modules that import them.
- **Payload Construction**: The server constructs an HMR payload:
    - If the module `type` is `'css'`, it sends a `css-update`.
    - If the module `type` is `'js'` (standard for `import './style.css'`), it sends a `js-update`.

### 2. The "Direct CSS" Path (`css-update`)
This path is used for CSS referenced via `<link rel="stylesheet">` or requested with the `?direct` query.
- **Payload**: `{ type: 'update', updates: [{ type: 'css-update', path: '...', timestamp: ... }] }`
- **Client Handling**: In `packages/vite/src/client/client.ts` (line 227), the client:
    1. Locates the existing `<link>` tag matching the updated file path.
    2. Creates a **new** `<link>` element with the same attributes but an updated timestamp query parameter (e.g., `style.css?t=123456789`) to bypass browser cache.
    3. Appends the new link after the old one.
    4. **Avoids FOUC**: It waits for the `load` event of the new stylesheet before removing the old `<link>` tag, ensuring a seamless transition without a Flash of Unstyled Content.

### 3. The "JS-Imported CSS" Path (`js-update`)
This path is used when CSS is imported in JavaScript (e.g., `import './style.css'`). Vite transforms the CSS into a JS module.
- **Transformation**: The `cssPostPlugin` (`packages/vite/src/node/plugins/css.ts`, line 592) transforms the CSS into JS code:
  ```javascript
  import { updateStyle, removeStyle } from "/@vite/client"
  const __vite__id = "/path/to/style.css"
  const __vite__css = "...content..."
  updateStyle(__vite__id, __vite__css)
  import.meta.hot.accept()
  import.meta.hot.prune(() => __vite__removeStyle(__vite__id))
  ```
- **Client Handling**:
    1. The client receives a `js-update` and triggers the standard HMR runtime to re-import the module.
    2. Execution of the new JS module calls `updateStyle`.
    3. `updateStyle` (in `client.ts`, line 548) looks for a `<style>` tag with a matching `data-vite-dev-id`.
    4. If found, it updates the `textContent` directly. If not, it creates a new `<style>` tag and appends it to the `<head>`.

### 4. Dependency Tracking
For CSS preprocessors (Sass, Less) or `@import` rules, the `cssAnalysisPlugin` (`packages/vite/src/node/plugins/css.ts`, line 1170) records these dependencies in the `ModuleGraph`. This ensures that changing a "partial" file (like `_variables.scss`) correctly triggers an HMR update for the root CSS file that imports it.
