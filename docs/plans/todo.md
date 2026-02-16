# Indiseek MVP Implementation Plan

## Overview

Build a codebase research service that combines structural (Tree-sitter + SCIP), semantic (embeddings), map (LLM summaries), and lexical (BM25) indexing with a Gemini-powered agent loop to answer deep questions about the Vite codebase. Exposed as a single `POST /query` HTTP endpoint.

## Current State

Empty project. Only `docs/SPEC.md` exists. No code, no dependencies, no project structure.

## Desired End State

A working Python service that:
1. Indexes a local clone of the Vite repo using all four indexing strategies
2. Exposes `POST /query` accepting `{"prompt": "..."}` and returning `{"answer": "...", "evidence": [...]}`
3. Uses Gemini tool-calling to autonomously navigate the indexes and synthesize answers

**Verification:** `curl -X POST http://localhost:8000/query -d '{"prompt": "How does Vite HMR propagation work when a CSS file changes?"}'` returns a structured answer with file:line references and an evidence trail.

## What We're NOT Building

- No repo cloning/management (manually clone Vite, point the indexer at it)
- No incremental indexing (full re-index every time)
- No auth, no multi-user, no rate limiting
- No caching of query results
- No MCP server integration (just HTTP)
- No UI (curl only)
- No streaming responses
- No persistent conversation/follow-up queries

## Tech Decisions

| Component | Choice | Notes |
|-----------|--------|-------|
| Language | Python 3.10+ | Required by tree-sitter v0.25+ |
| LLM | Gemini (via `google-genai`) | Behind a provider interface |
| Embeddings | `gemini-embedding-001` | 768 dims (reduced from 3072 default) |
| Vector DB | LanceDB | Behind a storage interface |
| Structural DB | SQLite | Tree-sitter symbols + SCIP cross-refs |
| Lexical search | Tantivy (via `tantivy-py`) | BM25 scoring |
| AST parsing | tree-sitter + tree-sitter-typescript | v0.25.2 QueryCursor API |
| Cross-refs | scip-typescript | Protobuf output, parsed via `protobuf` lib |
| HTTP | FastAPI | Minimal, just `POST /query` |
| Agent loop | Gemini function calling API | AUTO mode, model chooses tools |

## Project Structure

```
indiseek/
├── pyproject.toml
├── .gitignore
├── .env.example            # Checked in — documents all config vars with placeholder values
├── .env                    # NOT checked in — actual secrets and local paths
├── CLAUDE.md               # Project-level agent instructions (build, test, run)
├── docs/
│   ├── SPEC.md
│   └── plans/
│       └── todo.md
├── src/
│   └── indiseek/
│       ├── __init__.py
│       ├── config.py              # Paths, API keys, model names
│       ├── indexer/
│       │   ├── __init__.py
│       │   ├── parser.py          # Tree-sitter: parse TS, extract symbols, chunk by scope
│       │   ├── scip.py            # Load SCIP protobuf into SQLite
│       │   ├── embedder.py        # Embed AST chunks via Gemini, store in LanceDB
│       │   ├── summarizer.py      # LLM-summarize each file, build map
│       │   ├── lexical.py         # Build Tantivy BM25 index
│       │   └── pipeline.py        # Orchestrate full indexing run
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── sqlite_store.py    # SQLite operations (symbols, SCIP, map)
│       │   └── vector_store.py    # LanceDB operations (embed, search)
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── read_map.py        # read_map(path?) tool
│       │   ├── search_code.py     # search_code(query, mode?) tool — hybrid semantic+lexical
│       │   ├── resolve_symbol.py  # resolve_symbol(name, action) tool — SCIP/tree-sitter
│       │   └── read_file.py       # read_file(path, start?, end?) tool
│       ├── agent/
│       │   ├── __init__.py
│       │   ├── loop.py            # Agent loop: Gemini tool-calling with scratchpad
│       │   └── provider.py        # LLM provider interface + Gemini implementation
│       └── api/
│           ├── __init__.py
│           └── server.py          # FastAPI app with POST /query
├── proto/
│   └── scip.proto                 # Downloaded from sourcegraph/scip
├── scripts/
│   ├── generate_scip.sh           # Run scip-typescript against Vite
│   └── index.py                   # CLI: python scripts/index.py
└── tests/
    ├── __init__.py
    ├── test_parser.py
    ├── test_storage.py
    ├── test_tools.py
    └── test_agent.py
```

