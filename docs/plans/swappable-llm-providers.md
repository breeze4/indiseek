# Swappable LLM Providers

## Problem

All three agent loops and 4 multi-agent sub-agents directly use `genai.Client` and ~12 `google.genai.types` classes. There are `EmbeddingProvider` and `GenerationProvider` protocols in `provider.py` but the agent loops bypass them. Supporting OpenAI (or any non-Gemini provider) for the agent loop requires abstracting away the tool-calling conversation format.

## Three Abstractions Needed

| Capability | Current Protocol | Used By | Status |
|---|---|---|---|
| Embeddings | `EmbeddingProvider` | `Embedder`, query-time semantic search | Protocol exists, only Gemini impl |
| Simple generation | `GenerationProvider` | `Summarizer` | Protocol exists, only Gemini impl |
| Tool-calling conversation | *(none)* | All 3 agent loops + 4 sub-agents | Not abstracted at all |

The tool-calling conversation is the hard part. The other two are straightforward: implement OpenAI versions of existing protocols.

## Design: ChatClient Protocol

### Canonical Types

```
ToolCallRequest:
    id: str           # unique per call (OpenAI requires this, generate UUID for Gemini)
    name: str
    args: dict

ToolResult:
    call_id: str      # matches ToolCallRequest.id
    name: str
    result: str

Message:
    role: str          # "user", "assistant"
    text: str | None
    tool_calls: list[ToolCallRequest] | None
    tool_results: list[ToolResult] | None

ChatResponse:
    text: str | None
    tool_calls: list[ToolCallRequest]
    prompt_tokens: int
    completion_tokens: int
    message: Message   # the assistant message to append to conversation history
```

### ChatClient Protocol

```
chat(
    messages: list[Message],
    tools: list[dict] | None,    # provider-agnostic schemas from ToolRegistry.get_declarations()
    tool_mode: str,              # "auto" or "none"
    system: str | None,
    json_mode: bool = False,     # for PlannerAgent structured output
) -> ChatResponse
```

Each provider implementation converts between the canonical types and its own SDK types:
- `GeminiChatClient`: Message <-> types.Content, dict <-> types.FunctionDeclaration, etc.
- `OpenAIChatClient`: Message <-> OpenAI chat format dicts

### Embedding Constraint

Embeddings are **per-repo, set at index time**. If a repo is indexed with Gemini embeddings (768d), it must be queried with Gemini embeddings. Mixing providers produces garbage results. The repo record should track which embedding provider was used, and query-time code must use the same one.

## File Structure

```
src/indiseek/agent/
    provider.py          # canonical types + protocols (ChatClient, EmbeddingProvider, GenerationProvider)
    gemini_provider.py   # GeminiChatClient, GeminiEmbeddingProvider, GeminiGenerationProvider
    openai_provider.py   # OpenAIChatClient, OpenAIEmbeddingProvider, OpenAIGenerationProvider
```

Move `GeminiProvider` out of `provider.py` into `gemini_provider.py`. Split it into `GeminiChatClient` (new, for agent loops) and `GeminiEmbeddingProvider` + `GeminiGenerationProvider` (existing logic, just moved).

## Config Changes

New env vars:
- `LLM_PROVIDER` — default provider for agent loop: "gemini" (default) or "openai"
- `EMBEDDING_PROVIDER` — default provider for new repo indexing: "gemini" (default) or "openai"
- `OPENAI_API_KEY`
- `OPENAI_MODEL` — default: "gpt-4o"
- `OPENAI_EMBEDDING_MODEL` — default: "text-embedding-3-small"
- `OPENAI_EMBEDDING_DIMS` — default: 1536

Per-repo overrides: `repos` table gets `llm_provider` and `embedding_provider` columns (nullable, falls back to env var defaults).

## Implementation Steps

### Step 1: Define canonical types and ChatClient protocol
- Add `ToolCallRequest`, `ToolResult`, `Message`, `ChatResponse` dataclasses to `provider.py`
- Add `ChatClient` protocol to `provider.py`
- Keep existing `EmbeddingProvider` and `GenerationProvider` protocols as-is
- No behavior change. Tests still pass.

### Step 2: Implement GeminiChatClient
- Create `gemini_provider.py`
- Move `GeminiProvider` class from `provider.py` to `gemini_provider.py`
- Add `GeminiChatClient` class that wraps `genai.Client` and implements `ChatClient`:
  - `chat()` converts `Message` list → `types.Content` list, calls `generate_content`, converts response → `ChatResponse`
  - Converts tool schemas (dicts from `get_declarations()`) → `types.FunctionDeclaration`
  - Handles `tool_mode` → `FunctionCallingConfig(mode="AUTO"/"NONE")`
  - Handles `system` → `system_instruction` in config
  - Handles `json_mode` → `response_mime_type="application/json"`
