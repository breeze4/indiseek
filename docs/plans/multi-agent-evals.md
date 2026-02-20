# Multi-Agent Eval Suite

Eval questions derived from popular Vite GitHub issues (2023-2025). Each question requires multi-subsystem understanding of the codebase — the kind of question that exposes the gravity well problem in single-agent mode.

## Eval Design

Each eval case has:
- **Question**: A natural-language research question derived from a real GitHub issue
- **Source issue**: The GitHub issue it came from, for context
- **Expected subsystems**: Which parts of the Vite codebase a good answer must cover (minimum 2)
- **Ground truth claims**: Specific factual claims a correct answer must contain, extracted from the issue discussion, PRs, and codebase reading
- **Anti-claims**: Common misconceptions or incomplete answers that would indicate the agent got stuck in a gravity well

## Scoring

For each eval, score on:
1. **Subsystem coverage** (0-1): What fraction of expected subsystems does the answer touch?
2. **Claim recall** (0-1): What fraction of ground truth claims appear in the answer?
3. **Claim precision** (0-1): What fraction of the answer's factual claims are correct?
4. **Anti-claim avoidance** (0-1): Does the answer avoid the listed anti-claims?

Overall score = average of the four. Target: single-agent baseline < 0.6, multi-agent > 0.8.

Scoring can be done manually for now. Automated LLM-as-judge scoring is a future optimization.

## Eval Cases

### Eval 1: Non-deterministic build hashes

**Source**: vitejs/vite#13672 (28 thumbs-up, 42 comments)

**Question**: "Why does Vite sometimes produce different output file hashes when building the same source code multiple times? What is the root cause and what workarounds exist?"

**Expected subsystems**:
- Rollup's parallel file operations (`maxParallelFileOps`)
- The CommonJS plugin (`@rollup/plugin-commonjs`) and its transform hook
- Vite's build pipeline and how it delegates to Rollup

**Ground truth claims**:
- The non-determinism comes from Rollup processing files in parallel — the CommonJS plugin's transform output depends on the order files are processed
- Specifically, the CommonJS proxy module for a dependency can be generated in two different forms depending on whether the dependency's main module has been transformed yet
- Setting `maxParallelFileOps: 1` in the Rollup config is a workaround that forces sequential processing
- The root issue is in `@rollup/plugin-commonjs`, not in Vite's own code
- The two different transform outputs produce different content hashes, leading to different filenames

