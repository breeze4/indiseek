# How Vite Hot Module Reloading Works for CSS

Vite handles CSS HMR through two distinct paths depending on how the CSS is consumed: imported via JS (`import './style.css'`) or referenced via `<link>` tags. Both achieve instant style updates without a full page reload, but the mechanisms differ.

## The Two Paths

### Path 1: Imported CSS (the common case)

When you write `import './style.css'`, Vite's CSS plugin (`plugins/css.ts`) transforms the CSS file into a JS module:

```js
import { updateStyle, removeStyle } from '/@vite/client'
const id = '/src/style.css'
const css = '/* actual CSS content */'
updateStyle(id, css)
import.meta.hot.accept()           // self-accepting — no propagation needed
import.meta.hot.prune(() => removeStyle(id))
```

`updateStyle()` creates a `<style>` tag with `data-vite-dev-id` and sets its `textContent`. On HMR updates, the same function fires again and simply overwrites the `textContent` of the existing `<style>` tag — instant, no flicker.

Because the module calls `import.meta.hot.accept()`, it is **self-accepting**: the HMR boundary stops at the CSS file itself. No importers need to re-execute. The server sends a `js-update` message, the client re-imports the JS wrapper, and the new CSS is applied.

### Path 2: `<link>` Referenced CSS

CSS served as a raw stylesheet (e.g., from `index.html` `<link>` tags) takes a different path. The server sends a `css-update` WebSocket message. The client handles it by:

1. Finding the existing `<link>` element by matching its `href`
2. Cloning the `<link>` tag and appending `?t=<timestamp>` to the URL
3. Inserting the new tag after the old one
4. Removing the old tag only after the new one fires its `load` event

This double-tag swap prevents a flash of unstyled content (FOUC) — the old styles stay applied until the new stylesheet finishes loading.

## Change Detection and Propagation

File watching uses `chokidar` (`server/index.ts`). When a CSS file changes:

1. **Invalidation**: `moduleGraph.onFileChange(file)` clears the cached transform result
2. **Propagation**: `handleHMRUpdate()` in `server/hmr.ts` walks the module graph upward via `propagateUpdate()` looking for HMR boundaries (self-accepting modules)
3. **Boundary resolution**: For imported CSS, the CSS module itself is the boundary. For CSS Modules (which export class name mappings), the module is *not* self-accepting — the importer must re-execute to pick up changed exports
4. **WebSocket dispatch**: The server sends an `update` payload with the boundary path and a timestamp

## CSS Preprocessing and Dependencies

Before any of this, `compileCSS()` handles the transform pipeline:

- Runs preprocessors (Sass/Less/Stylus) if the file extension requires it
- Runs PostCSS, which inlines `@import` statements via `postcss-import`
- Resolves `url()` references
- Processes CSS Modules (generates class name mappings)

Crucially, every file pulled in via `@import` is registered with `this.addWatchFile()`, so changes to imported partials trigger HMR on the root CSS file that imports them.

## CSS Modules: The Exception

CSS Modules (`*.module.css`) export a JS object mapping local class names to generated names. Because the exported *values* can change, these modules set `isSelfAccepting = false`. A change in a CSS Module propagates up to its JS importer, which must re-execute to get the new class name bindings. The style update itself still happens via `updateStyle()`, but the importer also re-renders.

## Summary

| Mechanism | Imported CSS | `<link>` CSS | CSS Modules |
|---|---|---|---|
| Transport | `js-update` via WebSocket | `css-update` via WebSocket | `js-update` via WebSocket |
| Application | Overwrite `<style>` textContent | Swap `<link>` tags | Overwrite `<style>` + re-execute importer |
| Self-accepting | Yes | N/A | No (propagates to importer) |
| FOUC risk | None | Prevented by double-tag swap | None |
