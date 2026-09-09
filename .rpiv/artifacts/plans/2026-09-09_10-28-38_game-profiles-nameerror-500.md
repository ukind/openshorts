---
date: 2026-09-09T10:28:38+0700
author: Yogiswara Utama
commit: fde9338
branch: main
repository: openshorts
topic: game-profiles NameError 500 — bind get_current_user_required
tags: [plan, app-py, cloud-auth, game-profiles, nameerror-fix]
status: ready
parent: .rpiv/artifacts/research/2026-09-09_08-58-32_game-profiles-nameerror-500.md
phase_count: 1
phases:
  - { n: 1, title: Bind get_current_user_required in the 8 game-profile cloud branches, files: [app.py], depends_on: [] }
unresolved_phase_count: 0
last_updated: 2026-09-09T11:22:21+0700
last_updated_note: "Step 9 triage — applied all 4 reviewer findings (curl body recipes, symmetric call-count AV bullet, billing-gate citation corrections)"
last_updated_by: Yogiswara Utama
---

# game-profiles NameError 500 Implementation Plan

## Overview

Eight game-profile endpoints in `app.py` call `get_current_user_required(request)` without the name ever being imported in that module, so every request reaching a cloud branch raises `NameError` and FastAPI returns 500 with a full ASGI traceback. The fix binds the name with one function-local `from cloud.auth import get_current_user_required` as the first statement of each endpoint's cloud branch — eight one-line insertions, copying the existing `restore_project` template (`app.py:3152-3157`) verbatim. Nothing else in the codebase changes.

## Requirements

- FR-1: Every one of the 8 game-profile cloud branches (`POST /api/game-profiles`, `GET /api/game-profiles`, `GET /api/game-profiles/export`, `POST /api/game-profiles/import`, `GET /api/game-profiles/{id}`, `PUT /api/game-profiles/{id}`, `DELETE /api/game-profiles/{id}`, `POST /api/game-profiles/{id}/duplicate`) must bind `get_current_user_required` before its bare call.
- FR-2 (AC-1): Self-host, unauthenticated `GET /api/game-profiles` answers `401 {"detail":"Authentication required"}` — one `INFO` access line, no `ERROR: Exception in ASGI application` traceback.
- FR-3 (AC-6): Cloud mode (`BILLING_ENABLED=1`) with a valid session JWT returns the designed 200 path — the fix restores, not alters, it.
- FR-4 (AC-3): `grep -c "from cloud.auth import get_current_user_required" app.py` returns 9 (8 new + 1 pre-existing in `restore_project`).
- FR-5: The function-local import convention is followed (never a module-level import) — `app.py:116-129` keeps the non-billing import path free of cloud imports.
- FR-6: The pre-existing test baseline (`tests/test_game_profiles.py`: 6 failed / 1 passed, AsyncMock misuse in the service layer) is unchanged — like-for-like.

## Current State Analysis

The billing gate at `app.py:116-129` binds only the optional variant: `if BILLING_ENABLED:` imports `get_current_user_optional` (`:119`); the `else:` shim defines a no-op optional variant (`:127-129`). `get_current_user_required` is never bound at module scope under any configuration — it exists only in the `cloud.auth` namespace (`cloud/auth.py:136-141`). The 8 bare calls compile to deferred `LOAD_GLOBAL` lookups that fail at first request, so the app boots clean and the defect is invisible until a cloud-branch request arrives. Any deployment with `LOCAL_GAMEPROFILES != "1"` reaches a call site — including billing mode, where the bug is also live today.

`cloud/auth.py`'s module-top chain is import-safe with `BILLING_ENABLED` unset: `cloud/config.py` reads env lazily via `@property`, the DB engine is created only inside `init_engine` (called under billing at `app.py:1740-1741`), and the `jwt`/`sqlalchemy` tops resolve because the Dockerfile installs `requirements-billing.txt` unconditionally (`Dockerfile:13,17,20`). The first self-host cloud-branch request executes those module tops once; every later request hits `sys.modules` cache.

### Key Discoveries

