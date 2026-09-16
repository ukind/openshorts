---
date: 2026-09-14T21:17:54+0700
author: Yogiswara Utama
commit: 73f670a
branch: main
repository: openshorts
topic: "AI Shorts local model option"
tags: [intent, frd, saasshorts, comfyui, local-models, video-mode]
status: ready
last_updated: 2026-09-14T21:17:54+0700
last_updated_by: Yogiswara Utama
---

# FRD: AI Shorts local model option

## Summary

Add a third "local" Video Mode to the AI Shorts generator that runs every paid stage on the developer's own GPU through external local servers: ComfyUI for image and video generation, a local TTS server for voiceover. The feature then works with no cloud access at all. The research phase must pick the best performance/quality models for each stage on an RTX 3080 with 10 GB VRAM, including non-mainstream candidates.

## Problem & Intent

Developer framing (verbatim): "i want add option to use this feature with local model".

Stated driver (intent answer): "No cloud access" — the app must run fully offline, or in places with no reliable access to the paid APIs (Hailuo, Kling, VEED, ElevenLabs, fal.ai).

On scope (verbatim): "full local but it need deep research about what LLM that good for all of that . also check my PC spec to findout the best optimized LLM to generate that".

On hosting (verbatim): "external local servers, and yes i already have comfyuii here: F:\AI\ComfyUI_windows_portable".

On speed and quality (verbatim): "batch / no limit . but need to find the best MODEL between performance and quality . as the current comfy for image generation is the best between two world (performance and quality) . i mean, maybe there is out there beside the mainstream one".

## Goals

- Every paid cloud call in the AI Shorts pipeline gets a local arm: actor image, voiceover TTS, talking-head video, lipsync, b-roll stills.
- Zero cloud calls in local mode: no fal.ai, no ElevenLabs, no other cloud API.
- The mode is selectable as a third Video Mode card in the existing 5-step wizard.
- Local server endpoints (ComfyUI, TTS) are configurable without code edits.
- Model selection per stage is researched against the actual hardware (RTX 3080, 10 GB VRAM) and the performance/quality balance, including non-mainstream models.

## Non-Goals

- Text stages (analysis, scripts): the existing LLM_*/X-LLM-* local path stays as-is.
- Subtitles and the final composite: already local (faster-whisper/Parakeet + FFmpeg); no work here.
- Cloud modes: lowcost and premium keep their current BYOK key gate and behavior.
- In-process model hosting inside the app: rejected in favor of external local servers.
- Silent cloud fallback when a local service is missing: local mode fails with a clear error instead.

## Functional Requirements

1. The wizard shows a third Video Mode card "local" beside low cost and premium (`dashboard/src/components/SaaShortsTab.jsx:427-455`) and sends `video_mode: "local"` to `POST /api/saasshorts/generate` (`app.py:7934`).
2. `generate_full_video` dispatches "local" to a local arm, in the pattern of the lowcost/premium branch (`saasshorts.py:1409-1414`).
3. The local arm replaces each paid call behind the same on-disk output contracts the current helpers satisfy (`saasshorts.py:591-662` `_fal_run`, `saasshorts.py:804` ElevenLabs):
   - Actor image and b-roll stills: text-to-image through the ComfyUI API (prompt → PNG).
   - Talking head: image + audio → MP4 through a ComfyUI video workflow (the installed Wan2.2 TI2V 5B is the baseline candidate).
   - Voiceover: text → MP3 through a local TTS server (model chosen by research).
   - Lipsync: video + audio → lipsynced MP4 through a local method (gap — research must close it).
4. A settings/env block holds the local server URLs — ComfyUI URL (default `http://127.0.0.1:8188`) and TTS server URL — following the `LLM_*` env + `X-LLM-*` header pattern (`app.py:180-206`).
5. When `video_mode == "local"`, `POST /api/saasshorts/generate` skips the `X-Fal-Key` / `X-ElevenLabs-Key` 400 gate (`app.py:7952-7955`). Cloud modes keep the gate unchanged.
6. Local mode sends no request to any cloud API. When a local service is unreachable, the job fails with an error that names the service and its configured URL.
7. The per-mode cost table renders a local row with $0 estimates (table exists at `dashboard/src/components/SaaShortsTab.jsx:1106-1114`).
8. Long jobs keep working: local generation runs in the existing background job + status polling lifecycle (`app.py:7991-8105`), and no added timeout kills a batch render.

