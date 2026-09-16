---
date: 2026-09-14T21:41:44+0700
author: Yogiswara Utama
commit: 73f670a
branch: main
repository: openshorts
topic: "AI Shorts local model option"
tags: [research, codebase, saasshorts, comfyui, local-models, video-mode, tts, lipsync]
status: ready
last_updated: 2026-09-14T21:41:44+0700
last_updated_by: Yogiswara Utama
---

# Research: AI Shorts local model option

## Research Question
Chained from the FRD `.rpiv/artifacts/discover/2026-09-14_21-17-54_ai-shorts-local-models.md` (developer intent: "i want add option to use this feature with local model", driver: "No cloud access"). The scope-tracer framed it as: how does the AI Shorts pipeline gain a local `video_mode` arm — ComfyUI image and video generation, a local TTS server, a local lipsync stage — with strict offline operation, batch semantics and no added time limit, and per-stage model selection against an RTX 3080 with 10 GB VRAM?

Nine sub-questions were answered: dispatch extension, on-disk artifact contracts, the ComfyUI client, the config chain, key-gate bypass, the wizard card, the voice contract, background-job semantics, and the hosting boundary. Four external sweeps covered the ComfyUI API, the 2026 open-weights video and lipsync landscape, local TTS servers, OmniVoice, and MiniMax.

## Summary
- Local mode is a third value on one unvalidated string that seven consumers already read. Six of them default an unknown value to premium. The only mode branch today is the Step-3 dispatch at `saasshorts.py:1409-1414`.
- Five paid call sites need local twins. They write fixed on-disk paths. Adapters that reproduce bytes at those paths keep the cache guards and the retry path unchanged.
- The ComfyUI client mirrors `_fal_run` (`saasshorts.py:591-662`): submit `POST /prompt`, poll `GET /history/{prompt_id}`, fetch `GET /view`. Audio inputs upload through `/upload/image` multipart. No `/upload/audio` endpoint exists.
- The audio track is the sharpest trap. The composite reads narration from `[0:a]` of the talking head (`saasshorts.py:1272-1284`). Cloud lipsync embeds that audio. Wan i2v output is silent. The local lipsync adapter must mux the TTS audio in.
- Two server-side header gates and four client-side key sends need a `local` conditional. The voices route already degrades keyless requests to a defaults list (`app.py:8133-8143`).
- Models settled with the developer: Wan2.2 TI2V-5B Q8_0 + fastwan LoRA for i2v (verified the newest open-weights Wan as of Sept 2026), LatentSync 1.5 pinned for lipsync, OmniVoice through `omnivoice-server` for TTS, Flux dev Q4 for actor portraits and schnell Q4 for b-roll stills.
- Rejected with sources: MiniMax H3 (33B, 12 GB floor, license excludes EU/UK/KR/US outputs), LTX-2.x (16 GB floor), XTTS-v2 (CPML license), fish-speech (CC-BY-NC), Dia (10-14 GB), F5-TTS as primary (CC-BY-NC weights).
- Precedent lesson: follow-up fixes cluster at gates and call sites, not in new modules. A gate sweep and mode-matrix route tests belong in the first plan.

## Detailed Findings

### Mode dispatch and pipeline (`saasshorts.py`)
- The mode string travels unchanged through five layers: wizard body (`SaaShortsTab.jsx:305`) → `SaaSGenerateRequest.video_mode` (`app.py:7934`, plain `str`, no validation) → `config["video_mode"]` (`app.py:8030`) → read once at `saasshorts.py:1407` → branch at `saasshorts.py:1409-1414`.
- An unknown value falls to the premium arm. In local mode `fal_key` is `None`, so the premium helper fails mid-job. Add `Literal` validation on the request model, or reject unknown modes at the route.
- `generate_full_video` reads `config["fal_key"]` and `config["elevenlabs_key"]` by direct dict access at `saasshorts.py:1348-1349`. A local config must still populate both keys with `None`, or those lines become `config.get(...)`.
- Stage order: Steps 1-2 run the actor image and voiceover in a two-worker `ThreadPoolExecutor` (`saasshorts.py:1390-1399`), Step 3 dispatches the talking head (`:1407-1414`), Step 4 generates b-roll in a three-worker pool (`:1426-1449`), Step 5 transcribes, Step 6 composites.
- The per-mode cost table at `saasshorts.py:1483-1503` branches on `video_mode` and feeds `cost_estimate` into the job result (`app.py:8055`). Local mode adds a third branch with $0 lines (FR7).

