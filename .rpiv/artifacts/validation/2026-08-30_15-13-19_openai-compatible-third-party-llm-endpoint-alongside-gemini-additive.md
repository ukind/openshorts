---
template_version: 1
date: 2026-08-30T15:13:19+0700
author: Yogiswara Utama
commit: 071c4c3
branch: main
repository: openshorts
topic: "Validation of OpenAI-compatible third-party LLM endpoint alongside Gemini (additive)"
status: ready
verdict: pass
parent: ".rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md"
tags: [validation, llm-provider, llm-client, gemini, openai-compat, ollama, minimax, openrouter]
last_updated: 2026-08-30T15:13:19+0700
---

## Validation Report: OpenAI-compatible third-party LLM endpoint alongside Gemini (additive)

### Implementation Status

- ✓ Phase 1: `llm_client.py` — the OpenAI-compat backend + unit tests — Fully implemented
- ✓ Phase 2: Subprocess pipeline vertical — `/api/process` path — Fully implemented
- ✓ Phase 3: In-process endpoints vertical — thumbnail text + SaaS text — Fully implemented
- ✓ Phase 4: Observability + MCP + docs — Fully implemented

Working tree is uncommitted (matches `git status` at session start): `llm_client.py` and `tests/test_llm_client.py` are new/untracked; `app.py`, `main.py`, `layout_picker.py`, `thumbnail.py`, `saasshorts.py`, `cloud/alerts.py`, `mcp_server.py`, `README.md`, `.env.example`, `CLAUDE.md`, `skills/openshorts/reference.md` are modified. All verification below runs against this working tree via `git diff HEAD`.

### Automated Verification Results

- ✓ Contract + pipeline + endpoint + alert-class tests: `.venv/Scripts/python.exe -m pytest tests/test_llm_client.py -q` — **63 passed** (matches Phase 4's claimed 63 items: 40 contract + 11 pipeline + 5 slice-3 endpoint + 4 slice-4 alert-class + rounding note in plan text — actual collected count is 63, consistent with the plan's own annotation)
- ✓ Pinned suites unmodified, no regressions: `.venv/Scripts/python.exe -m pytest tests/test_gemini_retry.py tests/test_gemini_block_split.py tests/test_alert_classify.py tests/test_clip_selection.py tests/test_layout_picker.py -q` — **71 passed**
- ✓ Module inert with no `LLM_*` env: `python -c "import llm_client; assert llm_client.active_config() is None"` — OK
- ✓ Classifier substring present: `grep -n "blocked this video" llm_client.py` — found in `_blocked()` message
- ✓ `RESOURCE_EXHAUSTED` token tuple byte-identical: `git diff HEAD -- main.py | grep '^[+-].*RESOURCE_EXHAUSTED'` — no output (unchanged)
- ✓ Dead redaction block untouched: `git diff HEAD -- app.py | grep '_SENSITIVE_LOG_RE'` — no output (unchanged)
- ✓ BILLING `LLM_*` sweep present exactly twice (spawn + resume): `grep -c 'startswith("LLM_")' app.py` → 2
- ✓ Self-host 400 hint wired on all 5 dual-gated endpoints: `grep -c 'detail=LLM_ENDPOINT_HINT' app.py` → 5
- ✓ `"llm provider"` classified before `_looks_like_proxy_error`: `grep -n '"llm provider"|_looks_like_proxy_error' cloud/alerts.py` — `llm provider` check at alerts.py:62-63, `_looks_like_proxy_error` def at :40, call at :64 (llm-provider branch is first inside `_classify_failure`)
- ✓ MCP forwards the BYOK header triple: `grep -n -i 'x-llm' mcp_server.py` → `x-llm-base-url`, `x-llm-key`, `x-llm-model` in `_FORWARD_HEADERS`
- ✓ All 8 changed/new Python modules compile: `python -m py_compile app.py main.py layout_picker.py thumbnail.py saasshorts.py cloud/alerts.py mcp_server.py llm_client.py` — no errors
- ✓ No regressions detected

### Code Review Findings

#### Matches Plan:

