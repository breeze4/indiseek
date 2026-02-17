"""Tests for agent loop and FastAPI server."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from indiseek.agent.loop import (
    SYSTEM_PROMPT_TEMPLATE,
    TOOL_DECLARATIONS,
    AgentLoop,
    AgentResult,
    EvidenceStep,
)
from indiseek.storage.sqlite_store import Chunk, SqliteStore, Symbol
from indiseek.tools.search_code import CodeSearcher, HybridResult


# ── Fixtures ──


@pytest.fixture
def store(tmp_path):
    """Create a fresh SqliteStore with schema initialized."""
    db = SqliteStore(tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture
def repo_dir(tmp_path):
    """Create a temporary repo with sample files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "main.ts").write_text(
        "function hello() {\n  console.log('hello');\n}\n"
    )
    return repo


@pytest.fixture
def searcher():
    """Create a CodeSearcher with no backends (for mocking)."""
    return CodeSearcher()


# ── Tool declarations tests ──


class TestToolDeclarations:
    def test_four_tools_defined(self):
        assert len(TOOL_DECLARATIONS) == 4

    def test_tool_names(self):
        names = {t.name for t in TOOL_DECLARATIONS}
        assert names == {"read_map", "search_code", "resolve_symbol", "read_file"}

    def test_search_code_has_required_query(self):
        search_tool = next(t for t in TOOL_DECLARATIONS if t.name == "search_code")
        schema = search_tool.parameters_json_schema
        assert "query" in schema["required"]

    def test_resolve_symbol_has_required_fields(self):
        resolve_tool = next(t for t in TOOL_DECLARATIONS if t.name == "resolve_symbol")
        schema = resolve_tool.parameters_json_schema
        assert "symbol_name" in schema["required"]
        assert "action" in schema["required"]

    def test_read_file_has_required_path(self):
        read_tool = next(t for t in TOOL_DECLARATIONS if t.name == "read_file")
        schema = read_tool.parameters_json_schema
        assert "path" in schema["required"]

    def test_system_prompt_template_exists(self):
        assert len(SYSTEM_PROMPT_TEMPLATE) > 100
        assert "repo_map" in SYSTEM_PROMPT_TEMPLATE
        assert "max_iterations" in SYSTEM_PROMPT_TEMPLATE


# ── Tool execution tests ──


class TestToolExecution:
    def test_execute_read_map(self, store, repo_dir, searcher):
        store.insert_file_summaries([
            ("src/main.ts", "Main entry point", "ts", 3),
        ])
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        result = agent._execute_tool("read_map", {})
        assert "Main entry point" in result

    def test_execute_read_map_with_path(self, store, repo_dir, searcher):
        store.insert_file_summaries([
            ("src/main.ts", "Main entry point", "ts", 3),
            ("lib/util.ts", "Util lib", "ts", 5),
        ])
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        result = agent._execute_tool("read_map", {"path": "src"})
        assert "main.ts" in result
        assert "util.ts" not in result

    def test_execute_read_file(self, store, repo_dir, searcher):
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        result = agent._execute_tool("read_file", {"path": "src/main.ts"})
        assert "function hello" in result

    def test_execute_read_file_with_lines(self, store, repo_dir, searcher):
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        result = agent._execute_tool(
            "read_file", {"path": "src/main.ts", "start_line": 1, "end_line": 1}
        )
        assert "function hello" in result
        assert "console.log" not in result

    def test_execute_resolve_symbol(self, store, repo_dir, searcher):
        store.insert_symbols([
            Symbol(
                id=None, file_path="src/main.ts", name="hello",
                kind="function", start_line=1, start_col=0,
                end_line=3, end_col=1, signature="function hello()",
            ),
        ])
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        result = agent._execute_tool(
            "resolve_symbol", {"symbol_name": "hello", "action": "definition"}
        )
        assert "hello" in result
        assert "src/main.ts" in result

    def test_execute_search_code(self, store, repo_dir):
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            HybridResult(
                chunk_id=1, file_path="src/main.ts", symbol_name="hello",
                chunk_type="function", content="function hello() {}", score=0.9,
                match_type="lexical",
            ),
        ]
        agent = AgentLoop(store, repo_dir, mock_searcher, api_key="fake")
        result = agent._execute_tool("search_code", {"query": "hello"})
        assert "hello" in result
        mock_searcher.search.assert_called_once_with("hello", mode="hybrid", limit=10)

    def test_execute_search_code_with_mode(self, store, repo_dir):
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        agent = AgentLoop(store, repo_dir, mock_searcher, api_key="fake")
        agent._execute_tool("search_code", {"query": "test", "mode": "lexical"})
        mock_searcher.search.assert_called_once_with("test", mode="lexical", limit=10)

    def test_execute_search_code_strips_file_paths(self, store, repo_dir):
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = []
        agent = AgentLoop(store, repo_dir, mock_searcher, api_key="fake")
        agent._execute_tool("search_code", {"query": "createServer src/server/index.ts"})
        mock_searcher.search.assert_called_once_with("createServer", mode="hybrid", limit=10)

    def test_execute_unknown_tool(self, store, repo_dir, searcher):
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        result = agent._execute_tool("nonexistent", {})
        assert "Unknown tool" in result

    def test_execute_tool_error_handling(self, store, repo_dir, searcher):
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        # read_file with a non-existent file returns an error message, not an exception
        result = agent._execute_tool("read_file", {"path": "nonexistent.ts"})
        assert "Error" in result or "not found" in result


