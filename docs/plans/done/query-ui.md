# Query UI for Dashboard

## Context

The dashboard has pages for inspecting index state, triggering indexing operations, and searching code chunks, but querying the agent loop still requires `curl -X POST /query`. This adds a Query page where users submit natural language questions and see the agent's answer + evidence trail in the browser, with live progress as the agent calls tools.

The agent loop (`AgentLoop.run()`) takes 30-120 seconds per query (up to 15 Gemini API calls). The existing `/query` endpoint is synchronous — blocks until the loop finishes. For the UI we need non-blocking execution with real-time progress.

The dashboard already has background task execution with SSE streaming (TaskManager + `/tasks/{id}/stream` + `useTaskStream` hook). We reuse all of that.

### Spec updates needed
- `docs/SPEC.md`: Remove "No UI (curl only)" and "No streaming responses" from MVP constraints (both are now implemented)
- `docs/SPEC-dashboard.md`: Remove "Not a query interface for the agent loop" from "What This Is NOT", add Query page to Pages section

## Files to modify/create

**Modified:**
- `src/indiseek/agent/loop.py` — add `on_progress` callback to `run()`
- `src/indiseek/api/dashboard.py` — add `POST /run/query` endpoint
- `frontend/src/api/client.ts` — add query types and `runQuery()`
- `frontend/src/api/hooks.ts` — add `useRunQuery()` hook
- `frontend/src/App.tsx` — add Query nav item and route
- `docs/SPEC.md` — update MVP constraints
- `docs/SPEC-dashboard.md` — add Query page spec

**Created:**
- `frontend/src/pages/Query.tsx` — query page component

## Implementation Checklist

- [ ] **Step 1: Update specs.** In `docs/SPEC.md`, remove "No UI (curl only)" and "No streaming responses" from the MVP constraints list. In `docs/SPEC-dashboard.md`, remove "Not a query interface for the agent loop" from "What This Is NOT" and add a "6. Query" page section describing the feature. App still works unchanged.

- [ ] **Step 2: Add `on_progress` callback to `AgentLoop.run()`.** In `src/indiseek/agent/loop.py`, add `on_progress: Callable[[dict], None] | None = None` parameter to `run()`. After each tool call completes (at line 538 where `evidence.append(...)` happens), call `on_progress({"step": "query", "iteration": iteration+1, "tool": call.name, "args": args, "summary": summary})`. When `on_progress` is None, behavior is identical to today. Existing `/query` endpoint and tests unchanged.

- [ ] **Step 3: Add `POST /run/query` endpoint to dashboard router.** In `src/indiseek/api/dashboard.py`, add a new endpoint. It lazy-inits the agent loop (same `create_agent_loop()` from `server.py`), submits `agent.run(prompt, on_progress=callback)` to the existing `_task_manager`, returns `{"task_id", "name": "query", "status": "running"}`. The task result dict is `{"answer": str, "evidence": [{"tool", "args", "summary"}]}`. The existing SSE endpoint (`GET /tasks/{id}/stream`) and task list endpoints handle the rest. Returns 409 if a task is already running, 400 if GEMINI_API_KEY is not set.

- [ ] **Step 4: Add query API function and types to frontend client.** In `frontend/src/api/client.ts`, add `QueryResult` interface (`{ answer: string, evidence: { tool: string, args: Record<string, unknown>, summary: string }[] }`) and `runQuery(prompt: string)` function calling `POST /dashboard/api/run/query`.

- [ ] **Step 5: Add `useRunQuery` hook.** In `frontend/src/api/hooks.ts`, add `useRunQuery()` — a `useMutation` wrapping `runQuery()`, invalidates tasks query on success. Same pattern as existing `useRunOperation`.

- [ ] **Step 6: Create Query page.** New file `frontend/src/pages/Query.tsx`. Layout: prompt textarea + Submit button at top. Below: live progress log showing each tool call as it streams in (reuse pattern from Operations page `ProgressLog`). Below that: answer section (appears on done event, rendered in a styled `<div>` with `whitespace-pre-wrap`). Below that: collapsible evidence trail showing each tool call with its summary. States: idle (prompt input only), running (input disabled, progress visible, pulsing dot), complete (answer + evidence shown, input re-enabled).

- [ ] **Step 7: Add Query route to App.** In `frontend/src/App.tsx`, import `Query` page, add nav item with `MessageSquare` icon from lucide-react, add `<Route path="/query" element={<Query />} />`.

- [ ] **Step 8: Build and verify.** `cd frontend && npm run build` succeeds. `pytest` passes. `ruff check` on modified Python files passes. Manual test: start server, open `/dashboard`, navigate to Query, submit a question, verify progress streams in real time, answer renders, evidence trail shown.

## Verification

1. `pytest` — all existing tests pass (no behavioral changes to existing code)
2. `ruff check src/indiseek/agent/loop.py src/indiseek/api/dashboard.py` — clean
3. `cd frontend && npm run build` — compiles
4. Manual: submit a query in the dashboard, watch tool calls stream in, see the answer render with evidence
