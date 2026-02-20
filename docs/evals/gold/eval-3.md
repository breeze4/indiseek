Now I have a complete picture. Let me write the comprehensive answer.

## Answer

### Why `vite build --watch` Performs Complete Rebuilds

The fundamental reason is architectural: Vite delegates watch-mode bundling entirely to Rolldown's `watch()` function, which re-executes the complete build pipeline (all Rollup hooks from `options` through `writeBundle`) on every detected file change. There is no module-level caching, no incremental compilation, and no mechanism to skip re-processing unchanged modules.

### 1. The Watch Entry Point

When `--watch` is passed on the CLI, it's normalized to `watch: {}` at `cli.ts:134-136`:

```
ret.watch = watch ? {} : undefined
```

This flows into `buildOptions` and then to `createBuilder()` → `builder.buildApp()` → `buildEnvironment()`.

### 2. The Watch Branch in `buildEnvironment()`

At `build.ts:779-885`, the `buildEnvironment()` function has two mutually exclusive paths:

- **Watch path** (`build.ts:800-851`): Calls `watch()` from Rolldown, passing the full `rollupOptions` plus a `watch` config. The returned `RolldownWatcher` emits events for `BUNDLE_START`, `BUNDLE_END`, and `ERROR`.
- **Normal path** (`build.ts:854-871`): Calls `rolldown(rollupOptions)` then `bundle.write()` or `bundle.generate()`.

The critical code at `build.ts:826-834`:
```ts
const { watch } = await import('rolldown')
const watcher = watch({
  ...rollupOptions,
  watch: {
    ...rollupOptions.watch,
    ...options.watch,
    notify: convertToNotifyOptions(resolvedChokidarOptions),
  },
})
```

The entire `rollupOptions` object — including all plugins, input entries, output config, and transform settings — is passed to `watch()` as a monolithic config. Rolldown's watcher uses this to **re-run the full bundle from scratch** on each change.

### 3. The Commented-Out Cache Line

At `build.ts:624`, there's a telling commented-out line:
```ts
// cache: options.watch ? undefined : false,
```

In Rollup (the predecessor), the `cache` option on `rollup()` accepted a previous build's `bundle.cache` object, allowing Rollup to skip `load`/`transform` calls for unchanged modules. This is the exact mechanism that would enable incremental rebuilds. By passing `undefined` (Rollup's default), the watcher would have used Rollup's internal cache between rebuilds. By passing `false`, it would have been explicitly disabled.

This line is **commented out entirely** in the Rolldown migration, meaning no cache option is passed at all. Rolldown's `watch()` function receives no caching directive.

### 4. The Disabled `shouldTransformCachedModule` Hook

At `constants.ts:35`, the `shouldTransformCachedModule` hook is commented out of the `ROLLUP_HOOKS` array:
```ts
// 'shouldTransformCachedModule',
```

This Rollup hook was the mechanism for plugins to decide whether a cached module transform should be reused or invalidated. Since it's excluded from the hooks list, Vite's plugin wrapping in `injectEnvironmentToHooks()` (`build.ts:1237-1283`) never wraps this hook, and Rolldown never calls it.

### 5. Every Plugin Resets State on Each Rebuild

Multiple plugins explicitly clear their internal caches on every build cycle, confirming the assumption of full rebuilds:

- **`vite:css`** (`css.ts:318-321`): `buildStart()` creates a brand-new `moduleCache` Map on every build, discarding all cached CSS module results:
  ```ts
  buildStart() {
    // Ensure a new cache for every build (i.e. rebuilding in watch mode)
    moduleCache = new Map<string, Record<string, string>>()
  }
  ```

- **`vite:css-post`** (`css.ts:516-521`): `renderStart()` creates new `pureCssChunks`, `chunkCSSMap`, and `codeSplitEmitQueue` on every render cycle:
  ```ts
  renderStart() {
    // Ensure new caches for every build (i.e. rebuilding in watch mode)
    pureCssChunks = new Set<RenderedChunk>()
  }
  ```

- **`vite:prepare-out-dir`** (`prepareOutDir.ts:14-15`): The `options()` hook clears the rendered set, which means the output directory preparation (emptying + copying publicDir) runs on each rebuild:
  ```ts
  options() {
    rendered.delete(this.environment)
  }
  ```

- **`vite:reporter`** (`reporter.ts:307-309`): `buildStart()` resets transform counters; `renderStart()` (`reporter.ts:317-318`) resets chunk counters — confirming all modules are re-transformed from scratch.

- **`ChunkMetadataMap`** (`build.ts:1186-1235`): The `clearResetChunks()` method is called on every `BUNDLE_START` event (`build.ts:839`), resetting chunk metadata tracking for the new full rebuild.

### 6. The Full Hook Lifecycle Re-executes

On each file change, Rolldown's watcher triggers the complete Rollup/Rolldown hook sequence:

1. `watchChange` — notifies plugins of the changed file (e.g., `asset.ts:313` invalidates asset cache, `worker.ts:653` invalidates worker bundles, `packages.ts:283` invalidates package.json cache)
2. `options` — plugins receive the full options again
3. `buildStart` — plugins reset all state (CSS caches, transform counters)
4. For every module in the graph: `resolveId` → `load` → `transform` → `moduleParsed`
5. `renderStart` — output caches reset
6. For every chunk: `renderChunk` (including minification via `buildEsbuildPlugin` at `esbuild.ts:394-423`)
7. `generateBundle` / `writeBundle` — manifest generation, asset emission, size reporting
8. `buildEnd`

**Every single module** goes through the full `resolveId` → `load` → `transform` pipeline again, even if its source hasn't changed.

