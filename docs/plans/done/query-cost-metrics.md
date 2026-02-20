# Query Cost Metrics

## Context

Every Gemini API call returns `usage_metadata` with token counts (`prompt_token_count`, `candidates_token_count`, `total_token_count`), but we discard all of it. The API should return cost/usage info so we can track spend per query. The agent loops (single, classic, multi) each make multiple LLM round-trips per query, and none accumulate token counts.

We are using `gemini-3-flash-preview` as our generation model (update the default in `config.py` from `gemini-2.0-flash`). Embedding model remains `gemini-embedding-001`.

## Design

### UsageStats accumulator

New dataclass in `src/indiseek/agent/strategy.py` (next to `QueryResult`):

```
@dataclass
class UsageStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    requests: int = 0

    def add(prompt, completion):  # accumulate from one API call
    def to_dict() -> dict        # serialize for JSON/API
    def estimated_cost(model) -> float  # compute $ from token counts + model rates
```

Pricing constants as a dict keyed by model ID. Standard and batch rates per 1M tokens:

| Model | Standard In | Standard Out | Batch In | Batch Out |
|---|---|---|---|---|
| `gemini-3-flash-preview` | $0.50 | $3.00 | $0.25 | $1.50 |
| `gemini-3-pro-preview` | $2.00 | $12.00 | $1.00 | $6.00 |
| `gemini-3.1-pro-preview` | $2.00 | $12.00 | $1.00 | $6.00 |
| `gemini-embedding-001` | $0.15 | — | $0.075 | — |

Note: 3.x-pro has higher rates above 200k context — we'll use the ≤200k rate since agent queries are well under that. Store both standard and batch rates; use standard by default, let callers pass `batch=True` to get batch pricing.

Source: https://ai.google.dev/gemini-api/docs/pricing

### Capture in agent loops

Each loop already has the `response` object from `generate_content()`. Read `response.usage_metadata.prompt_token_count` and `.candidates_token_count` after each call and accumulate into a `UsageStats` instance.

Attach to result: `QueryResult.metadata["usage"] = stats.to_dict()`

### Persist in SQLite

Add columns to `queries` table: `prompt_tokens INTEGER`, `completion_tokens INTEGER`, `estimated_cost REAL`. Use the existing `_migrate_add_column` pattern. Update `complete_query()` to accept and store these.

### API response

Add `usage` field to `SyncQueryResponse` and the background task result dict. Shape: `{prompt_tokens, completion_tokens, total_tokens, requests, estimated_cost}`.

## Files to modify

- `src/indiseek/config.py` — update `GEMINI_MODEL` default to `gemini-3-flash-preview`
- `src/indiseek/agent/strategy.py` — add `UsageStats` dataclass + pricing constants
- `src/indiseek/agent/loop.py` — accumulate usage per iteration, attach to result
- `src/indiseek/agent/classic.py` — same
- `src/indiseek/agent/multi.py` — accumulate across all 4 agent phases, attach to result
- `src/indiseek/storage/sqlite_store.py` — add columns, update `complete_query()`
- `src/indiseek/api/dashboard.py` — add usage to response models and endpoint handlers

## Checklist

- [x] 1. Update `GEMINI_MODEL` default to `gemini-3-flash-preview` in `config.py`
- [x] 2. Add `UsageStats` dataclass and pricing map to `strategy.py`
- [x] 3. Capture usage in `AgentLoop.run()` (loop.py), attach to `QueryResult.metadata`
- [x] 4. Capture usage in `ClassicAgentLoop.run()` (classic.py), attach to `QueryResult.metadata`
- [x] 5. Capture usage in `MultiAgentOrchestrator.run()` (multi.py), attach to `QueryResult.metadata`
- [x] 6. Add `prompt_tokens`, `completion_tokens`, `estimated_cost` columns to `queries` table; update `complete_query()` and `insert_cached_query()`
- [x] 7. Add `usage` field to API response models and wire through both query endpoints
- [x] 8. Run tests, lint, verify end-to-end

## Verification

1. `ruff check src/` — no lint errors
2. `pytest` — existing tests pass
3. Manual: `curl -X POST http://localhost:8000/api/query -d '{"prompt":"test"}'` — response includes `usage` object with non-zero token counts and cost
