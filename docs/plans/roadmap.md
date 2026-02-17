# Indiseek Roadmap

## Current State

MVP complete: single-repo (Vite) indexing pipeline (tree-sitter, SCIP, embeddings, file summaries, BM25), agent loop (Gemini tool-calling), FastAPI query API, React dashboard with file tree, search, query page with history + caching. All hardcoded to one repo, one LLM provider.

Phases 1–9 of `docs/plans/todo.md` are done (all checklist items complete; some manual verification items remain for Phases 3–6). Unimplemented plans exist for agent-loop improvements (self-contained query service, iteration budget tuning), query history (backend done, frontend sidebar not wired), and hierarchical directory summaries.

---

## Tier 1 — Must-Have

These are the features required to make Indiseek useful beyond a single-user prototype.

### 1.1 Repo Management + Indexing Status

**What:** Multi-repo support. Add, remove, configure repos via UI. See every repo's indexing status — which pipeline steps have run, when, how many files/chunks/symbols each has.

**Scope:**
- `repos` table in SQLite: `id, name, url, local_path, created_at, last_indexed_at, commit_sha`
- All existing tables get a `repo_id` foreign key (files, chunks, symbols, SCIP, summaries, queries)
- Dashboard page showing all repos with status cards (parsed / embedded / summarized / lexical — counts and percentages)
- Per-repo pipeline status: which steps have completed, last run timestamp, current commit SHA
- Add/remove repo via dashboard (clone happens server-side)
- Repo selector in the query page — queries run against a specific repo

**Key decisions:**
- Repos are git clones on local disk. The service manages cloning.
- Each repo has its own LanceDB table and Tantivy index directory.
- SQLite stays as one database with `repo_id` partitioning (not separate databases per repo — keeps management simple).

### 1.2 Commit SHA Freshness + Partial Updates

**What:** Track which commit SHA each repo was last indexed at. Detect drift. Re-index only changed files using `git diff`.

**Scope:**
- On index completion, store the HEAD commit SHA in `repos.commit_sha` and `repos.last_indexed_at`
- `GET /dashboard/api/repos/{id}/status` returns current HEAD vs indexed SHA, with drift indicator
- Incremental re-index: `git diff --name-only {indexed_sha}..HEAD` → re-parse changed files, re-embed changed chunks, re-summarize changed files, update lexical index
- Deleted files: remove their rows from all tables
- New files: full pipeline for those files only
- Dashboard shows "stale" indicator when indexed SHA != HEAD

**Change detection — how the service learns about new commits:**

Three options, in order of recommendation:

1. **GitHub Actions workflow (recommended first step).** A simple workflow file (`.github/workflows/indiseek-notify.yml`) in each repo that POSTs to the Indiseek API on push. No public URL needed if running on internal network. Minimal setup — one YAML file per repo. Payload includes repo name, new SHA, branch.

2. **API polling with conditional requests.** `GET /repos/{owner}/{repo}/commits` with `If-None-Match` (ETag). Poll every 15 minutes. Doesn't consume rate limit when nothing changed. Works behind firewalls. Degrades gracefully for many repos (100 repos × 4/hr = 400 requests/hr, well within 5000/hr limit).

3. **GitHub App with webhook subscription (later).** Centralized webhook receiver. One app installation covers all repos. Requires public HTTPS endpoint. Best for production at scale but more complex setup (app registration, signature verification, public URL or tunnel). Build this when there are 10+ repos.

For now, start with option 1 (Actions) + a manual "check for updates" button in the dashboard that does a `git fetch` and compares SHAs.

**Scheduled refresh:**
- Cron-style background task that runs `git fetch` + SHA comparison for all repos on a configurable interval (default: every 30 minutes)
- If drift detected, auto-queue incremental re-index (or just flag it for manual trigger, depending on preference)

### 1.3 Provider-Neutral LLM Interface

**What:** Support Anthropic and OpenAI in addition to Gemini. The agent loop and all LLM-dependent steps (summarization, embedding) should work with any provider.

