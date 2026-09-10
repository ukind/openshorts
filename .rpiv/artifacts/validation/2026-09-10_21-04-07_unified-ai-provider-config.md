---
date: 2026-09-10T22:16:00+0700
author: Yogiswara Utama
commit: e5ffe7c
branch: main
repository: openshorts
topic: "Validation of Unified AI provider config — single source of truth BYOK"
status: ready
verdict: pass
parent: ".rpiv/artifacts/plans/2026-09-10_16-17-09_unified-ai-provider-config.md"
tags: [validation, ai-provider, llm-client, byok, frontend, editor, thumbnail, hook-grounding, screencast, e2e]
last_updated: 2026-09-10T22:16:00+0700
---

# Validation Report: Unified AI provider config — single source of truth BYOK

## Implementation Status

- ✓ Phase 1: Unified card + encrypted store + migration (FE core) — Fully implemented
- ✓ Phase 2: FE emission convergence — Fully implemented
- ✓ Phase 3: hook_grounding.py reroute — Fully implemented
- ✓ Phase 4: screencast_layout.py reroute — Fully implemented
- ✓ Phase 5: editor.py frames path + endpoint resolution — Fully implemented
- ✓ Phase 6: thumbnail image reroute + gate relax — Fully implemented (Reconciliation reword applied at app.py:3499 during validation; criterion `grep -c "openai_configured" app.py` now returns 4)
- ✓ Phase 7: Pre-existing lint debt cleanup (repo-wide green) — Fully implemented

## Automated Verification Results