**Anti-claims**:
- "This is caused by timestamp-based hashing" (wrong — hashes are content-based)
- "This is a Vite bug" (it's upstream in Rollup/CommonJS plugin)
- Answer only discusses Vite's config loading without mentioning Rollup's parallel processing

---

### Eval 2: Circular imports in HMR

**Source**: vitejs/vite#16580 (15 thumbs-up)

**Question**: "How does Vite's HMR system handle modules that have circular imports? Trace what happens when a file that participates in a circular dependency is edited."

**Expected subsystems**:
- HMR client code (`packages/vite/src/client/client.ts`) — the browser-side update application
- Module graph and the `isWithinCircularImport` flag
- Server-side HMR propagation (`packages/vite/src/node/server/hmr.ts`)

**Ground truth claims**:
- When a module in a circular dependency chain is updated, the HMR client imports the modules in a potentially different order than the original load
- The `isWithinCircularImport` flag is set on modules detected to be in circular chains
- When the re-import triggers a different evaluation order, `ReferenceError: Cannot access '...' before initialization` occurs because an ES module export is accessed before the module body executes
- The HMR client catches this error and falls back to a full page reload (see the try/catch in `client.ts` around the dynamic import)
- PRs #14867 and #15118 attempted to address this but the problem persists for certain patterns

**Anti-claims**:
- "Vite fully supports circular imports in HMR" (it doesn't — it falls back to full reload)
- Answer only discusses server-side without mentioning the client-side import ordering problem
- Answer doesn't mention the `isWithinCircularImport` flag

---

### Eval 3: Watch mode does full rebuild

**Source**: vitejs/vite#16104 (18 thumbs-up)

**Question**: "Why does `vite build --watch` perform a complete rebuild on every file change instead of doing an incremental rebuild like webpack? What would need to change for incremental builds to work?"

**Expected subsystems**:
- Vite's build command and how it sets up watch mode
- Rollup's watch implementation and its limitations
- The plugin pipeline (transform, renderChunk) and why it can't be partially re-run

**Ground truth claims**:
- Vite delegates build to Rollup, and Rollup's watch mode triggers a full rebuild on each change
- Rollup doesn't have an incremental build mode — it re-runs the entire plugin pipeline from scratch
- This is architecturally different from webpack, which maintains an in-memory module graph between builds and only re-processes changed modules
- The `vite-plugin-incremental-build` community plugin works around this by caching outputs and short-circuiting unchanged modules
- The Rolldown project (Rust-based Rollup replacement for Vite) is expected to eventually address this

**Anti-claims**:
- "Vite's dev server already does incremental updates" (true but irrelevant — this is about `vite build --watch`, not dev mode)
- Confusing HMR (dev mode) with build watch mode
- "Just use `vite dev` instead" (misses the point — build watch is needed for library development, SSR builds, etc.)

---

### Eval 4: ssrTransform syntax errors

**Source**: vitejs/vite#19096 (14 thumbs-up, 5 hearts)

**Question**: "How does Vite's `ssrTransform` rewrite module imports for SSR, and why can it produce syntax errors when processing minified code?"

**Expected subsystems**:
- `ssrTransform` implementation (`packages/vite/src/node/ssr/ssrTransform.ts`)
- The `(0, ...)` pattern used for namespace import rewriting
- How semicolon insertion interacts with minified (single-line) code

**Ground truth claims**:
- `ssrTransform` rewrites `import` statements to `__vite_ssr_import__` calls and wraps default export accesses with `(0, identifier)` to avoid `this` binding issues
- When code is minified (no line breaks), the semicolon inserted after the import rewrite can collide with the `(0, ...)` wrapper, producing invalid syntax like `};(0, ...)`
- The bug specifically manifests with code like `@headlessui/vue` which ships minified dist files
- The fix involves checking whether a semicolon is actually needed before inserting one

**Anti-claims**:
- "This is an esbuild bug" (it's in Vite's own ssrTransform)
- Answer discusses SSR in general without identifying the specific semicolon insertion mechanism
- Answer doesn't mention the `(0, ...)` pattern

---

### Eval 5: CSS url() references bypass plugin pipeline

**Source**: vitejs/vite#14686 (18 thumbs-up)

**Question**: "How does Vite handle asset references inside CSS `url()` declarations? Why don't Vite plugins receive `resolveId` and `load` hook calls for these assets, and what workarounds exist?"

**Expected subsystems**:
- CSS plugin (`packages/vite/src/node/plugins/css.ts`) and its url() rewriting logic
- The normal plugin pipeline for JS imports (`resolveId` → `load` → `transform`)
- Static asset handling and how CSS assets are processed differently from JS imports

**Ground truth claims**:
- Vite handles CSS `url()` references through its own CSS plugin, which rewrites URLs to point to hashed asset filenames
- This URL rewriting happens inside the CSS plugin directly, bypassing the normal `resolveId`/`load`/`transform` plugin hook chain that JS imports go through
- This means plugins like `vite-imagetools` that transform assets via the plugin pipeline never see CSS-referenced assets
- There's a performance concern with running all plugins on CSS url resolution (mentioned in PR #10555)
- The `resolve.alias.customResolvers` config can be used as a partial workaround for custom resolution

**Anti-claims**:
- "Vite processes CSS url() assets the same way as JS imports" (it doesn't)
- Answer only discusses CSS Modules or PostCSS without addressing the url() pipeline specifically

---

### Eval 6: Empty sourcemaps during dependency optimization

**Source**: vitejs/vite#17474 (28 thumbs-up)

**Question**: "Why does Vite generate empty source maps (with no sources or mappings) when optimizing dependencies with esbuild? What is the expected behavior?"

**Expected subsystems**:
- Dependency optimizer (`packages/vite/src/node/optimizer/`)
- esbuild integration for dependency pre-bundling
- Source map handling and forwarding in the optimizer output

**Ground truth claims**:
- Vite uses esbuild to pre-bundle dependencies into optimized chunks in `node_modules/.vite/deps/`
- When a dependency doesn't ship its own source maps (or ships incomplete ones), esbuild produces a source map that maps to... nothing, resulting in `{"sources":[],"mappings":""}`
- These empty source maps cause browser DevTools warnings ("No sources are declared in this source map")
- The issue affects modules with no build step (plain JS files) as well as minified dist bundles
- Related to the broader issue of how Vite's optimizer chains source maps from the original package through esbuild's transform

**Anti-claims**:
- "Vite strips source maps during optimization" (it doesn't strip them — it generates empty ones)
- Answer only discusses Vite's build-time sourcemaps without addressing the dev-time dependency optimizer

## Gold Standard Generation

Gold standard answers are generated by Claude Code reading the Vite source directly — no indiseek agent involved. This gives the highest-quality reference answers to score the agent strategies against.

```bash
# Generate all 6 gold answers (skips existing, clean context per question)
bash scripts/eval-gold.sh

# Generate a single eval
bash scripts/eval-gold-once.sh 1 "Why does Vite sometimes produce different output file hashes..."

# Regenerate a specific eval
rm docs/evals/gold/eval-1.md
bash scripts/eval-gold-once.sh 1 "..."
```

Output: `docs/evals/gold/eval-{1..6}.md`

Each run launches a fresh Claude Code session with `--print` (no state carryover) pointed at `repos/vite/`. The prompt instructs thorough source code reading with file:line references.

## Running Agent Evals

To run the indiseek agent strategies against the same questions:
1. Index the Vite repo: `python scripts/index.py --repo vite --embed --summarize --lexical`
2. Query with single-agent: `curl -X POST http://localhost:8000/api/query -d '{"prompt": "<question>", "repo_id": <id>, "mode": "single"}'`
3. Query with multi-agent: `curl -X POST http://localhost:8000/api/query -d '{"prompt": "<question>", "repo_id": <id>, "mode": "multi"}'`
4. Score each response against the gold standard + ground truth claims
5. Record results in the table below

## Results

| Eval | Gold | Single-Agent | Multi-Agent | Notes |
|------|------|-------------|-------------|-------|
| 1. Non-deterministic build | | | | |
| 2. Circular imports HMR | | | | |
| 3. Watch mode rebuild | | | | |
| 4. ssrTransform syntax | | | | |
| 5. CSS url() pipeline | | | | |
| 6. Empty sourcemaps | | | | |

## Notes

- Evals 1 and 3 test cross-boundary understanding (Vite ↔ Rollup). Good for testing whether the planner decomposes across project boundaries.
- Evals 2 and 4 test server ↔ client understanding. Good for testing whether researchers explore both sides.
- Evals 5 and 6 test subsystem-specific depth (CSS pipeline, optimizer). Good for testing whether researchers find the right code paths.
- The ground truth claims are derived from issue discussions and may need refinement after manually verifying against the actual indexed Vite version.
