# Tier 1 Implementation Techniques: Deep Research

Distilled findings from SOTA research on each Tier 1 improvement. These are the specific techniques we should use when implementing `docs/plans/agent-loop-tier1.md`.

## 1. Question Decomposition: What Actually Works

### Don't: Naive "list your sub-questions"

The obvious approach — asking the model to list sub-questions — is the weakest. The model produces vague, overlapping sub-questions and doesn't track them well.

### Do: Focused ReAct (Question Reiteration)

The Focused ReAct paper (arXiv:2410.10779) showed **530% accuracy gains** with a dead-simple technique: **restate the original question at the start of every reasoning step**. This prevents context drift as the conversation grows.

Implementation: Don't burn a whole iteration on planning. Instead, prepend the original question to every tool response:

```
[QUESTION: How does Vite hot module reloading work when doing CSS?]
[Iteration 3/20, 8 tool calls used]
```

This is cheaper than a planning turn and addresses the same root cause (the agent losing sight of the question after several tool calls).

### Do: Structured Planning Only for Complex Queries

LangChain's Plan-and-Execute uses a Pydantic `Plan(steps: List[str])` with structured output. The replanner receives `past_steps: List[Tuple[task, result]]` and either updates the plan or returns a final response.

Key insight from the research: **planning overhead costs 5-20x more tokens than simple chains**. For simple queries ("where is X defined?") this is pure waste. For complex queries ("how does the CSS HMR system work end-to-end?") it's essential.

Decision: **Adaptive planning**. Spend the planning turn only when the question is complex. Detect complexity heuristically:
- Questions with "how" or "why" → likely need planning
- Questions with specific symbol names → likely don't
- Questions longer than ~15 words → likely need planning

### Do: Inline Sub-Question Tracking

Instead of a separate planning phase, embed the plan in the system prompt. After the model's first response (which typically includes a research plan per the existing "Plan first" instruction), extract it and reinject it as a checklist:

```
[RESEARCH PLAN]
1. [ ] Find where CSS file changes are detected (watcher)
2. [ ] Trace how the update propagates through the module graph
3. [ ] Find how the browser applies the CSS update
4. [x] Identify the entry point (hmr.ts found via search)
```

This uses the evidence trail we already collect to auto-check items.

## 2. Self-Critique: The CRITIC Pattern

### The MIT Verdict

MIT's 2024 TACL paper proved that **intrinsic self-correction (no external feedback) degrades performance**. The model repeats its own errors. Self-critique only works with **external grounding**.

Implication: Our critique step must use tools, not just ask the model to think harder.

### The CRITIC Framework (ICLR 2024)

CRITIC (Gou et al.) is the most relevant pattern for us. It's designed for tool-calling agents:

1. Generate initial answer
2. Prompt: **"What's the problem with the above answer?"**
3. For each identified problem, **execute a tool call to verify**
4. Revise answer based on verified facts

This produced **10-30 percentage point improvements** across QA, code, and math tasks.

### Implementation: Critique-with-Tools

Instead of a pure text critique ("review your evidence"), the critique prompt should force tool use:

```
PAUSE before writing your final answer. Review your research:

1. List every factual claim you plan to make (e.g., "function X is defined in file Y",
   "A calls B", "the update is sent via WebSocket").
2. For each claim that you haven't directly verified with a tool call, make the
   verification call NOW. Use resolve_symbol to check definitions/callers,
   read_file to confirm implementations.
3. Note any claims you cannot verify — flag them as uncertain in your answer.

You have 2-3 more iterations for verification. Be targeted.
```

The key difference from our original plan: the critique step isn't just reflection — it's **verification with tools**. This addresses the MIT finding by providing external feedback (tool results) rather than relying on intrinsic self-correction.

### Chain-of-Verification (CoVe) Insight

