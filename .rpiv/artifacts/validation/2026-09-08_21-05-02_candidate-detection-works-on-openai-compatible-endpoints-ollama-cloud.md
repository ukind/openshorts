---
template_version: 1
date: 2026-09-08T21:05:02+0700
author: Yogiswara Utama
commit: 51c1861
branch: main
repository: openshorts
topic: "Validation of candidate detection works on OpenAI-compatible endpoints (Ollama Cloud)"
status: ready
verdict: pass
parent: ".rpiv/artifacts/plans/2026-09-08_10-21-18_candidate-detection-openai-compat.md"
tags: [validation, plan, ai-provider, openai-compatible, ollama, silent-video, vision-probe, cheap-events, params]
last_updated: 2026-09-08T21:05:02+0700
---

## Validation Report: Candidate detection on OpenAI-compatible endpoints

Implementation lives in the uncommitted working tree against `51c1861` (main). The dirty set is exactly the plan's declared write-set: `ai_provider.py`, `main.py`, `log_view.py`, `README.md`, `CLAUDE.md`, `tests/test_no_double_route.py`, `tests/test_log_view.py`, modified; `tests/test_ai_provider.py` new. No phase touched `app.py`, `cloud/semantic_analyzer.py`, or `cheap_events.py` (ordering constraint holds).

### Implementation Status

- ✓ Phase 1: Provider param passthrough — Fully implemented
- ✓ Phase 2: Vision capability probe — Fully implemented
- ✓ Phase 3: Capability gates + visible warning — Fully implemented
- ✓ Phase 4: Silent-video path rewrite — Fully implemented
- ✓ Phase 5: Cheap events for short videos — Fully implemented
- ✓ Phase 6: Docs sync — Fully implemented

### Automated Verification Results

- ✓ Targeted suites (final state, `.venv/Scripts/python.exe -m pytest tests/test_ai_provider.py tests/test_no_double_route.py tests/test_log_view.py tests/test_llm_client.py -q`): 117 passed in 8.94 s — exactly the plan's final expectation (37 `test_ai_provider` = 27 Phase-1 + 10 Phase-2; 14 `test_no_double_route`; neighbors 37+6+60 = 103).
- ✓ Full-suite acceptance (`python -m pytest -q`): 935 passed, 10 failed — matches the plan's final-state claim (935 = 889 baseline + 46 new). The 10 failures are identical in name and count to the documented pre-existing baseline: `test_game_profiles` ×3, `test_generation_controls` ×3, `test_semantic_analyzer` ×2, `test_subtitles` ×2. Attribution (Step 2.6): all four failing files are byte-identical to HEAD (`git diff --quiet HEAD -- <files>` → clean), i.e. pre-existing at base, outside the run's delta — no NEW failures, criterion met.
- ✓ Phase 6 repo grep: `grep -rn "silent" README.md CLAUDE.md` — the three hits (README:130 multimodal candidate-detection copy, README:403 "truncates silently", CLAUDE.md:230 "silently renders GENERAL") pair nothing with Gemini-only; `grep -n "Gemini watches" README.md` — zero hits.

### Code Review Findings

#### Matches Plan:

- Phase 1 — `ai_provider.py:33-72` sanitize helpers (bounds temperature 0-2, max_tokens 1-8192, timeout 5-600 s, garbage/empty/NaN → unset) verbatim; `ai_provider.py:86-95` `AIProvider.__init__` stores sanitized params; `ai_provider.py:131-138` / `ai_provider.py:324-331` both constructor signatures pass through; `ai_provider.py:373-399` D1 precedence chain (per-call > stored > frozen; explicit-None kwarg = unset; `timeout` key only when a value exists); `ai_provider.py:638-649` factory forwards all three kwargs to both branches. `GeminiProvider.generate_content` untouched — D7 pinned by the passing source-inspection test.
- Phase 2 — `ai_provider.py:538-618` probe section placed after `OpenAICompatibleProvider`, before `create_ai_provider`: module-level `_VISION_PROBE_CACHE`, valid 8x8 PNG `_PROBE_IMAGE` (validity pinned by test), JSON-demanding `_PROBE_PROMPT`, transient-FIRST `_PROBE_TRANSIENT_TOKENS` classification, `does not support image` reject substring, tri-state `probe_vision_support` with per-identity cache and Gemini exemption; probe call carries `max_tokens=16`, `timeout=15`.
- Phase 3 — `main.py:2133-2148` deep gate probes a throwaway deep-identity provider before any extraction; `main.py:2153` extraction gated on `not _used_native_video and _deep_probe_ok`; `main.py:2359` fallback print guarded by `elif _deep_probe_ok:` (no factually-wrong "not enough frames" line on a gate trip); `main.py:2547-2553` vision gate with `scored` guard and the user-facing skip line; `log_view.py:30-35` unanchored capturing rule appended as the last `_RULES` entry (timestamp stripped on the cloud view); `tests/test_log_view.py:64-69` pins both timestamped skip lines.
- Phase 4 — `main.py:2928-2943` `_silent_visual_prompt` helper; `main.py:2946-3037` full `get_visual_clips` rewrite: no `GEMINI_API_KEY` wall on the openai branch, no raw `genai.Client` (dead `from google import genai` import dropped at line 21; `genai_types` import kept), openai branch constructs with `temperature=0.2, max_tokens=3000, timeout=90` (deep-scan shape), probes before frame extraction, sends 12 frames with `schema=gemini_worker.VisualResponse`; Gemini branch keeps the native upload through `prov.client` and now ATTACHES it (`contents=[file_upload, prompt]`, D4) plus `raise_if_blocked` (developer checkpoint); `main.py:3629` caller label `"OpenAI-compatible endpoint"` makes the FRD acceptance string literal; finally-cleanup deletes the upload.
- Phase 5 — `main.py:1956-1985` cheap-events seeding relocated to immediately before the ≤120 s gate in its own try/except; `main.py:2105-2110` old block deleted keeping `_deep_candidates = []` and the seeding `try:` opener (the corrected off-by-one was honored — the deep-scan body still runs inside the surviving try, so deep stays off for 61-120 s videos); `main.py:2020-2035` short-path payload splits `audio_events` (all in-window events) / `scene_boundaries` (scene_change only, ±0.5 s window, top 10) replacing the dead `'cheap_events' in locals()` guard.
- Phase 6 — all five stale spots fixed: README:148 mermaid node ("Provider watches the video"), README:406-412 "What still needs Gemini" bullet, README:573 `GEMINI_API_KEY` env row (silent videos removed), README:616 capability-matrix row ("yes — via the pipeline provider, with a vision model"), CLAUDE.md:149 "Stays Gemini" list (silent-video removed, two-line wrap preserved).

