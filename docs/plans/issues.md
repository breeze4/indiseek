# Features

Some ideas:
 - need an interface to be able to see what is indexed, all these views showing whats in each store and how its being used by the query - showing traces of the queries and how they worked and what they retrieve, what was sent to LLMs, what was received
- search code UI is kind of lame - it also resets the query input and results when you navigate around; it shoudl also hold search history and "recent files looked at"

# Agent Loop Issues

Observed from live query trace (2025-02-15): "How does Vite HMR propagation work when a CSS file changes?" — 13 tool calls, answer produced.

## 1. File paths in search_code queries

4/13 calls include file paths in the semantic query (e.g. `search_code('handleHotUpdate packages/vite/.../css.ts')`). The system prompt warns against this but the model ignores it. The loop should detect and strip file paths from `search_code` queries before execution rather than relying on the model to follow instructions.

**Fix:** Server-side — regex-strip file path patterns from the query string before passing to the searcher. Log when stripping occurs so it shows up in traces.

## 2. Deduplication of similar searches

Steps 3, 7, 9 in the trace are all variants of the same query ("hotUpdate in css.ts"). The loop should track recent queries and short-circuit with a cached result instead of burning an iteration on a near-duplicate search.

**Fix:** Keep a list of past `search_code` queries. Before executing, check similarity (simple: normalized edit distance or token overlap). If a query is >80% similar to a previous one, return the previous results with a note: "Similar to your earlier search — here were those results."

## 3. File read coalescing

The agent read `css.ts` four times at different line ranges (1-540, 3000-3539, 1050-1150, 400-500). Each read burns an iteration. The loop could cache file contents after the first read and serve subsequent range requests from memory. Alternatively, for files under the 15k char truncation limit, send the full file on first read.

**Fix:** Cache file contents in a dict keyed by path. On subsequent `read_file` calls to the same path, serve from cache. Consider sending the full file (up to the truncation limit) on first access to avoid multiple round trips.

## 4. resolve_symbol used too late

Only one `resolve_symbol` call in the entire trace, at step 13. The system prompt says to prefer it for call graph navigation, but the model defaults to `search_code` for everything. By the time it uses SCIP, it's out of budget.

**Fix:** Strengthen the system prompt guidance — e.g. "After your first 1-2 search_code calls, switch to resolve_symbol for navigating between related functions. resolve_symbol is faster and more precise than searching for a symbol name." Could also inject a mid-loop hint if resolve_symbol hasn't been used by iteration 5.

## 5. No parallel tool calls

Every iteration used exactly 1 tool call. The model could batch independent calls in a single iteration (e.g. `search_code('CSS HMR') + resolve_symbol('handleHMRUpdate', 'definition')`). This would cut iteration count significantly.

**Fix:** Add explicit guidance to the system prompt: "You can call multiple tools in a single iteration. Batch independent lookups together — e.g. a search_code and a resolve_symbol in the same turn." Gemini supports parallel function calls; we just need to encourage it.
