"""Strategy pattern: unified types, tool registry, and strategy registry."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from indiseek.tools.read_file import DEFAULT_LINE_CAP, format_file_content
from indiseek.tools.read_map import read_map
from indiseek.tools.resolve_symbol import resolve_symbol
from indiseek.tools.search_code import (
    CodeSearcher,
    QueryCache,
    format_results,
    strip_file_paths,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unified result types
# ---------------------------------------------------------------------------


@dataclass
class EvidenceStep:
    """One step in the agent's evidence trail."""

    tool: str
    args: dict
    summary: str


@dataclass
class QueryResult:
    """Unified result from any query strategy."""

    answer: str
    evidence: list[EvidenceStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    strategy_name: str = ""


# ---------------------------------------------------------------------------
# QueryStrategy protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class QueryStrategy(Protocol):
    name: str

    def run(
        self,
        prompt: str,
        on_progress: Callable[[dict], None] | None = None,
    ) -> QueryResult: ...


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------


@dataclass
class ToolDef:
    """Definition of a tool callable by strategies."""

    fn: Callable[..., str]
    schema: dict
    description: str


class ToolRegistry:
    """Registry of tools available to strategies."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, name: str, fn: Callable[..., str], schema: dict, description: str) -> None:
        self._tools[name] = ToolDef(fn=fn, schema=schema, description=description)

    def execute(self, name: str, args: dict) -> str:
        """Execute a tool by name. Handles errors and truncation."""
        if name not in self._tools:
            return f"Unknown tool: {name}"

        logger.debug("  tool exec: %s(%s)", name, args)
        t0 = time.perf_counter()

        try:
            result = self._tools[name].fn(**args)
        except Exception as e:
            result = f"Error: {e}"
            logger.error("  tool error: %s: %s", name, e)

        # Truncate long results
        if len(result) > 15000:
            logger.debug("  truncating result from %d to 15000 chars", len(result))
            result = result[:15000] + "\n... (truncated)"

        elapsed = time.perf_counter() - t0
        logger.debug("  tool done: %s -> %d chars (%.3fs)", name, len(result), elapsed)
        return result

    def get_declarations(self) -> list[dict]:
        """Provider-agnostic tool declarations."""
        decls = []
        for name, tool_def in self._tools.items():
            decls.append({
                "name": name,
                "description": tool_def.description,
                "parameters_json_schema": tool_def.schema,
            })
        return decls

    def get_gemini_declarations(self) -> list:
        """Gemini FunctionDeclaration objects."""
        from google.genai import types

        return [
            types.FunctionDeclaration(
                name=name,
                description=tool_def.description,
                parameters_json_schema=tool_def.schema,
            )
            for name, tool_def in self._tools.items()
        ]

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())


# ---------------------------------------------------------------------------
# build_tool_registry — wires up the four agent tools
# ---------------------------------------------------------------------------


def build_tool_registry(
    store: Any,
    searcher: CodeSearcher,
    repo_id: int,
    file_cache: dict[str, str] | None = None,
    query_cache: QueryCache | None = None,
    resolve_cache: dict[tuple[str, str], str] | None = None,
) -> ToolRegistry:
    """Create a ToolRegistry with all four tools wired up.

    Caches are optional — callers can pass their own or let us create fresh ones.
    This allows strategies to manage cache lifetime (per-run, per-agent, etc.).
    """
    if file_cache is None:
        file_cache = {}
    if query_cache is None:
        query_cache = QueryCache()
    if resolve_cache is None:
        resolve_cache = {}

    registry = ToolRegistry()

    # -- read_map --
    def _read_map(path: str | None = None) -> str:
        return read_map(store, path=path, repo_id=repo_id)

    registry.register(
        "read_map",
        _read_map,
        schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Subdirectory path to scope results to.",
                },
            },
        },
        description=(
            "Returns directory structure and file summaries for a subdirectory. "
            "The full repository map is already in the system prompt — use this tool only "
            "to drill into a specific subdirectory for more detail."
        ),
    )

    # -- search_code --
    def _search_code(query: str, mode: str = "hybrid") -> str:
        query = strip_file_paths(query)
        cached = query_cache.get(query)
        if cached is not None:
            return f"[Cache hit — similar query already executed]\n{cached}"
        results = searcher.search(query, mode=mode, limit=10)
        result = format_results(results, query)
        query_cache.put(query, result)
        return result

    registry.register(
        "search_code",
        _search_code,
        schema={
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
        description=(
            "Search code by meaning or keywords. Returns top 10 code chunks ranked by relevance.\n"
            "\n"
            "Modes:\n"
            '- "lexical": Exact identifiers (updateStyle, handleHMRUpdate, ERR_NOT_FOUND)\n'
            '- "semantic": Concepts ("how CSS changes are applied in the browser")\n'
            '- "hybrid" (default): Combines both. Best when unsure.\n'
            "\n"
            "For symbol cross-references (who calls X, where is X defined), use resolve_symbol instead.\n"
            "For reading a specific file you already know, use read_file instead."
        ),
    )

    # -- resolve_symbol --
    def _resolve_symbol(symbol_name: str, action: str) -> str:
        cache_key = (symbol_name, action)
        if cache_key in resolve_cache:
            logger.debug("  resolve cache hit: %s/%s", symbol_name, action)
            return f"[Cache hit]\n{resolve_cache[cache_key]}"
        result = resolve_symbol(store, symbol_name, action, repo_id=repo_id)
        resolve_cache[cache_key] = result
        return result

    registry.register(
        "resolve_symbol",
        _resolve_symbol,
        schema={
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
        description=(
            "Navigate the code's call graph using precise cross-reference data. More accurate "
            "than searching for symbol names — use this as your primary navigation tool after "
            "initial discovery.\n"
            "\n"
            "Actions:\n"
            '- "definition": Where is this symbol defined? Start here.\n'
            '- "references": Where is this symbol used across the codebase?\n'
            '- "callers": What functions call this symbol? Understand usage patterns.\n'
            '- "callees": What does this function call? Trace execution flow downward.\n'
            "\n"
            "Tip: After search_code finds a symbol, call resolve_symbol('name', 'definition') "
            "AND resolve_symbol('name', 'callers') together to get the full picture in one turn."
        ),
    )

    # -- read_file --
    def _read_file(
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        # Enforce minimum read window
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

        if path in file_cache:
            logger.debug("  file cache hit: %s", path)
            content = file_cache[path]
            result = format_file_content(content, path, start_line, end_line)
        else:
            content = store.get_file_content(path, repo_id=repo_id)
            if content is None:
                return f"Error: File '{path}' not found in index."
            file_cache[path] = content
            result = format_file_content(content, path, start_line, end_line)

        # Add implicit symbol definitions found in this range
        s = start_line or 1
        e = end_line or min(len(content.splitlines()), DEFAULT_LINE_CAP)
        symbols = store.get_symbols_in_range(path, s, e, repo_id=repo_id)
        if symbols:
            sym_lines = ["\nSymbols defined in this range:"]
            for sym in symbols:
                sym_lines.append(f"  - {sym['name']} ({sym['kind']}) at line {sym['start_line']}")
            result += "\n" + "\n".join(sym_lines)

        return result

    registry.register(
        "read_file",
        _read_file,
        schema={
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
        description=(
            "Read source code with line numbers. Default cap is 200 lines. "
            "Use start_line/end_line for large files.\n"
            "\n"
            "Use this when you know the file path and need to examine the actual implementation.\n"
            "This is the ONLY way to scope to a specific file — search_code cannot filter by path.\n"
            "Reading the implementation after finding a symbol is almost always more valuable "
            "than running another search."
        ),
    )

    return registry


# ---------------------------------------------------------------------------
# Strategy Registry
# ---------------------------------------------------------------------------

# Routing heuristic patterns that suggest a complex (multi-agent) query
_COMPLEX_PATTERNS = re.compile(
    r"\b(how|why|explain|describe|walk me through|end.to.end|flow|architecture|"
    r"pipeline|lifecycle|process|interaction|relationship)\b",
    re.IGNORECASE,
)


class StrategyRegistry:
    """Registry of query strategies with factory functions."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., QueryStrategy]] = {}

    def register(self, name: str, factory_fn: Callable[..., QueryStrategy]) -> None:
        self._factories[name] = factory_fn

    def create(self, name: str, **kwargs: Any) -> QueryStrategy:
        if name not in self._factories:
            available = ", ".join(sorted(self._factories.keys()))
            raise ValueError(f"Unknown strategy {name!r}. Available: {available}")
        return self._factories[name](**kwargs)

    def list_strategies(self) -> list[str]:
        return sorted(self._factories.keys())

    def auto_select(self, prompt: str) -> str:
        """Heuristic: pick the best strategy for a prompt."""
        if len(prompt.split()) > 15:
            return "multi"
        if _COMPLEX_PATTERNS.search(prompt):
            return "multi"
        return "single"


# Module-level singleton
strategy_registry = StrategyRegistry()