### On-disk artifact contracts
- Fixed paths: `{slug}_actor_option_{i}.png` (`saasshorts.py:711-773`), `{slug}_actor.png` (`:782-791`), `{slug}_voice.mp3` (`:795-831`, path built at `:1357`), `{slug}_head.mp4` (`:862-897`), `{slug}_broll_{i}.mp4` (`:1429`).
- B-roll is a Ken Burns pan over a Flux still (`:1030-1044`), not true video generation. The local arm keeps that shape and swaps the image source.
- Consumers and their assumptions: `_get_media_duration` probes with ffprobe (`:1484`), `generate_tiktok_subs` reads the audio (`:1107`), the composite normalizes every segment to 1080x1920 at 30 fps and concatenates (`:1303-1316`), `mark_ai_generated` tags the output (`:1479`).
- The head MP4 must carry the narration audio track. The composite pulls all narration from `[0:a]` of the talking head (`:1272-1284`). A silent Wan i2v clip breaks Step 6 with a filtergraph error on a missing audio stream.

### ComfyUI client
- House pattern to mirror: `_fal_run` at `saasshorts.py:591-662` submits, polls every 5 s, raises on FAILED/CANCELLED and on timeout.
- ComfyUI equivalent: `POST {base}/prompt` with `{"prompt": <api-format workflow JSON>}` returns a `prompt_id`. Poll `GET /history/{prompt_id}` until the entry appears with outputs. Execution errors surface in the history entry status. Fetch each output with `GET /view?type=output&filename=...&subfolder=...`. The websocket `/ws` channel gives progress but is optional. Polling alone is enough for batch.
- Input injection: `POST /upload/image` with the multipart field `image` accepts images and audio files alike. The response carries `filename` and `subfolder` for the `LoadImage` / `LoadAudio` nodes. No `/upload/audio` endpoint exists. This replaces `_fal_upload_file` (`saasshorts.py:665`) — no CDN, no public URL, no key.
- Workflow templates are API-format JSON dicts. Parameterize by patching node fields in Python: prompt text, seed, checkpoint name, uploaded filenames. No SDK is needed. Ship the templates as JSON files next to the client module.
- VRAM sequencing: Wan (8 GB class) and LatentSync and Flux each fit alone on 10 GB. The client must run stages one at a time. ComfyUI frees VRAM between prompts by default.
- Errors must name the service and URL (FR6): raise with both, because the job handler stores only `str(e)` (`app.py:8096`).
- Timeouts: FR8 mandates no added time limit. The local client polls without a deadline, or with a very high default. The 600 s / 900 s bounds live in cloud callers only (`saasshorts.py:591`, `:968`).

### Config chain
- Precedent: `resolve_llm` at `app.py:180-206` — header triple wins, else `LLM_*` env, implemented by `llm_client.config_from` (`llm_client.py:166-195`) with per-task model fallback and actionable error copy (`:380-389`).
- New vars follow the same shape: `COMFYUI_URL` (default `http://127.0.0.1:8188`) and `TTS_BASE_URL`, plus optional per-stage model names. Optional headers (`X-ComfyUI-Url`, `X-TTS-Base-Url`) resolve at the route and travel through `config`, exactly as `fal_key` does (`app.py:8025-8026`).
- Placement constraint: `generate_full_video` runs in `run_in_executor` (`app.py:8033-8048`). The executor thread cannot see request headers. Resolution belongs in the route handler, not at adapter import time.
- Resume caveat: header-derived config does not survive a redeploy resume, env-derived config does (`app.py:189-191`). Batch local runs favor env configuration.
- Env namespace rule: the new vars are satellite-style config in the `llm_client` sense. They do not touch `AI_PROVIDER` / `OPENAI_*` / `GEMINI_*`.

