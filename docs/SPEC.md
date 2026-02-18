To build a service capable of "researching" a codebase rather than just retrieving snippets, you need to move beyond simple vector storage. The "Harness Engineering" approach implies an agent that *navigates* code like a developer does—starting with a map, drilling down into specific subsystems, and following references—rather than just grabbing top-k chunks based on semantic similarity.

Here is a breakdown of indexing strategies to support that level of agentic inquiry, ranging from standard retrieval to the structural navigation required for deep context gathering.

### 1. The "Map" Index (High-Level Context)

Standard RAG fails on large codebases because it retrieves disjointed snippets without understanding the system architecture. You need to index a "Table of Contents" that fits in the context window.

* **File-Level Summarization:** During ingestion, run an LLM over every file to generate a 1-sentence summary of its responsibility. Store these in a hierarchical tree structure (matching the file system).
* *Usage:* The agent reads the root `README` and the file-tree summary first. It sees `src/auth/` and `src/payment/` and decides to explore `src/payment/` based on the user's prompt, rather than searching the whole repo.


* **Dependency Graphing:** Index the `package.json`, `go.mod`, or `requirements.txt` to build a dependency map.
* *Usage:* If the prompt asks "how do we handle PDF generation?", the agent checks the dependency list, sees `pdfkit`, and then knows to search for code importing that library.



### 2. The Structural Index (Code-Native Navigation)

Code is hyper-linked text. Treating it like prose (pure semantics) loses the most important signal: execution flow. You need to index the *relationships*, not just the text.

* **AST Parsing (Tree-sitter):** Use Tree-sitter to parse code into an Abstract Syntax Tree. Extract all class names, function signatures, and exported variables.
* *Storage:* Store these symbols in a relational DB or a graph DB (like Neo4j) with edges for `defines`, `calls`, and `imports`.
* *Usage:* When the agent finds a function `processPayment()`, it can query the graph for "all functions that call `processPayment()`" to understand usage patterns, rather than guessing via text search.


* **SCIP / LSIF (Language Server Index Format):** These are the standards used by GitHub Code Search and Sourcegraph. They pre-compute "Go to Definition" and "Find References" information.
* *Strategy:* Generate SCIP data during ingestion. Expose a tool to the agent: `get_definition(symbol_name)`. This allows the agent to traverse the call graph 100% accurately without hallucinating relationships.



### 3. The Semantic Index (Concept Retrieval)

This is the standard vector search layer, but it requires specific tuning for code.

