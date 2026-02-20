"""Classic agent loop: original 6017e4d behavior as a strategy."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from google import genai
from google.genai import types

from indiseek import config
from indiseek.agent.loop import _error_hint, _extract_usage, create_agent_loop
from indiseek.agent.strategy import (
    EvidenceStep,
    QueryResult,
    ToolRegistry,
    UsageStats,
    build_tool_registry,
    strategy_registry,
)
from indiseek.storage.sqlite_store import SqliteStore
from indiseek.tools.read_map import read_map
from indiseek.tools.search_code import (
    CodeSearcher,
    QueryCache,
    format_results,
    strip_file_paths,
    summarize_results,
)

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 12
SYNTHESIS_PHASE = 10  # iteration index where we force text-only (0-based)

SYSTEM_PROMPT_TEMPLATE = """\
You are a codebase research agent. Your job is to answer questions about a codebase \
by using the tools available to you.

## Repository map
The top-level directory structure and file summaries are shown below. \
Use this to orient yourself — you do NOT need to call read_map() for the full tree.

{repo_map}

## Tool usage

### search_code(query, mode?)
Full-text and semantic search over code chunks. The `query` parameter is a plain search \
string — natural language or identifier names. It does NOT support field filters, boolean \
operators, or any special syntax. To narrow results to a specific file, use read_file instead.

Good queries: `"HMR CSS propagation"`, `"createServer"`, `"module graph invalidation"`
Bad queries:  `"HMR path:src/server.ts"`, `"foo AND bar"`, `"createServer file:index.ts"`

Modes: "hybrid" (default, best), "semantic" (meaning-based), "lexical" (exact keywords).

### resolve_symbol(symbol_name, action)
Precise cross-reference lookup using SCIP index data. Use this to navigate the call graph \
— it is much more accurate than searching for a symbol name.
- "definition": where the symbol is defined
- "references": all usage sites
- "callers": which functions call this symbol
- "callees": which functions this symbol calls

### read_file(path, start_line?, end_line?)
Read source code with line numbers. Default cap is 200 lines. Use start_line/end_line \
for large files. This is the ONLY way to scope to a specific file — search_code cannot \
filter by file path.

### read_map(path?)
Drill into a subdirectory for file summaries. The full tree is already above — only \
call this if you need detail on a specific directory.

## Strategy
1. **Plan first**: In your first turn, state your research plan. What are you looking \
for and which tools will you use first?
2. **Batch calls**: You MUST call multiple tools in a single turn whenever you need \
independent pieces of information. Every iteration costs budget — combine independent \
lookups into a single turn to maximize what you learn per iteration. Examples:
   - Found a function? Call `resolve_symbol(name, 'definition')` AND `resolve_symbol(name, 'callers')` together.
   - Starting research? Call `search_code(query)` AND `read_map(path)` in the same turn.
   - Reading related files? Call `read_file` multiple times in one turn.
3. **Targeted search**: Start with 1-2 targeted searches to find relevant files and symbol names.
4. **Switch to resolve_symbol early**: After your initial 1-2 searches, STOP searching and \
switch to `resolve_symbol` to navigate the call graph. It gives you precise definitions, \
references, callers, and callees — far more reliable than searching for symbol names. \
This is your primary navigation tool after the initial discovery phase.
5. **Cite evidence**: Always cite specific file paths and line numbers in your answer.

### Example: Parallel Research
If asked "How is the dev server created?", a good first turn might be:
- Thought: "I'll start by searching for 'createServer' and also check the main server file."
- Call: `search_code(query='createServer')`
- Call: `read_file(path='src/node/server/index.ts', start_line=1, end_line=100)`

## Budget
You have {max_iterations} iterations. Each iteration is one round of tool calls. \
Plan to use at most 7-8 iterations for research, then synthesize your answer. \
If you're past iteration 8, stop researching and write your answer with what you have.

Be thorough but efficient. Don't read entire files when a targeted search suffices. \
Don't repeat a search you've already done. Synthesize your findings into a clear, \
structured answer with evidence."""