## SQLite Schema

```sql
-- Symbols extracted by Tree-sitter
CREATE TABLE symbols (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,           -- function, class, method, interface, type, enum, variable
    start_line INTEGER NOT NULL,
    start_col INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    end_col INTEGER NOT NULL,
    signature TEXT,               -- first line of declaration
    parent_symbol_id INTEGER,     -- for methods inside classes
    FOREIGN KEY (parent_symbol_id) REFERENCES symbols(id)
);
CREATE INDEX idx_symbols_name ON symbols(name);
CREATE INDEX idx_symbols_file ON symbols(file_path);
CREATE INDEX idx_symbols_kind ON symbols(kind);

-- AST-scoped code chunks (for embedding)
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL,
    symbol_name TEXT,             -- which symbol this chunk belongs to (nullable for top-level)
    chunk_type TEXT NOT NULL,     -- function, class, method, module_header, etc.
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_estimate INTEGER        -- rough token count for context budgeting
);
CREATE INDEX idx_chunks_file ON chunks(file_path);

-- SCIP cross-references
CREATE TABLE scip_symbols (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,  -- SCIP symbol string (e.g. "npm . vite 5.0.0 src/`HMR`#`propagate`().")
    documentation TEXT            -- docstring if available
);
CREATE INDEX idx_scip_symbol ON scip_symbols(symbol);

CREATE TABLE scip_occurrences (
    id INTEGER PRIMARY KEY,
    symbol_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    start_col INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    end_col INTEGER NOT NULL,
    role TEXT NOT NULL,           -- definition, reference
    FOREIGN KEY (symbol_id) REFERENCES scip_symbols(id)
);
CREATE INDEX idx_scip_occ_symbol ON scip_occurrences(symbol_id);
CREATE INDEX idx_scip_occ_file ON scip_occurrences(file_path);

-- SCIP relationships (e.g. implementation, type definition)
CREATE TABLE scip_relationships (
    id INTEGER PRIMARY KEY,
    symbol_id INTEGER NOT NULL,
    related_symbol_id INTEGER NOT NULL,
    relationship TEXT NOT NULL,   -- implementation, reference, type_definition
    FOREIGN KEY (symbol_id) REFERENCES scip_symbols(id),
    FOREIGN KEY (related_symbol_id) REFERENCES scip_symbols(id)
);

-- File summaries (Map)
CREATE TABLE file_summaries (
    id INTEGER PRIMARY KEY,
    file_path TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL,        -- 1-sentence LLM-generated summary
    language TEXT,                -- ts, tsx, json, etc.
    line_count INTEGER
);
CREATE INDEX idx_summaries_path ON file_summaries(file_path);
```

---

## Phase 1: Project Scaffolding + Tree-sitter Parsing + SQLite Storage

### Overview
Set up the Python project, implement Tree-sitter parsing of TypeScript files, extract symbols and AST-scoped chunks, and store them in SQLite. After this phase, we can parse any TypeScript file and query its structure.

### Changes Required:

#### 1. Project setup
**Files**: `pyproject.toml`, `.gitignore`, `src/indiseek/__init__.py`

- `pyproject.toml` with dependencies: `tree-sitter>=0.25`, `tree-sitter-typescript`, `lancedb`, `tantivy`, `google-genai`, `fastapi`, `uvicorn`, `protobuf`, `python-dotenv`
- `.gitignore`: standard Python ignores + `data/` (indexed data), `*.scip`, `__pycache__`, `.venv`, `.env`
- Package init

#### 2. Environment and configuration
**Files**: `.env.example`, `.env`, `src/indiseek/config.py`

