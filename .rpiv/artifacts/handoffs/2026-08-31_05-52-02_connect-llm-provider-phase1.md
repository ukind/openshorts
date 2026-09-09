---
date: 2026-08-31T05:52:02+0700
author: Yogiswara Utama
commit: 35e9d7e
branch: main
repository: openshorts
topic: "Connect LLM provider — Phase 1 backend implementation"
tags: [implementation, llm-provider, backend, fastapi, phase-1]
status: complete
last_updated: 2026-08-31T05:52:02+0700
last_updated_by: Yogiswara Utama
type: feature_development
---

# Handoff: Phase 1 (backend status channel + connection probe) of the LLM-provider-frontend plan

## Task(s)
Implementing Phase 1 of `.rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md` — "Backend — status channel + connection probe". **Status: complete.** All 8 "Automated Verification" checkboxes for Phase 1 are checked off in the plan file and pass locally. "Manual Verification" checkboxes (require a live `./dev.sh` server, optionally a real provider account) were **not run** this session and remain unchecked.

The skill invocation explicitly scoped this session to Phase 1 only ("Phase 1: Backend — status channel + connection probe"). Phases 2–5 of the same plan (dashboard/browser-side work) are **not started**.

## Critical References
- `.rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md` — the plan; only the Phase 1 section was read/executed this session. Note its "Ordering Constraints" section: Phase 1 alone is not a shippable state (Phases 2-5 all read the new `/api/config` fields), and the "What We're NOT Doing" section forbids rewording any `llm_client` error message (the `"LLM provider"` prefix is load-bearing for `cloud/alerts.py::_classify_failure`).
- `.rpiv/artifacts/designs/2026-08-30_16-58-07_connect-llm-provider-frontend.md` — parent design doc referenced by the plan; not read directly this session.