**Scope:**
- Abstract the existing `GeminiProvider` into a `LLMProvider` protocol (already partially exists in `provider.py`)
- Three concrete implementations: `GeminiProvider`, `AnthropicProvider`, `OpenAIProvider`
- Provider selection via config: `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...`, etc.
- Each provider implements: `generate(messages, tools) -> response`, `embed(texts) -> vectors`
- Tool-calling format differs per provider — each implementation handles its own format translation
- Embedding: Gemini uses `gemini-embedding-001`, OpenAI uses `text-embedding-3-small`, Anthropic doesn't have embeddings (use OpenAI or Gemini as embedding provider separately)
- Summarization: any provider works, just needs `generate()`
- Agent loop: needs tool-calling support. All three providers have it.

**Provider availability notes (from user):**
- Anthropic API key may be available via UI but could get locked down — need to confirm with Ronn Brashear
- AWS Bedrock possible but requires SSO approval — defer unless there's organizational pressure
- OpenAI is straightforward if keys are available
- Gemini remains the default since it's already working and has free tier

**Approach:** Build the Anthropic provider first (most likely second provider). OpenAI third. Bedrock later if needed. Keep Gemini as default.

### 1.4 Stable Tool API

**What:** The four core tools (`read_map`, `search_code`, `resolve_symbol`, `read_file`) are already implemented. This item is about stabilizing their contracts for external consumption.

**Scope:**
- Document the tool API formally (input types, output format, error cases)
- Consistent error handling across all tools (return structured error messages, not stack traces)
- Versioned tool schemas if the API surface changes
- Tool outputs should be deterministic and cacheable where possible
- This mostly involves cleanup and documentation, not new features

### 1.5 Evidence Trail Everywhere

**What:** Every response — in the API, in the UI, in Slack (later) — shows exactly what the agent read, what it searched, what it found, and how it arrived at its answer. No black-box answers.

**Scope:**
- Evidence trail already exists in the query response (`evidence` field). Make it richer:
  - Each evidence step should include: tool name, arguments, a human-readable summary of what was found, timestamp, duration
  - For `read_file`: show which lines were read, which symbols were in that range
  - For `search_code`: show top N results with scores, which results the agent actually used
  - For `resolve_symbol`: show the full definition/reference chain
- UI improvements:
  - Evidence trail in query results should be expandable with syntax-highlighted code snippets
  - Clicking a file reference in the evidence should navigate to the file tree or show inline
  - Timeline view showing the agent's reasoning path
- API response: structured JSON evidence that external consumers can parse

**Builds on Phase 8 work:** Evidence summaries are now human-readable per-tool. This item expands on that with richer detail and UI improvements.

---

## Tier 2 — Wedge Multipliers

Features that make Indiseek significantly more useful for adoption and daily use.

### 2.1 Log/Metric Index View

**What:** Treat log emit sites and metric instrumentation points as first-class indexed entities. A separate query path that combines research with reasoning — "where is this metric emitted, what triggers it, what's the data flow?"

**Scope:**
- During indexing, identify log/metric emit patterns in code (e.g., `logger.info()`, `metrics.increment()`, `console.warn()`, `statsd.gauge()`)
- Store these as a new entity type in SQLite: `emit_sites(id, file_path, line, kind, message_template, symbol_context)`
- New dashboard page: browse all emit sites, filter by kind (log/metric/error), search by message template
- New query path: when a question is about logs/metrics/observability, the agent gets additional tools:
  - `search_emits(pattern)` — find emit sites by message pattern
  - `trace_emit(id)` — trace the code path that leads to a specific emit
- Deeper reasoning loop: more iterations allowed, agent explicitly reasons about data flow and conditions under which the emit fires
- This is the combination of research + reasoning that makes Indiseek more than a code search tool

**Why this is a multiplier:** Engineers constantly ask "what causes this log line?" or "where is this metric coming from?" These questions require tracing execution flow, not just text search. The existing tools can answer them but the agent doesn't know to look for emit sites specifically.

### 2.2 Slackbot Integration

**What:** Slack bot that accepts questions and returns answers with evidence trails. Forces the API to be clean and drives adoption.