`.env.example` (checked into git — documents all variables):
```
# Required
GEMINI_API_KEY=your-api-key-here

# Paths
REPO_PATH=/path/to/vite
DATA_DIR=./data

# Models (defaults shown)
GEMINI_MODEL=gemini-2.0-flash
EMBEDDING_MODEL=gemini-embedding-001
EMBEDDING_DIMS=768

# Server
HOST=0.0.0.0
PORT=8000
```

`src/indiseek/config.py`:
- Uses `python-dotenv` to load `.env` file
- All config read from env vars with sensible defaults
- `REPO_PATH`: path to the Vite clone (required for indexing/serving)
- `DATA_DIR`: path to store indexes, default `./data`
- `GEMINI_API_KEY`: required for embedding/summarization/agent
- `GEMINI_MODEL`: default `gemini-2.0-flash`
- `EMBEDDING_MODEL`: default `gemini-embedding-001`
- `EMBEDDING_DIMS`: default `768`
- `HOST` / `PORT`: default `0.0.0.0` / `8000`

#### 3. CLAUDE.md (project agent instructions)
**File**: `CLAUDE.md`

Initial content (updated at each phase as capabilities grow):
```markdown
# Indiseek

Codebase research service. Python 3.10+.

## Setup
pip install -e .
cp .env.example .env  # then fill in values

## Build/Install
pip install -e .

## Test
pytest

## Lint
ruff check src/

## Index (after Vite is cloned)
python scripts/index.py

## Serve
uvicorn indiseek.api.server:app

## Project Layout
- src/indiseek/ — main package
- scripts/ — CLI entry points
- tests/ — pytest tests
- docs/ — spec and plans
- proto/ — SCIP protobuf schema
```
This file is updated at the end of each phase to reflect the current state of what works and how to run it.

#### 4. SQLite storage
**File**: `src/indiseek/storage/sqlite_store.py`

- `SqliteStore` class that manages the SQLite database
- `init_db()`: creates all tables from schema above
- `insert_symbol(...)`, `insert_chunk(...)`: batch insert methods
- `get_symbols_by_name(name)`, `get_symbols_by_file(path)`: query methods
- `get_chunks_by_file(path)`: for embedding later

#### 5. Tree-sitter parser
**File**: `src/indiseek/indexer/parser.py`

- `TypeScriptParser` class
- Uses `tree-sitter-typescript` with `Language(tsts.language_typescript())` and `Language(tsts.language_tsx())` for `.ts`/`.tsx`
- `parse_file(path) -> list[Symbol]`: extracts function_declaration, class_declaration, method_definition, interface_declaration, type_alias_declaration, enum_declaration, lexical_declaration (exported)
- `chunk_file(path) -> list[Chunk]`: produces AST-scoped chunks (one per function/class/method). Falls back to file-level chunks for files that don't parse into clean scopes.
- Uses QueryCursor API (v0.25+)
- Handles both `.ts` and `.tsx` files

#### 6. Indexing CLI (parse only)
**File**: `scripts/index.py`

- Accepts repo path as argument
- Walks all `.ts`/`.tsx` files (respecting `.gitignore`)
- Parses each file, stores symbols and chunks in SQLite
- Prints summary: N files parsed, N symbols extracted, N chunks created

### Success Criteria:

#### Automated Verification:
- [x] `pip install -e .` succeeds
- [x] `cp .env.example .env` and fill in `REPO_PATH` — config loads without errors
- [x] `python scripts/index.py` completes without errors (reads REPO_PATH from .env)
- [x] SQLite database created with populated `symbols` and `chunks` tables
- [x] `SELECT count(*) FROM symbols` returns > 0
- [x] `SELECT count(*) FROM chunks` returns > 0
- [x] `SELECT * FROM symbols WHERE name = 'createServer'` returns results

#### Manual Verification:
- [x] Spot-check: symbols table contains realistic entries for known Vite functions
- [x] Spot-check: chunks contain actual code content, not garbage
- [x] Chunks are scoped by function/class, not arbitrary fixed-size windows

#### End-of-Phase:
- [x] Update `CLAUDE.md` with current working commands for this phase

---

## Phase 2: SCIP Cross-References

