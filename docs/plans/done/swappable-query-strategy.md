# Swappable Query Strategy

## Problem

The query path is hardcoded to two implementations (`AgentLoop` and `MultiAgentOrchestrator`) with:
- Duplicated tool execution logic between `loop.py` and `multi.py`
- Different result types (`AgentResult` vs `MultiAgentResult`) requiring different handling in the API
- Tight coupling to Gemini's `genai.Client` and `types.*` throughout
- No way to add a new agent strategy without modifying `dashboard.py`'s routing logic

We want to try different agent architectures (e.g., deeper multi-agent, tree-of-thought, iterative refinement, different LLM providers) without touching shared infrastructure each time.

## Design

Strategy Pattern. Three extractions:

### 1. Unified Result Type

Replace `AgentResult` and `MultiAgentResult` with a single `QueryResult` that both strategies produce.

```
QueryResult:
    answer: str
    evidence: list[EvidenceStep]       # flat list, always present
    metadata: dict                     # strategy-specific extras (plan, verification, etc.)
    strategy_name: str                 # which strategy produced this
```

The API layer doesn't need to know which strategy ran. It just reads `answer` and `evidence` from `QueryResult`. Strategy-specific details (like the planner's decomposition or verification results) go in `metadata` for the frontend to optionally display.

### 2. Tool Registry

Extract tool definitions and execution out of agent classes into a shared `ToolRegistry`.

```
ToolRegistry:
    tools: dict[name -> ToolDef]

    register(name, fn, schema, description)
    execute(name, args) -> str           # runs fn, handles errors, truncates
    get_declarations() -> list[dict]     # provider-agnostic schema
    get_gemini_declarations() -> list    # Gemini FunctionDeclaration objects
```

Each `ToolDef` holds:
- `fn: Callable[..., str]` — the implementation
- `schema: dict` — parameter schema (JSON Schema subset)
- `description: str` — tool description for LLM

The registry handles:
- Execution dispatch (replaces if/elif chains)
- Error handling (try/except with error messages)
- Result truncation (>15k chars)
- Caching is NOT in the registry — caching is strategy-specific behavior (some strategies may want different cache policies)

Tool implementations stay in `src/indiseek/tools/` as they are. The registry just wraps them.

### 3. Strategy Protocol and Registry

```python
class QueryStrategy(Protocol):
    name: str

    def run(self, prompt: str, on_progress: Callable | None = None) -> QueryResult:
        ...
```

A `StrategyRegistry` maps names to factory functions:

```python
strategy_registry = StrategyRegistry()
strategy_registry.register("single", create_single_agent_strategy)
strategy_registry.register("multi", create_multi_agent_strategy)

# At query time:
strategy = strategy_registry.create("single", repo_id=1)
result = strategy.run("How does HMR work?")
```

Factory functions receive shared infrastructure (store, searcher, tool_registry, api_key, model) and return a configured strategy instance.

The "auto" mode routing heuristic lives outside any strategy — it's a function that picks which strategy name to use based on the prompt.

## Switching Mechanism

### Per-Request Selection

The `mode` parameter on `POST /api/query` maps directly to a strategy name. Currently accepts `"auto"`, `"single"`, `"multi"`. After this work, it accepts any registered strategy name:

```
POST /api/query  {"prompt": "...", "mode": "single"}       # force single-agent
POST /api/query  {"prompt": "...", "mode": "multi"}        # force multi-agent
POST /api/query  {"prompt": "...", "mode": "tree-of-thought"}  # hypothetical new strategy
POST /api/query  {"prompt": "...", "mode": "auto"}         # auto-select (default)
POST /api/query  {"prompt": "..."}                         # same as auto
```

Invalid strategy names return 400 with available options.

### Auto-Select

`mode=auto` (the default) runs a heuristic to pick the best strategy for the prompt. The heuristic lives in `StrategyRegistry.auto_select(prompt) -> str`, not in the API layer. Current logic (word count, pattern matching for how/why/explain/flow/architecture) moves there unchanged. Strategies can optionally declare a `complexity_hint` ("simple", "complex") so the heuristic can route appropriately without hardcoding strategy names.

### Discovery

`GET /api/strategies` returns the list of registered strategy names. The frontend can use this to populate a dropdown in the query UI. No frontend changes required initially — the existing `mode` field in the request body is sufficient.

### API Code Path

The API goes from two code paths with two result types to one:

```python
# Before: branching on agent type, different result handling
if use_multi:
    agent = _get_multi_agent(repo_id)
    result = agent.run(prompt)       # MultiAgentResult
    evidence = [flatten differently...]
else:
    agent = _get_agent_loop(repo_id)
    result = agent.run(prompt)       # AgentResult
    evidence = result.evidence

# After: one path, one result type
name = req.mode if req.mode != "auto" else strategy_registry.auto_select(req.prompt)
strategy = strategy_registry.create(name, repo_id=req.repo_id)
result = strategy.run(req.prompt)    # QueryResult, always
# result.answer, result.evidence — uniform regardless of strategy
```

## What Changes

### New file: `src/indiseek/agent/strategy.py`
- `QueryResult` dataclass
- `EvidenceStep` dataclass (moved from loop.py, canonical location)
- `QueryStrategy` protocol
- `ToolRegistry` class
- `StrategyRegistry` class
- `build_tool_registry(store, searcher, repo_id)` — factory that creates a registry with all four tools wired up
- `create_strategy(name, repo_id, **overrides)` — convenience function