Meta's CoVe paper found that the **Factored variant** works best: each verification question is answered **independently**, preventing the model from copying its own hallucinations. In practice for us, this means each verification tool call should focus on one specific claim, not try to verify everything at once.

### When to Skip Critique

Not every query needs verification. Skip it when:
- The agent used fewer than 5 tool calls (simple query, not much to verify)
- The agent already used `resolve_symbol` extensively (high-confidence evidence)
- The answer is short (1-2 paragraphs — few claims to verify)

Implement as a simple heuristic check before injecting the critique prompt.

## 3. Tool Documentation: What the Research Says

### The Critical Finding

Research (arXiv:2505.18135) showed that **tool descriptions alone shift usage by 10x+**. LLMs select tools "solely on natural language descriptions." This means our tool descriptions are the highest-leverage change.

### The Four-Line Pattern

Best tool descriptions follow this structure:
1. **What it does** (first sentence — most important, models may not read further)
2. **What it returns**
3. **When to use it** (positive framing)
4. **When to use something else instead** (redirect, not negation)

### Negative Examples Don't Work

LLMs are bad at negation. "Don't use this for X" is less effective than "For X, use tool Y instead." Frame boundaries positively.

### Concrete Improvements for Our Tools

**search_code** — Current description is 3 lines about what it doesn't support. Flip it:

```
Search code by meaning or keywords. Returns top 10 code chunks ranked by relevance.

Modes:
- "lexical": Use for exact identifiers (updateStyle, handleHMRUpdate, ERR_NOT_FOUND)
- "semantic": Use for concepts ("how CSS changes are applied in the browser")
- "hybrid" (default): Combines both. Best when you're not sure.

For symbol cross-references (who calls X, where is X defined), use resolve_symbol instead.
For reading a specific file you already know about, use read_file instead.
```

**resolve_symbol** — Current description doesn't explain when to use each action:

```
Navigate the code's call graph using precise cross-reference data. Much more accurate
than searching for symbol names — use this as your primary navigation tool after
initial discovery.

Actions:
- "definition": Where is this symbol defined? Start here.
- "references": Where is this symbol used across the codebase?
- "callers": What functions call this symbol? Use to understand usage patterns.
- "callees": What does this function call? Use to trace execution flow downward.

Tip: After finding a symbol via search_code, call resolve_symbol('name', 'definition')
AND resolve_symbol('name', 'callers') together to get the full picture in one turn.
```

**read_file** — Add when to use it:

```
Read source code with line numbers. Default cap is 200 lines.

Use this when you know the file path and need to examine the actual implementation.
This is the ONLY way to scope to a specific file — search_code cannot filter by path.

Tip: Reading the implementation after finding a symbol definition is almost always
more valuable than running another search.
```

### System Prompt: Search Mode Decision Table

Add a decision table that the model can reference:

```
| I have... | Use |
|-----------|-----|
| An exact function/variable name | search_code(query, mode="lexical") |
| A concept or "how does X work" question | search_code(query, mode="semantic") |
| A general first exploration | search_code(query, mode="hybrid") |
| A symbol name and want to trace its usage | resolve_symbol(name, "callers") |
| A file path and want to read the code | read_file(path) |
```

## 4. Exploration Tracking: Selective Surfacing

### The Paradox

