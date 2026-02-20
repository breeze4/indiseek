"""Tests for multi-agent pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from google.genai import types

from indiseek.agent.multi import (
    EvidenceBundle,
    Finding,
    MultiAgentOrchestrator,
    MultiAgentResult,
    PlannerAgent,
    ResearchPlan,
    ResearcherAgent,
    SubQuestion,
    SynthesizerAgent,
    VerificationResult,
    VerifierAgent,
    _is_complex_query,
)
from indiseek.agent.strategy import EvidenceStep, QueryResult, ToolRegistry, build_tool_registry
from indiseek.tools.search_code import CodeSearcher, QueryCache
from tests.helpers import _make_fn_call_response, _make_text_response


# ── Data structure tests ──


class TestDataStructures:
    def test_sub_question_defaults(self):
        sq = SubQuestion(question="How?", target_area="src/")
        assert sq.question == "How?"
        assert sq.initial_actions == []
        assert sq.verification_hint == ""

    def test_research_plan(self):
        plan = ResearchPlan(
            original_question="How does auth work?",
            sub_questions=[
                SubQuestion(question="How is the token validated?", target_area="src/auth/"),
            ],
        )
        assert plan.original_question == "How does auth work?"
        assert len(plan.sub_questions) == 1

    def test_finding(self):
        f = Finding(
            tool="read_file",
            args={"path": "src/main.ts"},
            result_summary="Main entry point",
            relevant_code="function main() {}",
            file_path="src/main.ts",
            line_range=(1, 10),
        )
        assert f.tool == "read_file"
        assert f.line_range == (1, 10)

    def test_evidence_bundle(self):
        bundle = EvidenceBundle(
            sub_question="How?",
            findings=[],
            coverage_note="Fully answered",
        )
        assert bundle.coverage_note == "Fully answered"

    def test_verification_result(self):
        vr = VerificationResult(claim="X calls Y", status="verified")
        assert vr.correction == ""

    def test_multi_agent_result_defaults(self):
        result = MultiAgentResult(answer="The answer")
        assert result.evidence_bundles == []
        assert result.verification_results == []
        assert result.plan is None


# ── Tool registry tests ──


class TestToolRegistry:
    def test_build_tool_registry(self, store, searcher):
        registry = build_tool_registry(store, searcher, repo_id=1)
        assert set(registry.tool_names) == {"read_map", "search_code", "resolve_symbol", "read_file"}

    def test_execute_read_map(self, store, searcher):
        store.insert_file_summaries([("src/main.ts", "Main entry", "ts", 3)])
        registry = build_tool_registry(store, searcher, repo_id=1)
        result = registry.execute("read_map", {})
        assert "Main entry" in result

    def test_execute_read_file(self, store, searcher):
        store.insert_file_content("src/main.ts", "function hello() {}\n")
        registry = build_tool_registry(store, searcher, repo_id=1)
        result = registry.execute("read_file", {"path": "src/main.ts"})
        assert "function hello" in result

    def test_execute_unknown_tool(self, store, searcher):
        registry = build_tool_registry(store, searcher, repo_id=1)
        result = registry.execute("nonexistent", {})
        assert "Unknown tool" in result

    def test_truncates_long_results(self, store, searcher):
        store.insert_file_content("big.ts", "x" * 20000)
        registry = build_tool_registry(store, searcher, repo_id=1)
        result = registry.execute("read_file", {"path": "big.ts"})
        assert len(result) <= 15100  # 15000 + truncation message

    def test_gemini_declarations(self, store, searcher):
        registry = build_tool_registry(store, searcher, repo_id=1)
        decls = registry.get_gemini_declarations()
        assert len(decls) == 4
        names = {d.name for d in decls}
        assert names == {"read_map", "search_code", "resolve_symbol", "read_file"}


# ── Planner Agent tests ──


class TestPlannerAgent:
    def test_plan_produces_valid_research_plan(self):
        """Given a question and repo map, produces valid ResearchPlan."""
        mock_client = MagicMock()
        planner = PlannerAgent(mock_client, "gemini-2.0-flash")

        plan_json = json.dumps({
            "sub_questions": [
                {
                    "question": "How are CSS changes detected?",
                    "target_area": "src/node/server/",
                    "initial_actions": ["search_code('file watcher', mode='semantic')"],
                    "verification_hint": "Verify: which library handles file watching?",
                },
                {
                    "question": "How does the client apply CSS updates?",
                    "target_area": "src/client/",
                    "initial_actions": ["search_code('CSS update apply', mode='semantic')"],
                    "verification_hint": "Verify: does it replace <style> or <link>?",
                },
            ]
        })

        mock_client.models.generate_content.return_value = _make_text_response(plan_json)

        plan, usage = planner.plan("How does CSS HMR work?", "src/\n  main.ts")

        assert isinstance(plan, ResearchPlan)
        assert plan.original_question == "How does CSS HMR work?"
        assert len(plan.sub_questions) == 2
        assert plan.sub_questions[0].target_area == "src/node/server/"
        assert plan.sub_questions[1].verification_hint == "Verify: does it replace <style> or <link>?"

    def test_plan_fallback_on_malformed_json(self):
        """Malformed JSON falls back to single sub-question with original question."""
        mock_client = MagicMock()
        planner = PlannerAgent(mock_client, "gemini-2.0-flash")

        mock_client.models.generate_content.return_value = _make_text_response(
            "This is not valid JSON at all"
        )

        plan, usage = planner.plan("How does auth work?", "src/\n  main.ts")

        assert isinstance(plan, ResearchPlan)
        assert len(plan.sub_questions) == 1
        assert plan.sub_questions[0].question == "How does auth work?"

    def test_plan_fallback_on_empty_sub_questions(self):
        """Empty sub_questions array falls back to original question."""
        mock_client = MagicMock()
        planner = PlannerAgent(mock_client, "gemini-2.0-flash")

        mock_client.models.generate_content.return_value = _make_text_response(
            json.dumps({"sub_questions": []})
        )

        plan, usage = planner.plan("What?", "src/")

        assert len(plan.sub_questions) == 1
        assert plan.sub_questions[0].question == "What?"

    def test_plan_extracts_json_from_markdown_code_block(self):
        """JSON wrapped in markdown code block is parsed correctly."""
        mock_client = MagicMock()
        planner = PlannerAgent(mock_client, "gemini-2.0-flash")

        response_text = (
            "Here's the plan:\n```json\n"
            + json.dumps({
                "sub_questions": [
                    {"question": "Q1", "target_area": "src/a/"},
                    {"question": "Q2", "target_area": "src/b/"},
                ]
            })
            + "\n```"
        )
        mock_client.models.generate_content.return_value = _make_text_response(response_text)

        plan, usage = planner.plan("Multi-part question", "src/")

        assert len(plan.sub_questions) == 2


# ── Researcher Agent tests ──


class TestResearcherAgent:
    def test_research_produces_evidence_bundle(self, store, searcher):
        """Given a sub-question and mock tools, produces valid EvidenceBundle."""
        mock_client = MagicMock()
        researcher = ResearcherAgent(mock_client, "gemini-2.0-flash", store, searcher, 1)

        store.insert_file_summaries([("src/main.ts", "Main entry", "ts", 3)])

        # Simulate: one tool call, then text summary
        fn_resp = _make_fn_call_response("read_map", {})
        text_resp = _make_text_response(
            "FINDINGS:\n"
            "- [src/main.ts:1] Main entry point function\n\n"
            "COVERAGE: Fully answered the sub-question."
        )
        mock_client.models.generate_content.side_effect = [fn_resp, text_resp]

        sq = SubQuestion(
            question="What is the entry point?",
            target_area="src/",
            initial_actions=["read_map(path='src')"],
        )
        bundle, usage = researcher.research(sq, "src/\n  main.ts — Main entry")

        assert isinstance(bundle, EvidenceBundle)
        assert bundle.sub_question == "What is the entry point?"
        assert len(bundle.findings) == 1
        assert "Main entry point" in bundle.findings[0].result_summary
        assert bundle.coverage_note == "Fully answered the sub-question."

    def test_research_question_reiteration(self, store, searcher):
        """Tool responses include [QUESTION: ...] prefix."""
        mock_client = MagicMock()
        researcher = ResearcherAgent(mock_client, "gemini-2.0-flash", store, searcher, 1)
        store.insert_file_summaries([("src/main.ts", "Main entry", "ts", 3)])

        fn_resp = _make_fn_call_response("read_map", {})
        text_resp = _make_text_response("FINDINGS:\n- Found it.\nCOVERAGE: Done.")
        mock_client.models.generate_content.side_effect = [fn_resp, text_resp]

        # Capture function response parts
        captured_results = []
        orig_from_fn = types.Part.from_function_response

        def _capture_fn_response(**kwargs):
            captured_results.append(kwargs.get("response", {}).get("result", ""))
            return orig_from_fn(**kwargs)

        sq = SubQuestion(question="How does X work?", target_area="src/")

        with patch.object(types.Part, "from_function_response", side_effect=_capture_fn_response):
            bundle, usage = researcher.research(sq, "src/")

        assert len(captured_results) == 1
        assert "[QUESTION: How does X work?]" in captured_results[0]

    def test_research_stops_early_on_text_response(self, store, searcher):
        """Researcher stops when LLM returns text instead of tool calls."""
        mock_client = MagicMock()
        researcher = ResearcherAgent(mock_client, "gemini-2.0-flash", store, searcher, 1)

        text_resp = _make_text_response("FINDINGS:\n- Immediate answer.\nCOVERAGE: Done.")
        mock_client.models.generate_content.return_value = text_resp

        sq = SubQuestion(question="Simple question", target_area="")
        bundle, usage = researcher.research(sq, "src/")

        # Only 1 LLM call (no tool calls)
        assert mock_client.models.generate_content.call_count == 1
        assert isinstance(bundle, EvidenceBundle)


# ── Synthesizer Agent tests ──


class TestSynthesizerAgent:
    def test_synthesize_produces_answer(self):
        """Given evidence bundles, produces coherent answer."""
        mock_client = MagicMock()
        synth = SynthesizerAgent(mock_client, "gemini-2.0-flash")

        bundles = [
            EvidenceBundle(
                sub_question="How are files watched?",
                findings=[
                    Finding(
                        tool="read_file",
                        args={"path": "src/watcher.ts"},
                        result_summary="Uses chokidar to watch file changes",
                        relevant_code="const watcher = chokidar.watch(root)",
                        file_path="src/watcher.ts",
                        line_range=(10, 15),
                    )
                ],
                coverage_note="Fully answered",
            ),
            EvidenceBundle(
                sub_question="How does the client apply updates?",
                findings=[
                    Finding(
                        tool="read_file",
                        args={"path": "src/client.ts"},
                        result_summary="Replaces <style> tag content via DOM API",
                        relevant_code="style.textContent = newCSS",
                        file_path="src/client.ts",
                        line_range=(50, 55),
                    )
                ],
                coverage_note="Fully answered",
            ),
        ]

        mock_client.models.generate_content.return_value = _make_text_response(
            "# CSS HMR Flow\n\n"
            "File changes are detected by chokidar (src/watcher.ts:10). "
            "The client applies updates by replacing <style> tag content (src/client.ts:50)."
        )

        answer, usage = synth.synthesize("How does CSS HMR work?", bundles)

        assert "chokidar" in answer
        assert "src/watcher.ts" in answer
        assert "src/client.ts" in answer

    def test_synthesize_accepts_model_override(self):
        """SynthesizerAgent can use a different model."""
        mock_client = MagicMock()
        synth = SynthesizerAgent(mock_client, "gemini-2.0-pro")

        mock_client.models.generate_content.return_value = _make_text_response("Answer.")
        answer, usage = synth.synthesize("Q?", [])

        # Verify the model passed to generate_content
        call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs.kwargs["model"] == "gemini-2.0-pro"


# ── Verifier Agent tests ──


class TestVerifierAgent:
    def test_verify_detects_corrections(self, store, searcher):
        """Given an answer with a deliberate inaccuracy, detects it."""
        mock_client = MagicMock()
        verifier = VerifierAgent(mock_client, "gemini-2.0-flash", store, searcher, 1)

        store.insert_file_summaries([("src/main.ts", "Main entry", "ts", 3)])

        # Simulate: verifier does one tool call, then outputs results
        fn_resp = _make_fn_call_response(
            "resolve_symbol", {"symbol_name": "createServer", "action": "definition"}
        )
        text_resp = _make_text_response(
            "VERIFICATION RESULTS:\n"
            "- [VERIFIED] createServer is defined in src/server/index.ts\n"
            "- [CORRECTED] createServer uses Express -> createServer uses Koa\n"
            "- [UNVERIFIABLE] The server supports HTTP/3\n"
        )
        mock_client.models.generate_content.side_effect = [fn_resp, text_resp]

        results, usage = verifier.verify(
            "createServer is in src/server/index.ts and uses Express.",
            ["Verify: what framework does createServer use?"],
            "src/",
        )

        assert len(results) == 3
        verified = [r for r in results if r.status == "verified"]
        corrected_items = [r for r in results if r.status == "corrected"]
        unverifiable = [r for r in results if r.status == "unverifiable"]
        assert len(verified) == 1
        assert len(corrected_items) == 1
        assert len(unverifiable) == 1
        assert "Koa" in corrected_items[0].correction

    def test_verify_no_corrections_needed(self, store, searcher):
        """When all claims are verified, returns original answer unchanged."""
        mock_client = MagicMock()
        verifier = VerifierAgent(mock_client, "gemini-2.0-flash", store, searcher, 1)

        text_resp = _make_text_response(
            "VERIFICATION RESULTS:\n"
            "- [VERIFIED] Function X is defined in file Y\n"
        )
        mock_client.models.generate_content.return_value = text_resp

        results, usage = verifier.verify(
            "Function X is defined in file Y.",
            [],
            "src/",
        )

        assert len(results) == 1
        assert results[0].status == "verified"


# ── Routing heuristic tests ──


class TestRoutingHeuristic:
    def test_how_questions_are_complex(self):
        assert _is_complex_query("How does the authentication middleware work?")

    def test_why_questions_are_complex(self):
        assert _is_complex_query("Why does the build fail?")

    def test_flow_questions_are_complex(self):
        assert _is_complex_query("Describe the end-to-end request flow")

    def test_long_questions_are_complex(self):
        assert _is_complex_query(
            "What happens when a user submits the login form and the server "
            "validates their credentials and returns a token?"
        )

    def test_simple_symbol_lookup_is_not_complex(self):
        assert not _is_complex_query("createServer")

    def test_short_definition_query_is_not_complex(self):
        assert not _is_complex_query("Where is X defined?")


# ── Orchestrator tests ──


class TestOrchestrator:
    def test_fallback_to_single_agent_on_one_sub_question(self, store, searcher, tmp_path):
        """When planner produces <2 sub-questions, falls back to single-agent."""
        mock_client = MagicMock()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        orchestrator = MultiAgentOrchestrator(
            store, repo_dir, searcher, api_key="fake", repo_id=1,
        )
        orchestrator._client = mock_client

        # Planner returns 1 sub-question
        plan_json = json.dumps({
            "sub_questions": [
                {"question": "Where is X?", "target_area": "src/"},
            ]
        })
        mock_client.models.generate_content.return_value = _make_text_response(plan_json)

        # Mock the single-agent fallback
        mock_single_agent = MagicMock()
        mock_single_agent.run.return_value = QueryResult(
            answer="X is in src/main.ts", evidence=[], strategy_name="single",
        )

        with patch("indiseek.agent.loop.AgentLoop", return_value=mock_single_agent):
            result = orchestrator.run("Where is X?")

        assert isinstance(result, QueryResult)
        assert result.answer == "X is in src/main.ts"
        assert result.metadata.get("plan") is not None
        mock_single_agent.run.assert_called_once()

    def test_full_pipeline_runs_all_phases(self, store, searcher, tmp_path):
        """Full pipeline: planner -> researcher -> synthesizer -> verifier."""
        mock_client = MagicMock()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        store.insert_file_summaries([("src/main.ts", "Main entry", "ts", 3)])

        orchestrator = MultiAgentOrchestrator(
            store, repo_dir, searcher, api_key="fake", repo_id=1,
        )
        orchestrator._client = mock_client

        # Planner response (2 sub-questions)
        plan_resp = _make_text_response(json.dumps({
            "sub_questions": [
                {"question": "Q1?", "target_area": "src/a/", "verification_hint": "Check A"},
                {"question": "Q2?", "target_area": "src/b/", "verification_hint": "Check B"},
            ]
        }))

        # Researcher 1: immediate text response
        researcher1_resp = _make_text_response(
            "FINDINGS:\n- Found A stuff\nCOVERAGE: Done"
        )
        # Researcher 2: immediate text response
        researcher2_resp = _make_text_response(
            "FINDINGS:\n- Found B stuff\nCOVERAGE: Done"
        )

        # Synthesizer response
        synth_resp = _make_text_response("Combined answer about A and B.")

        # Verifier response (no tool calls, immediate output — no corrections)
        verify_resp = _make_text_response(
            "VERIFICATION RESULTS:\n"
            "- [VERIFIED] A stuff is correct\n"
            "- [VERIFIED] B stuff is correct\n"
        )

        mock_client.models.generate_content.side_effect = [
            plan_resp,         # Planner
            researcher1_resp,  # Researcher 1
            researcher2_resp,  # Researcher 2
            synth_resp,        # Synthesizer
            verify_resp,       # Verifier
        ]

        progress_events = []
        result = orchestrator.run("How does it work?", on_progress=lambda e: progress_events.append(e))

        assert isinstance(result, QueryResult)
        assert result.answer == "Combined answer about A and B."
        assert result.strategy_name == "multi"
        assert len(result.evidence) == 2  # flat evidence from bundles
        assert result.metadata.get("plan") is not None
        assert len(result.metadata.get("evidence_bundles", [])) == 2
        assert len(result.metadata.get("verification_results", [])) == 2

        # Verify progress callbacks were fired for all phases
        phases = {e.get("phase") for e in progress_events}
        assert "planner" in phases
        assert "researcher" in phases
        assert "synthesizer" in phases
        assert "verifier" in phases
