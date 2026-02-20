Now I have all the evidence needed. Let me write the comprehensive answer.

## Answer

When a file participating in a circular dependency is edited, Vite's HMR system handles it through a multi-layered approach spanning server-side graph traversal, cycle detection tagging, and client-side graceful degradation. Here's the full trace:

### 1. Module Graph: No Special Circular Handling in Data Structures

The `EnvironmentModuleNode` class (`packages/vite/src/node/server/moduleGraph.ts:14-82`) stores bidirectional import relationships via plain `Set`s:

- `importers: Set<EnvironmentModuleNode>` (line 28) — who imports this module
- `importedModules: Set<EnvironmentModuleNode>` (line 30) — what this module imports

Circular imports are represented naturally: if A imports B and B imports A, then A appears in B's `importers` and B appears in A's `importers`. There is **no special data structure** for cycles — the graph simply contains them as back-edges.

### 2. File Change Triggers `updateModules`

When a file is saved, `updateModules()` (`hmr.ts:632-742`) is called. It allocates a single shared `traversedModules = new Set<EnvironmentModuleNode>()` (line 642) and iterates over all module nodes associated with the changed file, calling `propagateUpdate()` for each.

### 3. `propagateUpdate` — Two-Layer Cycle Prevention

The core propagation function (`hmr.ts:756-843`) walks **upward** through importers to find HMR boundaries (modules that call `import.meta.hot.accept()`). It uses two distinct mechanisms to handle cycles:

**Layer 1 — Global visited set** (`traversedModules`, line 762-765):
```typescript
if (traversedModules.has(node)) {
  return false   // already processed, not a dead end
}
traversedModules.add(node)
```
This shared `Set` prevents any node from being traversed more than once across the entire propagation run. When a circular back-edge leads to an already-visited node, propagation stops silently (returns `false`, meaning "no dead end").

**Layer 2 — Per-path chain check** (line 835-840):
```typescript
if (
  !currentChain.includes(importer) &&
  propagateUpdate(importer, traversedModules, boundaries, subChain)
) {
  return true
}
```
The `currentChain` array tracks the specific path from the changed module up to the current node. If an importer already appears in the current DFS path, it's a cycle on this branch — the function skips recursing into it without treating it as a dead end. This prevents stack overflow from circular back-edges.

**Critically**: hitting a cycle does NOT trigger a full page reload. The cycle is simply skipped. What matters is whether the propagation can find an HMR boundary somewhere along the non-circular paths.

### 4. `isNodeWithinCircularImports` — Tagging Boundaries

When `propagateUpdate` finds an HMR boundary (a self-accepting module at line 779, a partially-accepted module at line 795, or an importer that accepted the dep at line 812), it calls `isNodeWithinCircularImports()` (`hmr.ts:855-921`) to compute a boolean flag.

This function answers: "Is this HMR boundary node part of an import cycle that includes the changed file?"

The algorithm, with the source's own example (`hmr.ts:861-875`):
```
A -> B -> C -> ACCEPTED -> D -> E -> NODE
     ^--------------------------|
```
- `node` = ACCEPTED (the boundary)
- `nodeChain` = [NODE, E, D, ACCEPTED] (the upward chain from changed file to boundary)

The function recursively walks ACCEPTED's importers (C, then B). For each, it checks `nodeChain.indexOf(importer)`. If B's importer D is found in the nodeChain, a cycle is confirmed — D imports B but D is also downstream of ACCEPTED.

Cycle guards within this function:
- `traversedModules` set (line 877-880) — prevents infinite recursion
- `importer === node` self-import skip (line 884) — treated as safe
- `!currentChain.includes(importer)` (line 910) — avoids re-traversing the current DFS path

When `--debug hmr` is active, the full circular path is logged (lines 890-904).

### 5. The `isWithinCircularImport` Flag Flows to the Client

The `PropagationBoundary` interface (`hmr.ts:71-75`) carries the flag:
```typescript
interface PropagationBoundary {
  boundary: EnvironmentModuleNode & { type: 'js' | 'css' }
  acceptedVia: EnvironmentModuleNode
  isWithinCircularImport: boolean
}
```

In `updateModules`, boundaries are mapped to `Update` objects (line 679-694) that include `isWithinCircularImport`. These are sent over WebSocket to the browser as part of the `'update'` payload.

### 6. Cache Invalidation: `invalidateModule` Cycle Safety

Before sending the update, `updateModules` calls `invalidateModule()` (`moduleGraph.ts:166-234`) to clear cached transform results. This function recursively walks up importers, using a `seen` set with a nuanced check (line 189):
```typescript
if (seen.has(mod) && prevInvalidationState === mod.invalidationState) {
  return
}
```
A module can be re-visited if its invalidation state changed (e.g., it was soft-invalidated via one path but hard-invalidated via another). This ensures correct invalidation propagation even through circular imports without infinite loops.