### Overview
Generate a SCIP index for the Vite repo using `scip-typescript`, parse the protobuf output in Python, and load the cross-reference data into SQLite. After this phase, we can answer "what calls X?" and "where is X defined?" with precision.

### Changes Required:

#### 1. SCIP proto generation
**File**: `proto/scip.proto` (downloaded from sourcegraph/scip)

- Download the proto file
- Generate Python bindings: `protoc --python_out=src/indiseek/indexer/ proto/scip.proto`
- Produces `src/indiseek/indexer/scip_pb2.py`

#### 2. SCIP generation script
**File**: `scripts/generate_scip.sh`

- Installs `@sourcegraph/scip-typescript` via npm (if not present)
- Runs `scip-typescript index` against the Vite repo
- Produces `index.scip` in the repo root
- Instructions for prerequisites (Node.js, npm)

#### 3. SCIP loader
**File**: `src/indiseek/indexer/scip.py`

- `ScipLoader` class
- `load_scip_index(scip_path) -> None`: reads protobuf, iterates documents and occurrences
- For each document: extracts file_path, symbols, occurrences (definition/reference with ranges)
- For each symbol: extracts relationships (implementation, type_definition)
- Stores everything in SQLite via `SqliteStore`

#### 4. SQLite storage additions
**File**: `src/indiseek/storage/sqlite_store.py`

- Add methods: `insert_scip_symbol(...)`, `insert_scip_occurrence(...)`, `insert_scip_relationship(...)`
- Add queries: `get_definition(symbol)`, `get_references(symbol)`, `get_callers(symbol)`, `get_callees(symbol)`
- `get_callers`: find all symbols that have occurrences at the same location as a reference to the target
- `get_callees`: find all symbols referenced within the definition range of the target

#### 5. Update indexing CLI
**File**: `scripts/index.py`

- Add `--scip-path` argument (default: `{repo_path}/index.scip`)
- After tree-sitter parsing, load SCIP data if the file exists
- Print additional summary: N SCIP symbols loaded, N occurrences, N relationships

### Success Criteria:

#### Automated Verification:
- [x] `bash scripts/generate_scip.sh /path/to/vite` produces `index.scip`
- [x] `python scripts/index.py` loads both tree-sitter and SCIP data
- [x] `SELECT count(*) FROM scip_symbols` returns > 0
- [x] `SELECT count(*) FROM scip_occurrences` returns > 0
- [x] `SELECT count(*) FROM scip_occurrences WHERE role = 'definition'` returns > 0
- [x] `SELECT count(*) FROM scip_occurrences WHERE role = 'reference'` returns > 0

#### Manual Verification:
- [x] Query for a known symbol (e.g., `createServer`) returns its definition location
- [x] Query for references to `createServer` returns multiple files
- [x] Cross-reference data matches what you'd see in an IDE "Find References"

#### End-of-Phase:
- [x] Update `CLAUDE.md` with SCIP generation and loading commands

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 3: Semantic Embedding (LanceDB)

### Overview
Embed the AST-scoped code chunks from Phase 1 using Gemini's embedding API, store them in LanceDB, and implement semantic search. After this phase, we can ask natural language questions and get relevant code chunks back.

### Changes Required:

#### 1. LLM provider interface
**File**: `src/indiseek/agent/provider.py`

- `LLMProvider` protocol with methods: `embed(texts) -> list[list[float]]`, `generate(messages, tools?) -> response`
- `GeminiProvider` implementation using `google-genai`
- Embedding: `client.models.embed_content(model="gemini-embedding-001", contents=texts, config=EmbedContentConfig(output_dimensionality=768))`
- Handles batching (Gemini has limits on batch size)

#### 2. Vector storage
**File**: `src/indiseek/storage/vector_store.py`

- `VectorStore` class wrapping LanceDB
- `init_db()`: creates table with schema: `vector` (768-dim float32), `chunk_id` (int), `file_path` (str), `symbol_name` (str), `chunk_type` (str), `content` (str)
- `add_chunks(chunks_with_vectors)`: batch insert
- `search(query_vector, limit=10) -> list[SearchResult]`: cosine similarity search
- Returns results with file_path, symbol_name, content, score