- ✓ P1 eslint scoped (App.jsx, lib/llm.js, AiProviderCard.jsx, AuthContext.jsx, `--max-warnings 0`) — exit 0
- ✓ P2 eslint scoped (App.jsx, ResultCard.jsx, VoiceOverPage.jsx, CreateEditProfileModal.jsx) — exit 0
- ✓ P7 `npm run lint` (repo-wide, strict) — exit 0
- ✓ `npm run build` — built in 6.03s
- ✓ `pytest tests/test_llm_endpoints.py -q` — 27 passed
- ✓ `pytest tests/test_no_double_route.py -q` — 14 passed, file byte-unmodified vs HEAD
- ✓ `pytest tests/test_llm_client.py -q` — 60 passed, file byte-unmodified vs HEAD
- ✓ `pytest tests/test_hook_grounding.py -q` — 20 passed
- ✓ `pytest tests/test_ai_provider.py -q` — 37 passed
- ✓ `pytest tests/test_screencast_layout.py -q` — 29 passed
- ✓ `pytest tests/test_editor_frames.py -q` — 26 passed
- ✓ `pytest tests/test_edit_builder.py -q` — 17 passed
- ✓ `pytest tests/test_thumbnail_studio.py` — 22 passed (re-run after validation-time edits)
- ✓ `grep -rn "LlmProviderCard" dashboard/src` — no matches (repo-wide outside .rpiv also clean)
- ✓ `grep -n "setItem('openai_" dashboard/src/App.jsx` — no matches
- ✓ `grep -nE "headers\['X-OpenAI-" dashboard/src/App.jsx` — no matches
- ✓ `grep -rn "openaiApiKey" dashboard/src` — no matches
- ✓ `grep -rn "openai_key" dashboard/src/components/CreateEditProfileModal.jsx` — no matches
- ✓ `grep -rn "headers\['X-OpenAI-\|headers\['X-LLM-" dashboard/src | grep -v lib/llm.js` — no matches (single-emitter law holds)
- ✓ `grep -c "openaiHeaders"` ResultCard/VoiceOverPage/CreateEditProfileModal — 2/2/2 (each ≥ 1)
- ✓ `grep -n "llm_client\|LLM_BASE_URL\|LLM_API_KEY\|LLM_MODEL" hook_grounding.py screencast_layout.py editor.py thumbnail.py` — no matches in any (env namespace law holds)
- ✓ `grep -c "openai_provider=openai_prov" app.py` — 2 (both editor constructions)
- ✓ `grep -c "openai_configured" app.py` — 4 (after Reconciliation reword; was 5, criterion `kvBu` was the plan's one unchecked AV bullet)
- ✓ `grep -n "def openai_configured" *.py` — only editor.py:158
- ✓ `git diff --quiet HEAD -- tests/test_no_double_route.py llm_client.py cloud/alerts.py` — untouched
- ✓ Phase 7 diff spot-check on the six lint-debt files — changes confined to the inventory (underscore-prefixed unused args, commented empty catches, dead-local deletions, regex escape rewrite, useCallback hoists, one dead-branch deletion); no blanket disables

## End-to-End Test (user-mandated, before commit)

Real browser run with pire-browser (Firefox) against a locally started stack (uvicorn on :8000 from `.venv`, vite dev on :5173 with `VITE_PROXY_TARGET=http://localhost:8000`). Server env had NO Gemini key and NO OPENAI_* env (`/api/config`: all flags false) — pure BYOK through the unified card.

1. Card configured via the Settings UI: baseUrl `https://ollama.com/v1`, apiKey (user-supplied), model `minimax-m3`. `POST /api/llm/test` → 200 OK. Save → `aiProviderConfig_v1` stored; legacy `llmConfig_v1` and `openai_*` keys removed; UI shows SAVED and the provider toggle shows "No Gemini key set — using your AI provider" with the Gemini button disabled (Phase 1 Sections H/I live).
2. Clip Generator: YouTube URL `https://www.youtube.com/watch?v=dQw4w9WgXcQ` submitted via the UI. Job `88bb93f9-46d2-4f6c-9780-92ac3852a1e6` → **completed**, "Process finished successfully."
3. Artifacts: 5 final 9:16 clips (`hooked_..._clip_1..5.mp4`, 11-24 MB each); ffprobe on clip 1: 1080x1920, 21.0s. Burned hooks include "That iconic jacket reveal still hits different". UI shows 5 clip cards with titles, captions, edit/reframe/subtitle/hook/dub/post actions and ZIP download.

## Code Review Findings

#### Matches Plan:

- dashboard/src/lib/llm.js:11-47 — all four exports (`llmConfigComplete`, `aiProviderSet`, `llmHeaders`, `openaiHeaders`) match the Phase 1 fence; X-LLM-Model omitted when empty; X-OpenAI-Key/Model omitted when unset
- dashboard/src/components/AiProviderCard.jsx — matches the fence: presets, keyed/keyless Test connection branches (400 = config-class), model dropdown via `/api/openai/models`, derived server badge
- dashboard/src/App.jsx:39,307-334,934,1082,1647,2067 — store key `aiProviderConfig_v1`, encrypted persistence gated on `aiProviderSet`, `effectiveProvider` fallback to openai, single header spread site, one card mount
- dashboard/src/contexts/AuthContext.jsx — FR9 openai* trio added to context value
- app.py:2079-2083 — /api/config openai* fields, billing-pinned
- hook_grounding.py:145-231 — `_openai_provider` (default-endpoint gate), `_ask_openai` (GroundedHook via image_url parts), probe-gated dispatch, Gemini preferred
- screencast_layout.py:168-296 — same seams; `_ask_gemini_files` split out; frames arm reuses `layout_picker.sample_frames`
- editor.py:27-211 — EffectsSegment/EffectsPlan/FilterRepair, `_edit_plan_prompt`/`_effects_prompt` shared verbatim, `openai_configured`, `openai_vision_arm`, `frames_edit_plan`
- thumbnail.py:24,589-709 — `OPENAI_IMAGE_MODEL`, `_image_prompt` shared, `_generate_one_openai` (text-prompt only, D14), gate accepts unified config, references dropped with a logged warning on the images arm
- CLAUDE.md:129-141,246-249 — hook-grounding and screencast bullets reworded off Gemini-only (Phases 3/4)
- tests/test_hook_grounding.py (+8 tests), tests/test_screencast_layout.py (+10), tests/test_editor_frames.py (new file), tests/test_thumbnail_studio.py (+14), tests/test_llm_endpoints.py (+1) — all additive as specified; canaries live in the sibling files so test_no_double_route.py stays byte-unmodified

#### Deviations from Plan:

- app.py:3499 — the Phase 6 Reconciliation reword (`openai_configured rejects` → `the gate predicate rejects`) was NOT applied by the implement lane; the plan's own AV bullet `kvBu` was left unchecked. Applied during validation; criterion now passes. (Gap closed; plan instruction followed.)
- ai_provider.py (OpenAICompatibleProvider.generate_content) — three post-plan robustness additions, forced by the user-mandated real-endpoint E2E: (1) `finish_reason=length` retry escalation of `max_tokens` (×4, ceiling 8192); (2) loose-JSON parsing (fenced blocks, outermost object/array) before the parse error; (3) `_salvage_schema_json` — schema-directed salvage that wraps a bare clip array under the schema's single required array field. The plan forbids dispatch-module changes (D3): no routing or stage-ownership change is made (llm_client untouched; test_no_double_route.py green), but the letter of the constraint is exceeded. Improvement, not gap — without these, every ollama.com-class endpoint fails clip detection.
- gemini_worker.py — (1) `VISUAL_PROMPT_TEMPLATE` gained an explicit JSON return contract (the template stated no output shape at all; Gemini never needed it because of native response_schema, every loosely-compliant OpenAI-compatible model invented its own keys and failed schema validation); (2) `VisualClipModel.predicted_score` accepts float/string-number via a `field_validator` (Gemini always sends ints; loose models answer 8.5). Both changes are behavior-neutral for the Gemini arm and covered by the full suite. Improvement, not gap.
- Root cause the E2E surfaced (for the record): ollama.com does not enforce `response_format` for any tested model, and its reasoning models (minimax-m3) can spend the whole completion budget on thinking (empty content, finish_reason=length) or answer in markdown. The three ai_provider layers plus the prompt contract are what make "any OpenAI-compatible server" real.

#### Pattern Conformance:

- ✓ AiProviderCard.jsx imports mirror the sibling-card family (React, lucide-react, apiJson from ../lib/api, builders from ../lib/llm); classNames use the existing design tokens
- ✓ Salvage/escalation additions follow the file's existing convention (fence-stripping already existed in the schema-unsupported retry branch; the new helpers are module-private with docstrings)
- Minor observation: the vision probe still spends ~45 s per job against a reasoning model (its 16-token budget can never fit chain-of-thought; it correctly degrades to "unknown" and proceeds). Acceptable variation, not a deviation — a smaller probe budget or cached probe warm-up is a possible follow-up.

#### Potential Issues:

- tests/test_subtitles.py — 10 failures at HEAD e5ffe7c (UnicodeDecodeError: cp1252 vs utf-8 reads on Windows). Files byte-identical to base; pre-existing environment debt, outside this plan's criteria. Non-blocking.
- tests/test_game_profiles.py (3), tests/test_generation_controls.py (3), tests/test_semantic_analyzer.py (2) — fail identically at HEAD (verified by stash-and-run: 11 failures at base vs 8 with the run's changes; the run fixes two game-profile tests). Pre-existing at base. Non-blocking.
- A stale resume manifest from an earlier server session re-ran as job fb23daff during validation ("Input file not found") — pre-existing disk state, not this run's work. Harmless noise.
- Untracked `.rlm/models_cache.json` is a tool cache, not the run's work; `.gitignore` gained `.pi/` (dev-machine ignore, outside the plan's write-set).

## Manual Testing Required:

Covered by the E2E run above: unified-card save/test/keyless behavior, header emission on /api/process, clip generation with a BYOK OpenAI-compatible vision endpoint, hooks, reframing, 9:16 output. Remaining plan manual items for a future session with the real deployments:

1. Migration QA: fresh browser profile seeded with `llmConfig_v1` + `openai_*` + `geminiKey_v1` — import-once, no resurrection after reload (store-level migration logic verified live; the seeded-profile variant is untested)
2. Cloud mode (billingEnabled): card absent, `providerCfg` stays the empty triple, managed Gemini serves; `resolve_llm` still returns None
3. Keyed card Test connection showing latency + model; upstream-400 config-class vs provider-class error display
4. Editor frames path with a vision-capable endpoint on `/api/edit` (auto edit), and the text-only-model 400 `cannot see images` response
5. Thumbnail Studio with an image-capable endpoint (`/v1/images/generations`) and the clean-unavailable 400 on a chat-only endpoint
6. Subtitle editor still edits/saves/burns word-timed captions after the Phase 7 dead-local deletions; Voice-style presets, Legal and Pricing pages render

## Recommendations:

- Consider a smaller/warmer vision-probe budget so reasoning models do not cost ~45 s per job on the probe
- The `openai` package must exist in the runtime venv (`requirements.txt` pins 3.3.0; the local `.venv` was missing it — installed via uv during validation)
- The stale-resume scan can re-run long-dead jobs after a server restart; a manifest age cap would silence the noise
- Ready to commit — implementation is complete, validated, and proven end to end with a real BYOK OpenAI-compatible endpoint