# ── File read caching tests ──


class TestFileReadCaching:
    def test_read_file_caching_same_file(self, store, repo_dir, searcher):
        """Second read of same file should come from cache, not disk."""
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        result1 = agent._execute_tool("read_file", {"path": "src/main.ts"})
        assert "function hello" in result1

        # Patch Path.read_text to count calls from here on
        with patch.object(Path, "read_text", wraps=Path.read_text) as spy:
            result2 = agent._execute_tool("read_file", {"path": "src/main.ts"})
            assert spy.call_count == 0  # served from cache
        assert "function hello" in result2

    def test_read_file_caching_different_files(self, store, repo_dir, searcher):
        """Different files should both be cached."""
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        agent._execute_tool("read_file", {"path": "src/main.ts"})
        assert "src/main.ts" in agent._file_cache
        # Need a second file — it's already in repo_dir from the fixture? No,
        # repo_dir fixture only has src/main.ts. Let's create one.
        (repo_dir / "src" / "extra.ts").write_text("const x = 1;\n")
        agent._execute_tool("read_file", {"path": "src/extra.ts"})
        assert "src/extra.ts" in agent._file_cache
        assert len(agent._file_cache) == 2

    def test_read_file_cache_line_range_slicing(self, store, repo_dir, searcher):
        """Cache hit with line range should return correct slice."""
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        # First read caches full content
        agent._execute_tool("read_file", {"path": "src/main.ts"})
        # Second read requests only line 2
        result = agent._execute_tool(
            "read_file", {"path": "src/main.ts", "start_line": 2, "end_line": 2}
        )
        assert "console.log" in result
        assert "function hello" not in result


# ── Search query caching tests ──


class TestSearchQueryCaching:
    def test_search_code_caching_similar_queries(self, store, repo_dir):
        """Similar queries should be served from cache; searcher called once."""
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            HybridResult(
                chunk_id=1, file_path="src/main.ts", symbol_name="hello",
                chunk_type="function", content="function hello() {}", score=0.9,
                match_type="lexical",
            ),
        ]
        agent = AgentLoop(store, repo_dir, mock_searcher, api_key="fake")
        result1 = agent._execute_tool("search_code", {"query": "HMR CSS hot update"})
        result2 = agent._execute_tool("search_code", {"query": "CSS HMR hot update"})
        # Searcher should only be called once — second was a cache hit
        assert mock_searcher.search.call_count == 1
        assert "Cache hit" in result2


# ── resolve_symbol hint tests ──


class TestResolveSymbolHints:
    def test_hint_injected_at_iteration_5(self, store, repo_dir, searcher):
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        agent._resolve_symbol_used = False
        hint = agent._maybe_inject_tool_hint(iteration=5)
        assert hint is not None
        assert "resolve_symbol" in hint

    def test_no_hint_before_iteration_5(self, store, repo_dir, searcher):
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        agent._resolve_symbol_used = False
        assert agent._maybe_inject_tool_hint(iteration=3) is None

    def test_no_hint_if_already_used(self, store, repo_dir, searcher):
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        agent._resolve_symbol_used = True
        assert agent._maybe_inject_tool_hint(iteration=6) is None


# ── Parallel tool call tests ──


