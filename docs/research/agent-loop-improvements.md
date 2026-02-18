# Agent Loop Improvements: Research & Plan

## Problem Statement

Indiseek's agent loop produces ~80% quality answers compared to unconstrained deep research (Claude Code subagent doing 46 tool calls over 3m45s). The gap is in edge cases, internal propagation mechanics, and minor inaccuracies. The agent has the right data — the index contains everything needed — but the loop doesn't explore deeply enough or verify what it finds.

Concrete example: querying "how does Vite CSS HMR work?" the agent missed CSS Modules as a behavioral variant, got the `<link>` tag swap mechanism slightly wrong, and didn't explore the module graph propagation logic. All of this was findable with the tools it had.

## Current Architecture

The loop is a basic tool-calling agent (`src/indiseek/agent/loop.py`):

- **Model**: `gemini-2.0-flash`
- **Max iterations**: 14 (synthesis forced at iteration 12, critique at 9)
- **System prompt**: Role + full repo map + tool docs + strategy hints + budget reminders
- **Tools**: `search_code` (hybrid RRF), `resolve_symbol` (SCIP + tree-sitter), `read_file`, `read_map`
- **Quality mechanisms**: CRITIC phase at iteration 9 (asks agent to verify claims before synthesis). Question reiteration (first tool response per turn). Budget warnings in last 4 iterations. resolve_symbol nudge at iteration 3 if unused.
- **Stopping**: LLM returns text (no function calls) or hits iteration 14.

The loop is single-pass: research → synthesize → done. No backtracking, no planning phase, no verification.

## Root Causes of the Quality Gap

1. **No planning phase**: The agent jumps into searching without decomposing the question. "How does CSS HMR work?" has at least 3 sub-questions (detection, propagation, application) but the agent treats it as one search.

2. **No self-critique**: The agent never asks "what did I miss?" After gathering evidence, it synthesizes immediately. There's no step where it reviews its evidence against the question and identifies gaps.

3. **Shallow exploration**: 22 tool calls sounds like a lot, but many are redundant or poorly targeted. The agent doesn't track what it's already covered or what areas remain unexplored.

4. **No verification**: Claims in the final answer aren't checked against the code. The `<link>` tag inaccuracy happened because the agent read the right file but misremembered the mechanism.

5. **Iteration budget pressure**: The agent gets budget warnings starting at iteration 8, pushing it to wrap up. For complex questions, 12 iterations with budget anxiety at 8 isn't enough depth.

## SOTA Techniques (Filtered for Relevance)

Full research surveyed ~40 papers and projects from 2024-2026. What follows is filtered to techniques that are (a) implementable in our architecture, (b) likely to close the specific quality gaps we observed, and (c) worth the complexity.

### Tier 1: High Impact, Low Effort

**1. Question Decomposition (Plan-and-Execute)**

Before any tool calls, have the LLM decompose the question into sub-questions. Each sub-question gets its own research phase.

- Source: Plan-and-Execute pattern, widely used in LangChain/LangGraph agents
- Why it helps: Forces breadth. "How does CSS HMR work?" becomes: (1) How are CSS changes detected? (2) How is the update propagated through the module graph? (3) How does the browser apply the update? (4) Are there different paths for different CSS types?
- Implementation: Add a planning turn before the research loop. Parse sub-questions. Track which ones have been addressed.

**2. Self-Critique Before Synthesis (Reflexion-lite)**

After the research phase, before writing the final answer, inject a critique step: "Review your evidence against the original question. What aspects haven't you covered? What claims are you making without direct code evidence?"

- Source: Reflexion (Shinn et al., NeurIPS 2023), Chain-of-Verification (CoVe, Meta 2023)
- Why it helps: The CSS Modules gap would have been caught. The agent read `css.ts` and saw `isSelfAccepting` logic but didn't flag it as a distinct behavioral path. A critique step would surface this.
- Implementation: At synthesis phase, instead of immediately asking for the answer, first ask the LLM to list (a) what it found, (b) what it didn't check, (c) what claims lack direct evidence. Then give it 2-3 more tool calls to fill gaps before final synthesis.

**3. Improved Tool Documentation with Examples**

The current tool descriptions are functional but don't teach strategy. Adding worked examples helps the model make better tool choices.

- Source: OpenAI prompt engineering guide, AVATAR (NeurIPS 2024)
- Why it helps: The agent made 22 tool calls but several were redundant or poorly targeted. Better tool docs reduce wasted iterations.
- Implementation: Add 2-3 concrete examples to each tool declaration showing when and why to use it.

**4. Exploration Tracking**

Track what the agent has explored (files read, symbols resolved, search queries) and surface it. This prevents redundant work and highlights gaps.

- Source: Microsoft Code Researcher (2025) — tracks exploration breadth, averages 10 files per trajectory vs SWE-agent's 1.33
- Why it helps: The agent can see what it hasn't done yet. "You've searched 3 times and read 2 files but haven't used resolve_symbol on any of the symbols you found."
- Implementation: Maintain a running summary of tools used, injected into each turn. Already partially done via evidence trail, but not surfaced to the LLM.

