---
date: 2026-08-30T14:42:31+0700
author: Yogiswara Utama
commit: 071c4c3
branch: main
repository: openshorts
topic: "OpenAI-compatible third-party LLM provider (4-phase implementation plan) — mid-plan handoff"
tags: [implementation, llm-provider, llm-client, gemini, openai-compat, byok, pipeline, thumbnail, saasshorts]
status: in_progress
last_updated: 2026-08-30T14:42:31+0700
last_updated_by: Yogiswara Utama
type: feature_development
---

# Handoff: OpenAI-compatible LLM provider — phases 1-2 done, 3-4 pending

## Task(s)

Executing `/skill:implement` on the plan
`.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md`
(no phase scoping — all 4 phases, sequentially). No phase was named in the skill
input, so every phase is in scope.

| Phase | Scope | Status |
|---|---|---|
| 1 | `llm_client.py` + `tests/test_llm_client.py` slice-1 contract tests | **COMPLETE** — all 11 checkboxes checked in plan (lines ~1069-1075) |
| 2 | Subprocess pipeline vertical: `main.py`, `layout_picker.py`, `app.py` hunks 1-5, slice-2 tests | **COMPLETE** — automated checks all checked; 3 live-run manual items left UNCHECKED with `_(...)` annotations in the plan (needs a real endpoint/YouTube job; code paths are test-pinned) |
| 3 | In-process endpoints vertical: `app.py` hunks 6-10, `thumbnail.py`, `saasshorts.py`, slice-3 tests | NOT STARTED |
| 4 | Observability + MCP + docs: `cloud/alerts.py`, slice-4 alert tests, `mcp_server.py`, `README.md`, `.env.example`, `skills/openshorts/reference.md`, `CLAUDE.md` | NOT STARTED |

Test state right now: `tests/test_llm_client.py` → **54 passed** (44 slice-1
items + 10 slice-2 pipeline tests). Pinned suites green unmodified
(test_gemini_retry, test_gemini_block_split, test_alert_classify,
test_clip_selection, test_layout_picker → 71 passed together in Phase 1;
first three → 31 passed after Phase 2).

## Critical References

- Plan (the contract — code fences are authoritative, already review-triaged):
  `.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md`
- Design parent (background only; plan supersedes where review findings applied):
  `.rpiv/artifacts/designs/2026-08-30_08-58-35_openai-compatible-llm-provider.md`
- New backend module: `llm_client.py` (chat() contract documented in its
  module docstring — read it before touching call sites)

## Recent changes

- `llm_client.py` — NEW, 1:1 from the plan's Phase 1 fence: `LlmConfig`
  (api_key `field(repr=False)`), `LlmError`/`LlmTransientError`,
  `config_from()` / `active_config()` (env chain `LLM_MODEL_<TASK>` →
  `LLM_MODEL`, never `GEMINI_MODEL*`), `_http_client` cached per base_url,
  3-rung JSON ladder (json_schema → json_object → bare with embedded
  contract), blocked-marker matching against structured error fields only,
  `_cost_from_usage` reusing `clip_selection.lookup_model_prices` with the
  (0.50, 3.00) estimated fallback.
- `tests/test_llm_client.py` — NEW: slice-1 (39 test functions) + slice-2
  pipeline section appended at `tests/test_llm_client.py:465` onward
  (`_main()` = per-test `pytest.importorskip("main")`).
- `main.py:25` — `import llm_client` after `import gemini_worker`.
- `main.py:1443` — `_run_gemini_stage(client, model_name, prompt, schema, llm=None)`;
  third-party branch returns `llm_client.chat(prompt, schema, config=llm)`
  inside the retry loop; new `except llm_client.LlmTransientError` clause
  (backoff, silent — recovered blips must NOT print "LLM provider", D9);
  `except llm_client.LlmError: raise`. Google transient token tuple untouched.
- `main.py:1505` — `_run_stage_split(..., label, llm=None)`; threads llm via
  `stage_kwargs = {"llm": llm} if llm is not None else {}` so the historical
  4-arg fake signature in pinned tests keeps working; bisect halves forward
  `llm=llm`; HEAD bisect print strings kept verbatim (with trailing comments).
