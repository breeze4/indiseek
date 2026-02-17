# Query Cache: Current Approach and Improvements

## Current Architecture

Two-layer caching system:

### Layer 1: Persistent Cache (API Level)

- Lives in SQLite `queries` table alongside query history
- On every `POST /dashboard/api/run/query`, checks all completed queries for fuzzy match before submitting to the agent loop
- Cache hit returns the full answer + evidence immediately, creates a new row with `status='cached'` and `source_query_id` pointing to the original
- Invalidated wholesale by timestamp — every indexing operation sets `last_index_at` in the `metadata` table, and only queries completed after that timestamp are cache candidates
- `force=True` parameter bypasses cache entirely

### Layer 2: In-Memory Cache (Agent Level)

- `QueryCache` class in `search_code.py` — list of `(query, result)` tuples
- Deduplicates `search_code` tool calls within a single agent run
- Cleared at the start of each `AgentLoop.run()` call
- No size limit (bounded in practice by 15-iteration agent limit)

### Similarity Algorithm

Both layers use `compute_query_similarity()` — Jaccard similarity on normalized token sets:

1. Lowercase
2. Strip punctuation (preserving underscores)
3. Split on whitespace into token set
4. `|intersection| / |union|` with threshold of 0.8

## What Works

- 48 tests covering unit, integration, edge cases
- Timestamp invalidation after re-indexing is correct
- `force` bypass gives users control
- Simple and predictable
- Creating a new `cached` row preserves full query history

## Weaknesses

### Matching Quality (biggest gap)

Jaccard on token bags is crude:

- **No synonym/abbreviation awareness** — "HMR" vs "Hot Module Replacement" scores 0.0
- **No stemming** — "creates" vs "create" vs "creation" scores 0.0
- **No semantic understanding** — "how does the dev server start?" vs "what happens when vite boots up?" scores 0.0 despite being the same question
- **Order-blind false positives** — "A calls B" vs "B calls A" scores 1.0

### Invalidation

- All-or-nothing. Summarizing one file invalidates the entire cache.
- No tracking of which files/symbols a query actually touched.
- No TTL — a query completed 1 second after `last_index_at` lives forever.

### Observability

- No hit/miss counters or hit rate tracking
- Individual cache hits are logged but no aggregate metrics

### Minor Issues

- Cached duration shows original query's duration instead of ~0s
- In-memory cache returns first match above threshold, not best match
- Persistent cache threshold (0.8) is hardcoded, not configurable
- No test for concurrent cache access (SQLite WAL mode should handle it, but untested)

## Potential Improvements

### 1. Semantic Similarity via Embeddings

Embed queries with the same Gemini embedding model already used for code chunks. Cosine similarity on query embeddings would catch rephrasings that Jaccard misses entirely. Could use a two-stage approach: Jaccard first (cheap, catches exact/near-exact), then embedding similarity for misses (more expensive but much more accurate).

Store query embeddings in LanceDB alongside the query ID.

### 2. Selective Invalidation

Track which files/symbols each query's evidence trail touched. After re-indexing, only invalidate queries whose evidence files were in the set of changed files. This would dramatically improve cache retention on incremental re-indexes.

Implementation: the evidence JSON already contains file paths from tool calls. Parse those on completion and store as a `query_files` junction table. On invalidation, diff the changed files against each query's file set.

### 3. Cache Metrics

Hit/miss counters, similarity scores on hits, hit rate over time. Could be as simple as incrementing counters in the `metadata` table and exposing them on the dashboard. Useful for tuning threshold and evaluating whether embedding similarity is worth the cost.

### 4. Fix Cached Duration

Show actual ~0s for cache hits, or show both original and cached response times. Currently misleading — a cached query shows the original 2.5s duration.

### 5. Stemming / Better Normalization

Use a lightweight stemmer (Porter or similar) during tokenization so "create"/"creates"/"created" reduce to the same stem. Low effort, moderate gain for Jaccard matching. Could also split camelCase/snake_case identifiers so `updateModule` and `update_module` share tokens.

### 6. In-Memory Cache: Best-Match Instead of First-Match

Currently returns the first entry above threshold via linear scan. Should scan all entries and return the highest similarity match. Trivial fix.

### 7. Configurable Threshold

The 0.8 is hardcoded at the API level (in-memory cache already accepts a custom threshold). Make it an env var or config setting to allow tuning without code changes.

## Files Involved

- `src/indiseek/tools/search_code.py` — `compute_query_similarity()`, `QueryCache` class
- `src/indiseek/storage/sqlite_store.py` — query persistence, `get_completed_queries_since()`
- `src/indiseek/api/dashboard.py` — persistent cache check in `run_query_op()`
- `src/indiseek/agent/loop.py` — in-memory cache integration
- `tests/test_cache.py` — 48 tests