### Key gates and SSRF
- Generate gate: `app.py:7952-7955` raises 400 when `X-Fal-Key` or `X-ElevenLabs-Key` is missing. The fix is a `video_mode == "local"` conditional. Cloud modes keep the 400 (FRD decision).
- Actor-options gate: `app.py:6664-6670` raises 400 without `X-Fal-Key`. Same conditional. The local branch generates options through ComfyUI and returns server-relative `/videos/saas_actors_{job_id}/...` URLs.
- SSRF: `assert_public_url` at `app.py:8008` guards user-supplied `http(s)` `selected_actor_url` values. Server-relative `/videos/` paths take the guard-free branch at `app.py:8018-8022`. Local adapters call `127.0.0.1` with their own `httpx` clients. No egress guard stands between the adapters and localhost.
- Voices route: `app.py:8120-8143` never returns 400. Keyless requests fall to `DEFAULT_VOICES` — six hardcoded cloud ids (`saasshorts.py:31-39`) — with `source: "defaults"`. In local mode this misleads: the picker offers voices no local server can render. The route needs a local branch that queries the TTS server's voice list.
- Client side sends both keys unconditionally: generate `SaaShortsTab.jsx:294-308`, retry `:341-355`, actor-options `:1023-1028`, voices `:188-192`. Pre-alerts at `:269-276` and the disable at `:1156` block local mode in the browser before any request. The bypass must land on both sides.

### Wizard UI
- Mode state: `videoMode` at `SaaShortsTab.jsx:57`, default `'lowcost'`. Card grid at `:429-456` renders two buttons under `grid-cols-1 sm:grid-cols-2`; a third card wants `sm:grid-cols-3`.
- Binary assumptions that break on a third value: cost panel header estimate `:1099`, line-item ternary `:1102` (local falls through to premium numbers), generate label `:1164`, and the missing-keys warning that renders for local mode although local mode needs no keys.
- FR7: the cost panel gains a local row with $0 line items. The card copy states the hardware need (10 GB VRAM class GPU, ComfyUI at the configured URL, TTS server at the configured URL).
- The AI Shorts nav badge at `dashboard/src/App.jsx:1222` says BYOK. That copy misleads in local mode.