Microsoft Code Researcher explores 10 files per trajectory (vs SWE-agent's 1.33) and gets much better results. But research also shows a **negative correlation (r = -0.42) between exploration breadth and efficiency**. Exhaustive reading without targeted validation dilutes signal.

The goal isn't "read more files." It's "know what you haven't checked yet."

### What to Surface (Compressed)

Research consensus: surface **decision-relevant state**, keep mechanical state internal.

**Good** (surface to LLM):
```
[Coverage: 3 searches | 4 files read (hmr.ts, css.ts, client.ts, moduleGraph.ts) |
Symbols: handleHMRUpdate, propagateUpdate, updateStyle |
NOT YET EXPLORED: client-side CSS application, CSS Modules handling]
```

**Bad** (too verbose, pollutes context):
```
Search 1: "CSS HMR update" → 10 results in hmr.ts, css.ts, ...
Search 2: "updateStyle" → 10 results in client.ts, ...
File read 1: hmr.ts lines 1-150, found handleHMRUpdate at line 45...
[continues for 500 tokens]
```

### The "NOT YET EXPLORED" Insight

The most valuable part of exploration tracking isn't what the agent HAS done — it can see that in conversation history. The value is in **surfacing what it HASN'T done**.

Implementation: After each tool call batch, compute the gap:
- Symbols found in search results but not yet resolved
- Files mentioned in search results but not yet read
- Related directories not yet explored

Surface only the gaps:
```
[Symbols found but not resolved: cssPostPlugin, isSelfAccepting, removeStyle]
[Files referenced but not read: packages/vite/src/shared/hmr.ts]
```

This directly addresses our root cause: the agent found `isSelfAccepting` in search results but never investigated it because it didn't realize it hadn't.

### How Much State is Too Much?

Research findings:
- Reasoning models (o1-style) are hurt by excessive in-context state
- Gemini Flash (our model) benefits from explicit state more than reasoning models
- Keep exploration summaries under ~100 tokens per injection
- Inject in tool responses, not system prompt (system prompt is static)

### Implementation: Unresolved Symbols Tracker

Track symbols that appear in search results but haven't been resolved:

```python
# After search_code returns results
for result in results:
    if result.symbol_name:
        self._discovered_symbols.add(result.symbol_name)

# Compute unresolved
resolved_names = {s[0] for s in self._symbols_resolved}
unresolved = self._discovered_symbols - resolved_names

# Surface only if there are unresolved symbols
if unresolved:
    result += f"\n[Unresolved symbols: {', '.join(sorted(unresolved)[:5])}. "
    "Consider using resolve_symbol to investigate these.]"
```

This is more actionable than a generic coverage summary because it tells the agent exactly what to do next.

## 5. Putting It All Together: The Revised Loop Structure

Based on all research, here's the refined loop design:

```
Iteration 0-1:  Initial search + discovery (tools enabled)
                → Question reiterated in every tool response
                → Discovered symbols tracked

Iteration 2-N:  Research phase (tools enabled)
                → Exploration gaps surfaced per turn
                → Unresolved symbols highlighted
                → Search mode guidance in tool descriptions

Iteration N-2:  CRITIC step (tools enabled)
                → "What claims will you make? Verify with tools."
                → Targeted verification calls
                → Only triggered if agent used 5+ tool calls

Iteration N:    Synthesis (tools disabled)
                → "Write your final answer with code citations."
```

Key changes from original plan:
1. **No separate planning turn** — use question reiteration instead (cheaper, proven)
2. **Critique is CRITIC-style** with tool verification, not just reflection
3. **Exploration tracking focuses on gaps** (unresolved symbols, unread files), not coverage counts
4. **Tool docs are restructured** with the four-line pattern and decision table
5. **Adaptive critique** — skip for simple queries

## Sources

- Focused ReAct (arXiv:2410.10779) — question reiteration, 530% accuracy gains
- CRITIC (Gou et al., ICLR 2024) — tool-interactive critiquing, 10-30pp improvement
- CoVe (Dhuliawala et al., Meta 2023) — factored verification prevents hallucination copying
- MIT TACL 2024 — self-correction only works with external feedback
- arXiv:2505.18135 — tool descriptions shift usage 10x+, negation doesn't work
- LangChain Plan-and-Execute — structured planning with Pydantic models
- Microsoft Code Researcher 2025 — 10 files/trajectory, structured memory, scratchpad
- SWE-Search (ICLR 2025) — value function with natural language explanations
- LocAgent — graph-guided exploration with confidence-based stopping
- Google ADK state management — session state with selective surfacing