- `llm_client.py` (new, 302 lines per diff) — `LlmConfig`/`LlmError`/`LlmTransientError`, `config_from`/`active_config`, the 3-rung JSON ladder, blocked/transient/error mapping, and the cost builder all present and exercised by the 63 passing tests; contract matches the plan's fenced code line-for-line on every spot-checked function signature.
- `main.py:24-1653` — `_run_gemini_stage(..., llm=None)` branches to `llm_client.chat()` before the Gemini call, with `LlmTransientError` (backoff, silent recovery print) and `LlmError` (re-raise) except clauses added ahead of the generic `except Exception`; `_run_stage_split` threads `llm` only when set, preserving the historical 4-arg shape; `get_viral_clips` gate widened to "Gemini key OR llm config", both `_run_stage_split` calls carry `llm=llm`, and the except chain propagates `LlmError`/`LlmTransientError` with "Third-party LLM error" ahead of the generic Gemini catch-all.
- `layout_picker.py:129-183` — `pick()` gate widened, guarded lazy `import llm_client`, third-party branch sends the same 12 frames through `llm_client.chat()` with `LayoutChoice`, failure reasoning avoids leaking "LLM provider" into the log tail.
- `app.py` — `resolve_llm()` added next to `resolve_gemini` (billing-pinned to `None`); all 5 dual gates (`/api/process`, `/api/thumbnail/analyze`, `/api/thumbnail/titles`, `/api/thumbnail/describe`, `/api/saasshorts/analyze`) raise the self-host `LLM_ENDPOINT_HINT` 400 or cloud's `gemini_missing_error()`; `/api/thumbnail/generate`'s gate is untouched, only `llm_cfg` resolution/threading added; subprocess env injection and the resume env rebuild both carry the `LLM_*` prefix sweep for BILLING.
- `thumbnail.py` — `analyze_video_for_titles`, `refine_titles`, `plan_thumbnail_concepts` gain `llm_config=None` and branch to `llm_client.chat(..., json_mode=True)`; `generate_youtube_description` branches to a plain-text `llm_client.chat(prompt, config=llm_config)` call (no `json_mode`), matching the plan's text-vs-JSON split; the `genai.Client` is only constructed when `llm_config is None`; `generate_thumbnail` threads `llm_config` into concepts only, image generation stays Gemini-pinned.
- `saasshorts.py` — `analyze_saas` and `generate_scripts` gain `llm_config=None` and branch to `llm_client.chat(...)` (`generate_scripts` carries `max_tokens=8192`); `research_saas_online` has zero diff (confirmed via `git diff -- saasshorts.py | grep research_saas_online` — no output).
- `cloud/alerts.py:45-66` — `"llm provider" in e` is the first branch of `_classify_failure`, ahead of `_looks_like_proxy_error`, matching D9's precedence requirement.
- `mcp_server.py:52-56` — `_FORWARD_HEADERS` gained `x-llm-base-url`, `x-llm-key`, `x-llm-model`.
- `.env.example`, `README.md`, `CLAUDE.md`, `skills/openshorts/reference.md` — all four docs updated with the env vars, capability matrix/recipes, and BYOK caveat described in Phase 4.
- "What We're NOT Doing" boundaries respected: `git diff --stat HEAD -- editor.py screencast_layout.py dashboard/` returns empty — none of these are touched.

#### Deviations from Plan:

None. Implementation is a faithful realization of the plan across all four phases.

#### Potential Issues:

- `cloud/alerts.py:45-51` carries an explicit code comment (citing "review C8, advisor-confirmed") noting that in production, `_classify_failure`'s callers are cloud-only and the BILLING env sweep strips `LLM_*`, so the new `"llm provider"` branch is currently unreachable from a real managed job — it ships as defense-in-depth per design D9 and is exercised directly by tests. This is a known, already-reviewed and accepted condition, not a new finding, but is noted here for visibility since it means the branch's real-world verification will only happen once self-host alerting consumes this path.

### Manual Testing Required:

1. Phase 2 — subprocess `/api/process` live routing:
   - [ ] Self-host with `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` set (no `GEMINI_API_KEY`): a YouTube job produces clips via the endpoint; logs show "Analyzing with the third-party endpoint"
   - [ ] Same instance with env unset and no `.env` `LLM_*` rows: pipeline logs identical to pre-change
   - [ ] Header-only job (`X-LLM-*` headers, no env) fails after a mid-job redeploy with the documented missing-key message

2. Phase 3 — in-process endpoints live routing:
   - [ ] Self-host with `LLM_*` env: `/api/thumbnail/analyze` returns titles via the endpoint; `/api/thumbnail/generate` still needs the Gemini key for images
   - [ ] `/api/saasshorts/analyze` with a URL and llm-only config completes with the "skipping grounded web research" log line
   - [ ] Cloud/billing mode: all endpoints behave exactly as before (`resolve_llm` returns `None`)

3. Phase 4 — observability/MCP live routing:
   - [ ] MCP caller with the `X-LLM-*` header triple reaches the third-party endpoint end-to-end
   - [ ] MiniMax M3 recipe sanity-checked against `POST /v1/chat/completions` (optional per design)

All of the above require a live third-party endpoint (Ollama Cloud, MiniMax, OpenRouter, etc.) and are explicitly marked `[ ]` (unverified) in the plan itself — the gate logic, branch selection, and log lines they depend on are all pinned by the passing automated test suite.

### Recommendations:

- Ready to commit — implementation is complete and validated. All automated verification from all 4 phases passes (63 + 71 tests, all grep/diff structural checks, clean compile of every changed module), and the diff matches the plan's fenced code on every spot-checked file.
- Before merging, run the Manual Testing checklist above against at least one live OpenAI-compatible endpoint (e.g. Ollama Cloud) to close out the plan's remaining `[ ]` items — this is runtime behavior no automated check can substitute for.