class TestParallelToolCalls:
    def test_system_prompt_encourages_parallel_tools(self):
        assert any(
            kw in SYSTEM_PROMPT_TEMPLATE.lower()
            for kw in ("batch", "parallel", "multiple tool")
        )

    def test_agent_handles_multiple_parallel_tool_calls(self, store, repo_dir, searcher):
        """Response with 3 simultaneous function_calls should all appear in evidence."""
        store.insert_file_summaries([
            ("src/main.ts", "Main entry", "ts", 3),
        ])
        store.insert_symbols([
            Symbol(
                id=None, file_path="src/main.ts", name="hello",
                kind="function", start_line=1, start_col=0,
                end_line=3, end_col=1, signature="function hello()",
            ),
        ])
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")

        # Build a response with 3 parallel function calls
        fn1 = MagicMock()
        fn1.name = "read_map"
        fn1.args = {}
        fn2 = MagicMock()
        fn2.name = "resolve_symbol"
        fn2.args = {"symbol_name": "hello", "action": "definition"}
        fn3 = MagicMock()
        fn3.name = "read_file"
        fn3.args = {"path": "src/main.ts"}

        content = MagicMock()
        content.role = "model"
        content.parts = [MagicMock(), MagicMock(), MagicMock()]

        candidate = MagicMock()
        candidate.content = content

        parallel_resp = MagicMock()
        parallel_resp.candidates = [candidate]
        parallel_resp.function_calls = [fn1, fn2, fn3]
        parallel_resp.text = None

        text_resp = _make_text_response("All done.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [parallel_resp, text_resp]

        result = agent.run("Test parallel")
        assert result.answer == "All done."
        assert len(result.evidence) == 3
        tools_used = {e.tool for e in result.evidence}
        assert tools_used == {"read_map", "resolve_symbol", "read_file"}


# ── Agent loop tests (mocked Gemini) ──


def _make_text_response(text: str):
    """Create a mock Gemini response with text content."""
    part = MagicMock()
    part.text = text
    part.function_call = None

    content = MagicMock()
    content.role = "model"
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content

    response = MagicMock()
    response.candidates = [candidate]
    response.function_calls = None
    response.text = text
    return response


def _make_fn_call_response(name: str, args: dict):
    """Create a mock Gemini response with a function call."""
    fn_call = MagicMock()
    fn_call.name = name
    fn_call.args = args

    fn_part = MagicMock()
    fn_part.function_call = fn_call

    content = MagicMock()
    content.role = "model"
    content.parts = [fn_part]

    candidate = MagicMock()
    candidate.content = content

    response = MagicMock()
    response.candidates = [candidate]
    response.function_calls = [fn_call]
    response.text = None
    return response


class TestAgentLoop:
    def test_direct_text_response(self, store, repo_dir, searcher):
        """Agent returns text on first call — no tool use."""
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")

        text_resp = _make_text_response("The answer is 42.")
        agent._client = MagicMock()
        agent._client.models.generate_content.return_value = text_resp

        result = agent.run("What is the answer?")
        assert isinstance(result, AgentResult)
        assert result.answer == "The answer is 42."
        assert result.evidence == []

    def test_one_tool_call_then_answer(self, store, repo_dir, searcher):
        """Agent calls one tool then returns text."""
        store.insert_file_summaries([
            ("src/main.ts", "Main entry", "ts", 3),
        ])
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")

        fn_resp = _make_fn_call_response("read_map", {})
        text_resp = _make_text_response("The repo has one file.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [fn_resp, text_resp]

        result = agent.run("What files exist?")
        assert result.answer == "The repo has one file."
        assert len(result.evidence) == 1
        assert result.evidence[0].tool == "read_map"

    def test_multiple_tool_calls(self, store, repo_dir, searcher):
        """Agent calls multiple tools across iterations."""
        store.insert_file_summaries([
            ("src/main.ts", "Main entry", "ts", 3),
        ])
        store.insert_symbols([
            Symbol(
                id=None, file_path="src/main.ts", name="hello",
                kind="function", start_line=1, start_col=0,
                end_line=3, end_col=1, signature="function hello()",
            ),
        ])
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")

        resp1 = _make_fn_call_response("read_map", {})
        resp2 = _make_fn_call_response("resolve_symbol", {"symbol_name": "hello", "action": "definition"})
        resp3 = _make_text_response("hello is defined at line 1.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [resp1, resp2, resp3]

        result = agent.run("Where is hello defined?")
        assert result.answer == "hello is defined at line 1."
        assert len(result.evidence) == 2
        assert result.evidence[0].tool == "read_map"
        assert result.evidence[1].tool == "resolve_symbol"

    def test_max_iterations(self, store, repo_dir, searcher):
        """Agent stops after max iterations."""
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")

        # Always return a tool call — agent should stop after MAX_ITERATIONS
        fn_resp = _make_fn_call_response("read_map", {})
        agent._client = MagicMock()
        agent._client.models.generate_content.return_value = fn_resp

        result = agent.run("Loop forever?")
        assert "maximum iterations" in result.answer
        assert len(result.evidence) == 15  # MAX_ITERATIONS

    def test_tool_error_captured(self, store, repo_dir):
        """Tool exceptions are caught and recorded as evidence."""
        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = RuntimeError("search failed")
        agent = AgentLoop(store, repo_dir, mock_searcher, api_key="fake")

        fn_resp = _make_fn_call_response("search_code", {"query": "broken"})
        text_resp = _make_text_response("Search failed, sorry.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [fn_resp, text_resp]

        result = agent.run("Search for something")
        assert len(result.evidence) == 1
        assert "Error" in result.evidence[0].summary

    def test_long_tool_result_truncated(self, store, repo_dir, searcher):
        """Tool results exceeding 15000 chars are truncated."""
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")

        # Create a file with a lot of content
        big_content = "x" * 20000
        (repo_dir / "big.ts").write_text(big_content)

        fn_resp = _make_fn_call_response("read_file", {"path": "big.ts"})
        text_resp = _make_text_response("Big file read.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [fn_resp, text_resp]

        result = agent.run("Read big file")
        # The evidence summary is further truncated to 200 chars
        assert len(result.evidence) == 1

    def test_conversation_history_grows(self, store, repo_dir, searcher):
        """Verify generate_content is called the expected number of times."""
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")

        fn_resp = _make_fn_call_response("read_map", {})
        text_resp = _make_text_response("Done.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [fn_resp, text_resp]

        result = agent.run("Test history")

        # generate_content called twice: once for the fn call, once for the text answer
        assert agent._client.models.generate_content.call_count == 2
        assert result.answer == "Done."
        assert len(result.evidence) == 1

    def test_budget_injected_into_evidence(self, store, repo_dir, searcher):
        """Tool results include remaining iteration count."""
        store.insert_file_summaries([
            ("src/main.ts", "Main entry", "ts", 3),
        ])
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")

        fn_resp = _make_fn_call_response("read_map", {"path": "src"})
        text_resp = _make_text_response("Done.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [fn_resp, text_resp]

        result = agent.run("Test budget")
        # First iteration => "Iteration 1/15, 1 tool calls used"
        assert "Iteration 1/15" in result.evidence[0].summary
        assert "tool calls used" in result.evidence[0].summary

    def test_system_prompt_includes_repo_map(self, store, repo_dir, searcher):
        """Dynamic system prompt includes the read_map output."""
        store.insert_file_summaries([
            ("src/main.ts", "Main entry point", "ts", 3),
        ])
        agent = AgentLoop(store, repo_dir, searcher, api_key="fake")
        prompt = agent._build_system_prompt()
        assert "Main entry point" in prompt
        assert "15 iterations" in prompt


# ── FastAPI server tests ──


class TestServer:
    def test_health_endpoint(self):
        from fastapi.testclient import TestClient

        from indiseek.api.server import app

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_query_endpoint(self):
        from fastapi.testclient import TestClient

        from indiseek.api.server import app, _get_agent_loop

        mock_loop = MagicMock()
        mock_loop.run.return_value = AgentResult(
            answer="The answer is 42.",
            evidence=[
                EvidenceStep(tool="read_map", args={}, summary="Read the map"),
            ],
        )

        with patch("indiseek.api.server._get_agent_loop", return_value=mock_loop):
            client = TestClient(app)
            resp = client.post("/query", json={"prompt": "What is 42?"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "The answer is 42."
        assert len(data["evidence"]) == 1
        assert data["evidence"][0]["step"] == "read_map()"

    def test_query_endpoint_with_evidence_args(self):
        from fastapi.testclient import TestClient

        from indiseek.api.server import app

        mock_loop = MagicMock()
        mock_loop.run.return_value = AgentResult(
            answer="Found it.",
            evidence=[
                EvidenceStep(
                    tool="search_code",
                    args={"query": "hello", "mode": "hybrid"},
                    summary="Searched for hello",
                ),
            ],
        )

        with patch("indiseek.api.server._get_agent_loop", return_value=mock_loop):
            client = TestClient(app)
            resp = client.post("/query", json={"prompt": "Find hello"})

        assert resp.status_code == 200
        data = resp.json()
        assert "query='hello'" in data["evidence"][0]["step"]

    def test_query_endpoint_error(self):
        from fastapi.testclient import TestClient

        from indiseek.api.server import app

        mock_loop = MagicMock()
        mock_loop.run.side_effect = RuntimeError("boom")

        with patch("indiseek.api.server._get_agent_loop", return_value=mock_loop):
            client = TestClient(app)
            resp = client.post("/query", json={"prompt": "crash"})

        assert resp.status_code == 500

    def test_query_missing_prompt(self):
        from fastapi.testclient import TestClient

        from indiseek.api.server import app

        client = TestClient(app)
        resp = client.post("/query", json={})
        assert resp.status_code == 422  # validation error


# ── EvidenceStep / AgentResult tests ──


class TestDataclasses:
    def test_evidence_step(self):
        step = EvidenceStep(tool="read_map", args={"path": "src"}, summary="Read src dir")
        assert step.tool == "read_map"
        assert step.args == {"path": "src"}
        assert step.summary == "Read src dir"

    def test_agent_result_default_evidence(self):
        result = AgentResult(answer="test")
        assert result.evidence == []

    def test_agent_result_with_evidence(self):
        result = AgentResult(
            answer="test",
            evidence=[
                EvidenceStep(tool="t", args={}, summary="s"),
            ],
        )
        assert len(result.evidence) == 1