- `main.py:1537-1551` — `get_viral_clips` head: `llm = llm_client.active_config()`,
  "Analyzing with the third-party endpoint" print branch, dual key gate,
  `client = genai.Client(api_key=api_key) if api_key else None`,
  `model_name = llm.model if llm is not None else (GEMINI_MODEL or default)`.
- `main.py:1607` + `main.py:1633` — both `_run_stage_split` call sites gained
  `llm=llm`.
- `main.py:1645-1651` — cost aggregate gains `price_estimated = any(...)` only
  on the llm path (Gemini aggregate shape unchanged).
- `main.py:1665-1674` — `get_viral_clips` except chain: new
  `except (llm_client.LlmError, llm_client.LlmTransientError)` printing
  "❌ Third-party LLM error: {e}" and re-raising, before the generic handler.
- `main.py:1681` — `get_visual_clips` no-key message reworded per plan.
- `layout_picker.py:169-232` — `pick()` rewritten: dual gate (GEMINI_API_KEY
  OR llm config, guarded lazy `import llm_client`), third-party branch calls
  `llm_client.chat(LAYOUT_CHOICE_PROMPT, LayoutChoice, images=frames, config=llm)`,
  failure handler prints `type(e).__name__` for llm_client errors (D9:
  reserved phrase must not leak into non-terminal log lines).
- `app.py:143-176` — hunk 1: `async def resolve_llm(request, task=None)` after
  `resolve_gemini` (billing → None; header triple `X-LLM-Base-Url`/`X-LLM-Key`/
  `X-LLM-Model` via `config_from`, else `active_config(task)`).
- `app.py:217-219` — hunk 5: `LLM_ENDPOINT_HINT` module constant after
  `gemini_missing_error`.
- `app.py:2097-2102` — hunk 2: `/api/process` dual gate, self-host 400 with
  `detail=LLM_ENDPOINT_HINT`, cloud keeps `gemini_missing_error()`.
- `app.py:2222-2233` — hunk 3: subprocess env injection (`LLM_BASE_URL`/
  `LLM_API_KEY`/`LLM_MODEL` from `llm_cfg`) + `elif BILLING_ENABLED:` prefix
  sweep `for _k in [k for k in env if k.startswith("LLM_")]: env.pop(_k, None)`.
- `app.py:911-914` — hunk 4: same BILLING sweep in `_resume_interrupted_jobs`
  env rebuild. `grep -c 'startswith("LLM_")' app.py` == 2 (plan gate, verified).

## Learnings

- **Bash heredocs corrupt long appends on this machine** (a 300-line
  `cat >> file << 'EOF'` was silently truncated mid-line — closing EOF never
  matched). Use the `write` tool for new files and `read`+`replace` for
  appends/repairs. Never heredoc plan-sized content.
- **CC Safety Net blocks any bash command containing the basename `.env`**
  (rule `secret.basename.env`). Do not retry or work around. The plan's
  Developer Context documents the same false positive. Verify "no LLM_* env"
  indirectly instead: `.venv/Scripts/python.exe -c "import llm_client; assert
  llm_client.active_config() is None"` — passing with no warning printed
  proves no base+key pair is loaded (a half-config would print the ⚠️ warning
  once; a full config would return non-None).
- `replace` tool: `remove_from`/`remove_to` must cover EXACTLY the intended
  range. Two incidents this session: (1) left a duplicate `#### Manual
  Verification:` header + stale unchecked items in the plan (fixed); (2) left
  a dangling `except Exception:` handler in `layout_picker.py` after replacing
  `pick()` (fixed — file re-parsed OK). Always check the post-edit diff tail
  before the next edit.
- `replace` on a file invalidates that file's anchors (`[E_STALE_ANCHOR]`) —
  re-`read` the region (or `grep` it) for fresh anchors.
- Pinned-suite constraint that shaped Phase 2: `tests/test_gemini_block_split.py`
  fakes `_run_gemini_stage` with exactly 4 positional params, and
  `tests/test_gemini_retry.py` calls it with 4 positional args — that is why
  `_run_stage_split` passes llm via `**stage_kwargs` only when set.
- httpx 0.28.1: `Client(base_url=...)` enforces a trailing slash (test asserts
  `"https://cache.test/v1/"`), and `_http_client` only rejects URLs at
  construction for control characters (`"http://exa\tmple.com"`); scheme-less
  strings are legal relative URLs, so `chat()` does its own scheme/host check.
