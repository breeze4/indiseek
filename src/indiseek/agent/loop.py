"""Agent loop: Gemini tool-calling with scratchpad."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types

from indiseek import config
from indiseek.indexer.lexical import LexicalIndexer
from indiseek.storage.sqlite_store import SqliteStore
from indiseek.storage.vector_store import VectorStore
from indiseek.tools.read_file import format_file_content
from indiseek.tools.read_map import read_map
from indiseek.tools.resolve_symbol import resolve_symbol
from indiseek.tools.search_code import (
    CodeSearcher,
    QueryCache,
    format_results,
    strip_file_paths,
    summarize_results,
)

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 15
SYNTHESIS_PHASE = 13  # iteration index where we force text-only (0-based)

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
Plan to use at most 8-10 iterations for research, then synthesize your answer. \
If you're past iteration 10, stop researching and write your answer with what you have.

Be thorough but efficient. Don't read entire files when a targeted search suffices. \
Don't repeat a search you've already done. Synthesize your findings into a clear, \
structured answer with evidence."""

TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="read_map",
        description="Returns directory structure and file summaries for a subdirectory. "
        "The full repository map is already in the system prompt — use this tool only "
        "to drill into a specific subdirectory for more detail.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Subdirectory path to scope results to.",
                },
            },
        },
    ),
    types.FunctionDeclaration(
        name="search_code",
        description="Hybrid semantic+lexical code search. Returns relevant code chunks "
        "ranked by relevance. The query is a plain search string (natural language or "
        "identifiers). Does NOT support field filters like 'path:', boolean operators, "
        "or special syntax. To scope results to a specific file, use read_file instead.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Plain search query — natural language or code "
                    "identifiers. No special syntax or field filters.",
                },
                "mode": {
                    "type": "string",
                    "description": "Search mode: 'hybrid' (default), 'semantic', or 'lexical'.",
                    "enum": ["hybrid", "semantic", "lexical"],
                },
            },
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="resolve_symbol",
        description="Look up symbol definition, references, callers, or callees using "
        "SCIP cross-references and tree-sitter data.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "symbol_name": {
                    "type": "string",
                    "description": "Name of the symbol to look up.",
                },
                "action": {
                    "type": "string",
                    "description": "What to look up.",
                    "enum": ["definition", "references", "callers", "callees"],
                },
            },
            "required": ["symbol_name", "action"],
        },
    ),
    types.FunctionDeclaration(
        name="read_file",
        description="Read source code from the repository with line numbers. "
        "Output is capped at 200 lines by default. Use start_line and end_line "
        "to read a specific range of a large file.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path within the repository.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line to read (1-based, inclusive). Optional.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line to read (1-based, inclusive). Optional.",
                },
            },
            "required": ["path"],
        },
    ),
]


def _error_hint(tool_name: str, args: dict, error_msg: str) -> str:
    """Return a corrective hint when a tool call fails."""
    hints: list[str] = []

    if tool_name == "search_code":
        query = args.get("query", "")
        # Detect field filter syntax the model keeps trying
        if "path:" in query or "file:" in query:
            hints.append(
                "search_code does not support 'path:' or 'file:' filters. "
                "Use a plain query like search_code(query='your search terms'). "
                "To scope to a specific file, use read_file(path='the/file.ts') instead."
            )
        elif "AND" in query or "OR" in query:
            hints.append(
                "search_code does not support boolean operators. "
                "Use a plain natural language query."
            )
        elif "Syntax Error" in error_msg or "Field does not exist" in error_msg:
            hints.append(
                "search_code only accepts a plain text query string. "
                "Remove special characters like parentheses, colons, and quotes. "
                "Use simple words: e.g. 'updateModuleInfo' not 'updateModuleInfo('."
            )

    if not hints:
        hints.append(
            f"The {tool_name} call failed. Check the arguments and try again "
            "with corrected parameters."
        )

    return "\nHINT: " + " ".join(hints)


@dataclass
class EvidenceStep:
    """One step in the agent's evidence trail."""

    tool: str
    args: dict
    summary: str


@dataclass
class AgentResult:
    """Result from an agent run."""

    answer: str
    evidence: list[EvidenceStep] = field(default_factory=list)


