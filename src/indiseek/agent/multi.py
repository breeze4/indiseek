"""Multi-agent pipeline: Planner -> Researchers -> Synthesizer -> Verifier."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types

from indiseek import config
from indiseek.agent.loop import _extract_usage
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
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures — contracts between agents
# ---------------------------------------------------------------------------


@dataclass
class SubQuestion:
    """One sub-question produced by the Planner."""

    question: str
    target_area: str  # directory or file hint
    initial_actions: list[str] = field(default_factory=list)
    verification_hint: str = ""


@dataclass
class ResearchPlan:
    """Output of the Planner agent."""

    original_question: str
    sub_questions: list[SubQuestion]


@dataclass
class Finding:
    """One piece of evidence found by a Researcher."""

    tool: str
    args: dict
    result_summary: str
    relevant_code: str
    file_path: str
    line_range: tuple[int, int] | None = None


@dataclass
class EvidenceBundle:
    """Output of a single Researcher agent."""

    sub_question: str
    findings: list[Finding]
    coverage_note: str  # what was found vs what remains unanswered


@dataclass
class VerificationResult:
    """One claim verified by the Verifier."""

    claim: str
    status: str  # "verified", "corrected", "unverifiable"
    correction: str = ""


@dataclass
class MultiAgentResult:
    """Final output of the multi-agent pipeline."""

    answer: str
    evidence_bundles: list[EvidenceBundle] = field(default_factory=list)
    verification_results: list[VerificationResult] = field(default_factory=list)
    plan: ResearchPlan | None = None


# ---------------------------------------------------------------------------
# Planner Agent
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """\
You are a codebase research planner. Given a question about a codebase and its \
directory structure, decompose the question into 2-5 specific sub-questions that \
each target a different part of the codebase.

For each sub-question:
1. State the sub-question clearly
2. Identify which part of the codebase likely contains the answer (directory or file path)
3. Suggest 1-2 initial tool calls (search_code query or resolve_symbol target)
4. Provide a verification hint — what specific fact should the Verifier check for this sub-question?

Think about the architecture: most features involve multiple subsystems \
(e.g., storage + API, parsing + transformation + output, client + server, \
detection + processing + delivery). Make sure your sub-questions cover \
different subsystems, not just different aspects of the same code.

## Examples

Question: "How does the authentication middleware work?"
Good decomposition:
- "How are incoming requests intercepted for auth checks?" -> middleware/routing layer
- "How are tokens validated and decoded?" -> auth/token layer
- "How is the authenticated user attached to the request context?" -> context/session layer

Question: "How does the build pipeline transform source files?"
Good decomposition:
- "How are source files discovered and loaded?" -> file resolution/loader
- "What transformations are applied and in what order?" -> transform/plugin pipeline
- "How is the final output written and what format does it use?" -> output/emitter

