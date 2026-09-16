---
date: 2026-09-15T06:23:22+0700
author: Yogiswara Utama
commit: 73f670a
branch: main
repository: openshorts
topic: "AI Shorts local model option — integration architecture"
confidence: high
complexity: medium
status: ready
verdict: pass
tags: [solutions, saasshorts, comfyui, tts, video-mode, adapters, local-models]
last_updated: 2026-09-15T06:23:22+0700
last_updated_by: Yogiswara Utama
---

# Solution Analysis: AI Shorts local model option — integration architecture

**Date**: 2026-09-15T06:23:22+0700
**Author**: Yogiswara Utama
**Commit**: 73f670a
**Branch**: main
**Repository**: openshorts

## Research Question
Chained from FRD `.rpiv/artifacts/discover/2026-09-14_21-17-54_ai-shorts-local-models.md` and research `.rpiv/artifacts/research/2026-09-14_21-41-44_ai-shorts-local-models.md`: **how does the AI Shorts pipeline gain a local `video_mode` arm** — ComfyUI image/video generation plus a local TTS server, strict offline operation, batch runs with no added time limit, and per-stage model selection against an RTX 3080 with 10 GB VRAM?

The *which models* question was settled during research (Wan2.2 TI2V-5B Q8_0 + fastwan LoRA for i2v, LatentSync 1.5 pinned for lipsync, OmniVoice via `omnivoice-server` for TTS, Flux dev Q4 portraits / schnell Q4 b-roll stills). The open design space this analysis explored is **how the local arm integrates into the pipeline**.

## Summary
**Problem**: Five paid cloud call sites (fal, ElevenLabs, VEED) must gain local twins that reproduce bytes at fixed on-disk paths, with key-gate bypass, keyless job start, and $0 cost display — without breaking the retry/cache machinery or the composite's narration-audio contract.

**Recommended**: Per-stage adapter modules — sibling local adapter functions beside the cloud helpers, dispatched by a third mode arm, mirroring `_fal_run`'s client shape. This is the repo's own proven move (lowcost mode, `86117d3`) applied a third time, with the ComfyUI/TTS clients isolated in a new module the way `30ce67e` isolated `llm_client` and `719d444` isolated `transcribe_backends`.

**Effort**: Medium (~6–8 days: ~300–390 new adapter lines, ~60–90 backend diff lines, ~40–60 dashboard lines, ~300–500 test lines, plus GPU-host smoke runs).

