"""Agent loop: Gemini tool-calling with scratchpad."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types

from indiseek import config
from indiseek.indexer.lexical import LexicalIndexer
from indiseek.storage.sqlite_store import SqliteStore
from indiseek.storage.vector_store import VectorStore
from indiseek.tools.read_file import read_file
from indiseek.tools.read_map import read_map
from indiseek.tools.resolve_symbol import resolve_symbol
from indiseek.tools.search_code import CodeSearcher, format_results

MAX_ITERATIONS = 15

SYSTEM_PROMPT = """\
You are a codebase research agent. Your job is to answer questions about a codebase \
by using the tools available to you.

Strategy:
1. Start by calling read_map() to understand the repository structure.
2. Based on the question, formulate a search strategy — use search_code for keyword/semantic \
searches, resolve_symbol for navigating definitions and references.
3. Use read_file to examine specific source code when you need exact details.
4. Gather enough evidence before synthesizing your answer.
5. Always cite specific file paths and line numbers in your answer.

Be thorough but efficient. Don't read entire files when a targeted search suffices. \
Synthesize your findings into a clear, structured answer with evidence."""

TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="read_map",
        description="Returns directory structure and file summaries for the repository. "
        "Call with no arguments for the full tree, or pass a path to scope to a subdirectory.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional subdirectory path to scope results to.",
                },
            },
        },
    ),
    types.FunctionDeclaration(
        name="search_code",
        description="Hybrid semantic+lexical code search. Returns relevant code chunks "
        "ranked by relevance. Use for finding code related to concepts or keywords.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — can be natural language or code identifiers.",
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
        "Specify start_line and end_line to read a specific range.",
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

    def _execute_tool(self, name: str, args: dict) -> str:
        """Execute a tool by name with the given arguments."""
        if name == "read_map":
            return read_map(self._store, path=args.get("path"))
        elif name == "search_code":
            query = args["query"]
            mode = args.get("mode", "hybrid")
            results = self._searcher.search(query, mode=mode, limit=10)
            return format_results(results, query)
        elif name == "resolve_symbol":
            return resolve_symbol(
                self._store, args["symbol_name"], args["action"]
            )
        elif name == "read_file":
            return read_file(
                self._repo_path,
                args["path"],
                start_line=args.get("start_line"),
                end_line=args.get("end_line"),
            )
        else:
            return f"Unknown tool: {name}"

    def run(self, prompt: str) -> AgentResult:
        """Run the agent loop until a text answer is produced or max iterations reached."""
        evidence: list[EvidenceStep] = []

        tools = [types.Tool(function_declarations=TOOL_DECLARATIONS)]
        gen_config = types.GenerateContentConfig(
            tools=tools,
            system_instruction=SYSTEM_PROMPT,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
        )

        contents: list[types.Content] = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )
        ]

        for _iteration in range(MAX_ITERATIONS):
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=gen_config,
            )

            # Append the model's response to conversation history
            model_content = response.candidates[0].content
            contents.append(model_content)

            # Check if model returned function calls
            if not response.function_calls:
                # Model returned a text answer — we're done
                return AgentResult(
                    answer=response.text or "(no answer)",
                    evidence=evidence,
                )

            # Execute each function call and build response parts
            fn_response_parts: list[types.Part] = []
            for call in response.function_calls:
                args = dict(call.args) if call.args else {}
                try:
                    result = self._execute_tool(call.name, args)
                except Exception as e:
                    result = f"Error executing {call.name}: {e}"

                # Truncate long results to stay within context limits
                if len(result) > 15000:
                    result = result[:15000] + "\n... (truncated)"

                evidence.append(
                    EvidenceStep(
                        tool=call.name,
                        args=args,
                        summary=result[:200] + "..." if len(result) > 200 else result,
                    )
                )

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

    if repo_path is None:
        repo_path = config.REPO_PATH

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
            if vs.count() > 0:
                vector_store = vs
                provider = GeminiProvider(api_key=api_key)

                def _embed(text: str) -> list[float]:
                    return provider.embed([text])[0]

                embed_fn = _embed
        except Exception:
            pass  # Semantic search unavailable

    # Try to open Tantivy index if it exists
    if config.TANTIVY_PATH.exists():
        try:
            li = LexicalIndexer(store, config.TANTIVY_PATH)
            li.open_index()
            lexical_indexer = li
        except Exception:
            pass  # Lexical search unavailable

    searcher = CodeSearcher(
        vector_store=vector_store,
        lexical_indexer=lexical_indexer,
        embed_fn=embed_fn,
    )

    return AgentLoop(
        store=store,
        repo_path=repo_path,
        code_searcher=searcher,
        api_key=api_key,
        model=model,
    )