Output as JSON:
{
  "sub_questions": [
    {
      "question": "...",
      "target_area": "src/some/directory/",
      "initial_actions": ["search_code('relevant query', mode='semantic')"],
      "verification_hint": "Verify: what specific function handles X? What module does it import from?"
    }
  ]
}"""


class PlannerAgent:
    """Decomposes a question into sub-questions, each scoped to a subsystem."""

    def __init__(self, client: genai.Client, model: str) -> None:
        self._client = client
        self._model = model

    def plan(self, question: str, repo_map: str) -> tuple[ResearchPlan, UsageStats]:
        """Produce a research plan from a question and repo map."""
        logger.info("Planner: decomposing question")
        t0 = time.perf_counter()
        usage = UsageStats()

        user_prompt = (
            f"## Repository map\n{repo_map}\n\n"
            f"## Question\n{question}\n\n"
            "Decompose this into sub-questions. Output JSON only."
        )

        response = self._client.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=PLANNER_SYSTEM_PROMPT,
                response_mime_type="application/json",
            ),
        )
        usage.add(*_extract_usage(response))

        elapsed = time.perf_counter() - t0
        raw = response.text or ""
        logger.info("Planner done: %d chars (%.2fs)", len(raw), elapsed)

        return self._parse_plan(question, raw), usage

    def _parse_plan(self, question: str, raw: str) -> ResearchPlan:
        """Parse the JSON plan, with fallback for malformed responses."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
            else:
                logger.warning("Planner returned unparseable response, falling back")
                return ResearchPlan(
                    original_question=question,
                    sub_questions=[
                        SubQuestion(question=question, target_area="", initial_actions=[])
                    ],
                )

        sub_questions = []
        for sq in data.get("sub_questions", []):
            sub_questions.append(
                SubQuestion(
                    question=sq.get("question", ""),
                    target_area=sq.get("target_area", ""),
                    initial_actions=sq.get("initial_actions", []),
                    verification_hint=sq.get("verification_hint", ""),
                )
            )

        if not sub_questions:
            logger.warning("Planner produced 0 sub-questions, falling back")
            sub_questions = [
                SubQuestion(question=question, target_area="", initial_actions=[])
            ]

        plan = ResearchPlan(original_question=question, sub_questions=sub_questions)
        logger.info("Planner produced %d sub-questions", len(plan.sub_questions))
        return plan


# ---------------------------------------------------------------------------
# Researcher Agent
# ---------------------------------------------------------------------------

RESEARCHER_MAX_ITERATIONS = 8
RESEARCHER_CONDENSATION_PHASE = 7  # iteration where we ask for condensation

RESEARCHER_SYSTEM_PROMPT = """\
You are a codebase researcher. Your job is to gather evidence for ONE specific \
sub-question about a codebase. You have access to code search and navigation tools.

## Repository map
{repo_map}

## Your sub-question
{sub_question}

## Target area
Start your investigation in: {target_area}

## Strategy
1. Start with the suggested initial actions, then follow the evidence.
2. Use resolve_symbol for precise navigation after initial searches.
3. Extract key code snippets — don't just note file paths, capture the relevant code.
4. Assess coverage: when you've answered the sub-question, stop early rather than \
wasting remaining budget.
5. You MUST call multiple tools in a single turn when you need independent pieces \
of information.

## Budget
You have {max_iterations} iterations. Focus on gathering evidence, not writing prose. \
Your output will be consumed by a synthesizer agent, so be thorough with code excerpts \
and file references.

## Output format
When you're done researching, write a summary in this format:

FINDINGS:
- [file:line] Description of what was found, with key code excerpts

COVERAGE: What aspects of the sub-question were and weren't answered.
"""