- Windows/venv: run tests with `.venv/Scripts/python.exe -m pytest ...`.
  `import main` pulls cv2/torch/mediapipe/google-genai — all present here; the
  slice-2+ tests still use per-test `importorskip` per the plan.
- `GeminiBlockedError` subclasses `ValueError` (gemini_worker.py:362) — any
  new `except (…, ValueError)` tuple must re-raise it FIRST (Phase 3's
  thumbnail.py critic block depends on this ordering).
- Test-count note: plan says "40 contract tests"; pytest reports 44 items
  because the outage test is parametrized ×5. Not a discrepancy to fix.

## Artifacts

- `.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md`
  — the plan; Phase 1 fully checked; Phase 2 automated + redaction checked,
  3 manual items annotated-unchecked. Phases 3 (fences start ~line 1700) and
  4 (~line 2100) still carry their full code fences to transcribe.
- `llm_client.py` — complete (do not diverge from the plan's Phase 1 fence).
- `tests/test_llm_client.py` — slice-1 + slice-2 in place; slice-3 section
  (`_thumb()`, `_saas()`, `_no_genai()`, 5 tests incl.
  `test_chat_json_mode_requests_json_object_rung`) and slice-4 section
  (`_alerts()`, 4 tests) still to append, boundaries marked by section
  comments per the plan.
- `main.py`, `layout_picker.py`, `app.py` — Phase 2 edits (see Recent changes).

## Action Items & Next Steps

1. **Phase 3** (`app.py` hunks 6-10, `thumbnail.py`, `saasshorts.py`, slice-3
   tests) — transcribe from the plan's Phase 3 fences verbatim; review
   resolutions already baked in: `generate_youtube_description` and
   `generate_scripts` are PLAIN-TEXT calls (NO `json_mode`), critic block
   re-raises `GeminiBlockedError` before the ValueError tuple (thumbnail.py
   needs `from gemini_worker import GeminiBlockedError` at module top),
   `llm_config` threaded into `plan_thumbnail_concepts` ONLY,
   `/api/thumbnail/generate` gate stays Gemini-only. Append the slice-3 test
   section with the `write`/`replace` tools, NOT a bash heredoc.
2. Run Phase 3 gates: `python -m pytest tests/test_llm_client.py -q`
   (expect 54 + 5 slice-3 = ~59 items), pinned suites again, diff guards:
   `research_saas_online` and `_generate_one`/image flow untouched.
3. **Phase 4**: `cloud/alerts.py` — `"llm provider"` check as the FIRST branch
   of `_classify_failure` (before `_looks_like_proxy_error`) + reachability
   comment; append slice-4 alert tests; `mcp_server.py:53` `_FORWARD_HEADERS`
   gains the 3 `x-llm-*` names; README "Using an OpenAI-compatible endpoint"
   section (env table, capability matrix, 4 recipes, BYOK caveat,
   troubleshooting); `.env.example` — new LLM_* block with ALL lines
   commented out (review finding; the file's convention); note in
   `skills/openshorts/reference.md` (BYOK triple) and `CLAUDE.md`
   (LLM_* chain note — CLAUDE.md is partly Spanish, match its style).
4. Phase 4 gates: alert grep order, `grep -n "x-llm" mcp_server.py` → 3,
   `tests/test_alert_classify.py` green unmodified, full
   `tests/test_llm_client.py` green.
5. Update the plan's Phase 3/4 checkboxes as verified (same annotation style
   for live-run-only manual items), then print the implement skill's
   **completion** closing block. Suggested follow-up already encoded in the
   skill: `/skill:validate <plan-path>`.

## Other Notes

- Plan checkbox lines to fill next: Phase 3 Success Criteria block starts at
  plan line ~2032 (`#### Automated Verification:` at 2031); Phase 4's at ~2310.
- The 3 unchecked Phase 2 manual items are intentionally annotated, not
  silently skipped — surface them in the completion summary.
- `git status` baseline for this handoff: modified `app.py`, `layout_picker.py`,
  `main.py`, plan file; new `llm_client.py`, `tests/test_llm_client.py`.
  Nothing committed yet — the implement skill's closing block asks the user
  to review the diff first.
- Working directory is `H:/fork/openshorts` on Windows (Git Bash tooling);
  repo has a `.venv` with all heavy deps installed.
