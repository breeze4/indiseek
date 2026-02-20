I now have complete evidence. Let me synthesize the final answer.

## Answer

### Root Cause: Non-Deterministic Set Insertion Order in CSS Processing Pipeline

The primary root cause of different output hashes across identical builds is **non-deterministic insertion order of CSS filenames into `Set<string>` data structures** during Vite's concurrent `renderChunk` processing, which then feeds into chunk hash computation via the `augmentChunkHash` hook.

There are multiple contributing subsystems, but they all trace back to the same fundamental issue: Rolldown (Vite's bundler) calls `renderChunk` hooks **concurrently** for different chunks, and Vite's CSS plugin uses shared mutable `Set` and `Map` data structures that accumulate results in arrival order.

---

### Subsystem 1: `augmentChunkHash` — The Direct Hash Instability Mechanism

**File:** `packages/vite/src/node/plugins/css.ts:971-979`

```typescript
augmentChunkHash(chunk) {
  if (chunk.viteMetadata?.importedCss.size) {
    let hash = ''
    for (const id of chunk.viteMetadata.importedCss) {
      hash += id
    }
    return hash
  }
},
```

This hook is called by Rolldown to augment each chunk's hash. It concatenates all CSS filenames from the chunk's `importedCss` Set **in insertion order** — there is no `.sort()` call. The returned string is mixed into the chunk's hash by Rolldown.

The `importedCss` Set is a `new Set<string>()` initialized per-chunk in `ChunkMetadataMap._getDefaultValue()` at `packages/vite/src/node/build.ts:1200-1202`.

**Entries are added in two places:**

1. **During `renderChunk`** at `css.ts:915-917`:
   ```typescript
   chunk.viteMetadata!.importedCss.add(
     this.getFileName(referenceId),
   )
   ```
   This runs after `codeSplitEmitQueue.run()` resolves. While the queue serializes CSS emission, the queue tasks are enqueued in the order `renderChunk` calls arrive — which is non-deterministic when Rolldown processes chunks in parallel.

2. **During `generateBundle`** at `css.ts:1083-1084`:
   ```typescript
   importedCss.forEach((file) =>
     chunk.viteMetadata!.importedCss.add(file),
   )
   ```
   This propagates CSS from pure-CSS chunks into their importers. The order here depends on which pure-CSS chunks were added to `pureCssChunks` first (see Subsystem 3).

**Impact:** If chunk A and chunk B both import CSS, and their `renderChunk` calls arrive in different order across builds, the `importedCss` Set in a parent chunk that imports both will have different iteration order. `augmentChunkHash` will produce a different string, yielding a different hash for the parent chunk. This cascades: any chunk that imports the parent also gets a different hash.

---

### Subsystem 2: CSS Content Concatenation Order

**File:** `packages/vite/src/node/plugins/css.ts:661-685`

```typescript
const ids = Object.keys(chunk.modules)
for (const id of ids) {
  if (styles.has(id)) {
    // ...
    chunkCSS = (chunkCSS || '') + styles.get(id)
  }
}
```

CSS content for each chunk is built by iterating `Object.keys(chunk.modules)` — the module ordering within a chunk as determined by Rolldown's internal graph traversal. If Rolldown's module ordering is not stable (e.g., due to parallel resolution or non-deterministic dependency graph traversal), the CSS concatenation order changes, producing different CSS content and thus a different CSS asset hash.

The `styles` Map at `css.ts:460` is populated concurrently by `transform` hook calls:
```typescript
const styles = new Map<string, string>()
// ...
styles.set(id, css)  // css.ts:612, inside async transform handler
```

While `styles.get(id)` lookups in `renderChunk` are keyed by specific IDs from `chunk.modules` (so Map insertion order doesn't matter for lookup), the Map is written from concurrent `transform` calls — meaning Map insertion order is non-deterministic. This would matter if anything iterated `styles` directly.

---

### Subsystem 3: `pureCssChunks` Set Ordering

**File:** `packages/vite/src/node/plugins/css.ts:464,865`

```typescript
pureCssChunks = new Set<RenderedChunk>()  // line 464
// ...
pureCssChunks.add(chunk)  // line 865, inside concurrent renderChunk
```

Pure CSS chunks (chunks with only CSS, no JS exports) are tracked in a Set that's populated from concurrent `renderChunk` calls. The insertion order depends on which `renderChunk` completes first.

At `css.ts:1062`, this Set is spread into an array:
```typescript
const pureCssChunkNames = [...pureCssChunks]
  .map((pureCssChunk) => prelimaryNameToChunkMap[pureCssChunk.fileName])
  .filter(Boolean)
```

This array feeds into `getEmptyChunkReplacer()` (line 1066) which builds a regex alternation. More importantly, the iteration at `css.ts:1071-1098` processes chunks and propagates `importedCss`/`importedAssets` from pure CSS chunks to their importers — in the order they appear in `bundle`, which itself depends on Rolldown's output ordering.

---

### Subsystem 4: `codeSplitEmitQueue` — Partial Mitigation

**File:** `packages/vite/src/node/plugins/css.ts:461-462`

```typescript
// queue to emit css serially to guarantee the files are emitted in a deterministic order
let codeSplitEmitQueue = createSerialPromiseQueue<string>()
```

The `createSerialPromiseQueue` at `packages/vite/src/node/utils.ts:1638-1659` works by:
1. Starting the task immediately (`const thisTask = f()` at line 1645)
2. Waiting for both the previous task AND the current task to complete before resolving
3. So tasks resolve in the order they were enqueued

This serializes the `emitFile` calls and ensures CSS assets are emitted in order. **However**, the enqueue order itself is determined by which `renderChunk` call reaches `codeSplitEmitQueue.run()` first — which is a race condition. The queue guarantees sequential emission but **not deterministic ordering** when the enqueue calls come from concurrent `renderChunk` hooks.

---

### Subsystem 5: Preload Dependency Array Ordering

**File:** `packages/vite/src/node/plugins/importAnalysisBuild.ts:369-420`

```typescript
const analyzed: Set<string> = new Set<string>()
const addDeps = (filename: string) => {
  // ... DFS through chunk.imports, collect importedCss
  chunk.viteMetadata!.importedCss.forEach((file) => {
    deps.add(file)
  })
}
```

The preload dependency list for dynamic imports is built by DFS traversal of `chunk.imports`, then iterating `importedCss` for each chunk. The resulting `deps` Set is spread into an array at line 419-420:
```typescript
[...deps].filter((d) => d.endsWith('.css'))
// or
[...deps]
```

This becomes the `__vite__mapDeps` array embedded in JS chunk code (line 475):
```typescript
`__vite__mapDeps([${renderedDeps.join(',')}])`
```

Since this array is embedded in the JS chunk's source code, its ordering affects the chunk's content hash. If `importedCss` iteration order differs (from Subsystem 1), the `__vite__mapDeps` array differs, the JS content differs, and the chunk hash differs.

---

### Subsystem 6: Worker Bundles — Independent Hash Computation

**File:** `packages/vite/src/node/plugins/worker.ts:162-239`

Workers are bundled independently via a separate `rolldown()` call inside the `load` hook:
```typescript
const bundle = await rolldown({ ...rollupOptions, input, ... })
result = await bundle.generate({
  entryFileNames: path.posix.join(config.build.assetsDir, '[name]-[hash].js'),
  // ...
})
```

The worker's output filename (with its `[hash]`) is computed by this independent Rolldown invocation. The filename is then embedded in the main bundle via a placeholder:

```typescript
// worker.ts:143-148
private generateEntryUrlPlaceholder(entryFilename: string): string {
  const hash = getHash(entryFilename)  // SHA-256 of the worker's output filename
  return `__VITE_WORKER_ASSET__${hash}__`
}
```

In the main bundle's `renderChunk` (worker.ts:558-614), these placeholders are replaced with the actual worker filename. Since workers are emitted with a pre-computed `fileName` (not `name`) at `worker.ts:643-649`, they bypass the main bundle's hash computation. But the worker's filename string is embedded in the main chunk's code, so any change in the worker's hash cascades into the main chunk's hash.

If the worker's own CSS or module ordering is non-deterministic (same issues as above apply to the worker sub-build), the worker filename changes, which changes the main bundle's content, which changes the main bundle's hash.

---

### Subsystem 7: Manifest Ordering (Secondary)

**File:** `packages/vite/src/node/plugins/manifest.ts:243-247`

```typescript
if (chunk.viteMetadata?.importedCss.size) {
  manifestChunk.css = [...chunk.viteMetadata.importedCss]
}
if (chunk.viteMetadata?.importedAssets.size) {
  manifestChunk.assets = [...chunk.viteMetadata.importedAssets]
}
```

The `css` and `assets` arrays in each manifest entry are spread from Sets without sorting. While the manifest top-level keys are sorted alphabetically (`sortObjectKeys` at line 328), the inner arrays are not. This means the manifest file itself can differ across builds even when the actual assets are identical, just in different order.

---

### What Does NOT Cause Non-Determinism

All uses of `Date.now()`, `Math.random()`, and `performance.now()` in the build path are for:
- Display timing (`build.ts:856,869,877`)
- Temp file naming (`config.ts:2574`, `optimizer/index.ts:954`)
- Dev server session tokens (`optimizer/optimizer.ts:44`)
- Debug logging (`importAnalysis.ts:269`)

**None of these values flow into build output hashes or filenames.**

Vite's `getHash()` function at `utils.ts:1122-1126` uses `crypto.hash('sha256', text, 'hex')` — fully deterministic for the same input. The non-determinism is in what inputs reach the hash function, not the hash function itself.

---

### Workarounds

1. **Pin Rolldown/Rollup version**: Since chunk hash computation is delegated to Rolldown, ensure the exact same version is used across builds. Even patch updates can change internal graph traversal order.

2. **Set `build.cssCodeSplit: false`**: This collects all CSS into a single file rather than per-chunk, reducing the number of CSS emission ordering points. The single-file path at `css.ts:1020-1043` relies on bundle iteration order (which the code comments assert is deterministic from Rolldown's side).

3. **Use `build.rollupOptions.output.manualChunks`**: Explicitly controlling chunk boundaries reduces the number of chunks processed concurrently, limiting the window for ordering races.

4. **Avoid workers in the build**: If workers aren't needed, removing them eliminates the independent sub-build hash computation that can cascade into the main bundle.

5. **Custom `augmentChunkHash` plugin**: A user plugin could override the CSS plugin's `augmentChunkHash` with a sorted version:
   ```javascript
   {
     name: 'deterministic-css-hash',
     enforce: 'post',
     augmentChunkHash(chunk) {
       if (chunk.viteMetadata?.importedCss.size) {
         return [...chunk.viteMetadata.importedCss].sort().join('')
       }
     }
   }
   ```
   However, this only addresses the `augmentChunkHash` vector, not the CSS content concatenation order or preload deps ordering.

6. **Content-addressable output via `build.rollupOptions.output.assetFileNames` / `chunkFileNames`**: Using `[hash]` only (no `[name]`) makes the filename purely a function of content, which at least makes same-content outputs share the same name — but this doesn't fix the root cause if the content itself differs due to ordering.

---

## Key Files

- **`packages/vite/src/node/plugins/css.ts`** — The CSS post-processing plugin. Contains `renderChunk` (CSS extraction, lines 642-969), `augmentChunkHash` (lines 971-979), `generateBundle` (CSS code-split emission, lines 983-1098), `codeSplitEmitQueue` (line 462), `pureCssChunks` Set (line 464), and `finalizeCss` (lines 1897-1919). This is the epicenter of the non-determinism.
- **`packages/vite/src/node/build.ts`** — Build orchestration. Defines output filename patterns with `[hash]` placeholders (lines 724-731), `ChunkMetadataMap` (lines 1186-1211) which initializes the `importedCss` and `importedAssets` Sets, and `injectChunkMetadata` (lines 1440-1459) which wires viteMetadata into Rolldown hooks.
- **`packages/vite/src/node/plugins/importAnalysisBuild.ts`** — Dynamic import preload analysis. Builds `__vite__mapDeps` arrays (lines 369-477) whose ordering depends on `importedCss` Set iteration order. Embeds these arrays in JS chunk content, affecting chunk hashes.
- **`packages/vite/src/node/plugins/worker.ts`** — Worker bundling. Independent `rolldown()` sub-builds (lines 162-239) produce worker filenames that embed into main chunk code via `__VITE_WORKER_ASSET__` placeholders (lines 143-149, 558-614).
- **`packages/vite/src/node/plugins/manifest.ts`** — Manifest generation. Spreads `importedCss`/`importedAssets` Sets into unsorted arrays (lines 243-247). Top-level keys are sorted (line 328) but inner arrays are not.
- **`packages/vite/src/node/utils.ts`** — Contains `getHash()` (SHA-256, line 1122), `createSerialPromiseQueue` (line 1638), and `sortObjectKeys` (line 1663).

## Summary

Vite's build can produce different output hashes for identical source because its CSS plugin (`css.ts`) accumulates CSS filenames into JavaScript `Set` objects during concurrent `renderChunk` processing, then feeds those Sets — in insertion order, without sorting — into `augmentChunkHash` (line 971) and preload dependency arrays (`importAnalysisBuild.ts:381`). Since Rolldown calls `renderChunk` concurrently for different chunks, the order CSS filenames are inserted into these Sets depends on which async operations complete first, creating a race condition. This non-deterministic ordering cascades: a different `augmentChunkHash` string changes the parent chunk's hash, which changes the hash of anything that imports it, and so on through the dependency graph. Worker sub-builds compound the issue by independently computing hashes that then embed into the main bundle.
---

## Eval Metrics

| Metric | Value |
|--------|-------|
| Duration (wall) | 0m 3s |
| Duration (API) | 14m 11s |
| Turns | 1 |
| Tool calls | 26 |
| Input tokens | 3 |
| Output tokens | 24 |
| Cache read tokens | 95,526 |
| Cache creation tokens | 5,628 |
| Cost | $3.3285 |

### Tool Usage

| Tool | Calls |
|------|-------|
| Read | 14 |
| TaskOutput | 5 |
| Task | 4 |
| Grep | 3 |

### Model Usage

- **claude-opus-4-6**: 16,545 in / 8,601 out, $1.4578
- **claude-haiku-4-5-20251001**: 5,243 in / 26,595 out, $0.7031
- **claude-sonnet-4-6**: 42 in / 11,020 out, $1.1676