- Bug origin: game-profile endpoints landed with bare auth calls in `9f280ca` (2026-09-02, entered via fork-parity merge `0f326fd`); re-landed untouched by `d450d08`. No follow-up fix exists; live at HEAD `fde9338`.
- Template: `restore_project`'s cloud branch (`app.py:3152-3157`, origin commit `927ee09`, 2026-07-18) groups `from cloud.auth import get_current_user_required` with sibling function-local imports immediately before the call. Zero follow-ups in 7 weeks — copy it verbatim.
- Line-verified call sites at HEAD (research numbers confirmed by direct read): create `app.py:7153`, list `:7205`, export `:7235`, import `:7301`, get one `:7392`, update `:7456`, delete `:7501`, duplicate `:7546`. Seven are Shape A (inside `else:`; export omits the `# Cloud mode` comment), import is Shape B (inside `if not is_local:`, `app.py:7300`).
- Self-host 401 path: anonymous → `get_current_user_optional` reads `Authorization` (`cloud/auth.py:111-112`), `if not token: return None` (`:122-123`) **before** any DB session → required wrapper raises `HTTPException(401, "Authentication required")` (`:139`) → one INFO log line. Dashboard catches swallow it (`App.jsx:779`, `:2308`, `:2353`); `GameProfilesPage.jsx` shows its existing red banner.
- Accepted edge (no code change): self-host + `Bearer osk_…`/`X-API-Key` reaches `database.session()` → `get_sessionmaker` raises `RuntimeError` (`cloud/database.py:37`) → 500. It 500s today anyway via NameError; exotic path (cloud account keys on a self-host box).
- Tests: zero route coverage — no test exercises any cloud branch (`tests/test_game_profiles.py` is service-only; `tests/test_game_profile_update_http.py` pins `LOCAL_GAMEPROFILES=1`). The suite can neither detect the bug nor prove the fix; acceptance is curl + grep.
- `POST /api/game-profiles/analyze` (`app.py:8109-8154`) calls no auth helper — out of scope, no NameError class can occur there.

## Desired End State

Self-host (`docker compose up`, `LOCAL_GAMEPROFILES` unset, no sign-in):

```
$ curl -si http://localhost:8000/api/game-profiles | head -3
HTTP/1.1 401 Unauthorized
...
{"detail":"Authentication required"}
```

Server log: one INFO access line, no traceback. GameProfilesPage shows its existing red error banner; the dashboard console is clean (mount fetch catch at `App.jsx:779` swallows).

Cloud mode (`BILLING_ENABLED=1`, valid session JWT):

```
$ curl -H "Authorization: Bearer <jwt>" http://localhost:8000/api/game-profiles
[]
```

— the designed 200 path, restored.

Local mode (`LOCAL_GAMEPROFILES=1`): byte-identical behavior to today — the local branch never touches the new lines.

## What We're NOT Doing

- **No new tests.** Inherited decision (research Q1/Q2): imports only. No test exercises a cloud branch today and the composite lesson says acceptance is the curl 401 + grep; adding a route test is a follow-up, not this plan.
- **No requirements.txt changes.** Inherited decision (Q3): Docker-only install surface. The CLAUDE.md bare-pip flow lacks PyJWT + sqlalchemy, so on that surface the 8 routes would trade `NameError` 500 for `ModuleNotFoundError` 500 — recorded caveat, not fixed here.
- **No fix for the osk_-key-on-self-host edge.** Inherited decision (Q4): `Bearer osk_…`/`X-API-Key` on a self-host box still 500s (RuntimeError at `cloud/database.py:37`, init_engine never ran) — same class as today's NameError 500, accepted and documented.
- **No frontend changes** — nav gating (`App.jsx:1215`), silent catches, or the red banner stay as-is.
- **No touching the local branch, the billing gate (`app.py:116-129`), `_user_from_request`, or the analyze endpoint (`app.py:8109-8154`).**
- **No module-level import fallback** — a module-level `from cloud.auth import ...` would fight the billing gate and violate FR-5.

## Decisions

### Fix shape: 8 function-local imports, one per cloud branch (inherited)

