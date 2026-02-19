# Frontend Strategy Picker

## Problem

The backend supports 3 query strategies (`classic`, `single`, `multi`) and exposes them via `GET /strategies` and the `mode` parameter on `POST /run/query`. The frontend has no UI for this — every query defaults to `mode="auto"` which resolves to `"classic"`.

## Scope

Add a strategy dropdown to the Query page that lets the user pick which strategy to use. Follow the existing patterns (native `<select>`, same Tailwind classes as the search mode selector in `Search.tsx`).

## Implementation Steps

### Step 1: Add `fetchStrategies` to API client

File: `frontend/src/api/client.ts`

- Add a `fetchStrategies` function that calls `GET /strategies` and returns `string[]`
- Add `mode` parameter to the `runQuery` function signature (default `"auto"`)
- Include `mode` in the POST body sent to `/run/query`

### Step 2: Add `useStrategies` query hook

File: `frontend/src/api/hooks.ts`

- Add a `useStrategies()` hook using TanStack Query that calls `fetchStrategies`
- `staleTime: Infinity` — strategies don't change at runtime

### Step 3: Add strategy selector to Query page

File: `frontend/src/pages/Query.tsx`

- Add `strategy` state (default `"auto"`)
- Fetch strategies with `useStrategies()`
- Render a `<select>` next to the Submit button in the form row, matching the search mode selector pattern from `Search.tsx`: `bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white`
- Options: "Auto" (value `"auto"`) + one option per strategy from the API
- Pass `strategy` through to `runQuery.mutate()` as the `mode` parameter
- Disabled while `isRunning`

### Step 4: Show which strategy was used in results

File: `frontend/src/pages/Query.tsx`

- Check if the backend already returns `strategy_name` in query results or history detail
- If available, display it as a small label/badge near the answer (e.g., "Strategy: multi")
- If not available in the API response, skip this — no backend changes in this plan

## Files Changed

1. `frontend/src/api/client.ts` — add `fetchStrategies`, update `runQuery` signature
2. `frontend/src/api/hooks.ts` — add `useStrategies` hook
3. `frontend/src/pages/Query.tsx` — add strategy state, selector UI, pass to mutation