**Scope:**
- Slack app using Bolt for Python
- Listens for mentions or slash command (`/indiseek how does X work?`)
- Calls the same query API endpoint
- Formats the response for Slack (markdown, code blocks, file references as links)
- Evidence trail as a threaded reply (not in the main response — too noisy)
- Repo selector: either default repo per channel or explicit `--repo` flag
- Rate limiting: one query at a time per channel (queue or reject duplicates)
- Error handling: timeout message if agent takes > 60s, error message if it fails

**Why this is a multiplier:** Meeting people where they are. Nobody wants to open a separate dashboard to ask a question. Slack is where engineers live. Also forces API discipline — if the Slack bot works well, the API is clean enough for any integration.

### 2.3 Admin Usage Analytics

**What:** Simple analytics showing how the service is being used. Drives improvement and justifies investment.

**Scope:**
- Already have `queries` table with timestamps, duration, status. Build on it:
  - Dashboard page: queries per day/week, average latency, cache hit rate, failure rate
  - Per-repo breakdown: which repos get the most queries
  - Freshness dashboard: how stale is each repo's index (hours since last update)
  - Tool usage: which tools does the agent use most, average iterations per query
- No external analytics service — everything derived from SQLite
- Simple charts (could use a lightweight charting library in the React frontend)

---

## Tier 3 — Nice-to-Have Later

### 3.1 Diagrams and Walkthrough Pages

Rich browsing experience: auto-generated architecture diagrams, interactive code walkthroughs, linked documentation pages. The Excalidraw generation in `scripts/generate_diagrams.py` is a starting point.

### 3.2 Reranking, Personalization, Saved Searches

- Rerank search results using a cross-encoder or LLM-based reranker
- Personalization: learn which files/areas a user cares about, weight results accordingly
- Saved searches: bookmark queries and their results, get notified when results change after re-indexing
- Recommendations: "People who asked about X also looked at Y"

### 3.3 More Languages and Indexers

- Python, Go, Java, Rust tree-sitter grammars
- Language-specific SCIP generators (scip-python, scip-go, etc.)
- Polyglot repos: index multiple languages in one repo
- Auto-detect language from file extension and use appropriate grammar

### 3.4 Codebase Evolution History

- Track how the codebase changes over time: which areas grow, which shrink, which churn
- Change-by-change tracking: what each commit changed, what it did architecturally
- If available: link to the PR/conversation that motivated the change
- Timeline view: "show me how the auth system evolved over the last 6 months"
- Requires indexing at multiple commit SHAs and diffing the results

---

## Ordering and Dependencies

```
1.1 Repo Management ──────────┐
                               ├──> 1.2 Partial Updates + Freshness
                               │
1.3 Provider-Neutral LLM ─────┤  (independent, can parallelize)
                               │
1.4 Stable Tool API ───────────┤  (mostly documentation/cleanup)
                               │
1.5 Evidence Trail ────────────┘

         │
         ▼

2.1 Log/Metric Index ─────────── (needs stable multi-repo + tool API)
2.2 Slackbot ─────────────────── (needs stable query API)
2.3 Usage Analytics ──────────── (needs query history, which exists)

         │
         ▼

3.x ── All Tier 3 items are independent of each other
```

**Suggested order within Tier 1:**
1. 1.1 Repo Management (everything else depends on multi-repo)
2. 1.2 Partial Updates (builds directly on repo management)
3. 1.3 Provider-Neutral LLM (independent, do whenever)
4. 1.5 Evidence Trail (improves existing functionality)
5. 1.4 Stable Tool API (cleanup pass after the above)

**Tier 2 can start as soon as Tier 1.1 and 1.2 are done.** 2.3 (analytics) can start even earlier since query history already exists.

---

## Open Questions

- **Anthropic API key availability:** Need to check with Ronn Brashear whether API keys will remain accessible or get locked down. This affects priority of 1.3.
- **Deployment model:** Currently runs locally. For Slackbot (2.2) and GitHub webhooks, need a stable deployment with a public URL (or at least internal network reachability). Docker compose? Internal Kubernetes?
- **Git clone storage:** For multi-repo (1.1), where do repos get cloned? Local disk? Shared volume? How much disk space is available?
- **Auth:** Currently no auth. Multi-user + Slack integration will eventually need it. Not urgent but worth thinking about before 2.2.
