# Indiseek

Codebase research service. Python 3.10+.

## Setup
```
pip install -e ".[dev]"
cp .env.example .env  # then fill in values
```

## Build/Install
```
pip install -e .
```

## Test
```
pytest
```

## Lint
```
ruff check src/
```

## Generate SCIP Index (requires Node.js)
```
bash scripts/generate_scip.sh /path/to/repo
```

## Index (after Vite is cloned and .env configured)
```
# Tree-sitter only
python scripts/index.py

# Tree-sitter + SCIP cross-references
python scripts/index.py --scip-path /path/to/repo/index.scip

# Tree-sitter + SCIP + semantic embeddings (requires GEMINI_API_KEY in .env)
python scripts/index.py --scip-path /path/to/repo/index.scip --embed

# Tree-sitter + SCIP + embeddings + file summaries (requires GEMINI_API_KEY in .env)
python scripts/index.py --scip-path /path/to/repo/index.scip --embed --summarize

# Full pipeline: all indexing steps including BM25 lexical index
python scripts/index.py --scip-path /path/to/repo/index.scip --embed --summarize --lexical
```

## Serve
```
uvicorn indiseek.api.server:app
```

## Agent Tools (after indexing)
```python
from indiseek import config
from indiseek.storage.sqlite_store import SqliteStore
from indiseek.indexer.lexical import LexicalIndexer
from indiseek.tools.read_map import read_map
from indiseek.tools.resolve_symbol import resolve_symbol
from indiseek.tools.read_file import read_file
from indiseek.tools.search_code import CodeSearcher, format_results

store = SqliteStore(config.SQLITE_PATH)

# read_map — directory tree with file summaries
read_map(store)                                       # full tree
read_map(store, path="packages/vite/src/node/server") # scoped

# resolve_symbol — definitions, references, callers, callees
resolve_symbol(store, "createServer", "definition")
resolve_symbol(store, "createServer", "references")
resolve_symbol(store, "createServer", "callers")
resolve_symbol(store, "createServer", "callees")

# read_file — source code with line numbers
read_file(config.REPO_PATH, "packages/vite/src/node/server/index.ts", 1, 50)

# search_code — hybrid semantic+lexical search
lexical = LexicalIndexer(store, config.TANTIVY_PATH)
lexical.open_index()
searcher = CodeSearcher(lexical_indexer=lexical)
results = searcher.search("HMR CSS propagation", mode="hybrid", limit=10)
format_results(results, "HMR CSS propagation")
```

## Project Layout
- src/indiseek/ — main package
- src/indiseek/tools/ — agent tools (read_map, search_code, resolve_symbol, read_file)
- scripts/ — CLI entry points
- tests/ — pytest tests
- docs/ — spec and plans
- proto/ — SCIP protobuf schema
