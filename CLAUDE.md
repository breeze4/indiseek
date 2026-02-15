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
```

## Serve
```
uvicorn indiseek.api.server:app
```

## Project Layout
- src/indiseek/ — main package
- scripts/ — CLI entry points
- tests/ — pytest tests
- docs/ — spec and plans
- proto/ — SCIP protobuf schema