class ResearcherAgent:
    """Gathers evidence for one sub-question via tool-calling loop."""

    def __init__(
        self,
        client: genai.Client,
        model: str,
        store: SqliteStore,
        searcher: CodeSearcher,
        repo_id: int,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._store = store
        self._searcher = searcher
        self._repo_id = repo_id
        self._tool_registry = tool_registry

    def research(self, sub_question: SubQuestion, repo_map: str) -> tuple[EvidenceBundle, UsageStats]:
        """Run the research loop for a single sub-question."""
        logger.info("Researcher: %r", sub_question.question[:80])
        t0 = time.perf_counter()
        usage = UsageStats()

        # Per-run caches (isolated per researcher)
        file_cache: dict[str, str] = {}
        query_cache = QueryCache()
        resolve_cache: dict[tuple[str, str], str] = {}

        # Build per-run tool registry with fresh caches
        tool_reg = self._tool_registry or build_tool_registry(
            self._store, self._searcher, self._repo_id,
            file_cache=file_cache,
            query_cache=query_cache,
            resolve_cache=resolve_cache,
        )

        system_prompt = RESEARCHER_SYSTEM_PROMPT.format(
            repo_map=repo_map,
            sub_question=sub_question.question,
            target_area=sub_question.target_area or "(not specified)",
            max_iterations=RESEARCHER_MAX_ITERATIONS,
        )

        gemini_decls = tool_reg.get_gemini_declarations()
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
        condensation_config = types.GenerateContentConfig(
            tools=tools,
            system_instruction=system_prompt,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="NONE")
            ),
        )

        # Initial user message
        initial_prompt = f"Research this sub-question: {sub_question.question}"
        if sub_question.initial_actions:
            initial_prompt += (
                "\n\nSuggested starting points: "
                + ", ".join(sub_question.initial_actions)
            )

        contents: list[types.Content] = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=initial_prompt)],
            )
        ]

        findings_text = ""
        tool_call_count = 0
        original_question = sub_question.question

        for iteration in range(RESEARCHER_MAX_ITERATIONS):
            # Force condensation on last iteration
            if iteration >= RESEARCHER_CONDENSATION_PHASE:
                gen_config = condensation_config
                if iteration == RESEARCHER_CONDENSATION_PHASE:
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_text(
                                    text="Summarize your findings now. List each finding "
                                    "with file:line references and key code excerpts. "
                                    "Note what was and wasn't answered."
                                )
                            ],
                        )
                    )
            else:
                gen_config = research_config

            logger.debug(
                "Researcher iter %d/%d for %r",
                iteration + 1,
                RESEARCHER_MAX_ITERATIONS,
                sub_question.question[:50],
            )

            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=gen_config,
            )
            usage.add(*_extract_usage(response))

            model_content = response.candidates[0].content
            contents.append(model_content)

            # Text response = done researching
            if not response.function_calls:
                findings_text = response.text or ""
                break

            # Execute tool calls
            fn_response_parts: list[types.Part] = []
            tool_call_count += len(response.function_calls)

            for i, call in enumerate(response.function_calls):
                args = dict(call.args) if call.args else {}
                logger.info(
                    "  Researcher -> %s(%s)",
                    call.name,
                    ", ".join(f"{k}={v!r}" for k, v in args.items()),
                )

                try:
                    result = tool_reg.execute(call.name, args)
                except Exception as e:
                    result = f"Error: {e}"
                    logger.error("  Researcher tool error: %s: %s", call.name, e)

                # Question reiteration: first tool response per turn
                if i == 0:
                    result = f"[QUESTION: {original_question}]\n" + result

                # Budget injection
                remaining = RESEARCHER_MAX_ITERATIONS - iteration - 1
                if remaining <= 2:
                    result += (
                        f"\n[Iteration {iteration + 1}/{RESEARCHER_MAX_ITERATIONS} "
                        "— summarize your findings now]"
                    )

                fn_response_parts.append(
                    types.Part.from_function_response(
                        name=call.name,
                        response={"result": result},
                    )
                )

            contents.append(types.Content(role="user", parts=fn_response_parts))

        elapsed = time.perf_counter() - t0
        logger.info(
            "Researcher done: %d tool calls, %d chars findings (%.2fs)",
            tool_call_count,
            len(findings_text),
            elapsed,
        )

        return self._parse_evidence(sub_question.question, findings_text), usage

    def _parse_evidence(self, sub_question: str, text: str) -> EvidenceBundle:
        """Parse researcher output into structured EvidenceBundle."""
        coverage_note = ""
        findings_text = text
        if "COVERAGE:" in text:
            parts = text.split("COVERAGE:", 1)
            findings_text = parts[0].strip()
            coverage_note = parts[1].strip()

        return EvidenceBundle(
            sub_question=sub_question,
            findings=[
                Finding(
                    tool="researcher_summary",
                    args={},
                    result_summary=findings_text,
                    relevant_code="",
                    file_path="",
                )
            ],
            coverage_note=coverage_note,
        )


# ---------------------------------------------------------------------------
# Synthesizer Agent
# ---------------------------------------------------------------------------

