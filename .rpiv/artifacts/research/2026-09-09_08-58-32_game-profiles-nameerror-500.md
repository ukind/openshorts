---
date: 2026-09-09T08:58:32+0700
author: Yogiswara Utama
commit: fde9338
branch: main
repository: openshorts
topic: "game-profiles NameError 500"
tags: [research, codebase, app-py, cloud-auth, game-profiles]
status: ready
last_updated: 2026-09-09T08:58:32+0700
last_updated_by: Yogiswara Utama
---

# Research: game-profiles NameError 500

## Research Question
Eight game-profile endpoints in `app.py` call `get_current_user_required(request)` without the name imported, so every cloud-branch request raises `NameError` and returns 500 with a full ASGI traceback. Verify the mechanism, map the exact insertion points for the function-local import fix, prove the self-host 401 and cloud-mode 200 paths, and bound the test/frontend blast radius. (Chained from `.rpiv/artifacts/discover/2026-09-09_08-40-30_game-profiles-nameerror-500.md`.)

## Summary
- **Root cause**: the billing gate binds only the optional variant — `from cloud.auth import get_current_user_optional` under billing (`app.py:119`), a no-op shim otherwise (`app.py:127-129`). `get_current_user_required` is never bound at module scope under **any** configuration. The 8 bare calls (`app.py:7153`, `:7205`, `:7235`, `:7301`, `:7392`, `:7456`, `:7501`, `:7546`) compile to deferred `LOAD_GLOBAL` lookups that fail at request time, so the app boots clean.
- **Cloud mode is broken today too** — `app.py:119` imports only the optional variant, so `BILLING_ENABLED=1` crashes the same 8 routes. FR-3's "keep returning profile data" is really "restore"; the fix adds one name binding and changes nothing else.
- **Fix shape**: one function-local `from cloud.auth import get_current_user_required` inside each cloud branch, template `app.py:3153` (origin commit `927ee09`, 7 weeks stable, zero follow-ups). Shape A (7 endpoints): first statement inside the `else:` block. Shape B (import endpoint): first statement inside `if not is_local:` (`app.py:7300-7301`).
- **Self-host post-fix**: anonymous request → `get_current_user_optional` returns `None` before any DB session (`cloud/auth.py:122-123`) → `HTTPException(401, "Authentication required")` at `cloud/auth.py:139` → one `INFO` access line, no traceback (AC-1). Dashboard mount/refresh/modal catches swallow it silently; GameProfilesPage shows its existing red banner.
- **Import safety**: the `cloud.auth` module-top chain is import-safe with `BILLING_ENABLED` unset (lazy env reads, deferred engine init). It needs PyJWT + sqlalchemy, which live only in `requirements-billing.txt`; the Dockerfile installs both unconditionally, so `docker compose up` is covered — **decision: Docker-only install surface**. The bare-pip flow documented in CLAUDE.md would trade `NameError` 500 for `ModuleNotFoundError` 500; recorded as a known caveat, not fixed.
- **Tests**: zero route coverage — no test exercises any cloud branch. Standalone baseline `6 failed / 1 passed` re-measured at `fde9338`; all six failures are pre-existing AsyncMock misuse in the service layer, disjoint from the `app.py` diff. `curl` 401 (AC-1) + `grep` (AC-3) carry the proof.

## Detailed Findings

### Name-binding mechanism (why request-time, why `_user_from_request` is healthy)
- `app.py:93` sets `BILLING_ENABLED`; `app.py:116-119` imports the cloud stack under billing and binds `get_current_user_optional` only; `app.py:127-129` else-branch shim covers the optional variant only. The required variant gets no binding on either side — it exists only in the `cloud.auth` namespace (`cloud/auth.py:136`).
- `def` compiles the body without running it; the decorators (`app.py:7111`, `:7183`, `:7221`, `:7267`, `:7362`, `:7415`, `:7485`, `:7511`) register routes at import time. CPython emits `LOAD_GLOBAL` for the bare name, resolved only when a handler runs → 500 at first request, never at boot.
- `_user_from_request` (`app.py:132-134`) stays healthy because it awaits `get_current_user_optional`, which is always bound. Its nine call sites (`app.py:144`, `:240`, `:346`, `:434`, `:459`, `:470`, `:488`, `:5591`, `:6060`) treat anonymous as BYOK.
- Placement trap: a reader who assumes the `else:` shim covers auth helpers will place a module-level binding in the wrong branch. The fix needs no shim — the function-local import binds a local (`LOAD_FAST`) inside each handler frame.
- Any deployment with `LOCAL_GAMEPROFILES != "1"` reaches a call site — including billing mode. Only `LOCAL_GAMEPROFILES=1` routes around the broken branch (via `LocalGameProfileRepository`, `app.py:7118`).