## Non-Functional Requirements

- **Performance**: no wall-time limit (batch mode). Research optimizes the performance/quality balance per stage instead. Quality bar: the developer's current Flux ComfyUI image setup ("best between two world").
- **Hardware fit**: every chosen model and workflow must run in 10 GB VRAM (RTX 3080) on Windows 11. Quantized/GGUF variants are acceptable.
- **Security**: BYOK keys stay client-side headers for cloud modes. Local mode adds no new secret handling.
- **UX**: the local card communicates hardware needs. Unreachable local services produce actionable errors (server name + URL), never a generic failure.
- **Reliability**: batch-friendly. Jobs can run long; status polling stays correct for multi-hour renders.

## Constraints & Assumptions

- Hardware (probed 2026-09-14): NVIDIA RTX 3080 10 GB VRAM, AMD Ryzen 5 5600 (6c/12t), 32 GB RAM, Windows 11 Pro.
- ComfyUI portable installed at `F:\AI\ComfyUI_windows_portable` (not running at probe time; default port 8188).
- Already installed there: Wan2.2-TI2V-5B-Q8_0 GGUF + `wan2.2_ti2v_5B_fastwan` LoRA + wan2.2 VAE + umt5-xxl fp8 encoder; SDXL base 1.0; Flux dev/schnell Q4 GGUF + t5-v1_1-xxl Q4 + clip_l + ae VAE; custom nodes ComfyUI-GGUF, ReActor (face swap, not lipsync), Frame-Interpolation, Wan22FirstLastFrameToVideo, comfyui-try-on.
- Gaps in the install: no TTS model, no dedicated lipsync node. Research must fill both.
- Assumption: the ComfyUI HTTP API (`/prompt`, `/history`, `/view`, websocket progress) is the integration surface; research validates against the installed ComfyUI version.
- Assumption: "no cloud access" is strict inside local mode — no partial fallback.
- The BYOK rule for cloud modes stays: header keys only, never env (`.env.example:109`).

## Acceptance Criteria

- [ ] With cloud domains unreachable (firewalled), `POST /api/saasshorts/generate` with `video_mode: "local"` runs end to end: job status reaches "completed" and the result MP4 exists on disk.
- [ ] The same request without `X-Fal-Key` / `X-ElevenLabs-Key` headers starts a job (no HTTP 400).
- [ ] `.env.example` documents the local provider vars (ComfyUI URL, TTS server URL), and the docs explain how to point the app at `F:\AI\ComfyUI_windows_portable`.

## Recommended Approach

Add a "local" arm to the existing `video_mode` dispatch in `saasshorts.generate_full_video`, with per-stage local adapters (a small ComfyUI API client for image and video, a local TTS client, a local lipsync client) that return the same on-disk artifacts the fal/ElevenLabs helpers produce today; configure through `LLM_*`-style env vars plus optional headers; add the third wizard card and bypass the key gate only for this mode. Research (next phase) selects per-stage models against the 10 GB VRAM budget, the performance/quality balance, and non-mainstream candidates, and closes the TTS and lipsync gaps.

## Decisions

### Local text path stays
**Question**: From the probe I inferred — text stages (analysis, scripts) already run on a local-capable path: LLM_BASE_URL/LLM_API_KEY/LLM_MODEL env or X-LLM-* headers, keyless OpenAI-compatible endpoints (app.py:180-206, saasshorts.py:361-363). Keep this as the local text path, or change it as part of the work?
**Recommended**: Keep as-is
**Chosen**: Keep as-is
**Rationale**: evidence: app.py:180-206, saasshorts.py:361-363 + confirmed

### Subtitles and composite out of scope
**Question**: From the probe I inferred — subtitles and the final composite are already local: faster-whisper/Parakeet transcription (transcribe_backends.py:21-25, called at saasshorts.py:1087-1095) and an FFmpeg composite (saasshorts.py:1303-1314). Keep them out of scope, or change?
**Recommended**: Keep out of scope
**Chosen**: Keep out of scope
**Rationale**: evidence: transcribe_backends.py:21-25, saasshorts.py:1303-1314 + confirmed

### Key gate bypassed in local mode only
**Question**: /api/saasshorts/generate returns 400 without X-Fal-Key and X-ElevenLabs-Key (app.py:7952-7955). A local run has no cloud keys. Which behavior do you want?
**Recommended**: Bypass in local mode; cloud modes keep the 400
**Chosen**: Bypass in local mode
**Rationale**: evidence: app.py:7952-7955 + confirmed; strict-offline intent makes env fallback keys and gate removal unnecessary