#### 3. Embedder
**File**: `src/indiseek/indexer/embedder.py`

- `Embedder` class
- `embed_chunks(chunks: list[Chunk]) -> None`: reads chunks from SQLite, embeds via GeminiProvider, stores in LanceDB
- Batches chunks (e.g., 20 at a time) to stay within API limits
- Progress output: "Embedding chunk N/M..."

#### 4. Update indexing CLI
**File**: `scripts/index.py`

- Add `--embed` flag (requires GEMINI_API_KEY env var)
- After tree-sitter + SCIP, run embedding step
- Print summary: N chunks embedded

### Success Criteria:

#### Automated Verification:
- [ ] `python scripts/index.py --embed` completes
- [ ] LanceDB directory created with data
- [ ] A test script can embed a query "HMR propagation" and search, returning > 0 results

#### Manual Verification:
- [ ] Semantic search for "HMR propagation CSS" returns chunks from `server/hmr.ts` or similar
- [ ] Results are ranked sensibly (most relevant code first)
- [ ] Embedding step completes in reasonable time (< 10 min for Vite)

#### End-of-Phase:
- [x] Update `CLAUDE.md` with embedding commands and GEMINI_API_KEY requirement

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation. Automated/manual verification items above require a valid GEMINI_API_KEY — code is implemented and unit-tested (12 tests, all passing).

---

## Phase 4: File Summaries (Map)

### Overview
LLM-summarize every file in the repo to build a navigable "map" — a hierarchical directory tree with 1-sentence descriptions. After this phase, the agent can orient itself in the codebase before drilling down.

### Changes Required:

#### 1. Summarizer
**File**: `src/indiseek/indexer/summarizer.py`

- `Summarizer` class
- `summarize_file(path, content) -> str`: sends file content to Gemini with prompt "Summarize this file's responsibility in one sentence"
- `summarize_repo(repo_path) -> None`: walks all source files, summarizes each, stores in SQLite `file_summaries` table
- Rate-limits API calls (e.g., 1-second delay between calls, or batch where possible)
- Skips non-source files (node_modules, dist, .git, etc.)

#### 2. SQLite storage additions
**File**: `src/indiseek/storage/sqlite_store.py`

- `insert_file_summary(file_path, summary, language, line_count)`
- `get_file_summaries(directory?) -> list[FileSummary]`: optionally scoped to a subdirectory
- `get_directory_tree() -> dict`: returns nested dict of `{dir: {file: summary, subdir: {...}}}`

#### 3. Update indexing CLI
**File**: `scripts/index.py`

- Add `--summarize` flag
- After embedding, run summarization step
- Print summary: N files summarized

### Success Criteria:

#### Automated Verification:
- [ ] `python scripts/index.py --summarize` completes
- [ ] `SELECT count(*) FROM file_summaries` returns > 0
- [x] `get_directory_tree()` returns a nested structure

#### Manual Verification:
- [ ] Spot-check summaries: they accurately describe file responsibilities
- [ ] Summaries are concise (1 sentence, not paragraphs)
- [ ] Directory tree is navigable and useful for orientation

#### End-of-Phase:
- [x] Update `CLAUDE.md` with summarization commands

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation. Automated/manual verification items above (except `get_directory_tree()`) require a valid GEMINI_API_KEY — code is implemented and unit-tested (57 tests, all passing).

---

## Phase 5: Lexical Index (Tantivy)

### Overview
Build a BM25 lexical index over the raw source code using Tantivy, and implement a hybrid search that combines semantic (LanceDB) and lexical (Tantivy) results. After this phase, exact-match searches work alongside semantic search.

### Changes Required:

#### 1. Lexical indexer
**File**: `src/indiseek/indexer/lexical.py`

- `LexicalIndexer` class
- `build_index(repo_path) -> None`: walks source files, adds each file's content to Tantivy index
- Schema: `file_path` (stored, indexed), `content` (stored, indexed with `en_stem`), `start_line` (stored), `end_line` (stored)
- Indexes at chunk level (reuses AST chunks from Phase 1) so results point to specific functions/classes