### Import-chain safety (self-host, `BILLING_ENABLED` unset)
- Execution order of `from cloud.auth import get_current_user_required`: `cloud/__init__.py:13` → `cloud/config.py` (Settings class `:134`, instance `:301`; every attribute is a lazy `@property` env read — instantiation reads zero env vars; `validate_required` `:304`/`:316` runs only via `setup_sync`, called under the billing gate `app.py:1755-1756`) → `import jwt` / fastapi / sqlalchemy (`cloud/auth.py:15-18`) → `from .config import settings` `:20` → `from . import config, database, metering, email_policy` `:21` → `from .models import ...` `:22`.
- `cloud/database.py`: `Base = declarative_base()` `:8`; the engine is created only inside `init_engine` `:14-26`, run only from `setup_async` (`cloud/__init__.py:49`), called under billing (`app.py:1740-1741`). `cloud/models.py` registers tables on in-memory metadata, no connections. `cloud/metering.py` tops are imports only. `cloud/email_policy.py:41` reads the repo-shipped `disposable_domains.txt`, exception-guarded (`:37-38`).
- The lazy `from . import api_keys` (`cloud/auth.py:109`) runs per request, not at import; its own top has no env reads, and the reverse lazy import at `cloud/api_keys.py:76` breaks the cycle.
- **Dependencies**: `PyJWT==2.10.1` (`requirements-billing.txt:10`) and `sqlalchemy[asyncio]==2.0.36` (`:4`) are absent from `requirements.txt` (20 entries). The official image installs both files unconditionally (`Dockerfile:13`, `:17`, `:20`) — one image serves both modes. Docker-only decision recorded in Developer Context.
- The local branch's `from cloud.local_game_profiles import ...` (`app.py:7118`, `:7189`, `:7231`, `:7344`, `:7368`, `:7421`, `:7491`, `:7517`) proves only the package top + config top are import-safe; `auth`/`database`/`metering`/`models` tops first execute at the fixed import.
- The reference pattern `app.py:3153` lives in `restore_project` (`app.py:3094-3095`), whose self-host branch returns at `:3150` — that import has **never** executed in a self-host process. The fix runs `cloud.auth` tops in self-host for the first time; the analysis above is the safety proof.

### Insertion map (two branch shapes)
Shape A — seven `else:` cloud branches. Insert as the first statement inside the `else:`, directly above the bare call (above or below the `# Cloud mode - use existing authentication` comment is equivalent; the export branch omits that comment):

| Endpoint | Route | `else:` | Bare call | Sibling service import |
|---|---|---|---|---|
| create | POST `/api/game-profiles` | `app.py:7151` | `app.py:7153` | `create_game_profile` `:7154` |
| list | GET `/api/game-profiles` | `app.py:7203` | `app.py:7205` | `list_game_profiles` `:7206` |
| export | GET `/api/game-profiles/export` | `app.py:7234` | `app.py:7235` | `list_game_profiles` `:7236` |
| get one | GET `/api/game-profiles/{id}` | `app.py:7390` | `app.py:7392` | `get_game_profile` `:7393` |
| update | PUT `/api/game-profiles/{id}` | `app.py:7454` | `app.py:7456` | `update_game_profile` `:7457` |
| delete | DELETE `/api/game-profiles/{id}` | `app.py:7499` | `app.py:7501` | `delete_game_profile` `:7502` |
| duplicate | POST `/api/game-profiles/{id}/duplicate` | `app.py:7544` | `app.py:7546` | `duplicate_game_profile` `:7547` |