#### Deviations from Plan:

- `main.py:2668` — the "✅ Vision analysis finished" `elif` also gained `and _vision_probe_ok`, beyond the letter of the Phase 3 fence (which showed only the gate pair). This is a necessary completion: without it, a tripped gate would fall into the elif and print a misleading "finished" line (and reference the gated body's `_vision_done`). Improvement, no action required; test `test_no_vision_skips_deep_and_vision_with_zero_extractions` exercises exactly this path.

#### Pattern Conformance:

- ✓ `tests/test_ai_provider.py` mirrors the `tests/test_llm_client.py` fixture style (sys.modules fake module injection, seen-dict assertions, stubbed `__init__`) as D10 specified; decision-ID comments (D1/D4/D5/D6) in code match the repo's convention of citing plan decisions inline.
- Note: the codebase-memory graph is stale relative to the working tree (indexed 2026-09-08T01:46Z, all touched files report `metadata_changed` / `read_source_and_reindex`), so every claim above was verified against live source (`git diff HEAD` per file + grep), not graph relations.

#### Potential Issues:

- Pre-existing at base, plan-recorded, out of scope (no action in this run): `main.py:2458` big-try handler still prints "⚠️ Cheap event seeding skipped" while it now guards only deep-scan/merge failures (Phase 5 (e) follow-up candidate — confirmed present in the shipped tree); the `^`-anchored verbatim rules in `log_view.py` never match timestamped production lines (Phase 3 (d) pre-existing cloud bug — unchanged by this plan).
- Watch item (plan Phase 1 (c), operationalized in Phase 4's manual gate): every OpenAI-compatible request now carries `temperature`/`max_tokens` from the chain and a `timeout` key where none ever was — the first live run against a local server must confirm the server accepts the `timeout` field.

### Manual Testing Required:

FRD live-run gates (explicitly outside this plan — no live endpoints available on this machine):

1. Silent video + vision model (Ollama Cloud + one local server):
   - [ ] Job log shows "🎥 Silent video — analyzing with Openai vision (no transcript)…", one probe call per provider identity, 12 frames over the full duration, clips render, no `GEMINI_API_KEY` anywhere in the flow
   - [ ] Local server accepts the now-present `timeout` request field (Phase 1 wire-shape watch item)
2. Silent video + text-only model:
   - [ ] Exactly one probe call, zero screenshot extractions, "👁️ Vision analysis skipped: …" appears before the "Clip detection failed — OpenAI-compatible endpoint did not return usable clips" error; the same line is visible in the cloud dashboard curated view
3. Short video (≤120 s) with scene/audio toggles on:
   - [ ] Cheap events extracted before the whole-clip shortcut; detail payload carries `audio_events: "12.0s:scream, …"` and `scene_boundaries: "5.0s, …"` instead of "none"/"none"; deep stays off
4. Gemini job (any path):
   - [ ] Suite-verified unchanged, except the silent path now attaches the uploaded file (D4 — video tokens bill) and blocked videos show the 🚫 message

### Recommendations:

- Ready to commit — implementation is complete and validated; all six phases land in one coherent change set per the plan's fences.
- File the two plan-recorded follow-up candidates (rename `main.py:2458` handler to its deep/merge scope; unanchor or fix the `^`-anchored `log_view.py` verbatim rules) as a small follow-up plan or issue — both are pre-existing and out of this plan's scope.
- Re-index the codebase graph after commit so the graph matches the shipped tree.