#### 2. Hybrid search integration
**File**: `src/indiseek/tools/search_code.py`

- `search_code(query, mode="hybrid") -> list[SearchResult]`
- mode `"semantic"`: LanceDB only
- mode `"lexical"`: Tantivy only
- mode `"hybrid"` (default): runs both, merges results with reciprocal rank fusion (RRF)
- Returns unified results with: file_path, content snippet, score, match_type

#### 3. Update indexing CLI
**File**: `scripts/index.py`

- Add `--lexical` flag
- After all other steps, build Tantivy index
- Print summary: N documents indexed in Tantivy

### Success Criteria:

#### Automated Verification:
- [x] `python scripts/index.py --lexical` builds the Tantivy index
- [x] Lexical search for `"handleHMRUpdate"` returns exact matches
- [x] Hybrid search combines semantic and lexical results

#### Manual Verification:
- [ ] Lexical search finds exact identifiers that semantic search misses
- [ ] Hybrid search produces better results than either alone for mixed queries
- [ ] Performance is acceptable (< 1s per search)

#### End-of-Phase:
- [x] Update `CLAUDE.md` with lexical indexing commands

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation. Automated verification items verified against Vite repo. Code is implemented and unit-tested (76 tests, all passing).

---

## Phase 6: Agent Tools

### Overview
Implement the four agent tools (`read_map`, `search_code`, `resolve_symbol`, `read_file`) as self-contained functions that can be registered with Gemini's function calling API. After this phase, all tools work independently and can be tested in isolation.

### Changes Required:

#### 1. read_map tool
**File**: `src/indiseek/tools/read_map.py`

- `read_map(path: str | None = None) -> str`
- Returns directory structure + file summaries from SQLite
- If path given, scopes to that subdirectory
- Formats as readable tree with summaries

#### 2. search_code tool (already started in Phase 5)
**File**: `src/indiseek/tools/search_code.py`

- Already has hybrid search logic
- Format results for LLM consumption: file path, line range, content snippet, relevance score

#### 3. resolve_symbol tool
**File**: `src/indiseek/tools/resolve_symbol.py`

- `resolve_symbol(symbol_name: str, action: str) -> str`
- Actions: `definition`, `references`, `callers`, `callees`
- First looks up symbol in tree-sitter `symbols` table by name
- Then uses SCIP data for precise cross-references
- Falls back to tree-sitter-only data if SCIP doesn't have the symbol
- Formats results with file:line references

#### 4. read_file tool
**File**: `src/indiseek/tools/read_file.py`

- `read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> str`
- Reads actual source from the indexed repo (using config.REPO_PATH)
- Returns content with line numbers
- Validates path is within repo

### Success Criteria:

#### Automated Verification:
- [ ] Each tool function can be called independently and returns formatted strings
- [ ] `read_map()` returns the full directory tree
- [ ] `read_map("packages/vite/src/node/server")` returns scoped results
- [ ] `search_code("HMR CSS propagation")` returns relevant chunks
- [ ] `resolve_symbol("createServer", "definition")` returns a file:line location
- [ ] `resolve_symbol("createServer", "references")` returns multiple locations
- [ ] `read_file("packages/vite/src/node/server/index.ts", 1, 50)` returns 50 lines

#### Manual Verification:
- [ ] Tool outputs are formatted for LLM readability (not too verbose, not too terse)
- [ ] resolve_symbol handles ambiguous names (multiple symbols with same name) gracefully

#### End-of-Phase:
- [ ] Update `CLAUDE.md` with tool testing examples

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation.

---

## Phase 7: Agent Loop + Query API

### Overview
Implement the Gemini-powered agent loop that uses tool-calling to navigate the indexes, and expose it via a FastAPI `POST /query` endpoint. This is the final phase — after this, the MVP is complete.

### Changes Required:

#### 1. Agent loop
**File**: `src/indiseek/agent/loop.py`