SYNTHESIZER_SYSTEM_PROMPT = """\
You are a technical writer synthesizing research about a codebase. Multiple \
researchers have each investigated a different aspect of the question. Your job \
is to combine their evidence into a comprehensive, well-structured answer.

## Rules
1. Structure the answer with markdown headers (##, ###). Every major aspect \
gets its own section.
2. Preserve key code snippets from the evidence — inline them in the answer \
with file:line citations.
3. If a researcher said they could NOT find or verify something, say so. \
NEVER fabricate code or behavior that isn't in the evidence.
4. Identify distinct behavioral paths or variants and give each its own section.
5. Your answer should be thorough — aim for a length proportional to the \
evidence provided. If the researchers wrote 2000 words of findings, your \
answer should not be 200 words.
6. End with a brief summary table or list of the key mechanisms if appropriate."""


class SynthesizerAgent:
    """Combines evidence bundles into a coherent answer. No tool access."""

    def __init__(self, client: genai.Client, model: str) -> None:
        self._client = client
        self._model = model

    def _format_evidence(self, evidence_bundles: list[EvidenceBundle]) -> str:
        """Format evidence bundles into a text block for the LLM."""
        sections = []
        for i, bundle in enumerate(evidence_bundles, 1):
            findings_text = "\n".join(
                f.result_summary for f in bundle.findings if f.result_summary
            )
            section = (
                f"### Researcher {i} (sub-question: \"{bundle.sub_question}\")\n"
                f"{findings_text}\n"
            )
            if bundle.coverage_note:
                section += f"\nCoverage: {bundle.coverage_note}\n"
            sections.append(section)
        return "\n".join(sections)

    def synthesize(
        self, question: str, evidence_bundles: list[EvidenceBundle]
    ) -> tuple[str, UsageStats]:
        """Produce a coherent answer from evidence bundles."""
        logger.info("Synthesizer: combining %d evidence bundles", len(evidence_bundles))
        t0 = time.perf_counter()
        usage = UsageStats()

        evidence_text = self._format_evidence(evidence_bundles)

        user_prompt = (
            f"## Question\n{question}\n\n"
            f"## Evidence from researchers\n\n"
            f"{evidence_text}\n\n"
            "Write a comprehensive, well-structured answer. Use markdown headers "
            "for each major section. Include code snippets from the evidence with "
            "file:line citations. Do not omit details that the researchers found."
        )

        response = self._client.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYNTHESIZER_SYSTEM_PROMPT,
            ),
        )
        usage.add(*_extract_usage(response))

        answer = response.text or "(no answer produced)"
        elapsed = time.perf_counter() - t0
        logger.info("Synthesizer done: %d chars (%.2fs)", len(answer), elapsed)
        logger.debug("Synthesizer raw output:\n%s", answer[:3000])
        return answer, usage

    def revise(self, answer: str, corrections: list[VerificationResult]) -> tuple[str, UsageStats]:
        """Revise an answer by applying specific corrections from the verifier."""
        logger.info("Synthesizer: revising answer with %d corrections", len(corrections))
        t0 = time.perf_counter()
        usage = UsageStats()

        corrections_text = "\n".join(
            f"- {c.claim} -> {c.correction}" for c in corrections
        )

        user_prompt = (
            f"## Current answer\n{answer}\n\n"
            f"## Corrections from fact-checker\n{corrections_text}\n\n"
            "Revise the answer to incorporate these corrections. Keep the same "
            "structure and level of detail — only change the parts that are wrong. "
            "Do not shorten or restructure the answer."
        )

        response = self._client.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYNTHESIZER_SYSTEM_PROMPT,
            ),
        )
        usage.add(*_extract_usage(response))

        revised = response.text or answer
        elapsed = time.perf_counter() - t0
        logger.info("Synthesizer revision done: %d chars (%.2fs)", len(revised), elapsed)
        logger.debug("Synthesizer revised output:\n%s", revised[:3000])
        return revised, usage


