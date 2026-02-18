# Multi-Agent Architectures for Code Research: Survey

Research compiled Feb 2026. Focused on multi-agent patterns applicable to codebase research (Indiseek's use case).

## Systems Surveyed

### 1. PRISM — Multi-Hop QA (arXiv 2510.14278)

Three agents: Analyzer, Selector, Adder. Precision-recall loop.

- **Analyzer**: Generates sub-queries from original question
- **Selector**: Picks relevant documents for each sub-query
- **Adder**: Decides if enough info has been gathered; if not, triggers more sub-queries
- **Performance**: 90.9% recall on HotpotQA
- **Key insight**: Precision-recall loop prevents both under-exploration and over-exploration

### 2. MA-RAG — Multi-Agent RAG (arXiv 2505.20096)

Four agents with graph state: Planner, Step Definer, Extractor, QA Agent.

- **Planner**: Decomposes query into chain-of-thought reasoning steps
- **Step Definer**: Translates each step into retrieval queries
- **Extractor**: Filters retrieved docs to relevant passages only
- **QA Agent**: Generates answer from extracted passages
- **Models**: LLaMA3-8B/70B + GPT-4o-mini
- **State**: Shared LangGraph state (`step_answers`, `step_docs_ids`, `step_notes`, `plan_summary`)

**Ablation results (70B model)**:

| Config | HotpotQA EM | 2WikimQA EM |
|--------|-------------|-------------|
| Full MA-RAG | 50.7 | 43.1 |
| w/o Extractor | 43.4 | 38.2 |
| w/o Planner | 36.2 | 26.4 |

Key finding: Removing the Planner drops performance by ~14 points. The Extractor adds ~7 points by filtering noise from retrieved docs.

### 3. Open SWE — LangChain (blog, 2025)

Four agents: Manager → Planner → Programmer → Reviewer.

- Sequential pipeline with human-in-the-loop at Planner stage
- Context engineering was their "biggest challenge"
- Subagents isolate context from main agent to avoid "context bloat"
- No quantitative benchmarks published

### 4. Microsoft Code Researcher (arXiv 2506.11060, May 2025)

Three-phase single-agent with structured memory: Analysis → Synthesis → Validation.

- **Analysis**: Multi-path exploration, issues multiple search actions simultaneously
- **Synthesis**: Filters irrelevant memory, generates patches
- **Validation**: Checks patches with external tools
- **Memory**: List of (action, result) pairs per reasoning step
- **Performance**: 58% crash resolution vs SWE-agent's 37.5% (kBenchSyz)
- **Exploration depth**: 10 files/trajectory vs SWE-agent's 1.33

### 5. VeriMAP — Verification-Aware Planning (arXiv 2510.17109, Oct 2024)

Four roles: Planner (gpt-4.1), Executor (gpt-4o-mini), Verifier (gpt-4o-mini), Coordinator.

- **Key innovation**: Planner generates both subtask DAG and per-subtask verification functions
- **Verification types**: Python assertions (~14/task) + natural language criteria (~7/task)
- **Coordination**: Topological sort → execute → verify (3 retries) → replan (5 cycles)
- **Performance**: +9.46% on BigCodeBench-Hard, +9.2% on Olympiads
- **Key insight**: Planner-generated verification catches nuanced errors generic verifiers miss

### 6. Anthropic Multi-Agent Research (blog, June 2025)

Orchestrator-worker pattern: Opus 4 lead + Sonnet 4 subagents.

- **Performance**: 90.2% improvement over single-agent Opus 4
- **Token usage explains 80% of performance variance** (BrowseComp eval)
- **Parallelization**: 3-5 subagents in parallel, each with 3+ parallel tool calls
- **Latency**: Research time cut by up to 90%
- **Cost**: ~15x more tokens than single-agent
- **Scaling rules**: Simple = 1 agent/3-10 calls, comparison = 2-4 agents/10-15 calls, complex = 10+ agents
- **Extended thinking**: Used for planning before acting, evaluating results after tool calls

### 7. Magentic-One (arXiv 2411.04468, Nov 2024)

Orchestrator + 4 specialized agents (WebSurfer, FileSurfer, Coder, ComputerTerminal).

- **Dual-ledger**: Task Ledger (facts, guesses, plan) + Progress Ledger (progress, assignments)
- **Outer loop**: Manages plan; inner loop: manages progress per step
- **Self-reflection**: If no progress → update Task Ledger, create new plan
- **Model-agnostic**: Default GPT-4o, can use different models per agent
- **Performance**: Competitive on GAIA, AssistantBench, WebArena without per-task customization

## LangChain Pattern Comparison

From "Choosing the Right Multi-Agent Architecture" (LangChain blog, 2025):

| Pattern | Best For | Token Efficiency |
|---------|----------|-----------------|
| **Subagents** | Parallel domains, centralized control | Strong (context isolation) |
| **Skills** | Lightweight specialization | Poor at scale (context accumulates) |
| **Handoffs** | Sequential workflows, state transitions | Moderate |
| **Router** | Distinct verticals, parallel dispatch | Strong |

Multi-domain query (2K tokens/domain): Subagents use ~9K tokens total, Skills use ~15K (67% more).

## Key Metrics Across Systems

| System | Performance Gain | Token Cost | Exploration Depth |
|--------|-----------------|------------|-------------------|
| Anthropic | +90.2% | 15x | 3-5 parallel agents |
| Code Researcher | +20.5pp vs SWE-agent | Not disclosed | 10 files/trajectory |
| VeriMAP | +9.46% | Moderate (retries) | DAG with replanning |
| MA-RAG | +14pp vs no-planner | Multi-loop | Planner-guided |
| PRISM | 90.9% recall | Precision-recall balanced | Iterative sub-queries |

## References

1. PRISM: arXiv 2510.14278
2. MA-RAG: arXiv 2505.20096
3. Open SWE: blog.langchain.com/introducing-open-swe
4. Code Researcher: arXiv 2506.11060
5. VeriMAP: arXiv 2510.17109
6. CodeSIM: arXiv 2502.05664
7. LangChain Multi-Agent Patterns: blog.langchain.com/choosing-the-right-multi-agent-architecture
8. Anthropic Research System: anthropic.com/engineering/multi-agent-research-system
9. Magentic-One: arXiv 2411.04468