### Tier 2: Medium Impact, Medium Effort

**5. Post-Answer Verification**

After generating the answer, extract factual claims and verify each against the codebase using tool calls.

- Source: SAFE (Search-Augmented Factuality Evaluator, Wei et al. 2024), multi-agent verification patterns
- Why it helps: The `<link>` tag inaccuracy (saying "updates href" instead of "clone and swap") would be caught by re-reading the relevant code section and comparing to the claim.
- Implementation: Parse the answer for claims like "function X does Y" or "file A contains B". Run `resolve_symbol` or `read_file` to verify. Correct or flag unverified claims. This adds 3-5 iterations but significantly improves accuracy.

**6. Dynamic Iteration Budget**

Instead of a fixed 12-iteration cap, let the critique step determine if more research is needed. Simple queries should finish in 5-6 iterations; complex ones might need 20.

- Source: General agent design pattern. MIT finding (2024): self-correction works with reliable external feedback.
- Why it helps: Budget pressure at iteration 8 forces premature synthesis. Complex questions need more room.
- Implementation: Start with 15 max iterations. After critique step, if significant gaps identified and budget allows, extend by 5 iterations. Hard cap at 25 to prevent runaway costs.

**7. Search Mode Guidance**

The agent has three search modes (semantic, lexical, hybrid) but the system prompt doesn't teach when to use each. The model defaults to hybrid for everything.

- Source: Hybrid search research consensus (2025): neither lexical nor semantic alone is sufficient, but knowing when to use each improves results 15%.
- Why it helps: Searching for "updateStyle" semantically dilutes the results. Lexical mode would find it directly. Searching for "how CSS changes are applied in the browser" benefits from semantic mode.
- Implementation: Add search mode guidance to system prompt: lexical for exact identifiers, semantic for concepts, hybrid as default.

### Tier 3: High Impact, High Effort (Future)

**8. Multi-Agent Architecture**

Split into Planner → Retriever → Synthesizer → Verifier agents, each with a narrow scope.

- Source: PRISM (multi-hop QA), Open SWE (LangChain), MA-RAG
- Why it helps: Each agent is focused and less likely to lose track. The planner decomposes, the retriever executes without synthesis pressure, the synthesizer works from clean evidence, the verifier checks.
- Trade-off: Significant complexity increase. Multiple LLM calls per phase. Only worth it if Tier 1-2 improvements plateau.

**9. LATS (Language Agent Tree Search)**

Model tool-calling as a search tree. Branch on different retrieval strategies, evaluate each branch, backtrack from dead ends.

- Source: LATS (ICML 2024) — 92.7% on HumanEval with GPT-4
- Why it helps: For ambiguous queries, the agent could try both "search for CSS HMR" and "resolve_symbol on handleCSSUpdate" as parallel branches, scoring each for relevance.
- Trade-off: High implementation complexity. Requires scoring function for intermediate states. Best reserved for when simpler approaches plateau.

**10. Episodic Memory**

Store past queries, trajectories, and answers. For new queries, retrieve similar past queries and include their findings as context.

- Source: A-MEM (ACL 2025), Letta/MemGPT
- Why it helps: If someone already asked "how does Vite HMR work?" the CSS-specific query can build on that answer rather than starting from scratch. Reduces redundant exploration.
- Trade-off: Requires memory management, retrieval, and staleness handling. Worth it at scale.

## Implementation Plan

### Phase 1: Question Decomposition + Self-Critique

Changes to `loop.py`:

1. **Planning turn** (iteration 0): Inject a user message after the initial prompt: "Before using any tools, decompose this question into 2-5 specific sub-questions that you need to answer. List them." Parse the response to extract sub-questions. Store them as a checklist.

2. **Sub-question tracking**: After each tool call batch, inject the checklist with completion status: "Sub-questions: [x] How are CSS changes detected? [ ] How does the module graph propagation work? [ ] How does the browser apply updates?"

3. **Critique turn** (before synthesis): At iteration N-3 (where N is max iterations), inject: "Review your evidence against each sub-question. For each: (a) mark as answered or unanswered, (b) list any claims you'd make without direct code evidence. Then use your remaining tool calls to fill the most critical gaps."

4. **Increase max iterations** from 12 to 16. Move synthesis phase from 10 to 14.

### Phase 2: Tool Docs + Search Guidance

1. **Add worked examples** to each tool declaration in `loop.py:104-187`. Show the tool being used correctly with realistic inputs and outputs.

2. **Add search mode guidance** to system prompt: table of when to use semantic vs lexical vs hybrid, with examples.

3. **Surface exploration state**: After each turn, append a brief summary of what's been explored: files read, symbols resolved, searches run. Let the LLM see its own coverage.

### Phase 3: Post-Answer Verification

1. **Claim extraction**: After the agent produces its answer, send it back with: "Extract every factual claim from your answer that references specific code (function names, file paths, behaviors). List them as checkable assertions."