- Update imports in `embedder.py` and `summarizer.py` (from `provider` → `gemini_provider`)
- No behavior change. Tests still pass.

### Step 3: Refactor ClassicAgentLoop to use ChatClient
- Constructor takes `ChatClient` instead of `api_key`/model
- Replace `self._client = genai.Client(...)` with `self._chat_client = chat_client`
- Replace `types.Content` construction with `Message` construction
- Replace `types.Part.from_function_response` with `ToolResult`
- Replace `get_gemini_declarations()` with `get_declarations()`
- Replace `_extract_usage(response)` with `response.prompt_tokens, response.completion_tokens`
- Replace `response.function_calls` / `response.text` with `ChatResponse` fields
- Replace `response.candidates[0].content` → `response.message` for conversation history
- Update `_create_classic_strategy` factory to construct `GeminiChatClient` and pass it
- Tests pass.

### Step 4: Refactor AgentLoop to use ChatClient
- Same changes as Step 3 applied to `loop.py`
- Update `create_agent_loop` factory function
- Tests pass.

### Step 5: Refactor MultiAgentOrchestrator to use ChatClient
- Same changes for `MultiAgentOrchestrator` and its four sub-agents:
  - `PlannerAgent(client, model)` → `PlannerAgent(chat_client)`
  - `ResearcherAgent(client, model, ...)` → `ResearcherAgent(chat_client, ...)`
  - `SynthesizerAgent(client, model)` → `SynthesizerAgent(chat_client)`
  - `VerifierAgent(client, model, ...)` → `VerifierAgent(chat_client, ...)`
- Update `create_multi_agent` factory
- Tests pass.

### Step 6: Implement OpenAI providers
- Create `openai_provider.py`
- `OpenAIChatClient`: implements `ChatClient` using `openai` SDK
  - Converts `Message` → OpenAI chat format (role, content, tool_calls, tool_call_id)
  - Converts tool schemas → OpenAI function definitions
  - Handles `json_mode` → `response_format={"type": "json_object"}`
- `OpenAIEmbeddingProvider`: implements `EmbeddingProvider`
- `OpenAIGenerationProvider`: implements `GenerationProvider` (thin wrapper on ChatClient, no tools)
- Add `openai` to optional dependencies in `pyproject.toml`
- Unit tests for OpenAI provider (mock the SDK)

### Step 7: Config and provider factory
- Add env vars to `config.py`: `LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_DIMS`
- Add `create_chat_client(provider: str) -> ChatClient` factory in `provider.py`
- Add `create_embedding_provider(provider: str) -> EmbeddingProvider` factory
- Add `embedding_provider` column to `repos` table (nullable, default NULL = use env var)
- Add `llm_provider` column to `repos` table (nullable, default NULL = use env var)
- Update `create_agent_loop`, `create_multi_agent`, strategy factories to use the factory
- Wire through API: strategy creation reads repo's provider config

### Step 8: Update indexing pipeline
- `scripts/index.py` respects `EMBEDDING_PROVIDER` env var (or per-repo setting)
- Summarizer respects `LLM_PROVIDER` env var (or per-repo setting)
- Store which embedding provider was used in repo metadata
- Query-time semantic search validates embedding provider matches what was used at index time

## Scope Boundary

NOT in this plan:
- Git auth (separate plan)
- Per-repo provider config UI in dashboard (can be added later, API-only for now)
- Agent loop deduplication (ClassicAgentLoop and AgentLoop share ~80% code — refactor opportunity but out of scope)
- Anthropic provider (add later using same pattern)

## Risks

- **OpenAI tool calling format differences**: OpenAI requires `tool_call_id` on every tool result. Gemini uses name matching. The canonical `ToolCallRequest.id` field handles this — Gemini adapter generates UUIDs, OpenAI adapter passes through the real IDs.
- **JSON mode differences**: Gemini uses `response_mime_type`, OpenAI uses `response_format`. Both are standard and well-supported. The `json_mode` parameter on `ChatClient.chat()` abstracts this.
- **Embedding dimension mismatch**: Must not mix. Enforced at query time by checking repo metadata.
- **Model capability differences**: Gemini and OpenAI have different strengths in tool calling. System prompts may need tuning per provider. Out of scope for now — optimize later based on testing.
