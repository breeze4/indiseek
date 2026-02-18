# Agent Loop Tier 1 Improvements

## How to use this plan

You are implementing this plan **one step at a time**. Each step is a self-contained unit.

1. Find the first step where NOT all checkboxes are `[x]`.
2. Implement that step. Read the files first, make the changes, run the verification commands.
3. When all verification commands pass, mark the checkboxes `[x]`.
4. Git commit. Stop.

**Files you will modify**: `src/indiseek/agent/loop.py` and `tests/test_agent.py`. That's it.

**DO NOT** change any tool implementations, API layer, or frontend code.

---

## Step 1: Increase iteration budget

Raise the loop limits to make room for the critique phase added in Step 4.

### Changes to `src/indiseek/agent/loop.py`

1. Change constant `MAX_ITERATIONS = 12` to `MAX_ITERATIONS = 20`
2. Change constant `SYNTHESIS_PHASE = 10` to `SYNTHESIS_PHASE = 18`
3. In `SYSTEM_PROMPT_TEMPLATE`, find the Budget section text `"Plan to use at most 7-8 iterations"` — change `7-8` to `12-14`
4. In `SYSTEM_PROMPT_TEMPLATE`, find `"past iteration 8"` — change to `"past iteration 14"`
5. In method `_maybe_inject_tool_hint`, find `if iteration == 8:` — change to `if iteration == 14:`
6. In same method, find the string `"iteration 8/12"` — change to `"iteration 14/20"`

### Changes to `tests/test_agent.py`

7. In `test_max_iterations`, find `assert len(result.evidence) == 12` — change `12` to `20`
8. In `test_system_prompt_includes_repo_map`, find `assert "12 iterations" in prompt` — change `"12 iterations"` to `"20 iterations"`

### Verification

- [x] `pytest tests/test_agent.py` passes
- [x] `ruff check src/` has no errors

---

## Step 2: Rewrite tool descriptions

Replace tool descriptions with the four-line pattern: purpose, returns, when to use, redirect to alternative. Also replace the system prompt tool docs with a decision table.

### Changes to `src/indiseek/agent/loop.py`

**In `TOOL_DECLARATIONS`**, replace the `description` string for each tool:

1. **search_code** — replace the description with:
```
Search code by meaning or keywords. Returns top 10 code chunks ranked by relevance.

Modes:
- "lexical": Exact identifiers (updateStyle, handleHMRUpdate, ERR_NOT_FOUND)
- "semantic": Concepts ("how CSS changes are applied in the browser")
- "hybrid" (default): Combines both. Best when unsure.

For symbol cross-references (who calls X, where is X defined), use resolve_symbol instead.
For reading a specific file you already know, use read_file instead.
```

2. **resolve_symbol** — replace the description with:
```
Navigate the code's call graph using precise cross-reference data. More accurate than searching for symbol names — use this as your primary navigation tool after initial discovery.

Actions:
- "definition": Where is this symbol defined? Start here.
- "references": Where is this symbol used across the codebase?
- "callers": What functions call this symbol? Understand usage patterns.
- "callees": What does this function call? Trace execution flow downward.

Tip: After search_code finds a symbol, call resolve_symbol('name', 'definition') AND resolve_symbol('name', 'callers') together to get the full picture in one turn.
```

3. **read_file** — replace the description with:
```
Read source code with line numbers. Default cap is 200 lines. Use start_line/end_line for large files.

Use this when you know the file path and need to examine the actual implementation.
This is the ONLY way to scope to a specific file — search_code cannot filter by path.
Reading the implementation after finding a symbol is almost always more valuable than running another search.
```

4. **read_map** — keep as-is.

**In `SYSTEM_PROMPT_TEMPLATE`**, replace the `## Tool usage` section (everything from `## Tool usage` up to but not including `## Strategy`) with:

```
## Tool usage

Detailed tool docs are in the tool declarations. Use this table to pick the right tool:

### When to use which tool
| I have... | Use |
|-----------|-----|
| An exact function/variable name | search_code(query, mode="lexical") |
| A concept or "how does X work" question | search_code(query, mode="semantic") |
| A general first exploration | search_code(query) — hybrid is default |
| A symbol name from search results | resolve_symbol(name, "definition") + resolve_symbol(name, "callers") |
| A file path I want to read | read_file(path) |
```

This removes the per-tool docs and "Good queries / Bad queries" examples from the system prompt (now redundant with the tool declarations).

### Verification

- [x] `pytest tests/test_agent.py` passes
- [x] `ruff check src/` has no errors

---

## Step 3: Add question reiteration and exploration tracking

Two features: (a) the original question is prepended to every tool response so the model doesn't lose context, and (b) the agent tracks which symbols it discovered vs. investigated and surfaces the gap.

### Changes to `src/indiseek/agent/loop.py`

**3a. New instance state in `__init__`** — add after the existing `self._resolve_symbol_used` line:

```python
self._files_read: set[str] = set()
self._symbols_resolved: set[tuple[str, str]] = set()
self._discovered_symbols: set[str] = set()
self._original_prompt: str = ""
```

**3b. Reset state in `run()`** — in the block where `self._file_cache.clear()` etc. are called, add:

```python
self._files_read.clear()
self._symbols_resolved.clear()
self._discovered_symbols.clear()
self._original_prompt = prompt
```