Ambiguity (research Q1, pre-resolved by developer): confirmed diagnosis — add the missing function-local import at the 8 call sites. Template `app.py:3152-3157` (`restore_project` cloud branch): imports grouped immediately before the call, inside the cloud branch only. Pro: matches the only other `get_current_user_required` binding site in `app.py` (pickaxe: sole site, 7 weeks stable); keeps the non-billing import path free of cloud imports. Con: none identified.

### Placement: directly above the bare call, below the `# Cloud mode` comment (simple)

Research declared above/below the comment equivalent. Chosen: first statement of the cloud branch **after** the `# Cloud mode - use existing authentication` comment (where present), directly above `user = await get_current_user_required(request)` — this mirrors the template's imports-then-call grouping with the smallest visual diff. Export has no comment: the import becomes the literal first statement of its `else:`. Shape B: first statement inside `if not is_local:` (`app.py:7300`), above the bare call at `:7301`.

### Self-host behavior: clean 401, no traceback (inherited)

Research Q2: only add the missing imports; anonymous self-host requests answer 401 "Authentication required"; cloud unchanged; console clean. Verified mechanism: `cloud/auth.py:111-123` (anonymous → None pre-DB) and `:136-141` (401 raise).

### Install surface: Docker only (inherited)

Research Q3: `docker compose up` is the supported self-host path; `Dockerfile:13,17,20` installs both requirements files so `import jwt` / `sqlalchemy` resolve. Bare-pip caveat recorded in research follow-ups.

### Self-host osk_/X-API-Key edge: accepted + recorded (inherited)

Research Q4: post-fix, a key-header request on self-host reaches `get_sessionmaker` → `RuntimeError` (`cloud/database.py:37`) → 500 + traceback. Same observable class as today (NameError 500). No code change.

## Phase 1: Bind get_current_user_required in the 8 game-profile cloud branches

### Overview

One phase, one file: inserts `from cloud.auth import get_current_user_required` as the first statement of each of the 8 game-profile cloud branches in `app.py`. Depends on nothing (foundation and only phase; no parallelism — single slice).

### Changes Required:

#### 1. app.py

**File**: app.py
**Changes**: MODIFY — 8 one-line insertions, one per game-profile cloud branch. Line numbers below are HEAD `fde9338` positions; each insertion adds 1 line, so later hunks shift down by the count of earlier insertions (apply top-to-bottom; exact context text makes position unambiguous).

```python
# --- Hunk 1/8 — create_game_profile, POST /api/game-profiles (HEAD app.py:7151-7154) ---
    else:
        # Cloud mode - use existing authentication
        from cloud.auth import get_current_user_required
        user = await get_current_user_required(request)
        from cloud.game_profiles import create_game_profile

# --- Hunk 2/8 — list_game_profiles, GET /api/game-profiles (HEAD app.py:7203-7206) ---
    else:
        # Cloud mode - use existing authentication
        from cloud.auth import get_current_user_required
        user = await get_current_user_required(request)
        from cloud.game_profiles import list_game_profiles

# --- Hunk 3/8 — export_game_profiles, GET /api/game-profiles/export (HEAD app.py:7234-7236; note: no comment line) ---
    else:
        from cloud.auth import get_current_user_required
        user = await get_current_user_required(request)
        from cloud.game_profiles import list_game_profiles

# --- Hunk 4/8 — import_game_profiles, POST /api/game-profiles/import (HEAD app.py:7299-7302; Shape B) ---
    is_local = os.environ.get("LOCAL_GAMEPROFILES") == "1"
    if not is_local:
        from cloud.auth import get_current_user_required
        user = await get_current_user_required(request)
        user_id = str(user.id)

# --- Hunk 5/8 — get_game_profile, GET /api/game-profiles/{profile_id} (HEAD app.py:7390-7393) ---
    else:
        # Cloud mode - use existing authentication
        from cloud.auth import get_current_user_required
        user = await get_current_user_required(request)
        from cloud.game_profiles import get_game_profile

# --- Hunk 6/8 — update_game_profile, PUT /api/game-profiles/{profile_id} (HEAD app.py:7454-7457) ---
    else:
        # Cloud mode - use existing authentication
        from cloud.auth import get_current_user_required
        user = await get_current_user_required(request)
        from cloud.game_profiles import update_game_profile

# --- Hunk 7/8 — delete_game_profile, DELETE /api/game-profiles/{profile_id} (HEAD app.py:7499-7502) ---
    else:
        # Cloud mode - use existing authentication
        from cloud.auth import get_current_user_required
        user = await get_current_user_required(request)
        from cloud.game_profiles import delete_game_profile

# --- Hunk 8/8 — duplicate_game_profile, POST /api/game-profiles/{profile_id}/duplicate (HEAD app.py:7544-7547) ---
    else:
        # Cloud mode - use existing authentication
        from cloud.auth import get_current_user_required
        user = await get_current_user_required(request)
        from cloud.game_profiles import duplicate_game_profile
```

