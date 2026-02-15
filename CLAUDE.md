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

## Index (after Vite is cloned and .env configured)
```
python scripts/index.py
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
- proto/ — SCIP protobuf schema (future)