**3c. Track tool usage in `_execute_tool`**:

- In the `search_code` branch, after `self._query_cache.put(query, result)`, add code to extract discovered symbol names from `results` (the list of `HybridResult`) and add them to `self._discovered_symbols`. Each result has a `.symbol_name` attribute (may be None — check before adding).
- In the `resolve_symbol` branch, after the result is computed, add `self._symbols_resolved.add((symbol_name, action))`.
- In the `read_file` branch, after the result is computed, add `self._files_read.add(args["path"])`.

**3d. Add `_exploration_gaps()` method** to `AgentLoop`:

```python
def _exploration_gaps(self) -> str:
    """Surface symbols found but not yet investigated."""
    resolved_names = {s[0] for s in self._symbols_resolved}
    unresolved = self._discovered_symbols - resolved_names
    if not unresolved:
        return ""
    names = sorted(unresolved)[:5]
    return f"\n[Symbols found but not yet resolved: {', '.join(names)}]"
```

**3e. Inject into tool responses** — in the main loop, in the block where tool results are built (where budget injection and `_maybe_inject_tool_hint` happen), add two injections:

1. **Question reiteration**: Before the budget injection block, prepend:
   ```python
   result = f"[QUESTION: {self._original_prompt}]\n" + result
   ```

2. **Gap surfacing**: After the `_maybe_inject_tool_hint` injection, append:
   ```python
   gaps = self._exploration_gaps()
   if gaps:
       result += gaps
   ```

### Changes to `tests/test_agent.py`

Add a new test class `TestExplorationTracking` with these tests:

- `test_discovered_symbols_tracked`: Execute `search_code` via mock results containing symbol names. Verify `agent._discovered_symbols` is populated.
- `test_resolved_symbols_tracked`: Execute `resolve_symbol`. Verify `agent._symbols_resolved` is populated.
- `test_gaps_surface_unresolved`: Set `agent._discovered_symbols = {"foo", "bar", "baz"}` and `agent._symbols_resolved = {("foo", "definition")}`. Call `agent._exploration_gaps()`. Assert it contains `"bar"` and `"baz"` but not `"foo"`.
- `test_gaps_empty_when_all_resolved`: Set discovered = resolved. Assert `_exploration_gaps()` returns `""`.
- `test_question_reiteration_in_responses`: Run agent with mock (one tool call + text response). Capture the function response parts sent to generate_content. Verify the result string contains `[QUESTION:`.

### Verification

- [x] `pytest tests/test_agent.py` passes
- [x] `ruff check src/` has no errors

---

## Step 4: Add CRITIC verification phase

Before the forced synthesis, inject a prompt that tells the model to verify its claims with tool calls. This only fires if the agent has made enough tool calls (skip for simple queries).

### Changes to `src/indiseek/agent/loop.py`

**4a. Add constants** near `MAX_ITERATIONS` and `SYNTHESIS_PHASE`:

```python
CRITIQUE_PHASE = 15
MIN_TOOL_CALLS_FOR_CRITIQUE = 5
```

**4b. Add critique prompt** as a module-level string:

```python
CRITIQUE_PROMPT = (
    "STOP. Before writing your final answer, verify your claims.\n\n"
    "1. List every factual claim you plan to make (e.g., 'function X is defined "
    "in file Y', 'A calls B', 'the update is sent via WebSocket').\n"
    "2. For each claim you haven't directly verified with a tool call, verify it NOW. "
    "Use resolve_symbol to check definitions/callers. Use read_file to confirm "
    "implementations.\n"
    "3. Flag any claims you cannot verify as uncertain.\n\n"
    "You have a few more iterations for targeted verification. Be specific — one "
    "claim per tool call."
)
```

**4c. Inject in main loop** — inside the `for iteration in range(MAX_ITERATIONS):` loop, right before the `generate_content` call, add:

```python
if iteration == CRITIQUE_PHASE and tool_call_count >= MIN_TOOL_CALLS_FOR_CRITIQUE:
    logger.info("--- Iteration %d/%d (CRITIC PHASE) ---", iteration + 1, MAX_ITERATIONS)
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text=CRITIQUE_PROMPT)],
    ))
```

Key: do NOT disable tools during the critique phase. The whole point is external verification via tool calls.

### Changes to `tests/test_agent.py`

Add imports for the new constants: `CRITIQUE_PHASE, MIN_TOOL_CALLS_FOR_CRITIQUE, CRITIQUE_PROMPT`.

Add a new test class `TestCritiquePhase` with:

- `test_critique_injected_when_enough_tool_calls`: Mock agent to make 6+ tool calls before reaching iteration 15. Verify the critique prompt text appears in the contents list.
- `test_critique_skipped_for_simple_queries`: Mock agent to make only 3 tool calls. Verify critique prompt does NOT appear in the contents.
- `test_critique_allows_tool_calls`: Verify that during the critique iteration, the model is called with `research_config` (tools enabled), not `synthesis_config`.

### Verification

- [x] `pytest tests/test_agent.py` passes
- [x] `ruff check src/` has no errors

---

## Step 5: Final full-suite verification

Run the complete test suite and linter to confirm no regressions.

### Commands to run

1. `pytest` (all tests, not just test_agent.py)
2. `ruff check src/`

### Verification

- [ ] `pytest` passes (all tests)
- [ ] `ruff check src/` has no errors