class ClassicAgentLoop:
    """Original agent loop behavior (6017e4d) as a strategy.

    Differences from the current 'single' strategy (AgentLoop):
    - 12 iterations max (not 14), synthesis at 10 (not 12)
    - Per-tool paragraph system prompt with good/bad query examples
    - Budget text: "7-8 iterations" with tighter wrap-up thresholds
    - Two-hint system: resolve_symbol nudge + iteration-8 budget warning
    - 3-tier budget injection on every tool response (not just last 4 iters)
    - No CRITIQUE_PHASE, no question reiteration, no exploration tracking
    """

    name = "classic"

    def __init__(
        self,
        store: SqliteStore,
        repo_path: Path,
        code_searcher: CodeSearcher,
        api_key: str | None = None,
        model: str | None = None,
        repo_id: int = 1,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._store = store
        self._repo_path = repo_path
        self._searcher = code_searcher
        self._repo_id = repo_id
        self._client = genai.Client(api_key=api_key or config.GEMINI_API_KEY)
        self._model = model or config.GEMINI_MODEL
        # Per-run caches and state (reset at start of each run())
        self._file_cache: dict[str, str] = {}
        self._query_cache = QueryCache()
        self._resolve_cache: dict[tuple[str, str], str] = {}
        self._resolve_symbol_used: bool = False
        # Build tool registry if not provided
        self._tool_registry = tool_registry or build_tool_registry(
            store, code_searcher, repo_id,
            file_cache=self._file_cache,
            query_cache=self._query_cache,
            resolve_cache=self._resolve_cache,
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt with the top-level repo map baked in."""
        repo_map = read_map(self._store, repo_id=self._repo_id)
        return SYSTEM_PROMPT_TEMPLATE.format(
            repo_map=repo_map,
            max_iterations=MAX_ITERATIONS,
        )

    def _execute_tool(self, name: str, args: dict) -> str:
        """Execute a tool via the registry, tracking resolve_symbol usage."""
        if name == "resolve_symbol":
            self._resolve_symbol_used = True
        return self._tool_registry.execute(name, args)

    def _maybe_inject_tool_hint(self, iteration: int) -> str | None:
        """Return a hint nudging the model toward better behavior based on progress."""
        hints = []
        if iteration >= 3 and not self._resolve_symbol_used:
            hints.append(
                "You haven't used resolve_symbol yet. It provides precise "
                "cross-reference data (definition, references, callers, callees) and "
                "is more accurate than searching for symbol names. Try it now."
            )

        if iteration == 8:
            hints.append(
                "You are at iteration 8/12. Review your collected evidence. "
                "If you have enough to answer, synthesize now. Otherwise, focus "
                "on the single most critical piece of information remaining."
            )

        if not hints:
            return None

        return "\n[HINT: " + " ".join(hints) + "]"

    def run(
        self,
        prompt: str,
        on_progress: Callable[[dict], None] | None = None,
    ) -> QueryResult:
        """Run the agent loop until a text answer is produced or max iterations reached."""
        logger.info("Classic agent run started: %r", prompt[:120])
        run_t0 = time.perf_counter()
        evidence: list[EvidenceStep] = []
        usage = UsageStats()
        tool_call_count = 0
        self._file_cache.clear()
        self._query_cache.clear()
        self._resolve_cache.clear()
        self._resolve_symbol_used = False

        logger.debug("Building system prompt (includes read_map)...")
        t0 = time.perf_counter()
        system_prompt = self._build_system_prompt()
        logger.debug("System prompt built: %d chars (%.3fs)", len(system_prompt), time.perf_counter() - t0)

        gemini_decls = self._tool_registry.get_gemini_declarations()
        tools = [types.Tool(function_declarations=gemini_decls)]
        research_config = types.GenerateContentConfig(
            tools=tools,
            system_instruction=system_prompt,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
        )
        synthesis_config = types.GenerateContentConfig(
            tools=tools,
            system_instruction=system_prompt,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="NONE")
            ),
        )

        contents: list[types.Content] = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )
        ]

        for iteration in range(MAX_ITERATIONS):
            # Force synthesis phase: no more tool calls allowed
            if iteration >= SYNTHESIS_PHASE:
                gen_config = synthesis_config
                if iteration == SYNTHESIS_PHASE:
                    logger.info("--- Iteration %d/%d (SYNTHESIS PHASE — tools disabled) ---",
                                iteration + 1, MAX_ITERATIONS)
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_text(
                            text="You have gathered enough evidence. Synthesize your "
                            "answer now. No more tool calls are available."
                        )],
                    ))
                else:
                    logger.info("--- Iteration %d/%d (synthesis) ---",
                                iteration + 1, MAX_ITERATIONS)
            else:
                gen_config = research_config
                logger.info("--- Iteration %d/%d ---", iteration + 1, MAX_ITERATIONS)

            t0 = time.perf_counter()
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=gen_config,
            )
            llm_elapsed = time.perf_counter() - t0
            usage.add(*_extract_usage(response))

            # Append the model's response to conversation history
            model_content = response.candidates[0].content
            contents.append(model_content)

            # Check if model returned function calls
            if not response.function_calls:
                # Model returned a text answer — we're done
                answer = response.text or "(no answer)"
                total = time.perf_counter() - run_t0
                logger.info(
                    "LLM returned final answer: %d chars (LLM %.2fs, total %.2fs)",
                    len(answer), llm_elapsed, total,
                )
                return QueryResult(
                    answer=answer, evidence=evidence,
                    metadata={"usage": usage.to_dict(self._model)},
                    strategy_name=self.name,
                )

            # Log what the model wants to call
            call_names = [c.name for c in response.function_calls]
            logger.info(
                "LLM requested %d tool call(s): %s (LLM %.2fs)",
                len(call_names), ", ".join(call_names), llm_elapsed,
            )

            # Execute each function call and build response parts
            remaining = MAX_ITERATIONS - iteration - 1
            tool_call_count += len(response.function_calls)
            fn_response_parts: list[types.Part] = []
            for call in response.function_calls:
                args = dict(call.args) if call.args else {}
                logger.info("  -> %s(%s)", call.name, ", ".join(f"{k}={v!r}" for k, v in args.items()))

                try:
                    # Special case for search_code to get raw results for summary
                    if call.name == "search_code":
                        query = strip_file_paths(args["query"])
                        mode = args.get("mode", "hybrid")
                        cached = self._query_cache.get(query)
                        if cached is not None:
                            result = f"[Cache hit — similar query already executed]\n{cached}"
                            summary = f"Search (Cache Hit): {query}"
                        else:
                            results = self._searcher.search(query, mode=mode, limit=10)
                            result = format_results(results, query)
                            self._query_cache.put(query, result)
                            summary = f"Search: {query} -> {summarize_results(results)}"
                            # Contextual suggestion: nudge toward resolve_symbol
                            if not self._resolve_symbol_used and results:
                                sym_names = list(dict.fromkeys(
                                    r.symbol_name for r in results if r.symbol_name
                                ))[:5]
                                if sym_names:
                                    result += (
                                        f"\n[TIP: Found symbols: {', '.join(sym_names)}. "
                                        "Use resolve_symbol to get precise definitions, "
                                        "callers, and callees instead of more searches.]"
                                    )
                    else:
                        result = self._execute_tool(call.name, args)
                        # Build summary based on tool
                        if call.name == "read_file":
                            summary = f"Read {args.get('path', '?')}"
                            if "start_line" in args:
                                summary += f" (lines {args['start_line']}-{args.get('end_line', '')})"
                        elif call.name == "resolve_symbol":
                            first_line = result.splitlines()[0] if result else ""
                            summary = f"Symbol: {args.get('symbol_name', '?')} ({args.get('action', '?')}) -> {first_line}"
                        elif call.name == "read_map":
                            summary = f"Map: {args.get('path', 'root')}"
                        else:
                            summary = result[:100] + "..." if len(result) > 100 else result
                except Exception as e:
                    result = f"Error: {e}"
                    result += _error_hint(call.name, args, str(e))
                    logger.error("  tool error: %s: %s", call.name, e)
                    summary = f"Error: {e}"

                # Truncate long results to stay within context limits
                if len(result) > 15000:
                    logger.debug("  truncating result from %d to 15000 chars", len(result))
                    result = result[:15000] + "\n... (truncated)"

                # 3-tier budget injection on every tool response
                if remaining <= 2:
                    result += (
                        f"\n[Iteration {iteration + 1}/{MAX_ITERATIONS}, "
                        f"{tool_call_count} tool calls used"
                        " — stop researching and synthesize your answer NOW]"
                    )
                elif remaining <= 5:
                    result += (
                        f"\n[Iteration {iteration + 1}/{MAX_ITERATIONS}, "
                        f"{tool_call_count} tool calls used"
                        " — start wrapping up research]"
                    )
                else:
                    result += (
                        f"\n[Iteration {iteration + 1}/{MAX_ITERATIONS}, "
                        f"{tool_call_count} tool calls used]"
                    )

                # Inject resolve_symbol hint if applicable
                hint = self._maybe_inject_tool_hint(iteration)
                if hint:
                    result += hint

                evidence.append(
                    EvidenceStep(
                        tool=call.name,
                        args=args,
                        summary=summary,
                    )
                )

                if on_progress is not None:
                    on_progress({
                        "step": "query",
                        "iteration": iteration + 1,
                        "tool": call.name,
                        "args": args,
                        "summary": summary,
                    })

                fn_response_parts.append(
                    types.Part.from_function_response(
                        name=call.name,
                        response={"result": result},
                    )
                )

            # Send function results back to the model
            contents.append(
                types.Content(role="user", parts=fn_response_parts)
            )

        # Max iterations reached
        total = time.perf_counter() - run_t0
        logger.warning("Max iterations (%d) reached without final answer (%.2fs)", MAX_ITERATIONS, total)
        return QueryResult(
            answer="Agent reached maximum iterations without producing a final answer. "
            "Partial evidence has been collected.",
            evidence=evidence,
            metadata={"usage": usage.to_dict(self._model)},
            strategy_name=self.name,
        )


# ---------------------------------------------------------------------------
# Strategy registration
# ---------------------------------------------------------------------------


def _create_classic_strategy(
    repo_id: int = 1,
    store: SqliteStore | None = None,
    repo_path: Path | None = None,
    api_key: str | None = None,
    model: str | None = None,
    **_kwargs,
) -> ClassicAgentLoop:
    """Factory function for the 'classic' strategy.

    Reuses create_agent_loop() for backend setup (SQLite, LanceDB, Tantivy),
    then wraps the result's components in a ClassicAgentLoop.
    """
    # Use the shared factory to set up backends, then extract what we need
    agent_loop = create_agent_loop(
        store=store, repo_path=repo_path, api_key=api_key, model=model, repo_id=repo_id,
    )
    return ClassicAgentLoop(
        store=agent_loop._store,
        repo_path=agent_loop._repo_path,
        code_searcher=agent_loop._searcher,
        api_key=api_key,
        model=model,
        repo_id=repo_id,
    )


def register_classic_strategy() -> None:
    """Register the classic strategy with the global registry."""
    strategy_registry.register("classic", _create_classic_strategy)


# Auto-register on import
register_classic_strategy()