The `# --- Hunk n/8 ...` lines above are location markers for implement, not code to paste — the pasted change is exactly one line per hunk: `        from cloud.auth import get_current_user_required` (8-space indent, matching the existing cloud-branch body indentation), inserted at the position shown. The 8 contexts byte-match HEAD `fde9338` (slice-verifier repr-verified, 2026-09-09).

### Success Criteria:

#### Automated Verification:
- [x] Syntax-clean: `python -m py_compile app.py`
- [x] All 9 bindings present: `test $(grep -c "from cloud.auth import get_current_user_required" app.py) -eq 9` (8 new + template at app.py:3153)
- [x] Every call site binds the name (symmetric check): `test $(grep -c "get_current_user_required(request)" app.py) -eq 9` (8 endpoints + template `app.py:3157`)
- [x] Test baseline unchanged, isolated like-for-like run: `python -m pytest tests/test_game_profiles.py -q 2>&1 | tail -n 5 | grep -qE "(^|[^0-9])6 failed, 1 passed"` (same split as HEAD fde9338, live-verified 2026-09-09; do not co-run with tests/test_game_profile_update_http.py — its module-level LOCAL_GAMEPROFILES setting leaks into a shared process)

#### Manual Verification:
- [ ] Self-host unauthenticated `GET /api/game-profiles` → `401 {"detail":"Authentication required"}`; server log shows one INFO access line, no `ERROR: Exception in ASGI application` traceback (AC-1)
- [ ] Remaining 7 game-profile routes each answer 401 JSON when unauthenticated (one curl each: POST create, GET export, POST import, GET one, PUT update, DELETE, POST duplicate). Body-bearing routes need bodies to reach the auth branch — POST create and PUT update: `curl -si -X POST http://localhost:8000/api/game-profiles -H 'Content-Type: application/json' -d '{}'` (update: same with `-X PUT` and the profile-id path); POST import: `-d '{"profiles":[{"name":"x","game_title":"y"}]}'` — otherwise they answer 422/400 before the handler's auth branch (create `app.py:7112` and update `:7416` take a required `profile_data: dict`; import `:7268` parses the body at `:7278-7293` before `if not is_local:`)
- [ ] Local mode (`LOCAL_GAMEPROFILES=1`) behaves exactly as before the change (local branch untouched by the inserted lines)
- [ ] GameProfilesPage renders its existing red error banner with `Error: 401: {"detail":"Authentication required"}` (previously `Error: 500: Internal Server Error`); dashboard console otherwise clean
- [ ] Cloud-mode restore verified by inspection (AC-6): each inserted line imports from `cloud.auth`, which billing mode already loads at startup (`app.py:116-119`) — a cached module lookup; the gate itself binds only the optional variant (`app.py:119`), so the inserted import is what binds the required wrapper under billing
## Ordering Constraints

Single phase — no ordering between phases. Within the phase: apply the 8 hunks in any order (context anchors are unique); the grep success criterion runs after all 8.

## Verification Notes

