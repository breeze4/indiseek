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

Hardcoded for MVP. No repo management, no multi-repo support, no incremental indexing.

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

- No repo cloning/management (manually clone Vite, point the indexer at it)
- No incremental indexing (full re-index every time)
- No auth, no multi-user, no rate limiting
- No caching of query results
- No MCP server integration (just HTTP for now)
- No persistent conversation/follow-up queries