class AgentLoop:
    """Gemini-powered agent loop with tool calling."""

    def __init__(
        self,
        store: SqliteStore,
        repo_path: Path,
        code_searcher: CodeSearcher,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._store = store
        self._repo_path = repo_path
        self._searcher = code_searcher
        self._client = genai.Client(api_key=api_key or config.GEMINI_API_KEY)
        self._model = model or config.GEMINI_MODEL
        # Per-run caches and state (reset at start of each run())
        self._file_cache: dict[str, str] = {}
        self._query_cache = QueryCache()
        self._resolve_cache: dict[tuple[str, str], str] = {}
        self._resolve_symbol_used: bool = False

    def _build_system_prompt(self) -> str:
        """Build system prompt with the top-level repo map baked in."""
        repo_map = read_map(self._store)
        return SYSTEM_PROMPT_TEMPLATE.format(
            repo_map=repo_map,
            max_iterations=MAX_ITERATIONS,
        )

    def _execute_tool(self, name: str, args: dict) -> str:
        """Execute a tool by name with the given arguments."""
        logger.debug("  tool exec: %s(%s)", name, args)
        t0 = time.perf_counter()

        if name == "read_map":
            result = read_map(self._store, path=args.get("path"))
        elif name == "search_code":
            query = strip_file_paths(args["query"])
            mode = args.get("mode", "hybrid")
            cached = self._query_cache.get(query)
            if cached is not None:
                result = (
                    f"[Cache hit — similar query already executed]\n{cached}"
                )
            else:
                results = self._searcher.search(query, mode=mode, limit=10)
                result = format_results(results, query)
                self._query_cache.put(query, result)
        elif name == "resolve_symbol":
            self._resolve_symbol_used = True
            symbol_name = args["symbol_name"]
            action = args["action"]
            cache_key = (symbol_name, action)
            if cache_key in self._resolve_cache:
                logger.debug("  resolve cache hit: %s/%s", symbol_name, action)
                result = f"[Cache hit]\n{self._resolve_cache[cache_key]}"
            else:
                result = resolve_symbol(self._store, symbol_name, action)
                self._resolve_cache[cache_key] = result
        elif name == "read_file":
            file_path = args["path"]
            start_line = args.get("start_line")
            end_line = args.get("end_line")

            # Enforce minimum read window: if range < 100 lines, expand to
            # 150 lines centered on the midpoint of the requested range.
            if start_line is not None and end_line is not None:
                span = end_line - start_line + 1
                if span < 100:
                    mid = (start_line + end_line) // 2
                    start_line = max(1, mid - 75)
                    end_line = start_line + 149
                    logger.debug(
                        "  read_file: expanded range to %d-%d (150 lines)",
                        start_line, end_line,
                    )

            if file_path in self._file_cache:
                logger.debug("  file cache hit: %s", file_path)
                content = self._file_cache[file_path]
                result = format_file_content(content, file_path, start_line, end_line)
            else:
                # Read from SQLite (source of truth)
                content = self._store.get_file_content(file_path)
                if content is None:
                    result = f"Error: File '{file_path}' not found in index."
                    content = None
                else:
                    self._file_cache[file_path] = content
                    result = format_file_content(content, file_path, start_line, end_line)

            # Add implicit symbol definitions found in this range
            if content is not None:
                # If no range, format_file_content uses DEFAULT_LINE_CAP (500)
                from indiseek.tools.read_file import DEFAULT_LINE_CAP
                s = start_line or 1
                e = end_line or min(len(content.splitlines()), DEFAULT_LINE_CAP)

                symbols = self._store.get_symbols_in_range(file_path, s, e)
                if symbols:
                    sym_lines = ["\nSymbols defined in this range:"]
                    for sym in symbols:
                        sym_lines.append(f"  - {sym['name']} ({sym['kind']}) at line {sym['start_line']}")
                    result += "\n" + "\n".join(sym_lines)
        else:
            result = f"Unknown tool: {name}"

        elapsed = time.perf_counter() - t0
        logger.debug("  tool done: %s -> %d chars (%.3fs)", name, len(result), elapsed)
        return result

    def _maybe_inject_tool_hint(self, iteration: int) -> str | None:
        """Return a hint nudging the model toward better behavior based on progress."""
        hints = []
        if iteration >= 3 and not self._resolve_symbol_used:
            hints.append(
                "You haven't used resolve_symbol yet. It provides precise "
                "cross-reference data (definition, references, callers, callees) and "
                "is more accurate than searching for symbol names. Try it now."
            )
        
        if iteration == 10:
            hints.append(
                "You are at iteration 10/15. Review your collected evidence. "
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
    ) -> AgentResult:
        """Run the agent loop until a text answer is produced or max iterations reached."""
        logger.info("Agent run started: %r", prompt[:120])
        run_t0 = time.perf_counter()
        evidence: list[EvidenceStep] = []
        tool_call_count = 0
        self._file_cache.clear()
        self._query_cache.clear()
        self._resolve_cache.clear()
        self._resolve_symbol_used = False

        logger.debug("Building system prompt (includes read_map)...")
        t0 = time.perf_counter()
        system_prompt = self._build_system_prompt()
        logger.debug("System prompt built: %d chars (%.3fs)", len(system_prompt), time.perf_counter() - t0)

        tools = [types.Tool(function_declarations=TOOL_DECLARATIONS)]
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
                return AgentResult(answer=answer, evidence=evidence)

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
                            summary = f"Read {args['path']}"
                            if "start_line" in args:
                                summary += f" (lines {args['start_line']}-{args.get('end_line', '')})"
                        elif call.name == "resolve_symbol":
                            # Extract result count from first line: "Definition of 'foo' (SCIP, 2 result(s)):"
                            first_line = result.splitlines()[0] if result else ""
                            summary = f"Symbol: {args['symbol_name']} ({args['action']}) -> {first_line}"
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

                # Inject iteration budget into the result
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
        return AgentResult(
            answer="Agent reached maximum iterations without producing a final answer. "
            "Partial evidence has been collected.",
            evidence=evidence,
        )


def create_agent_loop(
    store: SqliteStore | None = None,
    repo_path: Path | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> AgentLoop:
    """Create an AgentLoop with default configuration.

    Initializes all necessary backends (SQLite, LanceDB, Tantivy) from config.
    """
    if store is None:
        store = SqliteStore(config.SQLITE_PATH)
        logger.info("SQLite store: %s", config.SQLITE_PATH)

    if repo_path is None:
        repo_path = config.REPO_PATH
    logger.info("Repo path: %s", repo_path)

    # Set up code searcher with available backends
    vector_store = None
    embed_fn = None
    lexical_indexer = None

    # Try to open LanceDB if it exists
    if config.LANCEDB_PATH.exists() and config.GEMINI_API_KEY:
        try:
            from indiseek.agent.provider import GeminiProvider

            vs = VectorStore(config.LANCEDB_PATH, dims=config.EMBEDDING_DIMS)
            vs.init_table()
            count = vs.count()
            if count > 0:
                vector_store = vs
                provider = GeminiProvider(api_key=api_key)

                def _embed(text: str) -> list[float]:
                    return provider.embed([text])[0]

                embed_fn = _embed
                logger.info("Semantic search: enabled (%d vectors in LanceDB)", count)
            else:
                logger.info("Semantic search: disabled (LanceDB table empty)")
        except Exception as e:
            logger.warning("Semantic search: disabled (%s)", e)
    else:
        reasons = []
        if not config.LANCEDB_PATH.exists():
            reasons.append("LanceDB path missing")
        if not config.GEMINI_API_KEY:
            reasons.append("no GEMINI_API_KEY")
        logger.info("Semantic search: disabled (%s)", ", ".join(reasons))

    # Try to open Tantivy index if it exists
    if config.TANTIVY_PATH.exists():
        try:
            li = LexicalIndexer(store, config.TANTIVY_PATH)
            li.open_index()
            lexical_indexer = li
            logger.info("Lexical search: enabled (Tantivy at %s)", config.TANTIVY_PATH)
        except Exception as e:
            logger.warning("Lexical search: disabled (%s)", e)
    else:
        logger.info("Lexical search: disabled (Tantivy path missing)")

    searcher = CodeSearcher(
        vector_store=vector_store,
        lexical_indexer=lexical_indexer,
        embed_fn=embed_fn,
    )
    logger.info("Model: %s", model or config.GEMINI_MODEL)

    return AgentLoop(
        store=store,
        repo_path=repo_path,
        code_searcher=searcher,
        api_key=api_key,
        model=model,
    )