- AC-1 (curl 401): unauthenticated `GET /api/game-profiles` on self-host returns 401 `{"detail":"Authentication required"}`, one INFO log line, no `ERROR: Exception in ASGI application`. Verify the other 7 routes the same way (one curl each — the merge lesson: one unauthenticated curl per endpoint would have caught this on 2026-09-02).
- AC-3 (grep): `grep -c "from cloud.auth import get_current_user_required" app.py` returns 9 (8 new + template `app.py:3153`).
- AC-6 (cloud restore): code-inspection only — under billing, `cloud.auth` is already in `sys.modules` at startup (`app.py:116-119`), so each inserted line is one cached module lookup; the gate binds only the optional variant (`app.py:119`), so the inserted import itself is what binds the required wrapper under billing. No live cloud-DB test required.
- Baseline like-for-like: `python -m pytest tests/test_game_profiles.py -q` stays 6 failed / 1 passed (run in isolation — `tests/test_game_profile_update_http.py:32` leaks `LOCAL_GAMEPROFILES` into a shared process and changes the split). The suite imports no `app` and cannot regress from this change; the check pins "unchanged".
- Boot check: `python -m py_compile app.py` passes (import-time safety of the file itself). Full `import app` is deliberately NOT an acceptance line — it drags in the whole pipeline stack and may need model/env setup beyond this fix's scope.
- Frontend: mount fetch (`App.jsx:771`) + catches (`:779`, `:2308`, `:2353`, `GameProfilesPage.jsx` catch sites) — 401 degrades silently or to the existing red banner; no new UI state expected.

## Performance Considerations

Under billing, `cloud.auth` is loaded at startup (`app.py:116-119`); each inserted import line is one `sys.modules` cache hit per request — no file read, no re-execution. Self-host first cloud-branch request executes the `cloud.auth` module tops once (proven import-safe: lazy Settings properties, deferred engine init, guarded file loads — research Detailed Findings §Import-chain safety); all later requests hit the module cache. No measurable delta on either path.

## Migration Notes

Not applicable — no schema, no persisted data, no config migration. Rollback = revert the one file (`git revert` of a single-file change removes all 8 lines).

## Pattern References

- `app.py:3152-3157` — the template: `restore_project` cloud branch, `from cloud.auth import get_current_user_required` grouped with sibling function-local imports immediately before the call. Origin commit `927ee09` (2026-07-18), zero follow-ups in 7 weeks, sole such site in `app.py` at HEAD. Copy verbatim.
- `app.py:7154`, `:7206`, `:7236`, `:7350`, `:7393`, `:7457`, `:7502`, `:7547` — sibling function-local `from cloud.game_profiles import ...` lines already following the convention; the new lines sit beside them, above the auth call.

## Developer Context

- Research Developer Context Q/As (inherited, fixed): Q1 fix shape confirmed (8 function-local imports, existing convention); Q2 self-host = clean 401 only; Q3 Docker-only install surface, bare-pip caveat recorded; Q4 self-host osk_-key edge accepted + recorded, no code change.
- Design checkpoint (this session): design summary + 1-phase decomposition presented together (trivial decomposition; single-question gate per no-back-to-back rule). Developer selected "Proceed: 1 phase".
- Agent-dispatch notes: `integration-scanner` and `precedent-locator` not dispatched — research's Integration Points and Precedents & Lessons sections were already in context (skill's skip rule). `codebase-pattern-finder` not dispatched — the pattern template was pinned by research to `app.py:3152-3157` with origin commit and verified by direct HEAD read in the parent (zero information gain from re-dispatch). No external surfaces → no web research.
- Coverage gate: `check_index_coverage` clean (no recorded parse issues) on `app.py`, `cloud/auth.py`, `cloud/game_profiles.py`, `cloud/local_game_profiles.py`, `cloud/database.py`, `cloud/config.py`, both test files; index one generation stale vs HEAD, so all line numbers were taken from direct HEAD reads, not the graph.
- Step 8 reviewer notes: code review returned 1 row (suggestion: billing-gate citation drift); coverage review returned 3 rows (concerns: curl body recipes ×2, symmetric call-count check); zero agent failures. All claims re-grounded by orchestrator grep before triage. All 4 rows triaged `applied` at Step 9 (2026-09-09).

## Plan Review (Step 8)

_Independent post-finalization review by artifact-code-reviewer and artifact-coverage-reviewer subagents. Findings triaged at Step 9. All live-source claims in the rows below were re-grounded by the orchestrator (grep, 2026-09-09): gate `app.py:116`, optional import `:119`, shim `:127-129`, call-site count 9, import-line count 1, body-bearing routes `:7112`/`:7268`/`:7416`._