### 7. Minification Runs on Every Chunk Every Time

The `buildEsbuildPlugin` (`esbuild.ts:388-425`) runs `transformWithEsbuild()` on every chunk during `renderChunk`. This calls esbuild's `transform` API for transpilation and/or minification. In Rolldown's newer path, minification can also be handled natively via the `output.minify` option (`build.ts:741-754`), but either way it runs on every chunk on every rebuild with no caching.

Similarly, when `minify: 'terser'` is used, the `terserPlugin` (`terser.ts:38+`) spawns worker threads to minify each chunk independently — all from scratch on each rebuild.

### 8. What Would Need to Change for Incremental Builds

For Vite to support incremental rebuilds in watch mode, several changes would be needed across both Vite and Rolldown:

**A. Rolldown must implement build caching (Rolldown-side)**

Rolldown would need to implement the equivalent of Rollup's `bundle.cache` — a serialized module graph containing the `resolveId`, `load`, and `transform` results for each module. The `watch()` function would need to internally persist this cache between rebuilds and only re-process modules whose source files (or dependencies) have changed. This is the single biggest blocker — without Rolldown supporting it, Vite cannot do anything.

**B. Re-enable the `cache` option (`build.ts:624`)**

The commented-out line needs to be uncommented and set to pass `undefined` (enabling cache) for watch mode:
```ts
cache: options.watch ? undefined : false,
```

**C. Re-enable `shouldTransformCachedModule` hook (`constants.ts:35`)**

Plugins need the ability to invalidate cached transforms when their configuration changes. This hook must be uncommented and added to the `ROLLUP_HOOKS` array so it gets properly wrapped by `injectEnvironmentToHooks()`.

**D. Plugin state management must become incremental-aware**

Plugins that reset all state in `buildStart`/`renderStart` would need to selectively invalidate only affected entries:
- `vite:css` would need to only clear CSS module cache entries for changed files instead of creating a new Map
- `vite:css-post` would need to preserve chunk CSS mappings for unchanged chunks
- `vite:prepare-out-dir` should skip emptying the output directory on rebuilds (it partially handles this with the `rendered` Set, but the `options()` hook clears it)

**E. Minification caching**

A chunk-content-keyed cache for esbuild/terser/oxc minification results would avoid re-minifying unchanged chunks. Since chunk content hashes are already computed, this could key on the pre-minification hash.

**F. Tree-shaking must be incremental**

Rolldown's tree-shaking (dead code elimination) currently operates on the full module graph. For incremental builds, Rolldown would need to identify which modules are affected by a change, re-analyze only those, and propagate side-effect changes incrementally — a significant algorithmic challenge.

## Key Files

- **`packages/vite/src/node/build.ts`** — Core build function, watch branch (lines 800-851), commented-out cache (line 624), `resolveRolldownOptions()` (line 565), `ChunkMetadataMap` (lines 1186-1235), `injectEnvironmentToHooks()` (lines 1237-1283)
- **`packages/vite/src/node/watch.ts`** — Watch utilities: Chokidar option resolution, `convertToNotifyOptions()` bridge to Rolldown's notify API
- **`packages/vite/src/node/cli.ts`** — CLI entry point, `--watch` flag normalization (lines 134-136, 340)
- **`packages/vite/src/node/plugins/prepareOutDir.ts`** — Output directory preparation, resets on each rebuild via `options()` hook (line 14-15)
- **`packages/vite/src/node/plugins/css.ts`** — CSS plugin caches explicitly cleared on each build (lines 318-321, 516-521)
- **`packages/vite/src/node/plugins/esbuild.ts`** — `buildEsbuildPlugin` runs minification on every chunk on every rebuild (lines 388-425)
- **`packages/vite/src/node/plugins/reporter.ts`** — Reporter confirms all modules re-transformed on each rebuild (lines 307-309)
- **`packages/vite/src/node/plugins/index.ts`** — Full plugin pipeline assembly, ~30 plugins all re-execute on each build
- **`packages/vite/src/node/constants.ts`** — `shouldTransformCachedModule` commented out of `ROLLUP_HOOKS` (line 35)

## Summary

`vite build --watch` performs complete rebuilds because it delegates to Rolldown's `watch()` function which re-runs the entire Rollup hook lifecycle — `resolveId`, `load`, `transform`, `renderChunk`, minification — for every module and chunk on each file change. There is no module-level cache: the `cache` option is commented out (`build.ts:624`), the `shouldTransformCachedModule` hook is disabled (`constants.ts:35`), and plugins explicitly clear their internal caches on each `buildStart`/`renderStart`. For incremental builds to work, Rolldown itself must first implement a persistent module graph cache between watch cycles, after which Vite would need to re-enable the cache option, restore the `shouldTransformCachedModule` hook, and refactor its plugins to selectively invalidate rather than wholesale-reset their state.
---

## Eval Metrics

| Metric | Value |
|--------|-------|
| Duration (wall) | 5m 10s |
| Duration (API) | 6m 47s |
| Turns | 54 |
| Tool calls | 104 |
| Input tokens | 2,693 |
| Output tokens | 10,540 |
| Cache read tokens | 1,479,131 |
| Cache creation tokens | 55,065 |
| Cost | $2.1685 |

### Tool Usage

| Tool | Calls |
|------|-------|
| Grep | 60 |
| Read | 32 |
| Glob | 8 |
| Task | 2 |
| Bash | 2 |

### Model Usage

- **claude-opus-4-6**: 2,693 in / 10,540 out, $1.3607
- **claude-sonnet-4-6**: 38 in / 11,615 out, $0.8067
- **claude-haiku-4-5-20251001**: 748 in / 64 out, $0.0011
