# Index Inspector Dashboard

## Goal

A read-only web UI mounted on the existing FastAPI server that lets you inspect the state of all indexing pipeline stores (SQLite, LanceDB, Tantivy), identify coverage gaps (files parsed but not embedded, files not summarized, etc.), and test search queries against the indexed data.

## Architecture

- **Backend:** New API routes mounted on the existing FastAPI app under `/dashboard/api/...`
- **Frontend:** React SPA using Vite as build tool, shadcn/ui component library. Lives in `frontend/` directory with its own `package.json`. Client-side routing via React Router.
- **Serving:** FastAPI serves the built SPA static files at `/dashboard`. During development, the React dev server proxies API calls to FastAPI.
- **Read-only:** No write operations. All indexing remains CLI-only via `scripts/index.py`.

## Pages & Routes

### 1. Overview (`/dashboard`)

Top-level stats cards showing aggregate numbers per store:
- **SQLite:** total files parsed, total chunks, total symbols, total SCIP symbols, total SCIP occurrences, total file summaries
- **LanceDB:** total embedded chunks, whether the table exists and has vectors
- **Tantivy:** total indexed documents, whether the index directory exists

Below the cards: a **coverage summary bar** showing percentages — e.g., "1200/1817 files summarized (66%)", "1500/1817 chunks embedded (82%)".

### 2. File Tree (`/dashboard/files`)

Collapsible directory tree showing the full repo file tree (from `git ls-files`).

Each directory node shows aggregate pipeline coverage:
- Number of files parsed (have chunks in SQLite)
- Number of files with embeddings in LanceDB
- Number of files with summaries in `file_summaries`
- Number of files with chunks in Tantivy

Non-indexable files (images, binaries, configs that aren't in the pipeline) are shown but grayed out.

Clicking a directory expands it. Clicking a file navigates to a file detail view.

Color coding or icons per file to indicate status: fully indexed, partially indexed, not indexed.

### 3. File Detail (`/dashboard/files/:path`)

Shows for a specific file:
- File path, language, line count
- Summary (from `file_summaries`) if it exists, or "not summarized" indicator
- List of chunks extracted from this file (from `chunks` table) — each showing symbol name, chunk type, line range, token estimate
- Per-chunk indicators: whether it exists in LanceDB (embedded), whether it exists in Tantivy (lexical indexed)

Clicking a chunk navigates to the chunk detail view.

### 4. Chunk Detail (`/dashboard/chunks/:id`)

Shows full details for a single chunk:
- Chunk ID, file path, symbol name, chunk type, line range, token estimate
- Full source code content with syntax highlighting
- Pipeline status: present in SQLite (always), present in LanceDB (yes/no), present in Tantivy (yes/no)
- If embedded: confirmation that the vector exists (don't need to display the raw vector)

### 5. Search (`/dashboard/search`)

Standalone search page:
- Text input for query
- Mode selector: semantic, lexical, hybrid
- Limit selector (default 10)
- Results displayed as ranked list showing: rank, score, file path, symbol name, chunk type, line range, content preview
- Each result links to its chunk detail view

## Dashboard API Endpoints

All under `/dashboard/api/`:

- `GET /dashboard/api/stats` — Aggregate counts for all stores
- `GET /dashboard/api/tree?path=` — Directory tree with coverage counts for given path (default root). Returns children one level deep with aggregated stats.
- `GET /dashboard/api/files/:path` — File detail: summary, chunks, per-chunk pipeline status
- `GET /dashboard/api/chunks/:id` — Single chunk detail with pipeline presence
- `GET /dashboard/api/search?q=&mode=&limit=` — Execute search, return ranked results with scores

## Frontend Stack

- React 18+ with TypeScript
- Vite for build tooling
- React Router for client-side routing
- shadcn/ui (Radix + Tailwind) for components
- No state management library — TanStack Query for server state

## Development Workflow

- `cd frontend && npm run dev` — Runs Vite dev server with HMR, proxies `/dashboard/api` to FastAPI
- `cd frontend && npm run build` — Produces `frontend/dist/`
- FastAPI serves `frontend/dist/` at `/dashboard` in production
- `.gitignore` includes `frontend/node_modules/` and `frontend/dist/`

## What This Is NOT

- Not a replacement for the CLI indexing pipeline
- Not a way to trigger re-indexing or modify data
- Not a query interface for the agent loop (that's `POST /query`)
- Not authenticated or multi-user
