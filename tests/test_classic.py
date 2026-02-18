"""Tests for the classic agent loop strategy."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from google.genai import types

from indiseek.agent.classic import (
    MAX_ITERATIONS,
    SYNTHESIS_PHASE,
    SYSTEM_PROMPT_TEMPLATE,
    ClassicAgentLoop,
)
from indiseek.agent.strategy import QueryResult, strategy_registry
from indiseek.storage.sqlite_store import SqliteStore
from indiseek.tools.search_code import CodeSearcher, HybridResult


# ── Fixtures ──


@pytest.fixture
def store(tmp_path):
    db = SqliteStore(tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture
def repo_dir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def searcher():
    return CodeSearcher()


# ── Helpers ──


def _make_text_response(text: str):
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


# ── Constants ──


class TestClassicConstants:
    def test_max_iterations(self):
        assert MAX_ITERATIONS == 12

    def test_synthesis_phase(self):
        assert SYNTHESIS_PHASE == 10

    def test_strategy_name(self, store, repo_dir, searcher):
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")
        assert agent.name == "classic"


# ── System prompt ──


class TestClassicSystemPrompt:
    def test_per_tool_paragraphs(self):
        assert "### search_code(query, mode?)" in SYSTEM_PROMPT_TEMPLATE
        assert "### resolve_symbol(symbol_name, action)" in SYSTEM_PROMPT_TEMPLATE
        assert "### read_file(path, start_line?, end_line?)" in SYSTEM_PROMPT_TEMPLATE
        assert "### read_map(path?)" in SYSTEM_PROMPT_TEMPLATE

    def test_good_bad_query_examples(self):
        assert "Good queries:" in SYSTEM_PROMPT_TEMPLATE
        assert "Bad queries:" in SYSTEM_PROMPT_TEMPLATE

    def test_budget_text(self):
        assert "7-8 iterations" in SYSTEM_PROMPT_TEMPLATE
        assert "past iteration 8" in SYSTEM_PROMPT_TEMPLATE

    def test_no_decision_table(self):
        # Should NOT have the decision-table format
        assert "| I have..." not in SYSTEM_PROMPT_TEMPLATE

    def test_includes_repo_map(self, store, repo_dir, searcher):
        store.insert_file_summaries([
            ("src/main.ts", "Main entry point", "ts", 3),
        ])
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")
        prompt = agent._build_system_prompt()
        assert "Main entry point" in prompt
        assert "12 iterations" in prompt


# ── Hint injection ──


class TestClassicHints:
    def test_resolve_symbol_hint_at_iteration_3(self, store, repo_dir, searcher):
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")
        agent._resolve_symbol_used = False
        hint = agent._maybe_inject_tool_hint(3)
        assert hint is not None
        assert "resolve_symbol" in hint

    def test_no_hint_before_iteration_3(self, store, repo_dir, searcher):
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")
        agent._resolve_symbol_used = False
        assert agent._maybe_inject_tool_hint(2) is None

    def test_no_hint_if_already_used(self, store, repo_dir, searcher):
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")
        agent._resolve_symbol_used = True
        assert agent._maybe_inject_tool_hint(3) is None

    def test_iteration_8_budget_warning(self, store, repo_dir, searcher):
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")
        agent._resolve_symbol_used = True  # suppress resolve hint
        hint = agent._maybe_inject_tool_hint(8)
        assert hint is not None
        assert "8/12" in hint
        assert "synthesize" in hint.lower()

    def test_both_hints_at_iteration_8(self, store, repo_dir, searcher):
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")
        agent._resolve_symbol_used = False
        hint = agent._maybe_inject_tool_hint(8)
        assert "resolve_symbol" in hint
        assert "8/12" in hint


# ── Run loop ──


class TestClassicRun:
    def test_direct_text_response(self, store, repo_dir, searcher):
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")
        text_resp = _make_text_response("The answer is 42.")
        agent._client = MagicMock()
        agent._client.models.generate_content.return_value = text_resp

        result = agent.run("What is the answer?")
        assert isinstance(result, QueryResult)
        assert result.answer == "The answer is 42."
        assert result.evidence == []
        assert result.strategy_name == "classic"

    def test_one_tool_call_then_answer(self, store, repo_dir, searcher):
        store.insert_file_summaries([("src/main.ts", "Main entry", "ts", 3)])
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")

        fn_resp = _make_fn_call_response("read_map", {})
        text_resp = _make_text_response("One file.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [fn_resp, text_resp]

        result = agent.run("What files?")
        assert result.answer == "One file."
        assert len(result.evidence) == 1
        assert result.evidence[0].tool == "read_map"
        assert result.strategy_name == "classic"

    def test_max_iterations_is_12(self, store, repo_dir, searcher):
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")
        fn_resp = _make_fn_call_response("read_map", {})
        agent._client = MagicMock()
        agent._client.models.generate_content.return_value = fn_resp

        result = agent.run("Loop forever?")
        assert "maximum iterations" in result.answer
        assert len(result.evidence) == 12
        assert result.strategy_name == "classic"

    def test_budget_on_every_response(self, store, repo_dir, searcher):
        """Budget info is injected into every tool response (3-tier)."""
        store.insert_file_summaries([("src/main.ts", "Main entry", "ts", 3)])
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")

        fn_resp = _make_fn_call_response("read_map", {})
        text_resp = _make_text_response("Done.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [fn_resp, text_resp]

        captured_results = []
        orig_from_fn = types.Part.from_function_response

        def _capture(**kwargs):
            captured_results.append(kwargs.get("response", {}).get("result", ""))
            return orig_from_fn(**kwargs)

        with patch.object(types.Part, "from_function_response", side_effect=_capture):
            agent.run("Test budget")

        assert len(captured_results) == 1
        # First iteration (remaining=11): should have basic count, no urgency
        assert "[Iteration 1/12" in captured_results[0]
        assert "tool calls used]" in captured_results[0]

    def test_no_critique_injection(self, store, repo_dir, searcher):
        """Classic strategy does NOT inject CRITIQUE_PROMPT."""
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")

        fn_resp = _make_fn_call_response("read_map", {})
        agent._client = MagicMock()
        agent._client.models.generate_content.return_value = fn_resp

        agent.run("Test no critique")

        # Check that no call to generate_content received CRITIQUE text
        for call_args in agent._client.models.generate_content.call_args_list:
            contents = call_args.kwargs.get("contents") or call_args.args[1]
            for content in contents:
                if hasattr(content, "parts"):
                    for part in content.parts:
                        if hasattr(part, "text") and part.text:
                            assert "STOP. Before writing your final answer" not in part.text

    def test_no_question_reiteration(self, store, repo_dir, searcher):
        """Classic strategy does NOT prepend [QUESTION: ...] to tool responses."""
        store.insert_file_summaries([("src/main.ts", "Main entry", "ts", 3)])
        agent = ClassicAgentLoop(store, repo_dir, searcher, api_key="fake")

        fn_resp = _make_fn_call_response("read_map", {})
        text_resp = _make_text_response("Done.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [fn_resp, text_resp]

        captured_results = []
        orig_from_fn = types.Part.from_function_response

        def _capture(**kwargs):
            captured_results.append(kwargs.get("response", {}).get("result", ""))
            return orig_from_fn(**kwargs)

        with patch.object(types.Part, "from_function_response", side_effect=_capture):
            agent.run("Test no question reiteration")

        assert len(captured_results) == 1
        assert "[QUESTION:" not in captured_results[0]

    def test_search_code_tip_nudge(self, store, repo_dir):
        """search_code results include TIP with symbol names."""
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = [
            HybridResult(
                chunk_id=1, file_path="src/main.ts", symbol_name="createServer",
                chunk_type="function", content="function createServer() {}", score=0.9,
                match_type="lexical",
            ),
        ]
        agent = ClassicAgentLoop(store, repo_dir, mock_searcher, api_key="fake")

        fn_resp = _make_fn_call_response("search_code", {"query": "server"})
        text_resp = _make_text_response("Done.")

        agent._client = MagicMock()
        agent._client.models.generate_content.side_effect = [fn_resp, text_resp]

        captured_results = []
        orig_from_fn = types.Part.from_function_response

        def _capture(**kwargs):
            captured_results.append(kwargs.get("response", {}).get("result", ""))
            return orig_from_fn(**kwargs)

        with patch.object(types.Part, "from_function_response", side_effect=_capture):
            agent.run("Find server code")

        assert len(captured_results) == 1
        assert "[TIP:" in captured_results[0]
        assert "createServer" in captured_results[0]


# ── Strategy registration ──


class TestClassicRegistration:
    def test_classic_registered(self):
        # Import triggers registration
        import indiseek.agent.classic  # noqa: F401
        assert "classic" in strategy_registry.list_strategies()

    def test_auto_select_returns_classic(self):
        assert strategy_registry.auto_select("How does X work?") == "classic"
        assert strategy_registry.auto_select("short") == "classic"
        assert strategy_registry.auto_select("a " * 20) == "classic"