# ---------------------------------------------------------------------------
# Verifier Agent
# ---------------------------------------------------------------------------

VERIFIER_MAX_ITERATIONS = 6

VERIFIER_SYSTEM_PROMPT = """\
You are a codebase fact-checker. You have been given an answer about a codebase \
and verification hints from the research planner. Your job is to verify factual \
claims against the actual code.

## Repository map
{repo_map}

## Verification hints
{verification_hints}

## Process
1. Extract every factual claim that references specific code (function names, \
file paths, behaviors, call relationships).
2. For each claim, use the available tools to verify it.
3. Mark each claim as VERIFIED, CORRECTED, or UNVERIFIABLE.
4. If CORRECTED, provide the correct information from the code you read.

## Budget
You have {max_iterations} iterations. Focus on the most important claims first — \
especially those related to the verification hints.

## Output format
When done verifying, output ONLY the verification results. Do NOT rewrite the \
answer — just list corrections.

VERIFICATION RESULTS:
- [VERIFIED] claim description
- [CORRECTED] claim description -> what the code actually shows
- [UNVERIFIABLE] claim description
"""


class VerifierAgent:
    """Verifies claims in a synthesized answer against the codebase."""

    def __init__(
        self,
        client: genai.Client,
        model: str,
        store: SqliteStore,
        searcher: CodeSearcher,
        repo_id: int,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._store = store
        self._searcher = searcher
        self._repo_id = repo_id
        self._tool_registry = tool_registry

    def verify(
        self,
        answer: str,
        verification_hints: list[str],
        repo_map: str,
    ) -> tuple[list[VerificationResult], UsageStats]:
        """Verify claims in the answer. Returns list of verification results and usage."""
        logger.info("Verifier: checking answer (%d chars)", len(answer))
        t0 = time.perf_counter()
        usage = UsageStats()

        # Per-run caches
        file_cache: dict[str, str] = {}
        query_cache = QueryCache()
        resolve_cache: dict[tuple[str, str], str] = {}

        tool_reg = self._tool_registry or build_tool_registry(
            self._store, self._searcher, self._repo_id,
            file_cache=file_cache,
            query_cache=query_cache,
            resolve_cache=resolve_cache,
        )

        hints_text = "\n".join(f"- {h}" for h in verification_hints) if verification_hints else "(none)"

        system_prompt = VERIFIER_SYSTEM_PROMPT.format(
            repo_map=repo_map,
            verification_hints=hints_text,
            max_iterations=VERIFIER_MAX_ITERATIONS,
        )

        gemini_decls = tool_reg.get_gemini_declarations()
        tools = [types.Tool(function_declarations=gemini_decls)]
        verify_config = types.GenerateContentConfig(
            tools=tools,
            system_instruction=system_prompt,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
        )
        final_config = types.GenerateContentConfig(
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
                parts=[
                    types.Part.from_text(
                        text=f"Verify the factual claims in this answer:\n\n{answer}"
                    )
                ],
            )
        ]

        verification_text = ""
        tool_call_count = 0

        for iteration in range(VERIFIER_MAX_ITERATIONS):
            # Force output on last iteration
            if iteration >= VERIFIER_MAX_ITERATIONS - 1:
                gen_config = final_config
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text="Output your verification results now."
                            )
                        ],
                    )
                )
            else:
                gen_config = verify_config

            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=gen_config,
            )
            usage.add(*_extract_usage(response))

            model_content = response.candidates[0].content
            contents.append(model_content)

            if not response.function_calls:
                verification_text = response.text or ""
                break

            # Execute tool calls
            fn_response_parts: list[types.Part] = []
            tool_call_count += len(response.function_calls)

            for call in response.function_calls:
                args = dict(call.args) if call.args else {}
                logger.info(
                    "  Verifier -> %s(%s)",
                    call.name,
                    ", ".join(f"{k}={v!r}" for k, v in args.items()),
                )

                try:
                    result = tool_reg.execute(call.name, args)
                except Exception as e:
                    result = f"Error: {e}"
                    logger.error("  Verifier tool error: %s: %s", call.name, e)

                fn_response_parts.append(
                    types.Part.from_function_response(
                        name=call.name,
                        response={"result": result},
                    )
                )

            contents.append(types.Content(role="user", parts=fn_response_parts))

        elapsed = time.perf_counter() - t0
        logger.info(
            "Verifier done: %d tool calls (%.2fs)", tool_call_count, elapsed
        )
        logger.debug("Verifier raw output:\n%s", verification_text[:3000])

        return self._parse_verification(verification_text), usage

    def _parse_verification(self, text: str) -> list[VerificationResult]:
        """Parse verification output into structured results."""
        results: list[VerificationResult] = []

        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("- ["):
                continue

            if "[VERIFIED]" in line:
                claim = line.split("[VERIFIED]", 1)[1].strip()
                results.append(
                    VerificationResult(claim=claim, status="verified")
                )
            elif "[CORRECTED]" in line:
                parts = line.split("[CORRECTED]", 1)[1].strip()
                if " -> " in parts:
                    claim, correction = parts.split(" -> ", 1)
                    results.append(
                        VerificationResult(
                            claim=claim.strip(),
                            status="corrected",
                            correction=correction.strip(),
                        )
                    )
                else:
                    results.append(
                        VerificationResult(claim=parts, status="corrected")
                    )
            elif "[UNVERIFIABLE]" in line:
                claim = line.split("[UNVERIFIABLE]", 1)[1].strip()
                results.append(
                    VerificationResult(claim=claim, status="unverifiable")
                )

        return results