Shape B — import endpoint (`POST /api/game-profiles/import`, `app.py:7267`): `is_local = os.environ.get("LOCAL_GAMEPROFILES") == "1"` at `:7299`, guard `if not is_local:` at `:7300`, bare call at `:7301`, `user_id = str(user.id)` at `:7302`. Insert as the first statement inside the `if not is_local:` block (between `:7300` and `:7301`); its service import is deferred into the loop at `:7350`.

- The sibling function-local `from cloud.game_profiles import ...` lines execute on every cloud request and never moved to module scope — the convention exists to keep the non-billing import path free of cloud imports (`app.py:116-123`).
- `POST /api/game-profiles/analyze` (`app.py:8109-8154`) calls no auth helper: parses body `:8119`, resolves provider/keys from headers and body `:8133-8149`, runs the analyzer in an executor `:8150`/`:8154`. Out of scope; no `NameError` class can occur there.

### Self-host 401 path (AC-1)
- End-to-end: mount fetch `dashboard/src/App.jsx:771` → `apiJson` (`dashboard/src/lib/api.js:54`, `!res.ok` `:68`) → `list_game_profiles` `app.py:7184` → `else:` `:7204` → inserted import → call `:7205` → `get_current_user_optional` reads `Authorization` `:111`, token empty → `if not token: return None` `:122-123` (**before any DB session**; the only `database.session()` calls sit at `:119` key branch and `:132` JWT branch) → required wrapper raises `HTTPException(401, "Authentication required")` `cloud/auth.py:139` → FastAPI renders 401 JSON, uvicorn logs one `INFO` line. `ERROR: Exception in ASGI application` fires only for unhandled exceptions — none remain on this path.
- Client catches: `App.jsx:779` (mount, `/* ignore */`), `App.jsx:2308` (Refresh), `App.jsx:2353` (modal save refetch), `App.jsx:757-758` (selected-profile fallback to stub), `VoiceOverPage.jsx:382` — all silent. `GameProfilesPage.jsx` catch sites (`:30`, `:53`, `:74`, `:92`, `:102`, `:182`, `:208`) set `error` → full-page red banner (`:114-119`) replacing the page content; today that banner shows `Error: 500: Internal Server Error…`, post-fix `Error: 401: {"detail":"Authentication required"}` — same treatment, no new UI state.
- `apiJson` throws a typed `ApiError` (`dashboard/src/lib/api.js:44-53`, throw at `:77`) for every non-2xx; only 402 is special-cased (`:30-36`); no toast, no retry, no global handler. `apiFetch` attaches `Authorization: Bearer` from localStorage `openshorts_auth` when present (`:25`) — absent in self-host.
- **Accepted edge (recorded)**: self-host + `Bearer osk_…`/`X-API-Key` follows the key branch (`cloud/auth.py:113-119` → `cloud/api_keys.py:50` → `cloud/database.py:60`) into `get_sessionmaker`, which raises `RuntimeError` at `cloud/database.py:37` because `init_engine` never ran → 500 + traceback. Today the shim (`app.py:127-129`) never touched `api_keys`, so the fix opens this path — but the same request 500s today anyway via `NameError`. No code change (see Developer Context).
- A random JWT also 401s cleanly: `_decode_jwt` (`cloud/auth.py:56-60`) catches everything and returns `None`.

### Cloud-mode path (AC-6 / FR-3)
- Under billing, `app.py:117-119` already loaded `cloud.auth` into `sys.modules` at startup → each inserted import line is one cached module lookup that binds the existing function object. No file read, no re-execution, no behavioral delta.
- Valid Bearer JWT: optional extracts token `:111-112` → `_decode_jwt` `:124` → `uuid.UUID` `:129` → `database.session()` `:132` → `_load_current_user` `:78-99` (`session.get(User)` `:79`, `_active_subscription` `:82`, `_topups_fifo` `:83`, `UploadPostProfile` `:87`, `CurrentUser` `:93-99`) → service `create_game_profile` (`cloud/game_profiles.py:14-41`: local check `:18-19`, field filter `:26-31`, session `:33`, `begin()` `:34`, model `:35-38`, add/flush `:39-40`, return `:41`) → 200.
- Export reuses `list_game_profiles` (`app.py:7236-7237` → `cloud/game_profiles.py:44-57`); the import loop reuses `create_game_profile` per row (`app.py:7350-7351`). One inserted import per endpoint covers every downstream service call.
- Pre-fix, this exact request died with `NameError` at the call site — the fix restores the designed path; it does not alter it.

