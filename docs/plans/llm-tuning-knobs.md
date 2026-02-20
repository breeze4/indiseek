# LLM Generation Tuning Knobs

## Problem

All Gemini API calls in the agent loops use default generation parameters. No `temperature`, `thinking_config`, or `max_output_tokens` are set anywhere. The current model (`gemini-3-flash-preview`) defaults to `thinking_level=high`, which means every tool-dispatch iteration burns ~170 thinking tokens even for trivial "pick the right tool" decisions. Thinking tokens are billed as output tokens — the most expensive token class.

Live testing shows that `thinking_level=minimal` cuts total tokens roughly in half for tool-calling iterations (151 vs 320 for a realistic agent prompt) with identical tool selection. Temperature defaults to 1.0, but 0.0 produces more focused tool calls with less thinking overhead.

Additionally, `_extract_usage()` ignores `thoughts_token_count` entirely, so our cost tracking underreports the true cost on thinking models.

## Scope

This plan adds configurable generation parameters to the existing Gemini provider calls. It does NOT change the provider abstraction (that's the swappable-llm-providers plan). All changes are within the current Gemini-only codebase.

## Research Findings

### Thinking Config by Model Family

| Model Family | Parameter | Values | Disable? |
|---|---|---|---|
| Gemini 3 (current) | `thinking_level` | `minimal`, `low`, `medium`, `high` | `minimal` = no thinking |
| Gemini 2.5 | `thinking_budget` | `0` to `24576`, `-1` = dynamic | `0` = no thinking (Flash only) |
| Gemini 2.0 | N/A | N/A | No thinking support |

`thinking_level` errors on 2.5 models. `thinking_budget` errors on 3.x models. The implementation must detect the model family and use the correct parameter.

### Key Parameters Available

| Parameter | Range | Default | Impact |
|---|---|---|---|
| `temperature` | 0.0–2.0 | 1.0 | Lower = more deterministic tool calls |
| `max_output_tokens` | 1–65536 | 65536 | Prevents runaway responses |
| `thinking_config` | See above | `high` / dynamic | Biggest cost lever |
| `top_p` | 0.0–1.0 | 0.95 | Nucleus sampling |
| `top_k` | 1–N | 64 | Top-k selection |

### Phase-Specific Thinking Requirements

Not every agent iteration needs the same reasoning depth:

| Phase | Current Behavior | Optimal Thinking | Rationale |
|---|---|---|---|
| Research (tool dispatch) | `high` (default) | `minimal` or `low` | Model just picks a tool + args. No deep reasoning needed. |
| Critique (iteration 9) | `high` (default) | `high` | Model needs to evaluate its own claims. |
| Synthesis (final answer) | `high` (default) | `high` | Complex reasoning to synthesize findings. |
| Summarizer (file/dir) | `high` (default) | `minimal` | One-sentence summary of a file. Trivial task. |
| Planner (multi-agent) | `high` (default) | `low` or `medium` | Structured decomposition, moderate reasoning. |
| Verifier (multi-agent) | `high` (default) | `high` | Fact-checking requires careful reasoning. |

## Implementation Steps

### Step 1: Add generation config to `config.py`

Add new env vars with sensible defaults:

```
GEMINI_TEMPERATURE       — default: "" (empty = not set, use API default)
GEMINI_THINKING_LEVEL    — default: "" (empty = not set, use model default)
GEMINI_MAX_OUTPUT_TOKENS — default: "" (empty = not set, use API default)
```

Empty string means "don't set this parameter" (use API/model defaults). This preserves current behavior by default — opt-in only.

App still works identically after this step.

### Step 2: Track thinking tokens in `_extract_usage` and `UsageStats`

- Add `thinking_tokens: int = 0` field to `UsageStats`
- Update `UsageStats.add()` to accept an optional third arg for thinking tokens
- Update `UsageStats.merge()` to include thinking tokens
- Update `UsageStats.to_dict()` to include `thinking_tokens` in output
- Update `UsageStats.estimated_cost()`: on the AI Studio API, `candidates_token_count` already includes thinking tokens, so cost calculation doesn't change — but we surface the breakdown
- Update `_extract_usage()` to return a 3-tuple: `(prompt, completion, thinking)` where thinking comes from `usage_metadata.thoughts_token_count`
- Update all call sites of `usage.add(*_extract_usage(response))` — the 3-tuple unpacks cleanly since `add()` now accepts the third arg

Tests still pass. No behavior change. Cost tracking now reports thinking token breakdown.

### Step 3: Create a `build_generation_config` helper

Add a helper function (in `provider.py` or a new `generation_config.py`) that constructs `types.GenerateContentConfig` with the tuning parameters applied:

```
def build_generation_config(
    *,
    system_instruction: str | None = None,
    tools: list | None = None,
    tool_mode: str = "AUTO",
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    thinking_level: str | None = None,
    json_mode: bool = False,
) -> types.GenerateContentConfig:
```

This function:
- Applies `thinking_level` as `ThinkingConfig(thinking_level=...)` for Gemini 3 models
- Applies `thinking_budget` for Gemini 2.5 models (map level names to budget values: minimal=0, low=1024, medium=4096, high=-1)
- Detects model family from `config.GEMINI_MODEL` prefix ("gemini-3" vs "gemini-2.5")
- Sets `temperature`, `max_output_tokens` if non-None
- Sets `response_mime_type="application/json"` if `json_mode=True`
- Handles `tool_mode` -> `FunctionCallingConfig(mode=...)`
- Always sets `automatic_function_calling=disable=True` (agent loops always use manual dispatch)

No behavior change yet — nothing calls this helper.

### Step 4: Wire `build_generation_config` into `AgentLoop`

Replace the inline `types.GenerateContentConfig(...)` construction in `loop.py:248-267` with calls to `build_generation_config()`.

Two configs become:
- `research_config`: `thinking_level` from env var (default not set), `temperature` from env var, `tool_mode="AUTO"`
- `synthesis_config`: `thinking_level` from env var (default not set), `temperature` from env var, `tool_mode="NONE"`

Tests still pass. Behavior is identical when env vars are empty (API defaults preserved).

### Step 5: Wire into `ClassicAgentLoop` and `MultiAgentOrchestrator`

Same change as Step 4 applied to:
- `classic.py` — research_config and synthesis_config
- `multi.py` — all sub-agent configs (PlannerAgent, ResearcherAgent, SynthesizerAgent, VerifierAgent)

Tests still pass. Behavior is identical when env vars are empty.

### Step 6: Wire into `GeminiProvider.generate()` (summarizer)

Update `provider.py:GeminiProvider.generate()` to apply `thinking_level` and `temperature` from config. The summarizer generates trivial one-sentence summaries, so `minimal` thinking is appropriate — but we use the same env var for now (user controls it).

Tests still pass.

### Step 7: Add per-phase thinking level overrides

Now that the plumbing exists, add phase-specific defaults that can be overridden by env vars.

Add a new env var: `GEMINI_THINKING_RESEARCH` (default: empty). When set, research-phase iterations use this thinking level instead of `GEMINI_THINKING_LEVEL`. This lets users set e.g.:

```
GEMINI_THINKING_LEVEL=high          # default for synthesis, critique, verification
GEMINI_THINKING_RESEARCH=minimal    # override for tool-dispatch iterations
```

Apply in `AgentLoop`, `ClassicAgentLoop`, and `ResearcherAgent` (multi-agent). The synthesis/critique/verification phases always use `GEMINI_THINKING_LEVEL`.

### Step 8: Surface thinking config in API response

Update the `/api/query` response to include thinking token breakdown:

```json
{
  "usage": {
    "prompt_tokens": 12345,
    "completion_tokens": 6789,
    "thinking_tokens": 3456,
    "total_tokens": 19134,
    "requests": 14,
    "estimated_cost": 0.045
  }
}
```

This is just exposing what `UsageStats.to_dict()` already returns after Step 2.

### Step 9: Document tuning knobs

Add a "Tuning" section to the top-level CLAUDE.md documenting:
- Available env vars and their effects
- Recommended configurations (cost-optimized vs quality-optimized)
- How thinking tokens affect cost

## Recommended Default Configuration

For a cost-optimized setup that maintains quality:

```env
GEMINI_TEMPERATURE=0.2
GEMINI_THINKING_LEVEL=high
GEMINI_THINKING_RESEARCH=minimal
GEMINI_MAX_OUTPUT_TOKENS=16384
```

This keeps deep reasoning for synthesis/critique while cutting thinking overhead on tool-dispatch iterations.

## Not in Scope

- Per-request tuning via API parameters (future: let callers pass `temperature` etc. in the query body)
- Context caching (separate optimization, needs its own plan)
- Swappable providers (separate plan exists at `docs/plans/swappable-llm-providers.md`)
- `top_p`, `top_k`, `seed`, penalty parameters (diminishing returns — add later if needed)
- Dashboard UI for tuning (env vars are sufficient for now)

## Risks

- **Model family detection**: Parsing the model name prefix ("gemini-3" vs "gemini-2.5") is fragile. If Google changes naming conventions, the detection breaks. Mitigation: the helper falls back to not setting any thinking config if the model family is unrecognized (preserves model defaults).
- **Thinking quality tradeoff**: `minimal` thinking on research iterations could reduce tool selection quality for complex queries. Mitigation: this is opt-in via `GEMINI_THINKING_RESEARCH` env var, and users can benchmark with evals before committing.
- **API compatibility**: `thinking_level` / `thinking_budget` are relatively new parameters. If the SDK version doesn't support them, the import fails at runtime. Mitigation: the helper can catch `AttributeError` and fall back gracefully.