### Modified: `src/indiseek/agent/loop.py`
- `AgentLoop` implements `QueryStrategy` protocol
- `run()` returns `QueryResult` instead of `AgentResult`
- Tool execution delegates to `ToolRegistry.execute()` instead of internal `_execute_tool()`
- `AgentResult` kept as alias for backward compat (or removed if nothing external uses it)
- Tool declarations generated from `ToolRegistry.get_gemini_declarations()`
- Registration: module-level `register_single_strategy()` called at import

### Modified: `src/indiseek/agent/multi.py`
- `MultiAgentOrchestrator` implements `QueryStrategy` protocol
- `run()` returns `QueryResult` with multi-agent metadata in `metadata` field
- `execute_tool()` standalone function replaced by `ToolRegistry.execute()`
- Registration: module-level `register_multi_strategy()` called at import

### Modified: `src/indiseek/api/dashboard.py`
- `sync_query()` uses `StrategyRegistry` instead of separate `_get_agent_loop()` / `_get_multi_agent()`
- Single code path for result handling (no more if/else on agent type)
- `mode` parameter maps directly to strategy name (or "auto" for heuristic)

### Modified: `src/indiseek/agent/__init__.py`
- Imports trigger strategy registration so they're available

## What Does NOT Change

- Tool implementations (`tools/*.py`) — untouched
- Search backends (LanceDB, Tantivy, SQLite) — untouched
- System prompts — stay in their respective files
- Gemini client usage within strategies — each strategy still uses `genai.Client` directly (provider abstraction is a separate concern, out of scope)
- Frontend — untouched, it already uses `answer` + `evidence` from the API response
- Indexing pipeline — untouched

## Implementation Checklist

### Step 1: Create `strategy.py` with core types
- [x] `EvidenceStep` dataclass (move from loop.py)
- [x] `QueryResult` dataclass
- [x] `QueryStrategy` protocol
- [x] `ToolDef` dataclass
- [x] `ToolRegistry` class with register/execute/get_declarations/get_gemini_declarations
- [x] Unit tests for ToolRegistry (register, execute, error handling, truncation)

### Step 2: `build_tool_registry()` factory
- [x] Wire up `read_map`, `search_code`, `resolve_symbol`, `read_file` into registry
- [x] Each tool gets a thin wrapper that adapts its signature to `(args: dict) -> str`
- [x] Tool schemas defined as dicts in the factory (move from TOOL_DECLARATIONS constant)
- [x] `get_gemini_declarations()` produces equivalent output to current `TOOL_DECLARATIONS`
- [x] Unit test: build registry, execute each tool with mock store/searcher

### Step 3: `StrategyRegistry`
- [x] `register(name, factory_fn)` — factory_fn signature: `(tool_registry, store, repo_path, api_key, model, repo_id) -> QueryStrategy`
- [x] `create(name, **kwargs) -> QueryStrategy`
- [x] `list_strategies() -> list[str]`
- [x] `auto_select(prompt) -> str` — the routing heuristic (moved from dashboard.py)

### Step 4: Adapt `AgentLoop` to strategy protocol
- [x] `AgentLoop.run()` returns `QueryResult`
- [x] Replace `_execute_tool()` internals with `self._tool_registry.execute()`
- [x] Accept `ToolRegistry` in constructor instead of building tools internally
- [x] Remove duplicated `TOOL_DECLARATIONS` constant (use registry instead)
- [x] Keep caching logic inside `AgentLoop` (wraps registry calls with cache checks)
- [x] Register factory function for "single" strategy
- [x] Existing tests still pass

### Step 5: Adapt `MultiAgentOrchestrator` to strategy protocol
- [x] `MultiAgentOrchestrator.run()` returns `QueryResult` with metadata
- [x] Replace standalone `execute_tool()` function with `ToolRegistry.execute()`
- [x] Accept `ToolRegistry` in constructor
- [x] Register factory function for "multi" strategy
- [x] Existing tests still pass

### Step 6: Update API layer
- [x] `sync_query()` uses `strategy_registry.create(strategy_name, repo_id=req.repo_id)`
- [x] Single result-handling code path (reads `QueryResult.answer` and `QueryResult.evidence`)
- [x] Remove `_get_agent_loop()` and `_get_multi_agent()` helper functions
- [x] `mode` parameter: "auto" calls `strategy_registry.auto_select(prompt)`, others map to strategy names directly
- [x] Add `GET /api/strategies` endpoint listing available strategies

### Step 7: Verify end-to-end
- [x] `pytest` passes (373 tests, 0 failures)
- [x] `ruff check src/` clean
- [ ] Manual: query with mode=single, mode=multi, mode=auto all work
- [x] Evidence in API response is consistent format regardless of strategy

## Adding a New Strategy (After This Work)

To add a new strategy, a developer:

1. Creates a class implementing `QueryStrategy` (has `name` attr, `run()` method returning `QueryResult`)
2. Writes a factory function: `def create_my_strategy(tool_registry, store, ...) -> MyStrategy`
3. Registers it: `strategy_registry.register("my-strategy", create_my_strategy)`
4. Done — it's now selectable via `mode=my-strategy` in the API

No changes to dashboard.py, no changes to tool implementations, no changes to search backends.