### Dashboard fetch surface (route × caller)
| Route | Definition | Frontend callers |
|---|---|---|
| POST create | `app.py:7111` | `CreateEditProfileModal.jsx:332` |
| GET list | `app.py:7183` | `App.jsx:771`, `App.jsx:2308`, `App.jsx:2353`, `GameProfilesPage.jsx:27` |
| GET export | `app.py:7221` | `GameProfilesPage.jsx:39` |
| POST import | `app.py:7267` | `GameProfilesPage.jsx:66` |
| GET one | `app.py:7362` | `App.jsx:755`, `GameProfilesPage.jsx:179`, `GameProfilesPage.jsx:201`, `VoiceOverPage.jsx:378` |
| PUT update | `app.py:7415` | `CreateEditProfileModal.jsx:326` |
| DELETE | `app.py:7485` | `GameProfilesPage.jsx:88` |
| POST duplicate | `app.py:7511` | `GameProfilesPage.jsx:98` |

- The two routes `GameProfilesPage` never calls (create, update) live in `CreateEditProfileModal.handleSubmit` (`CreateEditProfileModal.jsx:326-332`, guarded `if (profile)` `:319`); the modal mounts from the page (`GameProfilesPage.jsx:261-269`) and from the dashboard (`App.jsx:2352-2354`, opened via `:2322`).
- The mount fetch (`App.jsx:767-783`, empty deps) fires on **every** dashboard load regardless of tab — that is the console-spam source. The selected-profile fetch (`App.jsx:747-764`) only fires after a successful list response (needs a stub match at `:752`), so it never runs post-fix in self-host.
- Nav entry `App.jsx:1215` is unconditional inside `navItems` (`:1203-1217`; contrast History `:1212`, conditional on `billingEnabled`/`isSignedIn`); page mounts at `:2079` on tab activation, unmounts on switch. Auth-gating the nav is a declared non-goal.
- `GameProfilesPage` render gates: `if (loading)` spinner `:106-112` → `if (error)` banner `:114-119` → empty-state only on success-with-zero `:164-167`.