| source   | plan-loc          | codebase-loc                | severity   | dimension             | finding   | recommendation   | resolution         |
| -------- | ----------------- | --------------------------- | ---------- | --------------------- | --------- | ---------------- | ------------------ |
| coverage | ## Verification Notes §1 + Composite Lessons §4 | <n/a> | concern | verification-coverage | Manual bullet 2 ("one curl each" over the other 7 routes) specifies no request bodies. POST create (`app.py:7112`, required `profile_data: dict`) and PUT update (`:7416`) answer 422 before the handler runs; POST import (`:7268`) answers 400 (body parsed at `:7278-7293` before the auth branch). A bodyless curl cannot reach the cloud branch on 3 of 7 routes, so the 401 mechanism never fires there. | Amend Manual bullet 2 with per-route curl recipes: create and update need `-H 'Content-Type: application/json' -d '{}'`; import needs a valid payload, `-d '{"profiles":[{"name":"x","game_title":"y"}]}'`. | applied: Manual bullet 2 now carries the per-route curl recipes (create/update `-d '{}'`, import valid profiles payload) |
| coverage | ## Precedents & Lessons §2 | <n/a> | concern | verification-coverage | Lesson "post-merge, grep merged regions for names used but never imported" lands nowhere: the only grep bullet counts import lines == 9 and passes even if a call site is missed; no bullet scans for undefined names; no recorded deferral. | Add an Automated Verification bullet asserting call-site count equals import count: `test $(grep -c "get_current_user_required(request)" app.py) -eq 9` (symmetric with the import-count check; live value 9 = 8 endpoints + template). | applied: symmetric AV bullet added (call-site count `-eq 9`) |
| coverage | ## Precedents & Lessons §4 | <n/a> | concern | verification-coverage | Lesson "one unauthenticated curl per new endpoint after a merge" lands only partially — same underspecification as row 1: the 3 body-bearing routes answer 422/400 instead of 401 and the lesson's detection mechanism never fires on them. | Amend Manual bullet 2 with the per-route body arguments (same fix as row 1); apply the complete recipe to future post-merge curl sweeps. | applied: same fix as row 1 — Manual bullet 2 amended with body recipes |
| code     | Phase 1 + Current State Analysis + AC-6 + Performance | app.py:116-129 | suggestion | codebase-fit | Billing-gate citations drift: plan says `app.py:118-129` (Current State Analysis, What We're NOT Doing) and `118-123` / "line ~123" (Current State Analysis, Success Criteria AC-6, Verification Notes, Performance Considerations); live source has `if BILLING_ENABLED:` at 116, the optional import at 119, shim at 127-129. FR-5 already cites the correct `116-129`. | Correct every billing-gate citation to `116-129` and the optional-import line to `119`. | applied: citations corrected to `116-129` (gate), `116-119` (startup load), `:119` (import line), `:127-129` (shim) across Current State Analysis, What We're NOT Doing, AC-6 criteria/notes, Performance |

## Plan History

- Phase 1: Bind get_current_user_required in the 8 game-profile cloud branches — approved as generated (slice-verifier OK/OK with 2 warnings, both applied pre-approval: AC-6 wording corrected, pytest baseline criterion made exit-0)

## References

- Research: `.rpiv/artifacts/research/2026-09-09_08-58-32_game-profiles-nameerror-500.md` (chained from `.rpiv/artifacts/discover/2026-09-09_08-40-30_game-profiles-nameerror-500.md`)
- Template origin: commit `927ee09` — "feat(projects): reopen archived projects from history"
- Bug origin: commit `9f280ca` — "feat: game profiles, multimodal candidate detection and clip quality pipeline" (via merge `0f326fd`); re-landed by `d450d08` — "Merge marsic/main: fork parity"
- Historical: `.rpiv/artifacts/plans/2026-09-06_14-41-23_marsic-fork-parity-merge.md`, `.rpiv/artifacts/validation/2026-09-07_21-23-26_marsic-fork-parity-merge.md`