### Intent driver: no cloud access
**Question**: You want an option to run the ai shorts generator on local models instead of the paid cloud video APIs (Hailuo, Kling, VEED). What problem does that solve today, and who hits it first?
**Recommended**: n/a — intent question
**Chosen**: "No cloud access"
**Rationale**: The app must run fully offline, or where the paid APIs are unreachable.

### Scope: full local
**Question**: For a fully offline run, every paid call needs either a local replacement or a local way to skip it. Which scope should the local option cover?
**Recommended**: Full local (local TTS + local talking head + one shared local text-to-image)
**Chosen**: Full local, with deep research into the best models and a PC-spec fit check (developer's words recorded in Problem & Intent)
**Rationale**: Strict offline intent requires every paid stage covered; model selection delegated to research

### Runtime: external local servers
**Question**: How should the local models run relative to the app? Your hardware (RTX 3080 10GB, Ryzen 5 5600, 32GB RAM) can host all needed model families, but the hosting form changes the design.
**Recommended**: External local servers
**Chosen**: External local servers; ComfyUI already installed at F:\AI\ComfyUI_windows_portable
**Rationale**: Ecosystem maturity and model freedom; the existing install removes most setup cost

### Integration: mode card + settings block
**Question**: Where should the local option live in the code and UI?
**Recommended**: Mode card + settings block
**Chosen**: Mode card + settings block
**Rationale**: Clean UX plus configurable endpoints; follows the video_mode dispatch (saasshorts.py:1409-1414) and the LLM_* config precedent (app.py:180-206)

### Time budget: batch, perf/quality research directive
**Question**: What wall time per video is acceptable?
**Recommended**: About 10 min (speed-optimized configs)
**Chosen**: Batch / no limit; research must find the best performance/quality balance per stage, including non-mainstream models; the current Flux ComfyUI image setup is the quality bar
**Rationale**: Developer's verbatim answer recorded in Problem & Intent

### Acceptance criteria selected
**Question**: Which observable checks must pass for this feature to count as done?
**Recommended**: All four offered checks
**Chosen**: Offline end-to-end + No-key start in local + Docs/env examples (Zero-cost display not selected; kept as Functional Requirement 7 because the table breaks without it)
**Rationale**: Developer's multi-select

## Open Questions

None deferred by the developer. Per-stage model selection (TTS, lipsync, video workflow details) is deliberately delegated to the research phase.

## Suggested Follow-ups

- Zero-cost display was offered as an acceptance criterion and not selected; the cost table still needs a local row so it does not break (`dashboard/src/components/SaaShortsTab.jsx:1106-1114`).
- No local TTS model and no dedicated lipsync node exist in the ComfyUI install (models/TTS empty; ReActor is a face swap, not lipsync) — research must close both (`F:\AI\ComfyUI_windows_portable`).
- The AI Shorts nav item carries a BYOK badge (`dashboard/src/App.jsx:1222`); local mode needs no keys, so the badge copy misleads in local mode.
- Marketing copy echoes the cloud-only video modes (`dashboard/src/components/Landing.jsx:504-506`, `:586`, `PricingPage.jsx:64`) — if local mode ships publicly, keep pricing claims accurate: cloud tiers stay paid, local stays free of per-video cost; quote both, never one alone.

## References

- Screenshot: `c:\Users\utama\OneDrive\Pictures\Screenshots\Screenshot 2026-09-14 211604.png` (AI Shorts setup step)
- `saasshorts.py` — pipeline; `_fal_run` :591, ElevenLabs :804, mode dispatch :1407-1414, cost tables :1485-1501
- `app.py` — SaaSShorts routes :6518+, generate :7940, key gate :7952-7955, LLM resolve :180-206, status :8105
- `dashboard/src/components/SaaShortsTab.jsx` — wizard :18, video mode cards :427-455, cost table :1106-1114
- `voiceover.py` — Qwen3-TTS local provider precedent :306-374, :880
- `ai_provider.py` — keyless local endpoint precedent :802-836
- `transcribe_backends.py` — already-local transcription :21-25
- `.env.example` — BYOK rule :109, LLM_* vars :60-69, keyless note :84-85