### Test blast radius (measured live at `fde9338`)
- Standalone: `6 failed, 1 passed in 0.55s` (`tests/test_game_profiles.py -q`, repo `.venv`, pytest 9.1.1). `test_game_profile_model` (`:192`, sync) passes.
- Failure mechanism: tests patch `cloud.database.session` (`:40`, `:65`, `:89`, `:119`, `:148`, `:172`) and wire the outer `async with` via `MagicMock.__aenter__` — that part works — but `mock_session_instance` is a bare `AsyncMock()` whose attribute calls return **coroutines**: `session.begin()` → `TypeError` (`cloud/game_profiles.py:34`, `:104`, `:133`); `result.scalars().all` → `AttributeError` (`:57`); `scalar_one_or_none()` returns a coroutine, asserted `is None` → `AssertionError` (`tests/test_game_profiles.py:102`); `duplicate` receives a truthy coroutine, `original.name` → `AttributeError` (`cloud/game_profiles.py:161`).
- Zero route coverage: the test file imports `cloud.models` + `cloud.game_profiles` only (`:3-17`); executed import graph leaves `app` and `cloud.auth` out of `sys.modules. No test drives any cloud branch, so the suite can neither detect the bug nor prove the fix.
- `tests/test_game_profile_update_http.py` imports `app` (`:37`) but sets `LOCAL_GAMEPROFILES=1` at module level **before** the import (`:28-32`) → exercises only the local branch; measured `5 passed` while the bug is live. It uses `httpx.ASGITransport` (`:45-46`), not `TestClient`.
- `tests/conftest.py` (10 lines) only sets `sys.path` and `BILLING_ENABLED=0` (`:10`); no app import → collection never runs `app.py` module code regardless.
- **Env leak caveat**: `test_game_profile_update_http.py:32` never restores `LOCAL_GAMEPROFILES`; in a shared process (alphabetical order) the six service tests then take the local branch and produce a different split — measured `3 failed, 9 passed` with different failure modes. Compare like-for-like: isolated command, before vs after.
- FRD drift correction: the FRD follow-up cites `cloud/game_profiles.py:36`; the actual `session.begin()` line is `:34` (`:104`, `:133` correct).
- `tests/test_semantic_integration.py:47/:76/:112` patch `main.get_game_profile` (`main.py:74` import, `:81` redefine) — a different symbol, not route coverage.

## Code References
- `app.py:93` — `BILLING_ENABLED` flag
- `app.py:116-129` — billing import gate; optional variant bound both sides, required variant never
- `app.py:132-134` — `_user_from_request` (healthy; optional-only)
- `app.py:3152-3157` — function-local import convention (template; runs in billing mode only)
- `app.py:7111-7180` — create endpoint (Shape A; call `:7153`)
- `app.py:7183-7218` — list endpoint (call `:7205`)
- `app.py:7221-7264` — export endpoint (call `:7235`)
- `app.py:7267-7359` — import endpoint (Shape B; `:7299-7303` guard + call, `:7350` service import)
- `app.py:7362-7413` — get one (call `:7392`)
- `app.py:7415-7483` — update (call `:7456`)
- `app.py:7485-7509` — delete (call `:7501`)
- `app.py:7511-7560` — duplicate (call `:7546`)
- `app.py:8109-8154` — analyze endpoint (no auth; out of scope)
- `cloud/auth.py:15-24` — module-top imports (jwt `:15`, sqlalchemy `:18`, siblings `:21`)
- `cloud/auth.py:101-133` — `get_current_user_optional` (lazy `api_keys` `:109`; anonymous returns `None` `:122-123`)
- `cloud/auth.py:136-140` — `get_current_user_required` (401 raise `:139`)
- `cloud/config.py:134-144,301,304-316` — lazy Settings properties; `validate_required` gated
- `cloud/database.py:14-37,41` — deferred engine; `get_sessionmaker` RuntimeError
- `cloud/game_profiles.py:14-41,44-57,79,104,133,156-161` — service layer + failing test lines
- `cloud/local_game_profiles.py:8-19` — stdlib-only local repository
- `cloud/api_keys.py:46-50,76` — key lookup + cycle break
- `cloud/email_policy.py:37-41` — guarded file load
- `dashboard/src/App.jsx:747-783,1215,2074-2079,2297-2354` — mount fetch, nav, page mount, dropdown/refresh/modal
- `dashboard/src/components/GameProfilesPage.jsx:19-30,39,66,88,98,106-119,176-208` — page surface + catch sites
- `dashboard/src/components/CreateEditProfileModal.jsx:265,326-342,359-362` — write routes + error banner
- `dashboard/src/lib/api.js:22-36,44-53,54-77` — apiFetch/ApiError/apiJson
- `dashboard/src/components/VoiceOverPage.jsx:378-382` — silent get-one catch
- `tests/test_game_profiles.py:3-17,40-186,192` — service-only tests
- `tests/test_game_profile_update_http.py:28-48` — local-branch HTTP test + env leak
- `tests/conftest.py:1-10` — path + billing-off only
- `requirements-billing.txt:4,10` — sqlalchemy, PyJWT
- `Dockerfile:13,17,20` — both requirements files installed unconditionally

## Integration Points

### Inbound References
- `dashboard/src/App.jsx:771` (mount), `:2308` (refresh), `:2353` (post-save) → GET list
- `dashboard/src/components/GameProfilesPage.jsx:27/:39/:66/:88/:98/:179/:201` → list/export/import/delete/duplicate/get-one
- `dashboard/src/components/CreateEditProfileModal.jsx:326/:332` → update/create; `:265` → analyze (unaffected)
- `dashboard/src/App.jsx:755` + `dashboard/src/components/VoiceOverPage.jsx:378` → get one
- `tests/test_game_profiles.py:9-16` → `cloud.game_profiles` service functions (not routes)
- `tests/test_game_profile_update_http.py:37-48` → app routes, local branch only

### Outbound Dependencies
- `cloud.auth.get_current_user_required` / `get_current_user_optional` — auth chain
- `cloud.game_profiles` — create/list/get/update/delete/duplicate service functions
- `cloud.local_game_profiles.LocalGameProfileRepository` — local branch (`LOCAL_GAMEPROFILES=1`)
- Packages: PyJWT, sqlalchemy[asyncio] (`requirements-billing.txt`) — Docker image installs both

### Infrastructure Wiring
- `app.py:116-129` billing gate — module-level cloud imports happen only under `BILLING_ENABLED`
- Per-endpoint `LOCAL_GAMEPROFILES` env checks (`app.py:7115`, `:7187`, `:7230`, `:7299`, `:7366`, `:7419`, `:7489`, `:7515`) — per-request branch, independent of billing
- `Dockerfile:13-20` — one image, both requirements files
- `tests/conftest.py:10` — `BILLING_ENABLED=0` for the suite

## Architecture Insights
- **Two independent mode flags**: `BILLING_ENABLED` gates startup module imports; `LOCAL_GAMEPROFILES` gates the per-request branch. The game-profile endpoints branch on `LOCAL_GAMEPROFILES` alone, so the cloud branch is reachable in self-host — that is why a missing import bites local dev, and why function-local (not module-level) placement is load-bearing.
- **House convention**: cloud imports are function-local everywhere else (`app.py:314`, `:1382-1384`, `:3153`); `app.py:119` is the deliberate module-level exception inside the billing gate. The fix copies the convention; a module-level import would fight the gate.
- **Deferred failure style**: bare-call style (vs `Depends(...)`) moves name resolution to request time — a `Depends(get_current_user_required)` default would have crashed at boot instead and been caught immediately.
- **AsyncMock discipline**: a bare `AsyncMock()` instance's attribute calls return coroutines; context-manager wiring alone is not enough. The suite's red baseline is mock-shape, not logic.
- **Merge hygiene**: byte-identity parity merges re-land latent defects by design; a post-merge grep of merged regions for used-but-not-imported names is the cheap countermeasure (the `d450d08` merge fixed one such NameError and missed this one).

## Precedents & Lessons
5 similar past changes analyzed.

### Precedent: Game-profile endpoints landed with the bare auth calls (bug origin)
**Commit(s)**: `9f280ca` — "feat: game profiles, multimodal candidate detection and clip quality pipeline" (2026-09-02), entered via upstream merge `0f326fd`
**Blast radius**: 20 files, +7900/−397 — app.py (+2438), cloud/ (game_profiles, local_game_profiles), tests/ (3 files)
**Follow-up fixes**: none — bug live at HEAD `fde9338` (7 days)
**Takeaway**: the code arrived untested on its auth path; this fix is the first follow-up.

### Precedent: Marsic fork-parity merge re-landed the same bug
**Commit(s)**: `d450d08` — "Merge marsic/main: fork parity" (2026-09-07)
**Blast radius**: 127 files, +21056/−6730
**Follow-up fixes**: none for this bug; the merge message fixed a *different* latent NameError (titles head)
**Lessons from docs**: the parity plan pinned "game profile modules — they land byte-identical"; validation recorded `6 failed / 1 passed` as a not-met gate ruled pre-existing
**Takeaway**: a merge review that catches one NameError class does not catch them all; a known-red test file hides new breakage.

### Precedent: Origin of the function-local import convention
**Commit(s)**: `927ee09` — "feat(projects): reopen archived projects from history" (2026-07-18)
**Blast radius**: 7 files, +567/−53
**Follow-up fixes**: none in 7 weeks; pickaxe shows it is the sole `from cloud.auth import get_current_user_required` site in app.py
**Takeaway**: copy the `app.py:3153` pattern verbatim; no helper, no module-level import.

### Precedent: Merge resolution dropped code, fixed same day
**Commit(s)**: `25222f5` — "fix(merge): job-start Gemini-key gate accepts a configured local LLM" (2026-09-02)
**Blast radius**: 1 file, +4/−1 (app.py)
**Takeaway**: merge-day defects surface fast when someone runs the affected path once; nobody ran a cloud-branch game-profile request for 7 days.

### Precedent: Latent crash surfaced when a gate widened
**Commit(s)**: `56707b7` — "fix(process): provider-only jobs no longer die at launch on None GEMINI_API_KEY" (2026-09-05)
**Blast radius**: 1 file, +4/−1 (app.py)
**Lessons from docs**: `.rpiv/artifacts/research/2026-09-06_06-09-59_marsic-fork-parity-merge.md` names it: "e2e a provider-only job and a no-keys job post-merge"
**Takeaway**: the no-credentials self-host case is a first-class path — test it explicitly (unauthenticated curl → 401).

### Composite Lessons
- **Tests will not catch this fix or a regression** (`9f280ca`, `d450d08`) — no test reaches a cloud branch; acceptance is the curl 401 (AC-1) + grep (AC-3) + like-for-like baseline comparison.
- **Fork-parity merges import latent defects wholesale** (`0f326fd`, `d450d08`) — post-merge, grep merged regions for names used but never imported.
- **Follow the `927ee09` pattern exactly** (`927ee09`) — one import line per endpoint inside the cloud branch; zero follow-ups in 7 weeks.
- **Silent frontend degradation hides endpoint 500s** (`App.jsx:779`) — one unauthenticated curl per new endpoint after a merge costs seconds and would have caught this on 2026-09-02.

## Historical Context (from `.rpiv/artifacts/`)
- `.rpiv/artifacts/discover/2026-09-09_08-40-30_game-profiles-nameerror-500.md` — the FRD this research answers
- `.rpiv/artifacts/plans/2026-09-06_14-41-23_marsic-fork-parity-merge.md` — the merge that brought the endpoints in (byte-identity rule)
- `.rpiv/artifacts/research/2026-09-06_06-09-59_marsic-fork-parity-merge.md` — its research sibling (gate-widening lesson)
- `.rpiv/artifacts/validation/2026-09-07_21-23-26_marsic-fork-parity-merge.md` — recorded the 6F/1P not-met gate as pre-existing

## Developer Context
**Q (discover: Root cause and fix placement): Pre-resolved — confirmed diagnosis, 8 bare calls in app.py with no import in scope**
A: Confirm (Recommended) — add the missing function-local import at the 8 call sites, matching the existing app.py convention.

**Q (discover: Self-host behavior after the fix): What should GET /api/game-profiles do on a self-host box with nobody signed in?**
A: Clean 401 (Recommended) — only add the missing imports; self-host answers 401 "Authentication required", console clean, cloud unchanged.

**Q (`requirements-billing.txt:4,10` + `Dockerfile:13,17,20`): The fix's import needs PyJWT + sqlalchemy, which exist only in requirements-billing.txt. Docker installs both unconditionally; the CLAUDE.md bare-pip flow (`pip install -r requirements.txt` + uvicorn) lacks both — on that surface the 8 routes trade NameError 500 for ModuleNotFoundError 500. Which install surface must this fix serve?**
A: Docker only — docker compose is the supported self-host path; the fix stays 8 one-line imports; the bare-pip caveat recorded in follow-ups.

**Q (`cloud/auth.py:113-119` → `cloud/database.py:37`): Post-fix on self-host, a request carrying `Bearer osk_…`/`X-API-Key` reaches `get_sessionmaker`, which raises RuntimeError (init_engine never ran) → 500 + traceback — a new path the shim never touched (though it 500s today via NameError anyway). Accept as a documented edge?**
A: Accept + record — exotic path (cloud account keys on a self-host box); behavior stays a 500, same class as today; no code change.

## Related Research
- `.rpiv/artifacts/research/2026-09-06_06-09-59_marsic-fork-parity-merge.md` — merge that introduced the endpoints

## Open Questions
None — the FRD carried no open questions, and both checkpoint ambiguities (install surface, key-header edge) were resolved during synthesis.
