# Indiseek MVP Manual Verification Runbook

All 7 phases are code-complete with passing unit tests. This runbook walks through every manual verification item that requires a real Vite repo, a real GEMINI_API_KEY, and actual end-to-end execution.

Work through the sections in order. Each step builds on the previous one.

---

## Prerequisites

### 1. Clone Vite

```bash
git clone https://github.com/vitejs/vite.git /path/to/vite
```

### 2. Install Indiseek

```bash
cd /path/to/indiseek
pip install -e ".[dev]"
```

### 3. Configure .env

```bash
cp .env.example .env
```

Edit `.env`:
```
GEMINI_API_KEY=<your real key>
REPO_PATH=/path/to/vite
DATA_DIR=./data
```

### 4. Verify prerequisites

```bash
# Node.js required for SCIP generation
node --version   # needs >= 18

# Python package loads without errors
python -c "from indiseek import config; print('REPO_PATH:', config.REPO_PATH)"
```

**Pass criteria:** REPO_PATH prints your Vite clone path. No import errors.

### 5. Clean slate

```bash
rm -rf ./data
```

---

## Step 1: Tree-sitter + SCIP Indexing

**What this validates:** Phase 1 (Tree-sitter parsing) and Phase 2 (SCIP cross-references) work against the real Vite codebase.

### 1a. Generate SCIP index

```bash
bash scripts/generate_scip.sh /path/to/vite
```

**Check:** Script completes without errors. Prints file size of `index.scip`.

**Pass criteria:** `/path/to/vite/index.scip` exists and is > 1 MB.

### 1b. Run Tree-sitter + SCIP indexing

```bash
python scripts/index.py --scip-path /path/to/vite/index.scip
```

**Check:** Output shows counts for files parsed, symbols extracted, chunks created, SCIP symbols/occurrences/relationships loaded.

**Pass criteria:**
- Files parsed: several hundred (Vite has ~400+ TS files)
- Symbols extracted: thousands
- Chunks created: thousands
- SCIP symbols: thousands
- SCIP occurrences: tens of thousands
- Zero errors (or only a handful of warnings for edge-case files)

### 1c. Spot-check SQLite

```bash
sqlite3 data/indiseek.db
```

```sql
-- Symbol counts
SELECT count(*) FROM symbols;
SELECT count(*) FROM chunks;
SELECT count(*) FROM scip_symbols;
SELECT count(*) FROM scip_occurrences;

-- Known symbol exists
SELECT name, kind, file_path, start_line FROM symbols WHERE name = 'createServer' LIMIT 5;

-- Chunks contain real code, not garbage
SELECT file_path, chunk_type, substr(content, 1, 200) FROM chunks WHERE symbol_name = 'createServer' LIMIT 1;

-- Chunks are scoped by function/class (not fixed-size windows)
SELECT chunk_type, count(*) FROM chunks GROUP BY chunk_type;

-- SCIP definition exists
SELECT s.symbol, o.file_path, o.start_line, o.role
FROM scip_symbols s
JOIN scip_occurrences o ON o.symbol_id = s.id
WHERE s.symbol LIKE '%createServer%' AND o.role = 'definition'
LIMIT 5;

-- SCIP references exist across multiple files
SELECT COUNT(DISTINCT o.file_path)
FROM scip_symbols s
JOIN scip_occurrences o ON o.symbol_id = s.id
WHERE s.symbol LIKE '%createServer%' AND o.role = 'reference';
```

**Pass criteria:**
- `createServer` appears in symbols with kind `function` and a path like `packages/vite/src/node/server/index.ts`
- Chunk content is actual TypeScript code, not binary or empty
- `chunk_type` distribution shows function, class, method, module_header — not just one type
- SCIP definition for `createServer` points to a real file and line
- References span multiple files (> 1)

**What you'll learn:** Whether the structural index accurately represents the Vite codebase's symbol graph.

---

## Step 2: Embedding