# ---------------------------------------------------------------------------
# Routing heuristic (used by StrategyRegistry.auto_select)
# ---------------------------------------------------------------------------

_COMPLEX_PATTERNS = re.compile(
    r"\b(how|why|explain|describe|walk me through|end.to.end|flow|architecture|"
    r"pipeline|lifecycle|process|interaction|relationship)\b",
    re.IGNORECASE,
)


def _is_complex_query(question: str) -> bool:
    """Heuristic: should this question use the multi-agent pipeline?"""
    if len(question.split()) > 15:
        return True
    if _COMPLEX_PATTERNS.search(question):
        return True
    return False


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class MultiAgentOrchestrator:
    """Ties the four agents together: Planner -> Researchers -> Synthesizer -> Verifier."""

    name = "multi"

    def __init__(
        self,
        store: SqliteStore,
        repo_path: Path,
        searcher: CodeSearcher,
        api_key: str | None = None,
        model: str | None = None,
        repo_id: int = 1,
    ) -> None:
        self._store = store
        self._repo_path = repo_path
        self._searcher = searcher
        self._repo_id = repo_id
        self._client = genai.Client(api_key=api_key or config.GEMINI_API_KEY)
        self._model = model or config.GEMINI_MODEL

    def run(
        self,
        question: str,
        on_progress: Callable[[dict], None] | None = None,
    ) -> QueryResult:
        """Run the full multi-agent pipeline."""
        logger.info("Multi-agent run started: %r", question[:120])
        run_t0 = time.perf_counter()
        total_usage = UsageStats()

        # Build repo map once — shared by all agents
        repo_map = read_map(self._store, repo_id=self._repo_id)

        # Phase 1: Plan
        if on_progress:
            on_progress({"step": "query", "phase": "planner", "summary": "Decomposing question into sub-questions..."})

        planner = PlannerAgent(self._client, self._model)
        plan, planner_usage = planner.plan(question, repo_map)
        total_usage.merge(planner_usage)

        # Fallback: if planner produces only 1 sub-question, fall back to
        # single-agent loop — multi-agent adds overhead without benefit.
        if len(plan.sub_questions) < 2:
            logger.warning(
                "Planner produced %d sub-question(s) — falling back to single-agent",
                len(plan.sub_questions),
            )
            from indiseek.agent.loop import AgentLoop

            single_agent = AgentLoop(
                self._store, self._repo_path, self._searcher,
                repo_id=self._repo_id,
            )
            single_result = single_agent.run(question, on_progress=on_progress)
            # Merge single-agent usage with planner usage
            single_usage_dict = single_result.metadata.get("usage", {})
            total_usage.prompt_tokens += single_usage_dict.get("prompt_tokens", 0)
            total_usage.completion_tokens += single_usage_dict.get("completion_tokens", 0)
            total_usage.requests += single_usage_dict.get("requests", 0)
            return QueryResult(
                answer=single_result.answer,
                evidence=single_result.evidence,
                metadata={
                    "plan": _plan_to_dict(plan),
                    "usage": total_usage.to_dict(self._model),
                },
                strategy_name=self.name,
            )

        if on_progress:
            on_progress({
                "step": "query",
                "phase": "planner",
                "summary": f"Plan: {len(plan.sub_questions)} sub-questions",
            })

        # Phase 2: Research (sequential for now)
        evidence_bundles: list[EvidenceBundle] = []
        for i, sq in enumerate(plan.sub_questions):
            if on_progress:
                on_progress({
                    "step": "query",
                    "phase": "researcher",
                    "researcher_index": i + 1,
                    "researcher_total": len(plan.sub_questions),
                    "summary": f"Researching: {sq.question[:80]}...",
                })

            researcher = ResearcherAgent(
                self._client, self._model, self._store, self._searcher, self._repo_id
            )
            bundle, researcher_usage = researcher.research(sq, repo_map)
            evidence_bundles.append(bundle)
            total_usage.merge(researcher_usage)

        # Phase 3: Synthesize
        if on_progress:
            on_progress({"step": "query", "phase": "synthesizer", "summary": "Synthesizing answer from evidence..."})

        synthesizer = SynthesizerAgent(self._client, self._model)
        answer, synth_usage = synthesizer.synthesize(question, evidence_bundles)
        total_usage.merge(synth_usage)

        # Phase 4: Verify
        if on_progress:
            on_progress({"step": "query", "phase": "verifier", "summary": "Verifying claims against code..."})

        verification_hints = [
            sq.verification_hint
            for sq in plan.sub_questions
            if sq.verification_hint
        ]
        verifier = VerifierAgent(
            self._client, self._model, self._store, self._searcher, self._repo_id
        )
        verification_results, verifier_usage = verifier.verify(
            answer, verification_hints, repo_map
        )
        total_usage.merge(verifier_usage)

        verified = sum(1 for v in verification_results if v.status == "verified")
        corrected = sum(1 for v in verification_results if v.status == "corrected")

        if on_progress:
            on_progress({
                "step": "query",
                "phase": "verifier",
                "summary": f"Verification: {verified} verified, {corrected} corrected",
            })

        # Phase 5: Re-synthesize if verifier found corrections
        final_answer = answer
        corrections = [v for v in verification_results if v.status == "corrected"]
        if corrections:
            if on_progress:
                on_progress({
                    "step": "query",
                    "phase": "synthesizer",
                    "summary": f"Re-synthesizing with {len(corrections)} correction(s)...",
                })
            final_answer, revise_usage = synthesizer.revise(answer, corrections)
            total_usage.merge(revise_usage)

        total = time.perf_counter() - run_t0
        logger.info("Multi-agent run complete: %.2fs", total)

        # Build flat evidence from bundles
        evidence = []
        for bundle in evidence_bundles:
            for f in bundle.findings:
                evidence.append(
                    EvidenceStep(
                        tool=f.tool,
                        args=f.args,
                        summary=f.result_summary,
                    )
                )

        return QueryResult(
            answer=final_answer,
            evidence=evidence,
            metadata={
                "plan": _plan_to_dict(plan),
                "evidence_bundles": [
                    {
                        "sub_question": b.sub_question,
                        "findings": [
                            {"tool": f.tool, "args": f.args, "summary": f.result_summary}
                            for f in b.findings
                        ],
                        "coverage_note": b.coverage_note,
                    }
                    for b in evidence_bundles
                ],
                "verification_results": [
                    {"claim": v.claim, "status": v.status, "correction": v.correction}
                    for v in verification_results
                ],
                "usage": total_usage.to_dict(self._model),
            },
            strategy_name=self.name,
        )


