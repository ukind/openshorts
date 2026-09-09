---
date: 2026-09-09T08:40:30+0700
author: Yogiswara Utama
commit: fde9338
branch: main
repository: openshorts
topic: "game-profiles NameError 500"
tags: [intent, frd, app-py, cloud-auth, game-profiles]
status: ready
last_updated: 2026-09-09T08:40:30+0700
last_updated_by: Yogiswara Utama
---

# FRD: game-profiles NameError 500

## Summary

Eight game-profile endpoints in `app.py` call `get_current_user_required(request)` without importing the name, so every cloud-branch request crashes with `NameError` and returns 500 with a full ASGI traceback. The fix adds the function-local import at each call site, matching the existing `app.py` convention. Self-host then answers a clean 401; cloud mode behavior does not change.

## Problem & Intent

`GET /api/game-profiles` returns 500 on every call in local dev (`docker compose up`): `NameError: name 'get_current_user_required' is not defined` at `app.py:7205` in `list_game_profiles`. The dashboard fetches the route once on mount (`App.jsx:771`), so the VSCode terminal fills with `ERROR: Exception in ASGI application` tracebacks.

Developer's own framing: the error "shows in the cli / console inside vscode terminal, it just to make sure the error does not bother the main project purpose."

## Goals

- No `ERROR`/traceback lines in the backend console when the dashboard loads and hits the game-profiles routes in self-host.
- The main video-processing pipeline stays untouched.
- Cloud mode (`BILLING_ENABLED=1`) behavior stays byte-identical to today's design.

## Non-Goals

- Fixing the 6 failing tests in `tests/test_game_profiles.py` — different root cause (AsyncMock misuse), pre-existing baseline.
- Making game profiles usable in self-host — the developer chose "silent but clean" (401) over enabling local mode by default.
- Auth-gating the Game Profiles nav/page in the frontend.
- Redesigning the `LOCAL_GAMEPROFILES` / `BILLING_ENABLED` mode system.

## Functional Requirements

1. Each of the 8 game-profile endpoints that take the cloud branch SHALL import `get_current_user_required` from `cloud.auth` at the point of use, before the call: create (`app.py:7153`), list (`:7205`), export (`:7235`), import (`:7301`), get one (`:7392`), update (`:7456`), delete (`:7501`), duplicate (`:7546`).
2. On self-host with no credentials, `GET /api/game-profiles` SHALL return HTTP 401 with body `{"detail": "Authentication required"}` — never 500.
3. In cloud mode, authenticated calls SHALL keep returning profile data as designed.
4. The `LOCAL_GAMEPROFILES=1` local branch SHALL keep working unchanged (it does not reference the missing name).

## Non-Functional Requirements

- **Performance**: no added latency; module imports resolve once and stay cached.
- **Security**: the fix restores the intended gate — today an unauthenticated cloud call 500s, after the fix it 401s. No DB access happens before credential checks (`cloud/auth.py:111-131`).
- **UX / Accessibility**: the dashboard already swallows the fetch error (`App.jsx:779`); after the fix the console stays clean.
- **Reliability**: no new failure modes; `get_current_user_required` raises a plain `HTTPException(401)` (`cloud/auth.py:139`) that FastAPI handles normally.

## Constraints & Assumptions

- Match the existing `app.py` convention for `cloud.auth` imports: function-local, not module-level (`app.py:119`, `app.py:3153`).
- No new dependencies, no schema changes, no frontend changes.
- Docker compose dev flow stays as-is; no `.env` or compose changes.
- Assumption: the 8 endpoints are wanted in this fork (they arrived in the marsic fork parity merge), so the fix is repair-in-place, not deletion.

## Acceptance Criteria

- [ ] `curl -i http://localhost:8000/api/game-profiles` on self-host returns `HTTP/1.1 401 Unauthorized` with `Authentication required` in the body; the uvicorn console prints one `INFO` access line and no `ERROR`/traceback.
- [ ] Loading the dashboard produces zero `ERROR: Exception in ASGI application` lines in the backend console.
- [ ] `grep -n "from cloud.auth import get_current_user_required" app.py` shows the import present in the cloud branch of all 8 endpoints listed in FR-1.
- [ ] `python -m pytest tests/test_game_profiles.py -q` shows the same baseline result as before the change (6 failed / 1 passed) — no new failures introduced.
- [ ] A full `python -m pytest -q` run shows no new failures vs the pre-existing env-failure baseline.
- [ ] With `BILLING_ENABLED=1` and a valid session, `GET /api/game-profiles` returns 200 with the profile list (cloud path unchanged).

## Recommended Approach

Add one function-local `from cloud.auth import get_current_user_required` line inside the cloud branch of each of the 8 game-profile endpoints, exactly mirroring the existing pattern at `app.py:3153`. No helper abstraction, no module-level import, no frontend or env changes.

## Decisions

### Root cause and fix placement
**Question**: Pre-resolved from codebase evidence — confirmed in Step 4 ("the root cause is 8 bare calls to get_current_user_required in app.py (7153–7546) with no import in scope — the function itself is fine (cloud/auth.py:136). Confirm this diagnosis?")
**Recommended**: Confirm — add the missing function-local import at the 8 call sites, matching the existing app.py convention.
**Chosen**: Confirm (Recommended).
**Rationale**: evidence: app.py:7153-7546 (bare calls), cloud/auth.py:136 (function exists, raises clean 401), app.py:3153 (existing function-local import convention) + confirmed.

### Self-host behavior after the fix
**Question**: "After the import fix, what should GET /api/game-profiles do on a self-host box (your docker compose dev, nobody signed in)?"
**Recommended**: Clean 401 — only add the missing imports; self-host answers 401 "Authentication required", console clean, cloud unchanged.
**Chosen**: Clean 401 (developer selected option A with its diff preview).
**Rationale**: smallest diff, one code path; matches stated intent that the error must not bother the main project purpose. "Local default ON" (defaulting `LOCAL_GAMEPROFILES` when `BILLING_ENABLED` is unset) was declined as added mode complexity; "env-only workaround" was declined because it leaves the latent NameError shipped.

## Open Questions

(none — nothing was explicitly deferred)

## Suggested Follow-ups

- `tests/test_game_profiles.py`: 6 of 7 tests fail on AsyncMock misuse (`async with session.begin():` at `cloud/game_profiles.py:36/:104/:133` receives a coroutine) — pre-existing, unrelated to this bug.
- The frontend nav entry for game-profiles is not auth-gated (`App.jsx:1215`) and fetches on mount (`App.jsx:771`); in self-host these requests now return silent 401s — hiding the nav when unauthenticated is a possible later polish.
- `POST /api/game-profiles/analyze` (`app.py:8109`) performs no auth at all, unlike its 8 siblings — revisit if cloud exposure matters.

## References

- Input: error traceback from the skill invocation (`app.py:7205`, `NameError: name 'get_current_user_required' is not defined`).
- Probe agent report (this session): auth helper behavior, `BILLING_ENABLED` flag, frontend fetch sites, test failures.
- `.rpiv/artifacts/plans/2026-09-06_14-41-23_marsic-fork-parity-merge.md` — the merge that brought the game-profile endpoints into `app.py`.