**Confidence**: High (all six dimensions scored with verified file:line evidence; the pattern is triple-precedented in this repo's history).

## Problem Statement

**Requirements:**
- A third `video_mode` value `local` alongside `premium` and `lowcost`, validated at the request boundary.
- Local replacements for five paid stages: actor stills (Flux dev Q4), voiceover (OmniVoice/TTS server), talking head (Wan2.2 i2v + LatentSync lipsync + narration mux), b-roll stills (Flux schnell Q4 + Ken Burns), voice listing.
- Key-gate bypass in local mode only (cloud modes keep the 400): `app.py:7952-7955`, `app.py:6669-6670`.
- Strict offline operation: no cloud fallback, errors name the service and URL (FR6).
- Batch semantics with no added time limit (FR8); per-stage model selection; $0 cost display (FR7).
- Byte-compatible artifacts at the existing fixed paths so retry and cache guards keep working.

**Constraints:**
- Hard: RTX 3080, 10 GB VRAM — stages run serially or through ComfyUI's own queue; no parallel local GPU submissions.
- Hard: the final composite pulls ALL narration audio from `[0:a]` of the talking-head input (`saasshorts.py:1273-1291`); Wan i2v output is silent, so the local head adapter must mux TTS audio in before returning.
- Hard: BYOK keys stay client-side headers for cloud modes (`.env.example:109`); local mode adds no new secret handling.
- Soft: env-derived config survives a redeploy resume (`app.py:189-191`); batch local runs favor `COMFYUI_URL` / `TTS_BASE_URL` as env constants.
- Soft: dashboard gains a third mode card (`sm:grid-cols-3`), local cost row, and non-misleading key warnings (FR7).

**Success criteria:**
- Offline end-to-end run: script → actor → voice → head → b-roll → subtitles → composite, zero cloud egress.
- No-key job start in local mode (202, not 400); invalid mode rejected at the boundary (422/400, not silent premium).
- Retry path still resumes from cached local intermediates; docs and `.env.example` updated.

## Current State

**Existing implementation:**
- `video_mode` travels: wizard body (`SaaShortsTab.jsx:305`) → `SaaSGenerateRequest.video_mode` (`app.py:7934`, plain `str`, **unvalidated**) → `config` (`app.py:8030`) → read at `saasshorts.py:1407` → branch at `saasshorts.py:1409-1414`. Unknown values fall to the premium arm.
- Five paid helpers share one signature shape: `(content…, key, output_path) → output_path` — `generate_actor_image` (`saasshorts.py:782-784`), `generate_voiceover` (`:795-800`), `generate_talking_head` (`:862-867`), `generate_talking_head_lowcost` (`:903-908`), `generate_broll` (`:986-988`).
- Cloud funnel: `_fal_run` submit/poll (`saasshorts.py:591-662`), `_fal_upload_file` CDN (`:665-708`), direct ElevenLabs POST (`:804-828`).
- Keys read by direct dict access in the orchestrator: `config["fal_key"]` / `config["elevenlabs_key"]` (`saasshorts.py:1350-1351`).

**Relevant patterns:**
- Sibling-function mode arm: `generate_talking_head_lowcost` + call-site dispatch — `saasshorts.py:903-984`, `:1409-1414` (landed in `86117d3`, survived 2 years unchanged).
- Env-selected local backend module: `transcribe_backends.py:364-380` (`TRANSCRIBE_BACKEND`, cloud fallback) — landed in `719d444` with 16 offline tests.
- Provider dispatch inside a loop: `voiceover.py:880-913` (`qwen` local vs `elevenlabs`), `QwenLocalTTS` gated loudly at `app.py:6923-6926`.
- Additive backend module: `llm_client.py` (`30ce67e`, +454 one file) with a fix chain that concentrated at gates and call sites — `25222f5` restored a gate half after a merge dropped it.
- MockTransport offline testing: `tests/test_llm_client.py:55-63`; fake-module injection: `tests/test_ai_provider.py`.

**Integration points:**
- `app.py:7952-7955` — generate key gate (mode-aware conditional needed)
- `app.py:6669-6670` — actor-options key gate (same)
- `app.py:8120-8143` — voices route (local branch queries TTS server `GET /v1/voices`; already keyless-tolerant)
- `saasshorts.py:1409-1414` — Step-3 dispatch (third arm)
- `saasshorts.py:1485-1501` — cost table (third $0 branch; today `local` falls into premium pricing)
- `saasshorts.py:1273-1291` — composite `[0:a]` narration contract (mux must live in the local head adapter)
- `saasshorts.py:1350-1351` — direct key dict access (must become `.get` or local must populate placeholders)
- `SaaShortsTab.jsx:294-308, 341-355, 1023-1028, 188-192, 269-276, 1156, 1164` — client-side key sends, pre-alerts, disable/label (all binary on 2 modes)

## Solution Options

### Option 1: Per-stage adapter modules
**How it works:**
New module(s) (e.g. `saasshorts_local.py`) hold a ComfyUI HTTP client (`POST /prompt`, `GET /history/{prompt_id}`, `GET /view`, `POST /upload/image` — the exact `_fal_run` submit/poll shape, no timeout per FR8) and a TTS HTTP client (`POST /v1/audio/speech`, `GET /v1/voices`). Local adapters mirror the cloud helpers' signatures minus the key param and write the same fixed artifact paths. The Step-3 dispatch at `saasshorts.py:1409-1414` and the cost table at `:1485-1501` gain a third arm; the other stage call sites (`:1391-1394`, `:1445-1448`) dispatch per stage.

**Pros:**
- Matches the repo's own mode precedent (sibling functions + call-site dispatch, `86117d3`) AND its local-backend precedent (module + env flag + tests, `719d444`).
- Isolation boundary absorbs the expected fix tail (VRAM/OOM/workflow drift) inside one module, exactly as `transcribe_backends` and `llm_client` absorbed theirs.
- Cache guards (`saasshorts.py:1367-1368`), retry path (`app.py:7958-7985`), and composite (`:1186-1316`) stay untouched.

**Cons:**
- All four stage dispatch sites (not just Step 3) must branch — actor image `:1391`, voiceover `:1392-1394`, talking head `:1411/:1414`, b-roll `:1445-1448` (which has **no mode branch today** and always calls cloud).
- Gates (`app.py:7952-7955`, `:6669-6670`) and cost table (`:1485-1501`) are outside the module and must be swept in the same first slice (precedent `25222f5`: the gate is the seam that broke once already).

**Complexity:** Medium (~6–8 days)
- Files to create: 1–2 adapters/client modules (~300–390 lines) + workflow-template JSONs + tests (~300–500 lines)
- Files to modify: `saasshorts.py` (~60–90 lines), `app.py` (~50–60 lines: gates, validation, config, voices route), `SaaShortsTab.jsx` (~40–60 lines), `.env.example`
- Risk level: Low-Medium — every hard edge is bounded to the two backend files plus the gate sweep.

### Option 2: In-helper mode branching
**How it works:**
No new modules; each existing `generate_*` helper gains an internal `if video_mode == "local"` branch calling ComfyUI/TTS inline; helpers gain a `video_mode` parameter.

**Pros:**
- Mechanically cheap on diff count; inherits the retry/cache machinery for free; the actor-options route (`app.py:6679-6686`) is covered automatically without learning about modes.

**Cons:**
- Fights every repo precedent: `30ce67e`, `719d444`, `f89a7eb`, and `QwenLocalTTS` are all separate modules with explicit gated flags; `86117d3` chose sibling-function dispatch. Zero in-helper precedent.
- Helpers don't receive `video_mode` today — 4–5 signature changes plus every submit site before any branch lands.
- The local branch bodies live in one already over-mixed file (dead code at `saasshorts.py:769-779`) with no isolation boundary; in-helper closures resist offline testing.
- Key gate bypass still needed; KeyError at `saasshorts.py:1350-1351` still fires; the mux seam is unseamed.

**Complexity:** Medium (~5–7 days but risk-concentrated)
- Files to modify: `saasshorts.py` (+~300 → ~1800 lines), `app.py` (+~10), JSX (~+40)
- Risk level: High — silent-cloud-spend and silent-video failure modes, no module boundary.

### Option 3: StageProvider abstraction
**How it works:**
Per-stage provider interface (Cloud/Local implementations) selected through a registry; the five paid call sites refactor against the interface.

**Pros:**
- House style exists twice over: `AIProvider` ABC + factory (`ai_provider.py:83, 127, 380, 802`, five consumer modules) and the `llm_client` backend-selection module.
- Cleanest long-term shape if more providers (e.g. HunyuanVideo A/B, Kokoro secondary) arrive.

**Cons:**
- Highest blast radius: 7 paid HTTP sites + 9 invocation points + the cross-module import `voiceover.py:417` must all survive one atomic landing.
- The registry absorbs none of the real hazards: silent-head mux, artifact-name cache contract, GPU serialization (`saasshorts.py:1390, 1443` parallel pools), mode-aware gates — each breaks independently outside the interface.
- Strictly more work than Option 1 for the same user-visible result today; premature for a single new provider arm.

**Complexity:** High (~8–12 days)
- Files to create: ~650-line provider module + registry; tests ~300–500 lines
- Files to modify: `saasshorts.py` (stage bodies become delegates), `app.py`, `SaaShortsTab.jsx`
- Risk level: Medium — bounded, but the atomic landing must keep the pipeline runnable mid-refactor.

### Option 4: Cloud-API facade
**How it works:**
A local facade service impersonates the fal queue protocol, the fal storage CDN (`_fal_upload_file` initiate/PUT/`file_url`), and the ElevenLabs TTS+voices API, so `saasshorts.py` call sites stay untouched; only base-URL env overrides and gate bypass change.

**Pros:**
- Smallest pipeline diff (<30 modified lines; five call sites + two routes adopt with zero edits); the funnel surface (`_fal_run` consumes just six protocol fields) is fully enumerable.

**Cons:**
- Must faithfully impersonate **three** cloud API surfaces, including the storage CDN indirection that exists only for cloud — pure overhead offline.
- `veed/lipsync` (`saasshorts.py:961-969`) has no local twin — the facade must still answer it with real local logic, dissolving the "call sites unchanged" premise at the lipsync stage.
- Still needs the silent-head mux and the `config["fal_key"]` KeyError fix — i.e. it edits the module it promises not to touch.
- Per-stage model selection (a stated requirement) is unnatural through an impersonation layer.

**Complexity:** Medium (~7–9 days)
- Files to create: one ~450–600-line facade service (three protocol surfaces + ComfyUI/TTS clients + mux)
- Files to modify: `saasshorts.py:28-29` env overrides, `saasshorts.py:1350-1351` KeyError fix, `app.py` gates
- Risk level: Medium-High — protocol fidelity drift produces errors the job log stores as opaque `str(e)`.

## Comparison

| Criteria | 1. Adapters | 2. In-helper | 3. StageProvider | 4. Facade |
|----------|-------------|--------------|------------------|-----------|
| Complexity | M | M | H | M-H |
| Codebase fit | **H** (3 precedents) | L (0 precedents) | H (2 precedents) | H (4 precedents) |
| Integration risk | M (4 bounded edges) | **H** | M | M-H (3 protocol surfaces) |
| Migration cost | ~450 total lines | ~350 in-file | ~900+ across 4 files | ~600 facade |
| Verification cost | L-M (CPU-only) | M (closures resist isolation) | M | M (protocol fidelity) |
| Retry/cache preserved | **Yes, untouched** | Yes (inherited) | Conditional (contract must hold) | Yes (if protocol exact) |

## Recommendation

**Selected:** Option 1 — Per-stage adapter modules.

**Rationale:**
- The only candidate with **no blocked cell** in the fit grid; the other three each carry at least one blocking or risk-dominant dimension.
- It is the repo's own proven move applied a third time: `86117d3` proved the sibling-arm + dispatch + cost-arm shape survives; `719d444`/`f89a7eb` proved the local-module + tests + env-docs shape; `30ce67e`'s fix chain proved the isolation boundary absorbs follow-ups at gates and call sites without leaking into the module.
- All five verified adoption sites share one adapter-mirrorable signature `(content…, key, output_path) → output_path` — the key param exists only for cloud auth/CDN and drops cleanly.
- Adapter-side audio mux keeps `composite_video` untouched, consistent with the b-roll `anullsrc` precedent (`saasshorts.py:1035-1038`) and the `has_audio_stream` guards (`voiceover.py:438`, `transcribe_backends.py:339-362`).
- Every needed test runs CPU-only against stubbed transports; the only hard constraint is lazy imports so the module never pulls GPU deps at import time (`QwenLocalTTS` pattern, `voiceover.py:312-348`).

**Why not alternatives:**
- Option 2: fights every in-repo precedent, disperses complexity into an over-mixed file, and its novelty verdict is **blocked** (zero precedent; all four durable local-provider precedents point the other way).
- Option 3: house-style and durable but the highest-blast-radius route to the same result; its interface absorbs none of the four real hazards for free. Revisit only if a second local provider family (e.g. HunyuanVideo A/B + Kokoro) actually lands.
- Option 4: "call sites unchanged" is an illusion once the CDN surface, the twinless VEED leg, the mux, and the KeyError fix are counted; per-stage model selection is unnatural through an impersonation layer.

**Trade-offs:**
- Accepting four dispatch sites instead of one (vs. the naive "extend Step 3 only" reading) in exchange for per-stage control and $0-cost correctness.
- Accepting the gate-sweep-in-first-slice requirement (two server gates + four client sends) in exchange for keeping cloud modes' behavior byte-identical.

**Implementation approach:**
1. **Slice 1 — client + adapters**: ComfyUI client (`_fal_run` shape, no deadline per FR8), TTS client, workflow-template JSONs, the four local stage adapters incl. the in-adapter narration mux; env constants `COMFYUI_URL` / `TTS_BASE_URL` following `saasshorts.py:28-29` idiom.
2. **Slice 2 — dispatch + gates**: `Literal["premium","lowcost","local"]` at `app.py:7934`; mode-aware gates (`:7952-7955`, `:6669-6670`); third arm at `saasshorts.py:1409-1414`, `:1485-1501`; keys to `.get` at `:1350-1351`; local voices branch at `app.py:8120-8143`.
3. **Slice 3 — wizard + cost**: third card (`sm:grid-cols-3`), local cost row (FR7), non-misleading key warnings (`SaaShortsTab.jsx:269-276, 1156, 1164`), BYOK badge copy (`App.jsx:1222`).
4. **Slice 4 — GPU-host calibration + smoke**: real 3080 timings, LatentSync 1.5 vs 1.6 A/B, startup probe for both local URLs.

**Integration points:**
- `saasshorts.py:1409-1414` — third dispatch arm (talking head)
- `saasshorts.py:1391-1394`, `:1445-1448` — per-stage adapter dispatch (actor, voiceover, b-roll)
- `saasshorts.py:1350-1351` — `.get` for keys (local passes None)
- `app.py:7952-7955`, `:6669-6670` — mode-aware gates
- `app.py:8120-8143` — voices route local arm
- `app.py:8024-8031` — config threading for URLs (env-derived preferred for resume survival, `app.py:189-191`)

**Patterns to follow:**
- Submit/poll client: `saasshorts.py:591-662` (clone; drop the timeout per FR8)
- Local backend module + env flag + tests: `transcribe_backends.py` (`719d444`)
- Lazy GPU imports: `voiceover.py:312-348`
- Actionable error copy naming service + URL: `llm_client.py:380-389`

**Risks:**
- Gate/call-site fix tail (precedent `25222f5`, `86117d3` follow-ups): mitigation — mode-matrix route tests in the first slice.
- Silent-head mux regression: mitigation — contract test asserting the muxed head carries an audio stream (ffmpeg-based, CI-safe).
- ComfyUI workflow drift against the installed portable: mitigation — version-pinned template JSONs in-repo, calibration on the 3080 host.
- `omnivoice-server` youth: mitigation — pin the version; FastAPI sidecar around the `omnivoice` library is the documented fallback.

## Scope Boundaries
- Building: local `video_mode` arm, adapters, gates, config, wizard card + cost row, env docs, offline tests.
- NOT doing: in-process model hosting (FRD fixed external servers), subtitles/composite changes (already local, out of scope per developer), text-stage changes (`LLM_*` path stays), no new secret handling, no cloud fallback in local mode.

## Testing Strategy

**Unit tests:**
- ComfyUI client submit/poll/fetch against `httpx.MockTransport` (incl. FAILED/CANCELLED, missing-history error, no-timeout behavior)
- TTS client: request body, non-200 error naming service+URL, voice-list parsing
- Dispatch matrix: monkeypatched local adapters, `video_mode="local"` → adapters called, cloud helpers not
- Mux arg-builder: local head command includes the narration input and maps audio
- Cache-guard behavior per artifact at the fixed paths

**Integration tests:**
- Route mode matrix on `/api/saasshorts/generate`: local + no keys → 202; premium + no keys → 400; invalid mode → 422/400 (never silent premium)
- Actor-options gate matrix (`app.py:6669-6670`)
- Retry path reuses local intermediates; 0-byte sweep unchanged
- Mux end-to-end on synthetic media: silent mp4 + tone mp3 → output has an audio stream

**Manual verification (RTX 3080 host):**
- [ ] Offline end-to-end run with cloud keys absent from the environment
- [ ] Wan2.2 i2v clip timing logged (no first-party 3080 timing exists yet)
- [ ] LatentSync 1.5 (pinned) vs 1.6 A/B on 10 GB
- [ ] VRAM sequencing: ComfyUI frees between prompts; no parallel local submissions OOM
- [ ] Voice picker lists TTS-server voices; clone/design previews render

## Open Questions
**Resolved during research:**
- Model landscape and per-stage defaults — settled in research (Wan2.2 TI2V-5B Q8_0, LatentSync 1.5, OmniVoice/`omnivoice-server`, Flux dev/schnell Q4), developer-confirmed
- Hosting form — external local servers, confirmed in-repo rejection of the in-process shape (`QwenLocalTTS` precedent noted, not reused)
- Integration architecture — resolved by this analysis (Option 1)

**Requires user input:**
- `share_to_gallery` (S3 upload inside the job) treatment in local mode — hide/disable the checkbox is the default assumption
- BYOK badge copy (`App.jsx:1222`) — default: mode-aware label

**Blockers:**
- None blocking. Conditional items: HunyuanVideo 1.5 license read (only if the A/B candidate is exercised); Higgs-codec Boson license read (only if shipping at scale); LatentSync 1.6 VRAM question (one local test decides).

## References
- `.rpiv/artifacts/research/2026-09-14_21-41-44_ai-shorts-local-models.md` — full research this analysis chains from (model landscape, config chain, artifact contracts)
- `.rpiv/artifacts/discover/2026-09-14_21-17-54_ai-shorts-local-models.md` — FRD with 9 recorded decisions
- `saasshorts.py:591-662` — `_fal_run` client pattern to clone
- `saasshorts.py:1273-1291` — the `[0:a]` narration contract driving the in-adapter mux
- `saasshorts.py:1409-1414`, `:1485-1501` — the two existing mode-branch sites
- `app.py:7952-7955`, `:6669-6670`, `:8120-8143` — gate/voices sweep targets
- `transcribe_backends.py:364-380` — env-selected local backend precedent
- `tests/test_llm_client.py:55-63` — MockTransport offline-test idiom
- Historical: `86117d3` (mode arm), `30ce67e` (additive backend + fix chain), `719d444`/`f89a7eb` (local GPU module + env docs), `25222f5` (gate-regression warning)
- Fit-dispatch evidence: 4 parallel codebase-analyzer reports, 2026-09-15 (agents 53f68d5f, 7329af20, 381b6734, 59222cb3), anchors re-verified against the worktree at commit `73f670a`