**What this validates:** Phase 3 — Gemini embedding API works, LanceDB stores vectors, semantic search returns relevant results.

### 2a. Run embedding

```bash
python scripts/index.py --embed
```

(Tree-sitter/SCIP data already exists from Step 1, so this only runs the embedding step.)

**Check:** Output shows "Embedding chunks..." with progress, then a count of chunks embedded.

**Pass criteria:**
- Completes without API errors
- Chunks embedded count matches (or is close to) the chunk count from Step 1
- `data/lancedb/` directory exists and contains data
- Completes in < 10 minutes for Vite

### 2b. Verify semantic search

```python
python3 -c "
from indiseek import config
from indiseek.storage.vector_store import VectorStore
from indiseek.agent.provider import GeminiProvider

provider = GeminiProvider()
vector_store = VectorStore(config.LANCEDB_PATH, dims=config.EMBEDDING_DIMS)

query_vec = provider.embed(['HMR propagation CSS'])[0]
results = vector_store.search(query_vec, limit=5)

for r in results:
    print(f'{r.score:.3f}  {r.file_path}  ({r.chunk_type}: {r.symbol_name})')
    print(f'  {r.content[:120]}...')
    print()
"
```

**Pass criteria:**
- Returns 5 results
- Top results reference files related to HMR (e.g., paths containing `hmr`, `server`, or `hot`)
- Results are ranked sensibly — most relevant code first, not random
- Scores decrease from top to bottom

**What you'll learn:** Whether semantic search can surface relevant code for natural language questions about Vite internals.

---

## Step 3: Summarization

**What this validates:** Phase 4 — LLM file summarization populates the map, summaries are useful.

### 3a. Run summarization

```bash
python scripts/index.py --summarize
```

**Check:** Output shows "Summarizing files..." with a count.

**Pass criteria:**
- Completes without API errors
- Files summarized count is in the hundreds

### 3b. Verify summaries

```bash
sqlite3 data/indiseek.db
```

```sql
SELECT count(*) FROM file_summaries;

-- Spot-check a known file
SELECT file_path, summary FROM file_summaries
WHERE file_path LIKE '%server/index.ts' LIMIT 3;

-- Check summary length — should be concise
SELECT file_path, length(summary) as len FROM file_summaries ORDER BY len DESC LIMIT 5;
SELECT file_path, length(summary) as len FROM file_summaries ORDER BY len ASC LIMIT 5;

-- Check a few random summaries for quality
SELECT file_path, summary FROM file_summaries ORDER BY RANDOM() LIMIT 5;
```

**Pass criteria:**
- `file_summaries` count is > 0 and roughly matches the number of source files
- Summaries for known files (like the server entry point) accurately describe the file's responsibility
- Summaries are concise — one sentence, not paragraphs (most should be < 200 characters)
- Random spot-checks produce summaries that make sense given the file path

### 3c. Verify directory tree

```python
python3 -c "
from indiseek import config
from indiseek.storage.sqlite_store import SqliteStore

store = SqliteStore(config.SQLITE_PATH)
tree = store.get_directory_tree()

# Print top-level keys
for key in sorted(tree.keys()):
    print(key)
"
```

**Pass criteria:**
- Tree has top-level entries matching the Vite repo structure (`packages/`, etc.)
- Structure is navigable and useful for orientation

**What you'll learn:** Whether the map gives an agent enough context to decide where to look before searching.

---

## Step 4: Lexical Index

**What this validates:** Phase 5 — Tantivy BM25 index works for exact-match search.

### 4a. Build lexical index

```bash
python scripts/index.py --lexical
```

**Check:** Output shows documents indexed in Tantivy and the index path.

**Pass criteria:**
- Completes without errors
- Document count matches chunk count from Step 1
- `data/tantivy/` directory exists

### 4b. Verify exact-match search