* **Chunking Strategy:** Do not chunk by fixed token count (e.g., 500 tokens). Chunk by *scope*. Use the AST to chunk by Function or Class. A 10-line function should be one chunk; a 500-line class might be multiple, split by methods.
* **Embeddings:** Use code-specific models (like `jina-embeddings-v2-code` or OpenAI's `text-embedding-3` tuned on code).
* *Skepticism Note:* Embeddings are notoriously bad at exact identifiers (e.g., distinguishing `user_id` from `userId`). They are good for "how does auth work?", but bad for "find the variable `x`".



### 4. The Lexical Index (Exact Match)

You strictly need an "old school" search engine (Inverted Index/BM25) alongside vectors.

* **Trigram/Regex Search:** If the user asks about a specific error code `ERR-502` or a variable `max_retries`, vector search will likely fail. A lexical index (using Tantivy, Lucene, or Elasticsearch) catches these exact matches.

### Architecture for the "Service"

To achieve the "researching" behavior you described, your API shouldn't just be `query -> response`. It should be an agent loop (The "Harness"):

1. **Ingestion Worker:**
* Clone Repo -> Parse (Tree-sitter) -> Chunk (AST-based) -> Embed -> Store (Vector DB + Graph/Relational DB).
* *Crucial Step:* Generate the "Map" (file tree summaries).


2. **The Agent Loop (The API):**
* **Input:** User prompt + Repo URL.
* **Tool - `read_map`:** Returns the directory structure and high-level summaries.
* **Tool - `search_code`:** Hybrid search (Semantic + Lexical).
* **Tool - `Maps_symbol`:** Uses the Structural Index to go to definitions/references.
* **Tool - `read_file`:** Reads actual content.



**The Workflow:**
The agent starts by reading the map. It hypothesizes where the relevant code is. It uses `search_code` to find entry points. It then uses `read_file` to analyze the code and `Maps_symbol` to trace execution flow. It accumulates context in a scratchpad before generating the final answer.

### Supporting Technologies

* **Retrieval/Storage:** **LanceDB** or **Chroma** (embedded vector DBs), **PostgreSQL** (for relational symbol data).
* **Parsing:** **Tree-sitter** (industry standard for robust parsing).
* **Orchestration:** **LangGraph** or **Temporal** to manage the agent's state and researching loop.

**Critical Risks:**

* **Context Window Pollution:** If you retrieve too much, the model gets "lost in the middle." The "Map" approach mitigates this by letting the agent *choose* what to read.
* **Stale Indexes:** Code changes fast. Your service needs a strategy for incremental indexing (only re-indexing changed files via Git diffs) or it will become expensive and slow.

---

## MVP Prototype

### Goal

Validate that combining structural (Tree-sitter + SCIP), semantic (embeddings), and map (LLM summaries) indexing produces a context API that is genuinely more useful to coding agents than what they can do with grep/read/LSP alone. The key question: can this approach surface non-obvious relationships, execution flows, and architectural context that an agent couldn't efficiently find on its own?

### Target Codebase

**Vite** (https://github.com/vitejs/vite) — ~40k LOC TypeScript. Selected because:
- Non-linear execution flow (plugin pipeline, module resolution, dev server vs build mode)
- "How does X actually work?" questions are hard to answer by grepping
- Real-world complexity without being enormous

Originally hardcoded for MVP. Multi-repo support and incremental sync were added post-MVP (see Multi-Repo Architecture below).

### Stack

- **Language:** Python
- **LLM Provider:** Google Gemini (behind a provider interface so we can swap to Anthropic, OpenAI, etc.)
- **Vector Storage:** LanceDB embedded (behind a storage interface so we can swap implementations)
- **Structural Storage:** SQLite for symbol graph (Tree-sitter extracted symbols + SCIP cross-references)
- **HTTP Framework:** FastAPI or Flask — minimal, just enough for curl-friendly endpoints
- **Orchestration:** Plain Python loop. No LangGraph/Temporal for now.

### Indexing Pipeline

Run once against a local clone of Vite. Produces all indexes needed for query time.

**Step 1 — Parse (Tree-sitter):**
- Parse all TypeScript/JavaScript files into ASTs
- Extract: function/class/variable declarations, exports, imports
- Chunk code by scope (function/class/method boundaries, not fixed token windows)

**Step 2 — Cross-Reference (SCIP):**
- Run scip-typescript against the Vite repo to generate a SCIP index
- Load into SQLite: symbol definitions, references, and relationships
- This gives us precise "go to definition" and "find all references" across files

**Step 3 — Embed (Semantic):**
- Take AST-scoped chunks from Step 1
- Embed with a code-specific model (e.g., jina-embeddings-v2-code or text-embedding-3)
- Store in LanceDB with metadata (file path, symbol name, chunk type)

**Step 4 — Summarize (Map):**
- LLM-summarize every file (1-sentence responsibility description)
- Build hierarchical directory tree with summaries
- Extract dependency info from package.json
- Store the map as a queryable structure

**Step 5 — Lexical Index:**
- Build a simple trigram or BM25 index over the raw source for exact-match queries
- Can use Tantivy (Rust-based, has Python bindings) or a simple in-process solution

### Query API

Single HTTP endpoint. Send a natural language question, get back an answer with full evidence trail.

**Endpoint:** `POST /query`

**Request:**
```
{ "prompt": "How does Vite's HMR propagation work when a CSS file changes?" }
```

**Response:**
```
{
  "answer": "synthesized answer with file:line references",
  "evidence": [
    { "step": "read_map", "detail": "Scanned directory tree, identified src/node/server/ as HMR-related" },
    { "step": "search_code", "detail": "Semantic search for 'HMR propagation CSS', found handleHMRUpdate in server/hmr.ts" },
    { "step": "resolve_symbol", "detail": "SCIP: handleHMRUpdate called by onFileChange in server/index.ts:234" },
    { "step": "read_file", "detail": "Read server/hmr.ts:45-120, found CSS-specific propagation logic" },
    ...
  ]
}
```

### Agent Loop (Internal)

Plain Python. The agent has access to these tools backed by the indexes:

- **`read_map(path?)`** — Returns directory structure + file summaries. Optionally scoped to a subdirectory.
- **`search_code(query, mode?)`** — Hybrid search: semantic (LanceDB) + lexical (exact match). Returns ranked code chunks.
- **`resolve_symbol(symbol_name, action)`** — Uses SCIP/Tree-sitter data. Actions: `definition`, `references`, `callers`, `callees`.
- **`read_file(path, start_line?, end_line?)`** — Reads actual source from the indexed repo.

The loop:
1. Agent reads the map to orient
2. Formulates a search strategy based on the question
3. Uses search_code to find entry points
4. Uses resolve_symbol to trace execution flow
5. Uses read_file to examine specific code
6. Accumulates findings in a scratchpad
7. Synthesizes answer with evidence trail
8. Returns structured response

### What We're NOT Building in MVP

- ~~No repo cloning/management~~ **DONE** — dashboard UI for add/clone/delete/sync repos
- ~~No incremental indexing~~ **DONE** — incremental sync via git diff + selective re-index
- No auth, no multi-user, no rate limiting
- ~~No caching of query results~~ **DONE** — fuzzy query cache with reindex invalidation
- No MCP server integration (just HTTP for now)
- No persistent conversation/follow-up queries

---

## Multi-Repo Architecture

All data tables are partitioned by `repo_id INTEGER DEFAULT 1`. Legacy single-repo data (pre-multi-repo) lives under `repo_id=1` with no migration required — the DEFAULT clause handles it.

### Storage Layout

**SQLite (shared database):**
- `repos` table: id, name, url, local_path, created_at, last_indexed_at, indexed_commit_sha, current_commit_sha, commits_behind, status
- All data tables (`symbols`, `chunks`, `file_summaries`, `file_contents`, `directory_summaries`, `scip_symbols`, `scip_occurrences`, `scip_relationships`, `queries`) have `repo_id` column with `DEFAULT 1`
- Composite UNIQUE constraints: `file_contents(file_path, repo_id)`, `directory_summaries(dir_path, repo_id)`, `file_summaries(file_path, repo_id)`, `scip_symbols(symbol, repo_id)`

**Per-repo storage backends:**
- LanceDB tables: `chunks` (legacy repo_id=1), `chunks_{repo_id}` (new repos)
- Tantivy index directories: `DATA_DIR/tantivy/` (legacy), `DATA_DIR/tantivy_{repo_id}/` (new repos)
- Git clones: `REPO_PATH` (legacy repo_id=1), `DATA_DIR/repos/{repo_id}/` (new repos)

Config helpers: `get_repo_path(repo_id)`, `get_lancedb_table_name(repo_id)`, `get_tantivy_path(repo_id)`.

### API

All existing dashboard endpoints accept `?repo_id=N` query parameter (default 1). All POST endpoints accept `repo_id` in the request body.

Repo management endpoints:
- `GET /repos` — list all repos
- `POST /repos` — clone a new repo (background task)
- `GET /repos/{id}` — repo details
- `DELETE /repos/{id}` — remove repo
- `POST /repos/{id}/check` — freshness check (git fetch + compare SHAs)
- `POST /repos/{id}/sync` — incremental sync (git pull + re-index changed files)

### CLI

`scripts/index.py --repo <name-or-id>` indexes a specific repo. Without `--repo`, falls back to `REPO_PATH` with `repo_id=1`.

### Frontend

React context (`RepoContext`) holds `currentRepoId`, persisted in localStorage. Repo selector dropdown in nav sidebar (only visible when 2+ repos exist). All pages pass `currentRepoId` to API calls. Repos management page at `/repos`.

---

## Post-MVP Roadmap

Detailed plan: `docs/plans/roadmap.md`

### Tier 1 — Must-Have
- ~~**Repo management:** Multi-repo support with UI for add/remove/status. All tables partitioned by `repo_id`.~~ **DONE** — see Multi-Repo Architecture above.
- **Partial updates:** ~~Git-diff-driven incremental re-indexing. Commit SHA freshness tracking.~~ **Freshness check and incremental sync implemented** (see `/repos/{id}/check` and `/repos/{id}/sync`). Remaining: Change detection via GitHub Actions workflow (first), API polling (fallback), GitHub App webhooks (later at scale). Scheduled refresh on configurable interval.
- **Provider-neutral LLM:** Anthropic and OpenAI alongside Gemini. Abstract provider protocol. Separate embedding provider config. Anthropic first, OpenAI second, Bedrock deferred.
- **Stable tool API:** Formal documentation, consistent error handling, versioned schemas for the four core tools.
- **Evidence trail everywhere:** Richer evidence steps (timestamps, durations, inline code), expandable UI with syntax highlighting, clickable file references.

### Tier 2 — Wedge Multipliers
- **Log/metric index view:** Emit sites as first-class entities. Separate query path with deeper reasoning for observability questions.
- **Slackbot:** Slack bot via Bolt for Python. Forces API cleanliness and drives adoption.
- **Usage analytics:** Queries/day, latency, cache hit rate, freshness, tool usage. All from SQLite.

### Tier 3 — Nice-to-Have
- **Multi-agent query pipeline:** Split single agent into Planner → Researcher(s) → Synthesizer → Verifier. Each researcher gets isolated context scoped to one sub-question/subsystem, preventing gravity well problem. Plan: `docs/plans/multi-agent-architecture.md`.
- Diagrams, walkthrough pages, rich browsing UI
- Reranking, personalization, saved searches
- More languages/indexers (Python, Go, Java, Rust)
- Codebase evolution history — change-by-change tracking over time