## Recent changes
- `llm_client.py:133` — added `_PROBE_TIMEOUT` (5s connect / 20s read) beside the existing `_TIMEOUT`.
- `llm_client.py:153-163` — added `_probe_client()`, a throwaway `httpx.Client` deliberately **not** using the shared `_clients` cache (probed URLs are user-supplied).
- `llm_client.py:165-183` — added `_require_usable(config)`, lifted verbatim out of `chat()`'s old inline guard.
- `llm_client.py:397` — `chat()`'s prologue now calls `_require_usable(config)` instead of the inline guard it used to have.
- `llm_client.py:481-497` — appended `probe(config)`, one minimal live call the new endpoint uses to validate a config without running a real job.
- `tests/test_llm_client.py` (+62 lines, after `test_http_client_rejects_invalid_url`) — 5 new `probe()` unit tests: success shape, 401→`LlmError`, 503→`LlmTransientError`, shared-cache-never-populated, malformed-URL/missing-model rejection.
- `app.py:1868-1894` — new `_env_llm_config()` helper: the server's own `LLM_*` config or `None`; loops `task="thumbnail"` then `"saas"` (mirrors `resolve_llm`'s per-task resolution so a task-only-configured server still reports `llmConfigured:true`).
- `app.py:1897-1911` — `get_config()` (`GET /api/config`) gained `llmConfigured` / `llmModel` / `llmBaseUrl` fields (never the key).
- `app.py:1913-1949` — new `POST /api/llm/test` (`llm_test`): self-host only (404 under `BILLING_ENABLED`), shares the existing probe rate limiter keyed by client host, resolves BYOK-header-or-env exactly like a real job (same task loop as `_env_llm_config`), runs `llm_client.probe()` in an executor, maps `LlmError` → 400 (caller's fault, no `"LLM provider"` prefix) or 502 (real upstream failure, prefix present), generic exceptions → 502.
- `app.py:1936-1940` — added a local `_redact(msg)` closure inside `llm_test()` that strips the resolved `cfg.api_key` from any error message before it becomes the `HTTPException` detail. **This was not in the plan's own code fence — see Learnings.**
- `tests/test_llm_endpoints.py` (NEW, 216 lines) — full HTTP-level contract for both endpoints: config fields, key-never-leaked on both the success and failure paths, BYOK-vs-env fallback, task-loop edge cases (thumbnail-only, saas-only, half-configured), 400-vs-502 discrimination, a provider-402-never-forwarded-as-402 case, and a subprocess-isolated `BILLING_ENABLED=1` test.
- `.rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md` — checked off all 8 Phase 1 "Automated Verification" boxes (`- [ ]` → `- [x]`). "Manual Verification" boxes left unchecked.

## Learnings
Two real mismatches surfaced between the plan's literal code fences and its own pinned tests. Both were raised via `ask_user_question` and resolved by the user choosing "Follow the plan" (the recommended option) for each:

1. **Key redaction gap.** `TestConnectionCheck::test_the_key_is_not_echoed_on_the_failure_path_either` (`tests/test_llm_endpoints.py`) mocks `llm_client.probe` to raise an `LlmError` whose message embeds the BYOK key — simulating a provider echoing the key back in its own error body via `_post`'s `_err_detail(resp)[:300]` — then asserts the key never reaches the 502 response body. The plan's shown `llm_test()` code did `detail=str(e)` verbatim, which cannot satisfy that assertion. **Fix:** added a local `_redact(msg)` closure in `llm_test()` (`app.py:1936`) doing `msg.replace(cfg.api_key, "***")`. This lives at the `app.py` endpoint boundary, not inside `llm_client.py`, so it does not reword any `llm_client` error message or touch the reserved `"LLM provider"` prefix.
2. **Billing test env gap.** `TestBillingDisablesTheWholeSurface`'s subprocess `SCRIPT` (`tests/test_llm_endpoints.py`) set `BILLING_ENABLED=1` + `LLM_*` env but omitted `DATABASE_URL`/`JWT_SECRET`. `cloud.setup_sync()` (called at `app.py` import time whenever `BILLING_ENABLED` is truthy) hard-fails via `cloud/config.py::validate_required()` without those two — a pre-existing, unrelated requirement, not introduced by this feature. **Fix:** added dummy `DATABASE_URL`/`JWT_SECRET` values to the `SCRIPT` string. Confirmed safe: the script's bare `TestClient(a.app, ...)` is never used as a context manager, so the ASGI lifespan (and the real `cloud.setup_async` DB engine) never runs — no request in the test touches a database.

Other notes:
- The 5 pre-existing failures seen in a full `pytest tests/ -q` run (`test_generation_controls.py::TestPromptTemplates` x3, `test_subtitles.py::TestFilterQuoting` x2) are a **pre-existing Windows-locale bug**: `open("main.py").read()` without an explicit encoding hits the default `cp1252` codepage, which can't decode a UTF-8 emoji byte in `main.py`. Verified via `git stash` that these fail identically on the untouched tree — **not a regression** from this change. Do not "fix" these as part of Phase 1 cleanup unless separately asked.
- Per `AGENTS.md`, `index_status` was checked before editing (`git.head_sha` matched current `HEAD`, graph fresh) and `check_index_coverage` showed no `parse_partial`/`skipped` flags on `llm_client.py`, `app.py`, or the touched test files.

## Artifacts
- `.rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md` — Phase 1 section, "Automated Verification" fully checked off; "Manual Verification" still open.

## Action Items & Next Steps
1. **Manual Verification** (Phase 1 plan, under "#### Manual Verification") — needs a live `./dev.sh` server and, optionally, a real or locally-stubbed OpenAI-compatible provider. Not run this session. Includes the rate-limiter check (16 requests/hour → 429) and the `BILLING_ENABLED=1` restart check.
2. Optionally run `/skill:validate .rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md` to formally re-verify Phase 1 against its success criteria — or proceed straight to Phase 2, since the plan's "Ordering Constraints" state Phases 1-5 merge as one unit and Phase 1 alone adds a capability channel/probe endpoint nothing yet consumes.
3. **Phase 2: Browser state — storage, migration, server-status wiring** — not started. It depends on Phase 1's three `/api/config` fields (`llmConfigured`, `llmModel`, `llmBaseUrl`), which now exist and are covered by tests.
4. **Nothing is committed to git yet.** `app.py`, `llm_client.py`, `tests/test_llm_client.py` are modified in the working tree; `tests/test_llm_endpoints.py` is untracked. Do not commit/push without the user's explicit go-ahead — consider `/skill:commit` once Phase 1 (or the full plan) is reviewed.

## Other Notes
- Full suite: `./.venv/Scripts/python.exe -m pytest tests/ -q` → 788 passed, 5 pre-existing unrelated failures (see Learnings), 0 new failures.
- `tests/test_llm_client.py` alone: 68 passed. `tests/test_llm_endpoints.py` alone: 17 passed.
- Repo convention: core backend is flat top-level modules (`app.py`, `llm_client.py`); the dashboard (Phases 2-5 of this plan) lives separately under `dashboard/`.
- Graph project slug for this repo (per `AGENTS.md`): `H-fork-openshorts`.