- `AgentLoop` class
- `run(prompt: str) -> AgentResult`
- Registers all four tools as Gemini function declarations
- System prompt instructs the agent to: read the map first, formulate search strategy, use tools to gather evidence, synthesize answer with file:line references
- Maintains a scratchpad (list of evidence steps)
- Loops: send to Gemini → if tool call, execute tool, append result → if text response, done
- Max iterations: 15 (prevent infinite loops)
- Returns `AgentResult(answer=str, evidence=list[EvidenceStep])`

#### 2. Gemini function calling setup
**File**: `src/indiseek/agent/loop.py`

- Define tool declarations matching the four tools:
  - `read_map(path?: string)` - "Returns directory structure and file summaries"
  - `search_code(query: string, mode?: string)` - "Hybrid semantic+lexical code search"
  - `resolve_symbol(symbol_name: string, action: string)` - "Look up symbol definition, references, callers, or callees"
  - `read_file(path: string, start_line?: integer, end_line?: integer)` - "Read source code from the repository"
- Use `types.Tool(function_declarations=[...])` with `AUTO` mode

#### 3. FastAPI server
**File**: `src/indiseek/api/server.py`

- `POST /query` endpoint
- Request body: `{"prompt": "..."}`
- Calls `AgentLoop.run(prompt)`
- Response body: `{"answer": "...", "evidence": [{"step": "...", "detail": "..."}]}`
- `GET /health` for basic health check
- Error handling: return 500 with error message if agent fails

#### 4. Server entry point
**File**: `scripts/serve.py`

- `uvicorn indiseek.api.server:app --host 0.0.0.0 --port 8000`
- Or just document: `uvicorn indiseek.api.server:app`

### Success Criteria:

#### Automated Verification:
- [ ] `uvicorn indiseek.api.server:app` starts without errors
- [ ] `curl http://localhost:8000/health` returns 200
- [ ] `curl -X POST http://localhost:8000/query -H "Content-Type: application/json" -d '{"prompt": "What files are in the server directory?"}'` returns a valid JSON response with answer and evidence

#### Manual Verification:
- [ ] Agent reads the map first before searching
- [ ] Agent uses multiple tools in sequence to build up context
- [ ] Evidence trail is coherent and traceable
- [ ] Answer to "How does Vite's HMR propagation work when a CSS file changes?" includes specific file:line references
- [ ] Answer is synthesized (not just a dump of search results)
- [ ] Agent doesn't loop infinitely or get stuck
- [ ] Response time is acceptable (< 60s for a complex question)

#### End-of-Phase:
- [ ] Update `CLAUDE.md` with full serve + query workflow, completing all sections

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation. This completes the MVP.

---

## Full Indexing Command

Once all phases are implemented, a full index run looks like:

```bash
# 0. Setup (one-time)
pip install -e .
cp .env.example .env
# Edit .env: set GEMINI_API_KEY, REPO_PATH, etc.

# 1. Clone Vite (one-time)
git clone https://github.com/vitejs/vite.git /path/to/vite

# 2. Generate SCIP index (requires Node.js)
bash scripts/generate_scip.sh /path/to/vite

# 3. Run full indexing pipeline (reads config from .env)
python scripts/index.py --scip-path /path/to/vite/index.scip --embed --summarize --lexical

# 4. Start the server (reads config from .env)
uvicorn indiseek.api.server:app

# 5. Query
curl -X POST http://localhost:8000/query \
    -H "Content-Type: application/json" \
    -d '{"prompt": "How does Vite HMR propagation work when a CSS file changes?"}'
```

## Dependencies (pyproject.toml)

```
tree-sitter>=0.25
tree-sitter-typescript
lancedb
tantivy
google-genai
fastapi
uvicorn[standard]
protobuf
python-dotenv
```

## References

- Spec: `docs/SPEC.md`
- tree-sitter Python: https://tree-sitter.github.io/py-tree-sitter/
- SCIP proto: https://github.com/sourcegraph/scip/blob/main/scip.proto
- scip-typescript: https://github.com/sourcegraph/scip-typescript
- Gemini SDK: https://pypi.org/project/google-genai/
- LanceDB: https://lancedb.github.io/lancedb/python/python/
- Tantivy-py: https://github.com/quickwit-oss/tantivy-py
