# Unify API Routes

## Context

API routes are split across two locations with two different prefixes:
- `server.py`: `/health`, `/query` (no query history)
- `dashboard.py`: `/dashboard/api/*` (all dashboard endpoints, query history)

This is confusing. Queries via `curl /query` don't appear in query history because `server.py` doesn't call `insert_query()`. The `/dashboard/api/` prefix is ugly.

**Goal**: One prefix for all API routes (`/api/*`), keep `/dashboard` for the SPA.

## Changes

### Step 1: Move router prefix from `/dashboard/api` to `/api`

**File**: `src/indiseek/api/server.py`

- Change `app.include_router(dashboard_router, prefix="/dashboard/api")` to `prefix="/api"`
- Move `/health` to the dashboard router (or keep on `app` at `/api/health` — see below)
- Remove the duplicate `/query` endpoint entirely — the dashboard's `/api/run/query` is the full-featured version with history
- Keep the SPA mount at `/dashboard` unchanged

**Decision on `/health`**: Move it into `dashboard.py` as a router endpoint at `/health`, which becomes `/api/health` after prefix. This keeps `server.py` clean — just app setup, CORS, router include, SPA mount.

### Step 2: Add synchronous `/query` to dashboard router

The old `/query` was synchronous (returns JSON directly, no background task). Curl users need this. Add it to `dashboard.py` as a router endpoint at `/query`, becoming `/api/query`. This version saves to query history like `/run/query` does.

Take the existing `server.py` query handler, add `insert_query`/`complete_query`/`fail_query` calls around it.

### Step 3: Update frontend API base

**File**: `frontend/src/api/client.ts`
- Change `API_BASE` from `'/dashboard/api'` to `'/api'`

**File**: `frontend/vite.config.ts`
- Change dev proxy key from `'/dashboard/api'` to `'/api'`

### Step 4: Update tests

- `tests/test_dashboard.py`: Change all `/dashboard/api/` prefixes to `/api/`
- `tests/test_agent.py` (server tests): Update `/health` to `/api/health`, remove `/query` tests or point to `/api/query`
- Any other test files referencing the old paths

### Step 5: Update CLAUDE.md / docs

- Update curl examples in `CLAUDE.md` that reference `/health`, `/query`, `/dashboard/api/`
- Update `docs/SPEC.md` if it has route references

## Files Modified

- `src/indiseek/api/server.py` — strip down to app setup + router include + SPA mount
- `src/indiseek/api/dashboard.py` — add `/health` and `/query` endpoints
- `frontend/src/api/client.ts` — change `API_BASE`
- `frontend/vite.config.ts` — change proxy path
- `tests/test_dashboard.py` — update route prefixes
- `tests/test_agent.py` — update route prefixes
- `CLAUDE.md` — update curl examples

## Verification

- [ ] `pytest` — all tests pass
- [ ] `ruff check src/` — no lint errors
- [ ] `curl http://localhost:8000/api/health` — returns `{"status": "ok"}`
- [ ] `curl -X POST http://localhost:8000/api/query -H 'Content-Type: application/json' -d '{"prompt": "test"}'` — returns answer AND appears in query history
- [ ] Frontend loads at `/dashboard` and all pages work
- [ ] Old paths (`/health`, `/query`, `/dashboard/api/*`) return 404