2. **Verification loop**: For each claim, run the appropriate tool (resolve_symbol for "X calls Y", read_file for "file Z contains W"). Mark each as verified or unverified.

3. **Revision**: If unverified claims found, either correct them or flag them in the output.

4. This phase requires 3-5 additional iterations. Adjust max iterations to 20, with verification starting after synthesis.

### Phase 4: Dynamic Budget + Memory (Future)

1. **Dynamic budget**: Start at 16 iterations. After critique, if >2 sub-questions unanswered, extend by 5. Hard cap at 25.

2. **Episodic memory**: Store (query, sub-questions, evidence, answer, verification_results) in SQLite. Before new queries, retrieve top-3 similar past queries by embedding similarity. Include as context.

## Key Papers & Sources

- Reflexion: Language Agents with Verbal Reinforcement Learning (Shinn et al., NeurIPS 2023)
- LATS: Language Agent Tree Search Unifies Reasoning, Acting, and Planning (Zhou et al., ICML 2024)
- Chain-of-Verification Reduces Hallucination in LLMs (Dhuliawala et al., Meta 2023)
- SAFE: Search-Augmented Factuality Evaluator (Wei et al., 2024)
- Code Researcher: Deep Research Agent for Large Systems Code (Microsoft Research, 2025)
- AVATAR: Optimizing LLM Agents via Contrastive Reasoning (NeurIPS 2024)
- Multi-Agent Reflexion: Collaborative Problem-Solving (2025)
- A-MEM: Agentic Memory for LLM Agents (ACL 2025)
- mini-SWE-agent (100 lines, 74%+ on SWE-bench) — argues against over-scaffolding
- Focused ReAct: question reiteration + early-stop on repetitive actions (530% accuracy gains)

## Post-Denoising Findings (Feb 2026)

After implementing Tier 1 improvements (question reiteration, exploration tracking, CRITIC phase, budget increase to 20), quality dropped from ~80% to ~65% on the CSS HMR eval. The stacked scaffolding overwhelmed Gemini Flash with noise — 34 tool calls, 11 cache hits, 2 irrelevant file reads.

A denoising pass (reduce to 14 iterations, question reiteration once per turn not per tool response, remove exploration gaps, budget warnings only in last 4 iterations, hints once per turn) cleaned up the noise but didn't recover quality. The eval scored ~55-60% with 22 tool calls and 1 cache hit. Fewer wasted iterations, but the agent still missed the same things.

### The "Gravity Well" Problem

The core issue is **not** scaffolding noise — it's search strategy. The agent falls into a "subsystem gravity well": initial searches find client-side code (updateStyle, client.ts), and all subsequent exploration stays client-side. It never crosses to the server-side HMR pipeline (handleHMRUpdate, propagateUpdate, server/hmr.ts), which is half the answer.

This happens because:
- Initial searches return results dominated by one subsystem
- The agent follows those results deeper (resolve_symbol on client symbols, read_file on client files)
- Nothing forces it to step back and ask "what other subsystems are involved?"
- The removed exploration gaps feature listed symbol names but pushed breadth indiscriminately — it didn't distinguish "unexplored subsystem" from "low-relevance symbol"

### New Techniques to Consider

**1. Subsystem-Aware Research Planning**

Strengthen the existing question decomposition idea (Tier 1 #1) with explicit subsystem identification. Instead of just "decompose into sub-questions," the planning prompt should say: "Identify the distinct subsystems involved (e.g., server-side detection, module graph propagation, client-side application). Plan research to cover each."

This is different from generic decomposition because it forces the agent to think about architecture before diving into code. The CSS HMR question naturally splits into server (file watching → module graph → WebSocket) and client (message handling → DOM update), but the agent never makes that split.

**2. Mid-Run Directional Nudge**

At ~iteration 5-6, inject a coverage check: "You've been exploring [client-side/server-side/one area]. Have you traced how the [other direction] works?" This is more targeted than the removed exploration gaps (which listed raw symbol names). It checks whether the agent has explored multiple subsystems, not whether it's resolved every symbol it found.

Implementation: track which directories the agent has read files from. If all reads are in the same subtree (e.g., `src/client/`), nudge toward the complement. This requires lightweight directory-level tracking, not symbol-level.

**3. Resolve_symbol Follow-Through**

The agent finds symbols in search results but doesn't follow the call graph. It resolved `updateStyle` definition but never resolved its callers. The existing contextual TIP after search results ("Found symbols: X, Y. Use resolve_symbol...") is being ignored.

Possible fixes:
- Make the TIP more directive: instead of listing symbols, pick the most promising one and suggest a specific action ("Try: resolve_symbol('handleHMRUpdate', 'callees') to trace the update flow")
- After the agent resolves a definition, automatically suggest resolving callers/callees of that same symbol in the next turn's hint
- Track resolve_symbol usage patterns: if the agent only ever does "definition" lookups and never "callers"/"callees", inject a hint about tracing the call graph