### 7. Client-Side: Attempt Import, Catch Failure

On the browser side (`packages/vite/src/client/client.ts:143-195`), the `importUpdatedModule` function handles the flag:

```typescript
const importPromise = import(base + url + `?t=${timestamp}`)
if (isWithinCircularImport) {
  importPromise.catch(() => {
    console.info(
      `[hmr] ${acceptedPath} failed to apply HMR as it's within a circular import. ` +
      `Reloading page to reset the execution order. ...`
    )
    pageReload()
  })
}
return await importPromise
```

The key design decision: **Vite still attempts the HMR update even for circular modules**. The flag only attaches a `.catch()` fallback. If the dynamic `import()` succeeds (which it sometimes does — ES modules handle some circular patterns fine), the HMR update applies normally. If it fails (because the browser's module execution order becomes non-deterministic after re-evaluation of a cycle participant), the error is caught gracefully and the page does a full reload via `pageReload()` (debounced at 20ms, line 141).

### 8. Circular Invalidation Detection

A separate mechanism handles `import.meta.hot.invalidate()` cycles (`hmr.ts:666-677`). When a module's `accept()` callback calls `this.hot.invalidate()`, a new HMR round starts with `firstInvalidatedBy` set. If the re-propagation finds that the invalidating module itself is a boundary's `acceptedVia`, that's a circular invalidation — Vite forces a full reload with the reason string `'circular import invalidate'`.

### 9. Module Runner (SSR): Separate Circular Handling

For SSR via the module runner (`packages/vite/src/module-runner/runner.ts`), circular imports are handled differently during module evaluation with three layers of detection:
- `callstack.includes(moduleId)` — direct stack check
- `isCircularModule(mod)` — fast bidirectional edge check (if any import is also an importer)
- `isCircularImport(importers, moduleId, visited)` — full graph DFS with `visited: Set<string>`

When a cycle is detected, the runner returns the partially-evaluated `mod.exports` rather than recursing — matching native ES module semantics.

## Key Files

- **`packages/vite/src/node/server/hmr.ts`** — Server-side HMR orchestration. Contains `updateModules` (line 632), `propagateUpdate` (line 756), `isNodeWithinCircularImports` (line 855), and the `PropagationBoundary` interface (line 71).
- **`packages/vite/src/node/server/moduleGraph.ts`** — `EnvironmentModuleNode` (line 14) with `importers`/`importedModules` sets, and `invalidateModule` (line 166) with its `seen`-set cycle guard.
- **`packages/vite/src/client/client.ts`** — Browser-side `importUpdatedModule` (line 150/170) that attaches the `.catch(() => pageReload())` fallback for circular imports.
- **`packages/vite/src/shared/hmr.ts`** — `HMRClient.fetchUpdate` (line 264) and `queueUpdate` (line 252) that orchestrate module re-import and callback invocation on the client.
- **`packages/vite/src/module-runner/runner.ts`** — SSR module evaluation with `isCircularModule`/`isCircularImport` detection and partial-export return.
- **`packages/vite/types/hmrPayload.d.ts`** — Wire protocol `Update` type with `isWithinCircularImport` field.

## Summary

Vite's HMR system handles circular imports through a "best-effort with graceful degradation" strategy. On the server, `propagateUpdate` uses both a shared `traversedModules` set and a per-path `currentChain` array to prevent infinite loops when walking the importer graph — cycles are simply skipped rather than causing dead-ends. A separate `isNodeWithinCircularImports` function tags each HMR boundary with whether it participates in a cycle, and this flag is sent to the browser. On the client, Vite optimistically attempts the module re-import even for circular modules, but attaches a `.catch()` handler that triggers a full page reload if the import fails due to execution order issues — making circular HMR updates succeed when possible and fail safely when not.
---

## Eval Metrics

| Metric | Value |
|--------|-------|
| Duration (wall) | 4m 6s |
| Duration (API) | 7m 51s |
| Turns | 12 |
| Tool calls | 60 |
| Input tokens | 7 |
| Output tokens | 4,441 |
| Cache read tokens | 179,849 |
| Cache creation tokens | 31,517 |
| Cost | $1.2037 |

### Tool Usage

| Tool | Calls |
|------|-------|
| Read | 32 |
| Grep | 13 |
| Bash | 8 |
| Glob | 4 |
| Task | 3 |

### Model Usage

- **claude-haiku-4-5-20251001**: 3,389 in / 11,736 out, $0.2348
- **claude-opus-4-6**: 7 in / 4,441 out, $0.3980
- **claude-sonnet-4-6**: 23 in / 8,512 out, $0.5709