```python
python3 -c "
from indiseek import config
from indiseek.storage.sqlite_store import SqliteStore
from indiseek.indexer.lexical import LexicalIndexer

store = SqliteStore(config.SQLITE_PATH)
lexical = LexicalIndexer(store, config.TANTIVY_PATH)
lexical.open_index()

results = lexical.search('handleHMRUpdate', limit=5)
for r in results:
    print(f'{r[\"score\"]:.3f}  {r[\"file_path\"]}:{r[\"start_line\"]}-{r[\"end_line\"]}')
    print(f'  {r[\"content\"][:120]}...')
    print()
"
```

**Pass criteria:**
- `handleHMRUpdate` returns exact matches (the identifier appears in the content)
- This is a case where lexical search should find results that semantic search might miss
- Results return in < 1 second

**What you'll learn:** Whether the lexical index complements semantic search for exact identifiers.

---

## Step 5: Tools Smoke Test

**What this validates:** Phase 6 — all four agent tools work against the real index.

Run this in a Python REPL (`python3`):

```python
from indiseek import config
from indiseek.storage.sqlite_store import SqliteStore
from indiseek.indexer.lexical import LexicalIndexer
from indiseek.tools.read_map import read_map
from indiseek.tools.resolve_symbol import resolve_symbol
from indiseek.tools.read_file import read_file
from indiseek.tools.search_code import CodeSearcher, format_results

store = SqliteStore(config.SQLITE_PATH)
lexical = LexicalIndexer(store, config.TANTIVY_PATH)
lexical.open_index()
```

### 5a. read_map

```python
# Full tree
result = read_map(store)
print(result[:2000])

# Scoped
result = read_map(store, path="packages/vite/src/node/server")
print(result)
```

**Pass criteria:**
- Full tree shows directory structure with file summaries
- Scoped result shows only the server subdirectory
- Output is formatted for LLM readability (not too verbose, not too terse)

### 5b. search_code

```python
searcher = CodeSearcher(lexical_indexer=lexical)

# Hybrid search
results = searcher.search("HMR CSS propagation", mode="hybrid", limit=10)
print(format_results(results, "HMR CSS propagation"))
```

**Pass criteria:**
- Returns results mixing semantic and lexical matches
- Results reference HMR-related files
- Hybrid search produces better results than either mode alone (try `mode="semantic"` and `mode="lexical"` separately to compare)

### 5c. resolve_symbol

```python
# Definition
print(resolve_symbol(store, "createServer", "definition"))

# References
print(resolve_symbol(store, "createServer", "references"))

# Callers
print(resolve_symbol(store, "createServer", "callers"))

# Callees
print(resolve_symbol(store, "createServer", "callees"))
```

