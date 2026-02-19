# Plan: Restore Original Agent Loop + Clean Strategy Pattern

## Context

After multi-repo work completed (`6017e4d`), 6 commits progressively degraded the agent loop quality:
- Increased iteration budget (12→20→14)
- Rewrote system prompt from working per-tool paragraphs to a compact decision table
- Added exploration tracking, question reiteration, CRITIC phase
- Added 1107-line multi-agent pipeline (Planner→Researcher×N→Synthesizer→Verifier)

Result: queries are slower and produce worse answers. The original 12-iteration single-agent loop was fast and produced high quality results.

**Goal**: Restore the original agent loop behavior as the "single" strategy. Keep "multi" as an opt-in strategy. Keep the strategy pattern infrastructure. Change `auto_select` to default to "single".

## Files to modify

| File | Action |
|------|--------|
| `src/indiseek/agent/loop.py` | Revert agent behavior to 6017e4d state (keep ToolRegistry + strategy registration) |
| `src/indiseek/agent/strategy.py` | Change `auto_select` to always return "single" |
| `src/indiseek/agent/multi.py` | No changes (keep as-is for opt-in use) |
| `src/indiseek/agent/__init__.py` | No changes |
| `src/indiseek/api/dashboard.py` | No changes (already supports mode=auto/single/multi) |
| `tests/test_agent.py` | Update any assertions that depend on changed constants |

## What the original (6017e4d) had that we're restoring

The original loop at `6017e4d` had these behaviors we want back:
- `MAX_ITERATIONS = 12`, `SYNTHESIS_PHASE = 10`
- Per-tool paragraph system prompt with good/bad query examples
- Budget text: "7-8 iterations for research, synthesize past iteration 8"
- `_maybe_inject_tool_hint`: resolve_symbol nudge at iteration >=3, budget warning at iteration 8
- Search_code special case in run() for summary building + `[TIP: Found symbols...]` nudge
- 3-tier budget injection on every tool response (`remaining <=2`: synthesize NOW, `<=5`: wrapping up, else: just iteration count)
- Single-pass tool execution loop (execute → build evidence → append Part)

## What was added after 6017e4d that we're removing

- `CRITIQUE_PHASE`, `MIN_TOOL_CALLS_FOR_CRITIQUE`, `CRITIQUE_PROMPT` — adversarial mid-loop injection
- `self._original_prompt` / `[QUESTION: ...]` prefix on tool responses — question reiteration
- `self._files_read`, `self._symbols_resolved` — exploration tracking fields
- Decision-table system prompt (replaced per-tool paragraphs)
- Two-pass tool execution loop (collect results first, then build parts)

## Checklist

### Step 1: Restore constants and remove CRITIQUE
- [ ] `MAX_ITERATIONS = 12` (currently 14)
- [ ] `SYNTHESIS_PHASE = 10` (currently 12)
- [ ] Delete `CRITIQUE_PHASE`, `MIN_TOOL_CALLS_FOR_CRITIQUE`, `CRITIQUE_PROMPT`

### Step 2: Restore system prompt
- [ ] Replace `SYSTEM_PROMPT_TEMPLATE` with the 6017e4d version (per-tool paragraphs, good/bad query examples, "7-8 iterations" budget text)

### Step 3: Clean up __init__ and run() state
- [ ] Remove `self._files_read`, `self._symbols_resolved`, `self._original_prompt` from `__init__`
- [ ] Remove their `.clear()` calls and assignments from `run()`
- [ ] Keep `self._resolve_symbol_used` (used by hint system, was in original)

### Step 4: Simplify _execute_tool
- [ ] Remove `_files_read.add()` and `_symbols_resolved.add()` tracking
- [ ] Keep `_resolve_symbol_used = True` for resolve_symbol (was in original)
- [ ] Keep delegating to `self._tool_registry.execute(name, args)`

### Step 5: Restore _maybe_inject_tool_hint
- [ ] Restore the two-hint version from 6017e4d:
  - resolve_symbol nudge at iteration >= 3 (same as current)
  - Iteration-8 budget warning: "You are at iteration 8/12. Review your collected evidence..."

### Step 6: Restore the run() tool execution loop
- [ ] Remove the CRITIQUE_PHASE injection block
- [ ] Restore the original single-pass tool execution loop from 6017e4d:
  - Keep search_code special case (for summary + TIP nudge) — was in original
  - Keep 3-tier budget injection on every tool response — was in original
  - Keep hint injection via `_maybe_inject_tool_hint` — was in original
  - Remove `[QUESTION: ...]` prefix injection — was NOT in original
  - Remove two-pass approach — was NOT in original
- [ ] Return `QueryResult` (not `AgentResult`) with `strategy_name=self.name` — this is the only new thing to keep

### Step 7: Change `auto_select` in strategy.py
- [ ] Change `auto_select()` to always return `"single"` (remove word count + regex heuristic)

### Step 8: Verify
- [ ] `ruff check src/` — no lint errors
- [ ] `pytest` — all tests pass, fix any failures in `test_agent.py`

## Verification

1. `ruff check src/` passes
2. `pytest` passes (all tests including `test_multi_agent.py`)
3. Manual: run a query and verify in logs:
   - Max 12 iterations, synthesis at 10
   - No CRITIQUE_PROMPT injection
   - No `[QUESTION: ...]` in tool responses
   - Per-tool paragraph system prompt
   - `mode=auto` routes to "single"
   - `mode=multi` still works