### TTS stage and voice contract
- Current stage: `generate_voiceover` posts to `{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}` and writes MP3 bytes (`saasshorts.py:795-831`). `voice_id` defaults to `"21m00Tcm4TlvDq8ikWAM"` (`:1350`, `app.py:8027`).
- In-process precedent `QwenLocalTTS` (`voiceover.py:306-374`) takes a free-text `voice_description` and exposes no catalog. The FRD fixed the hosting form as external local servers, so the adapter is an HTTP client in the `_fal_run` shape, not a torch singleton.
- OmniVoice (k2-fsa): 0.6B non-autoregressive diffusion TTS on a Qwen3-0.6B backbone, 24 kHz WAV, 600+ languages with English top-tier. Zero-shot cloning from 3-10 s reference audio plus `ref_text`. Voice design from attribute strings ("female, mid-30s, low pitch, american accent"). Inline `[laughter]` / `[sigh]` tags. License: Apache-2.0 code and model, except the bundled Higgs Audio V2 codec under a Boson community license (100k annual-active-user cap; self-hosted use is fine).
- Fit on the shared 10 GB card: ~2 GB weights, 5-7 GB service peak with ASR loaded. Always supply `ref_text` to skip Whisper and avoid the Windows torchcodec bug. Serialize requests and call `torch.cuda.empty_cache()` between generations (open VRAM-leak issue #199). Expect RTF 0.15-0.3 on 3090-class at 16 steps: a 40 s narration costs 6-12 s of GPU time.
- Server form: community `omnivoice-server` (MIT, pip-installable) exposes OpenAI-compatible `POST /v1/audio/speech`, `GET /v1/voices`, clone-profile CRUD, and mp3 output via pydub+ffmpeg. It is young and solo-maintained: pin the version. Fallback: a ~100-line FastAPI sidecar around the `omnivoice` library, using `VoiceClonePrompt.save/load` as the catalog format.
- Voice picker in local mode: query `GET /v1/voices` and map rows to `{voice_id, name}`. Voice-design attributes generate previews; a chosen voice persists as a clone profile.
- Alternates on the same API shape: Kokoro-FastAPI (0.9 GB, ~100x realtime, Elo 1061 — fast-draft secondary), Chatterbox-TTS-Server (mature server software, MIT, requires Python 3.10 on Windows). Rejected: XTTS-v2 (CPML non-commercial, dead upstream), fish-speech (CC-BY-NC weights, S2 needs 24 GB), Dia (10-14 GB, slower than realtime), F5-TTS as primary (CC-BY-NC weights), openedai-speech (self-declared obsolete).

### Background-job semantics
- The route builds the `saas_jobs` record (`app.py:7992-8005`), runs the pipeline in `run_in_executor` under `concurrency_semaphore` (`:8033-8048`), and streams the `log_msg` callback into `saas_jobs[job_id]["logs"]` (`:8034-8040`).
- Helpers log with bare `print()`. Those lines never reach the job log. Local adapters must accept and use the `log` callback, threaded through like the stage helpers do.
- The retry path (`app.py:7957-7990`) reuses `output_dir` and clears 0-byte `_final.mp4` files. The `_exists` guards (`saasshorts.py:1368-1369`) then skip cached intermediates. This works unchanged for local artifacts because they land at the same fixed paths.
- Nothing in `app.py` imposes a wall-time limit on a job. "No time limit" (FR8) means the local client must not import one. The shared semaphore still bounds concurrency: a long local render queues behind other jobs.

### Model landscape (external, 2025-2026 sources)
Video, image-to-video, 10 GB budget:
- Wan2.2 TI2V-5B Q8_0 GGUF: ~8 GB working floor, Apache 2.0, ComfyUI-native with GGUF nodes. Portrait 704x1280 at 24 fps, 120 frames = 5 s, documented on 3080 Ti-class hardware. With the fastwan distill LoRA expect roughly 4-10 min per 5 s clip. No FP8 compute on Ampere sm_86, so GGUF Q8 is the correct quant — the one already installed.
- Verified September 2026: Wan 2.5, 2.6, 2.7 and 3.0 are API-only. Wan2.2 remains the newest open-weights Wan. The open branch added only task models on the same base (Animate-2, Dancer).
- Quality superiors that do not fit 10 GB: LTX-2.3/2.5 (22B, 16 GB GGUF floor, fast path needs RTX 40-series), Wan2.2-I2V-A14B (12-16 GB painful), SkyReels-V3 (14-19B), MiniMax H3 (33B, ~12 GB floor with 64 GB RAM, and the license excludes EU/UK/KR/US outputs).
- HunyuanVideo 1.5 (Nov 2025, 8.3B): the only newer open i2v model that squeezes under 10 GB (GGUF Q4 plus text-encoder offload, 81-frame clips, 480p-720p edge). Praised for facial realism. Tencent community license — read the license file before any product use. Kept as an optional A/B, not the default.
- Speech-driven alternates: Wan2.2-S2V-1.3B (~8 GB, audio to video in one pass, minute-long clips, fidelity below TI2V-5B) and HuMo-1.5B (Apache 2.0, best documented lip-sync at this scale, ~3.9 s stitchable segments). Optional route where audio-reactive performance matters more than fidelity.

Lipsync:
- Default: LatentSync 1.5 through a ComfyUI wrapper node. Pin 1.5. LatentSync 1.6 retrained at 512 px and fixes the blurry teeth and lips of 1.5 as a drop-in checkpoint swap. VRAM reports conflict (one source claims OOM below 18 GB, others 6-8 GB). Test on the 3080 before switching.
- Fast lane: MuseTalk (~8 GB, 30+ fps, 256 px mouth-region cap). One-shot: EchoMimic v3 flash (~6.5 GB at 768x512).
- Every path still needs the audio mux: Wan i2v output is silent.

Image:
- Flux dev Q4 for actor portraits — realism priority on faces, the developer's stated quality bar. Flux schnell Q4 for b-roll stills — 30-60 s per 1080x1920 still, and Ken Burns motion hides the detail gap. SDXL + Lightning (6-10 s per image) stays the speed fallback. All fit 10 GB with room for the text encoder.

## Code References
- `saasshorts.py:28-41` — API base constants, `DEFAULT_VOICES`, SaaS model env
- `saasshorts.py:591-662` — `_fal_run`: submit, 5 s poll, timeout raise (house client pattern)
- `saasshorts.py:665-710` — `_fal_upload_file`: CDN upload the local path does not need
- `saasshorts.py:711-793` — `generate_actor_images` / `generate_actor_image`: PNG options at fixed paths
- `saasshorts.py:795-831` — `generate_voiceover`: ElevenLabs POST, MP3 bytes to `{slug}_voice.mp3`
- `saasshorts.py:834-855` — `get_elevenlabs_voices`: catalog shape `{voice_id, name, category, labels, preview_url}`
- `saasshorts.py:862-901` — `generate_talking_head`: Kling avatar, `{slug}_head.mp4`
- `saasshorts.py:903-984` — `generate_talking_head_lowcost`: Hailuo i2v then VEED lipsync (two-step precedent)
- `saasshorts.py:986-1050` — `generate_broll`: Flux still plus Ken Burns pan
- `saasshorts.py:1107-1140` — `generate_tiktok_subs`: subtitle generation from audio
- `saasshorts.py:1272-1316` — composite: `[0:a]` narration contract, 1080x1920 normalize, concat
- `saasshorts.py:1323-1469` — `generate_full_video`: artifact paths `:1356-1360`, key access `:1348-1350`, guards `:1368-1369`, mode dispatch `:1407-1414`, b-roll loop `:1426-1449`
- `saasshorts.py:1479-1503` — `mark_ai_generated`, `_get_media_duration`, per-mode cost tables
- `app.py:180-210` — `resolve_llm`: header-over-env config precedent
- `app.py:6655-6700` — actor-options route and its `X-Fal-Key` gate
- `app.py:7928-7937` — `SaaSGenerateRequest`: unvalidated `video_mode` field
- `app.py:7940-7955` — generate route, key gate
- `app.py:7957-7990` — retry path: output_dir reuse, 0-byte final sweep
- `app.py:7992-8060` — job record, executor, semaphore, result payload
- `app.py:8002-8022` — `selected_actor_url`: SSRF guard vs guard-free `/videos/` branch
- `app.py:8092-8118` — failure handling (`str(e)` into logs) and status polling
- `app.py:8120-8143` — voices route: keyless fallthrough to cloud defaults
- `dashboard/src/components/SaaShortsTab.jsx:57` — `videoMode` state
- `dashboard/src/components/SaaShortsTab.jsx:188-196` — voices fetch with key header
- `dashboard/src/components/SaaShortsTab.jsx:269-276` — missing-key pre-alerts
- `dashboard/src/components/SaaShortsTab.jsx:294-308` — generate send (keys + `video_mode`)
- `dashboard/src/components/SaaShortsTab.jsx:341-355` — retry send
- `dashboard/src/components/SaaShortsTab.jsx:429-456` — mode card grid
- `dashboard/src/components/SaaShortsTab.jsx:1019-1046` — actor pre-selection fetch
- `dashboard/src/components/SaaShortsTab.jsx:1096-1114` — cost panel (binary ternaries)
- `dashboard/src/components/SaaShortsTab.jsx:1156,1164` — button disable and label
- `dashboard/src/App.jsx:1222` — AI Shorts BYOK badge
- `llm_client.py:166-202` — `config_from` / `active_config`
- `llm_client.py:380-389` — actionable half-config error copy
- `voiceover.py:306-374` — `QwenLocalTTS` in-process precedent (rejected hosting form)
- `ai_provider.py:802-836` — keyless-endpoint factory precedent
- `.env.example:60-69,84-85,109-112` — LLM block, keyless note, BYOK rule, `TTS_*` vars

## Integration Points

### Inbound References
- `app.py:7940` — `POST /api/saasshorts/generate`: receives `video_mode`, keys, script; builds `config`; owns the gate and the job lifecycle
- `app.py:6655` — `POST /api/saasshorts/actor-options`: gated actor stills for the wizard pre-selection step
- `app.py:8120` — `GET /api/saasshorts/voices`: voice catalog for the picker
- `app.py:8105` — status polling: reads `saas_jobs[job_id]` logs and result
- `dashboard/src/components/SaaShortsTab.jsx:305,352` — the only senders of `video_mode`

### Outbound Dependencies
- Cloud today: fal queue (`saasshorts.py:602`), ElevenLabs (`:795-831`), VEED lipsync (`:962-979`), S3 gallery upload (`app.py:8050+` on `share_to_gallery`)
- Local, new: ComfyUI at `COMFYUI_URL` — `POST /prompt`, `GET /history/{id}`, `GET /view`, `POST /upload/image`; TTS server at `TTS_BASE_URL` — `POST /v1/audio/speech`, `GET /v1/voices`
- Both local targets are plain `httpx` calls from adapter modules. No proxy, no egress guard, no key.

### Infrastructure Wiring
- `config` dict threading: `app.py:8024-8031` builds it; `saasshorts.py:1346-1351` consumes it; adapters receive resolved URLs through it
- `concurrency_semaphore` + `run_in_executor`: `app.py:8033-8048`; local jobs share the queue bound, not a time bound
- `log_msg` callback: `app.py:8034-8040`; the only channel from adapter to polled job log
- Resume manifest: env-derived config survives (`app.py:189-191`); header-derived does not

## Architecture Insights
- Additive-arm pattern: one dispatch branch plus per-stage adapters with the cloud helpers' signatures minus the key parameter. Cache guards, retry, and job lifecycle stay untouched.
- The mode string is load-bearing in five layers (client ternaries, request model, route gate, pipeline dispatch, cost table). Validate it as a `Literal` at the request model so a typo fails at the boundary, not mid-job as premium.
- Stage ownership: the local adapters are new satellite clients in the `llm_client` sense. They read their own env namespace (`COMFYUI_URL`, `TTS_BASE_URL`) and never touch `AI_PROVIDER` / `GEMINI_*`.
- Strict-failure design: adapters raise with service name and URL; the job handler stores `str(e)` verbatim; the wizard surfaces it. No silent cloud fallback anywhere in local mode.
- Sequencing over parallelism on one GPU: Steps 1-2 run image and TTS in parallel today. On a 10 GB card shared with Wan, the local arm gains from serializing heavy stages or accepting ComfyUI's own queue to serialize them.

## Precedents & Lessons
3 similar past changes analyzed.

### Precedent: opt-in OpenAI-compatible backend
**Commit(s)**: `30ce67e` (2026-08-30) — "feat(llm): add an opt-in OpenAI-compatible chat backend alongside Gemini"
**Blast radius**: 1 file, +454 lines (`llm_client.py`); spread outward later to `app.py`, `main.py`, `layout_picker.py`, `cloud/alerts.py`, `mcp_server.py`, dashboard
**Follow-up fixes**: `e96407b` error classification + BYOK headers through MCP; `c0da654` moment picker on OpenAI-compatible servers; `25222f5` (2026-09-02) a merge dropped half of the local-LLM path; `56707b7` launch crash on None `GEMINI_API_KEY`; `bb06858` tolerate reasoning models; `73f670a` survive congested endpoints
**Takeaway**: the gate logic and per-feature call sites broke repeatedly after the first commit. A gate sweep and a hardening pass belong in the first plan.

### Precedent: AI Shorts low-cost mode
**Commit(s)**: `86117d3` (2026-03-19) — "feat: AI Shorts low-cost mode, UGC gallery, universal business support"
**Blast radius**: 10 files, +1621/-169 (`app.py` +403, `SaaShortsTab.jsx` +357, `saasshorts.py` +139, `.env.example` +1, plus App.jsx, Landing.jsx, UGCGallery.jsx, s3_uploader.py)
**Follow-up fixes**: `d128fca` (2026-03-24, five days later) gender voice selection in `SaaShortsTab.jsx`; `9c9342c` (2026-04-10) audio-path follow-up
**Takeaway**: a mode flag multiplies untested combinations. Mode-matrix route tests must exist before a third mode lands.

### Precedent: local GPU model module
**Commit(s)**: `719d444` (module + tests + env docs shape); `f89a7eb` — qwen-tts Docker GPU build, hf-cache volume, `.env.example` +12
**Takeaway**: reuse the module + tests + env-docs shape for the local adapters, and add a startup probe for the ComfyUI and TTS URLs in the same spirit as `llm_client`'s half-config warning.

### Composite Lessons
- Follow-up fixes land at gates and call sites, not in new modules (`30ce67e` series, `25222f5`). Plan the gate sweep and the mode-matrix route tests inside the first slice.
- Half-config detection catches broken setup early (`llm_client` base-without-key warning). A startup probe for `COMFYUI_URL` and `TTS_BASE_URL` gives the same protection (FR6 wants the error to name the service and URL anyway).
- Env config survives a redeploy resume; header config does not (`app.py:189-191`). Batch local runs read env.
- BYOK keys stay client-side headers for cloud modes (`.env.example:109`). Local mode adds no new secret handling.

## Historical Context (from `.rpiv/artifacts/`)
- `.rpiv/artifacts/discover/2026-09-14_21-17-54_ai-shorts-local-models.md` — FRD this research chains from
- `.rpiv/artifacts/research/2026-09-09_20-00-08_unified-ai-provider-config.md` — the env/header config chain precedent
- `.rpiv/artifacts/designs/2026-09-10_07-09-56_unified-ai-provider-config.md` — unified provider card design
- `.rpiv/artifacts/handoffs/2026-08-30_14-42-31_openai-compatible-llm-provider-implementation.md` — phased implementation shape for an additive backend
- `.rpiv/artifacts/validation/2026-08-30_15-13-19_openai-compatible-third-party-llm-endpoint-alongside-gemini-additive.md` — dual-gate endpoint validation

## Developer Context
**Q (discover: Local text path stays): text stages already run on a local-capable path (`LLM_*` env or `X-LLM-*` headers). Keep as-is?**
A: Keep as-is.

**Q (discover: Subtitles and composite out of scope): subtitles and the final composite are already local. Keep out of scope?**
A: Keep out of scope.

**Q (discover: Key gate bypassed in local mode only): `/api/saasshorts/generate` returns 400 without both keys. Which behavior?**
A: Bypass in local mode. Cloud modes keep the 400.

**Q (discover: Intent driver): what problem does the local option solve?**
A: "No cloud access" — the app must run fully offline.

**Q (discover: Scope): which paid calls get local replacements?**
A: Full local, with deep research into the best models and a PC-spec fit check.

**Q (discover: Runtime): how do the local models run?**
A: External local servers. ComfyUI already installed at `F:\AI\ComfyUI_windows_portable`.

**Q (discover: Integration): where does the option live?**
A: Mode card plus settings block, following the `video_mode` dispatch and the `LLM_*` config precedent.

**Q (discover: Time budget): acceptable wall time per video?**
A: Batch, no limit. Research finds the best performance/quality balance per stage, including non-mainstream models. The current Flux ComfyUI setup is the quality bar.

**Q (discover: Acceptance criteria): which observable checks count as done?**
A: Offline end-to-end run, no-key job start in local mode, docs/env examples. Zero-cost display kept as FR7.

**Q (`saasshorts.py:1409-1414`): which local talking-head recipe is the default?**
A: Wan2.2 i2v + LatentSync 1.5 (developer picked the recommended option).

**Q (`saasshorts.py:711` and `:986`): default image model for portraits and b-roll?**
A: Developer: "you decide, i need as realistic as possible but acceptable performance". Decided: per-stage split — Flux dev Q4 for actor portraits, Flux schnell Q4 for b-roll stills.

**Q (`saasshorts.py:795`): default local TTS server?**
A: Developer proposed OmniVoice (`k2-fsa/OmniVoice`). Research confirmed fit. Adopted via `omnivoice-server` (OpenAI-compatible surface), version pinned. Kokoro-FastAPI noted as fast-draft secondary on the same API shape.

**Q (model freshness): "isn't Wan2.2 quite old?" / "how about MiniMax?"**
A: Verified Wan2.2 is the newest open-weights Wan (2.5-3.0 are API-only). MiniMax H3 rejected: 33B does not fit 10 GB, and its license excludes EU/UK/KR/US outputs. Wan2.2 TI2V-5B stays the default. HunyuanVideo 1.5 kept as an optional A/B.

## Related Research
- `.rpiv/artifacts/research/2026-09-09_20-00-08_unified-ai-provider-config.md` — config chain and gate patterns shared by this feature
- `.rpiv/artifacts/research/2026-09-06_06-09-59_marsic-fork-parity-merge.md` — stage-ownership rules that the new adapters must respect

## Open Questions
FRD (carried verbatim): "None deferred by the developer. Per-stage model selection (TTS, lipsync, video workflow details) is deliberately delegated to the research phase." — model selection is now resolved in Developer Context; the items below remain open.
- LatentSync 1.6 on 10 GB: sources conflict (OOM below 18 GB vs 6-8 GB). One local test decides the upgrade from the pinned 1.5.
- HunyuanVideo 1.5 license: read the HF license file before any product use of the A/B candidate.
- No first-party RTX 3080 timing exists for Wan2.2 TI2V-5B. Log per-clip timings on the first runs; all speed figures are extrapolations.
- `omnivoice-server` is young and solo-maintained with an API-churn warning. Pin the version; the FastAPI sidecar around the `omnivoice` library is the fallback.
- `share_to_gallery` uploads to S3 (cloud) from inside the job (`app.py:8050+`). Strict offline implies hide or disable the checkbox in local mode. Exact treatment is a design decision.
- The BYOK badge at `dashboard/src/App.jsx:1222` needs copy that does not mislead in local mode.
- OmniVoice's bundled Higgs codec carries a Boson community license with a 100k annual-active-user cap. Self-hosted use is fine. A legal read is needed only if the product ships at scale.