def _plan_to_dict(plan: ResearchPlan) -> dict:
    """Serialize a ResearchPlan to a dict for metadata."""
    return {
        "original_question": plan.original_question,
        "sub_questions": [
            {
                "question": sq.question,
                "target_area": sq.target_area,
                "initial_actions": sq.initial_actions,
                "verification_hint": sq.verification_hint,
            }
            for sq in plan.sub_questions
        ],
    }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_multi_agent(
    store: SqliteStore | None = None,
    repo_path: Path | None = None,
    api_key: str | None = None,
    model: str | None = None,
    repo_id: int = 1,
) -> MultiAgentOrchestrator:
    """Create a MultiAgentOrchestrator with default configuration."""
    if store is None:
        store = SqliteStore(config.SQLITE_PATH)

    if repo_path is None:
        repo_path = config.get_repo_path(repo_id)

    from indiseek.indexer.lexical import LexicalIndexer
    from indiseek.storage.vector_store import VectorStore

    vector_store = None
    embed_fn = None
    lexical_indexer = None

    lancedb_table = config.get_lancedb_table_name(repo_id)
    if config.LANCEDB_PATH.exists() and config.GEMINI_API_KEY:
        try:
            from indiseek.agent.provider import GeminiProvider

            vs = VectorStore(
                config.LANCEDB_PATH,
                dims=config.EMBEDDING_DIMS,
                table_name=lancedb_table,
            )
            vs.init_table()
            count = vs.count()
            if count > 0:
                vector_store = vs
                provider = GeminiProvider(api_key=api_key)
                embed_fn = lambda text: provider.embed([text])[0]  # noqa: E731
                logger.info("Multi-agent semantic search: enabled (%d vectors)", count)
        except Exception as e:
            logger.warning("Multi-agent semantic search: disabled (%s)", e)

    tantivy_path = config.get_tantivy_path(repo_id)
    if tantivy_path.exists():
        try:
            li = LexicalIndexer(store, tantivy_path)
            li.open_index()
            lexical_indexer = li
            logger.info("Multi-agent lexical search: enabled")
        except Exception as e:
            logger.warning("Multi-agent lexical search: disabled (%s)", e)

    searcher = CodeSearcher(
        vector_store=vector_store,
        lexical_indexer=lexical_indexer,
        embed_fn=embed_fn,
    )

    return MultiAgentOrchestrator(
        store=store,
        repo_path=repo_path,
        searcher=searcher,
        api_key=api_key,
        model=model,
        repo_id=repo_id,
    )


# ---------------------------------------------------------------------------
# Strategy registration
# ---------------------------------------------------------------------------


def _create_multi_strategy(
    repo_id: int = 1,
    store: SqliteStore | None = None,
    repo_path: Path | None = None,
    api_key: str | None = None,
    model: str | None = None,
    **_kwargs,
) -> MultiAgentOrchestrator:
    """Factory function for the 'multi' strategy."""
    return create_multi_agent(
        store=store, repo_path=repo_path, api_key=api_key, model=model, repo_id=repo_id,
    )


def register_multi_strategy() -> None:
    """Register the multi-agent strategy with the global registry."""
    strategy_registry.register("multi", _create_multi_strategy)


# Auto-register on import
register_multi_strategy()