**Pass criteria:**
- Definition returns a single file:line location
- References returns multiple file:line locations across different files
- Callers/callees return related symbols (or an empty message if SCIP data doesn't have them)
- Ambiguous names (if any) are handled gracefully — results show which symbol they refer to

### 5d. read_file

```python
print(read_file(config.REPO_PATH, "packages/vite/src/node/server/index.ts", 1, 50))
```

**Pass criteria:**
- Returns 50 lines of TypeScript source code with line numbers
- Content matches the actual file on disk

**What you'll learn:** Whether each tool produces output that an LLM agent can usefully consume.

---

## Step 6: Server + Query

**What this validates:** Phase 7 — FastAPI server starts, health check works, query endpoint returns structured answers.

### 6a. Start the server

In one terminal:
```bash
uvicorn indiseek.api.server:app --host 0.0.0.0 --port 8000
```

**Pass criteria:** Server starts without errors, logs show "Uvicorn running on http://0.0.0.0:8000".

### 6b. Health check

```bash
curl http://localhost:8000/health
```

**Pass criteria:** Returns HTTP 200 with a JSON body (e.g., `{"status": "ok"}`).

### 6c. Simple query

```bash
curl -s -X POST http://localhost:8000/query \
    -H "Content-Type: application/json" \
    -d '{"prompt": "What files are in the server directory?"}' | python3 -m json.tool
```

**Pass criteria:**
- Returns HTTP 200
- Response has `answer` (string) and `evidence` (array) fields
- Answer mentions files in the server directory
- Evidence trail shows tool calls made

### 6d. Complex query

```bash
curl -s -X POST http://localhost:8000/query \
    -H "Content-Type: application/json" \
    -d '{"prompt": "How does Vite HMR propagation work when a CSS file changes?"}' | python3 -m json.tool
```

**Pass criteria:**
- Returns a structured answer with specific file:line references
- Answer is synthesized (not just a dump of search results)
- Evidence trail shows multiple tool calls
- Response time < 60 seconds

**What you'll learn:** Whether the full pipeline — from query to agent loop to tool execution to answer synthesis — works end-to-end.

---

## Step 7: Agent Behavior

**What this validates:** The agent loop follows the expected workflow — reads map first, uses multiple tools, traces references, synthesizes with citations.

These queries are designed to exercise specific agent behaviors. Run each through the `/query` endpoint.

### 7a. Does the agent read the map first?

```bash
curl -s -X POST http://localhost:8000/query \
    -H "Content-Type: application/json" \
    -d '{"prompt": "What is the overall architecture of Vite'\''s dev server?"}' | python3 -m json.tool
```

**Check the evidence trail.** The first step should be `read_map` (or similar), not `search_code`.

**Pass criteria:** Agent orients itself with the map before drilling into specific code.

### 7b. Does the agent use multiple tools?

```bash
curl -s -X POST http://localhost:8000/query \
    -H "Content-Type: application/json" \
    -d '{"prompt": "How does the plugin container work and which plugins are loaded by default?"}' | python3 -m json.tool
```

**Check the evidence trail.** Should see a mix of `read_map`, `search_code`, `resolve_symbol`, and `read_file`.

**Pass criteria:** Evidence trail contains at least 3 different tool types.

### 7c. Does the agent trace symbol references?

```bash
curl -s -X POST http://localhost:8000/query \
    -H "Content-Type: application/json" \
    -d '{"prompt": "What calls createServer and what does it do?"}' | python3 -m json.tool
```

**Check the evidence trail.** Should see `resolve_symbol` with action `definition` and `callers` (or `references`).

**Pass criteria:** Agent uses `resolve_symbol` to follow the call graph, not just text search.

### 7d. Does the agent produce cited answers?

For each query above, check the `answer` field.

**Pass criteria:**
- Answer references specific files with paths (e.g., `packages/vite/src/node/server/index.ts`)
- Answer includes line numbers or line ranges
- Answer is a coherent narrative, not bullet points of raw tool output
- Agent doesn't loop infinitely or get stuck (check evidence trail length — should be < 15 steps)

---

## Summary Checklist

After completing all steps, verify:

- [ ] Tree-sitter parsing produces realistic symbols and scoped chunks
- [ ] SCIP cross-references match IDE "Find References" behavior
- [ ] Semantic search for "HMR propagation CSS" returns HMR-related chunks
- [ ] Semantic results are ranked sensibly
- [ ] Embedding completes in < 10 min
- [ ] File summaries accurately describe file responsibilities
- [ ] Summaries are concise (1 sentence)
- [ ] Directory tree is navigable
- [ ] Lexical search finds exact identifiers that semantic search misses
- [ ] Hybrid search outperforms either mode alone
- [ ] Search performance < 1s
- [ ] Tool outputs are formatted for LLM readability
- [ ] resolve_symbol handles ambiguous names gracefully
- [ ] Agent reads the map first
- [ ] Agent uses multiple tools in sequence
- [ ] Evidence trail is coherent and traceable
- [ ] HMR CSS answer includes file:line references
- [ ] Answer is synthesized, not raw tool output
- [ ] Agent doesn't loop infinitely
- [ ] Response time < 60s for complex queries
