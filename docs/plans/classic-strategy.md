# Plan: Register Original Agent Loop as "classic" Strategy

## Context

After multi-repo work (6017e4d), 6 commits changed the agent loop: increased iteration budget (12->14), rewrote system prompt to a compact decision table, added CRITIQUE_PHASE, question reiteration, and exploration tracking. The result is slower queries with worse answers.

Rather than destructively reverting the current "single" strategy, we register the original 6017e4d behavior as a separate "classic" strategy. Nothing existing changes — the current single strategy and all its tests stay untouched. `auto_select` defaults to "classic" so it's the default, but users can still opt into "single" or "multi" via the API `mode` parameter.

## Files to create/modify

| File | Action |
|------|--------|
| `src/indiseek/agent/classic.py` | **New** — Classic strategy: original 6017e4d behavior |
| `src/indiseek/agent/__init__.py` | Add `import indiseek.agent.classic` |
| `src/indiseek/agent/strategy.py` | Change `auto_select()` to return `"classic"` |
| `tests/test_classic.py` | **New** — Tests for the classic strategy |

No changes to: `loop.py`, `multi.py`, `dashboard.py`, `test_agent.py`.

## Design

### classic.py structure

The classic strategy is a self-contained module following the same registration pattern as loop.py and multi.py. It implements the `QueryStrategy` protocol.

**Uses from strategy.py (shared, DRY):**
- `ToolRegistry` + `build_tool_registry()` — tool execution, caching, Gemini declarations
- `EvidenceStep`, `QueryResult` — result types

**Owns (original 6017e4d behavior):**
- `MAX_ITERATIONS = 12`, `SYNTHESIS_PHASE = 10`
- Per-tool paragraph system prompt with good/bad query examples, "7-8 iterations" budget text
- `_maybe_inject_tool_hint()` with two hints: resolve_symbol nudge (iter >= 3) + iteration-8 budget warning
- Single-pass tool execution loop with 3-tier budget injection on every tool response
- Inline `search_code` handling for `summarize_results()` + TIP nudge (same pattern as current loop.py)
- No CRITIQUE_PHASE, no question reiteration, no `_files_read`/`_symbols_resolved` tracking

**Class shape:**
```
class ClassicAgentLoop:
    name = "classic"

    __init__(store, repo_path, code_searcher, api_key, model, repo_id, tool_registry)
    _build_system_prompt() -> str
    _execute_tool(name, args) -> str     # delegates to registry, tracks _resolve_symbol_used
    _maybe_inject_tool_hint(iteration) -> str | None
    run(prompt, on_progress) -> QueryResult
```

Factory + registration at module bottom (same pattern as loop.py):
```
def _create_classic_strategy(...) -> ClassicAgentLoop:
    return create_classic_agent_loop(...)

def register_classic_strategy():
    strategy_registry.register("classic", _create_classic_strategy)

register_classic_strategy()  # auto-register on import
```

### strategy.py change

`auto_select()` becomes:
```python
def auto_select(self, prompt: str) -> str:
    return "classic"
```

The complex-query heuristic is removed. Users who want multi-agent can pass `mode=multi` explicitly.

### __init__.py change

Add one line:
```python
import indiseek.agent.classic  # noqa: F401
```

## Checklist

- [ ] Create `src/indiseek/agent/classic.py` with ClassicAgentLoop class
  - [ ] Constants: MAX_ITERATIONS=12, SYNTHESIS_PHASE=10
  - [ ] SYSTEM_PROMPT_TEMPLATE: exact 6017e4d per-tool paragraph version
  - [ ] __init__: store, repo_path, searcher, client, model, repo_id, caches, tool_registry
  - [ ] _build_system_prompt(): bake in repo map
  - [ ] _execute_tool(): delegate to registry, track _resolve_symbol_used only
  - [ ] _maybe_inject_tool_hint(): resolve_symbol nudge + iteration-8 warning
  - [ ] run(): single-pass loop, inline search_code, 3-tier budget on every response, no CRITIQUE, no question reiteration
  - [ ] _error_hint(): import from loop.py (shared)
  - [ ] create_classic_agent_loop() factory
  - [ ] register_classic_strategy() + auto-register on import
- [ ] Add `import indiseek.agent.classic` to `__init__.py`
- [ ] Change `auto_select()` in strategy.py to return `"classic"`
- [ ] Create `tests/test_classic.py`:
  - [ ] Constants are 12/10
  - [ ] System prompt has per-tool paragraphs and "7-8 iterations" budget text
  - [ ] _maybe_inject_tool_hint returns hints at correct iterations
  - [ ] run() returns QueryResult with strategy_name="classic"
  - [ ] Budget injection appears on every tool response (3-tier)
  - [ ] No CRITIQUE_PROMPT injection
  - [ ] Strategy registered and auto_select returns "classic"
- [ ] `ruff check src/` passes
- [ ] `pytest` passes (all existing tests unchanged)

## What's NOT changing

- `loop.py` — current "single" strategy untouched
- `multi.py` — multi-agent pipeline untouched
- `test_agent.py` — all existing tests pass as-is
- `dashboard.py` — already supports arbitrary strategy names via `strategy_registry.list_strategies()`
- Frontend — no changes needed (mode selection is server-side)
