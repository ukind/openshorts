---
date: 2026-09-09T20:00:08+0700
author: Yogiswara Utama
commit: e5ffe7c
branch: main
repository: openshorts
topic: "Unified AI provider config — single source of truth BYOK panel"
tags: [research, codebase, ai-provider, llm-client, byok, frontend, thumbnails, editor, hook-grounding, screencast]
status: ready
last_updated: 2026-09-09T20:00:08+0700
last_updated_by: Yogiswara Utama
---

# Research: Unified AI provider config — single source of truth BYOK panel

## Research Question
"i just wanna single source of truth, especially in the UI. im prefer the OpenAI / Compatible API BYOK menu. but does if we set via this menu, every feature related to this is well integrated" (discover D0). Today the answer is no: the BYOK card feeds only the `ai_provider` pipeline family; satellites read `X-LLM-*` only (`app.py:5866-6184`, `:6467`); five stages are Gemini-only. This research maps every config path, call site, and reroute target needed to make one menu power everything except live web search.

## Summary
- Two backend config families exist and stay (D3): `ai_provider.py` (pipeline, `AI_PROVIDER`/`OPENAI_*`/`GEMINI_*`) vs `llm_client.py` (satellites, `LLM_*`). Unification is config + UI, never dispatch — pinned by `tests/test_no_double_route.py`.
- The FE bridge works because both families are already per-job header-resolvable: `resolve_openai` (`app.py:152-165`) and `resolve_llm` (call sites `app.py:2082`, `:5857`, `:5964`, `:6034`, `:6176`, `:6460`). `/api/process` already consumes both (`app.py:2442-2444`).
- The FRD's job-env assumption (discover line 74) is verified: `app.py:2561-2587` builds the job env including `OPENAI_*` (`:2569-2575`) and `LLM_*` (`:2591-2594`); `app.py:1912` injects it into the subprocess; `tests/test_no_double_route.py:340-348` pins the contract. Derived `X-LLM-*` headers reach in-job satellite stages (layout picker, screencast, hook grounding) today.
- Four Gemini-only stages need reroute: `hook_grounding.py` (frames input, easiest), `screencast_layout.py` (whole-video Files API, medium), `editor.py` (whole-video Files API, dual input-mode per D2), `thumbnail.py` image path (endpoint-dependent `/v1/images/generations` + Gemini fallback). SaaS grounded research stays Gemini-only (no equivalent).
- FE cleanup: delete `LlmProviderCard.jsx` (sole `X-LLM-*` emitter at `:350-352`, zero other references), fold inline header builders, migrate three legacy localStorage keys into the encrypted store (developer checkpoint extended the FRD's two with `gemini_key` at `App.jsx:709`).

## Detailed Findings

### FE config layer — three stores, one card to delete
- `dashboard/src/App.jsx:693` writes `llmConfig_v1` (encrypted) — the unified store's scheme (D4).
- `App.jsx:703` writes `openai_*` plaintext; `App.jsx:709` writes `gemini_key` plaintext. Both migrate into the encrypted store on first load (D8 + checkpoint below); the import-site precedent is `App.jsx:246-250`.
- `App.jsx:311-318` holds the `llmConfig` state that dies with the card (D7).
- `dashboard/src/components/LlmProviderCard.jsx:350-352` is the only FE emitter of `X-LLM-*` headers. No other file references the card's save path — deletion blast radius is one stale test reference. Affordances to preserve in the unified card: presets `:18-22`, model dropdown `:58`, server-config badge state `App.jsx:1667-1670` (D6).
- Header assembly today: `handleProcess` `App.jsx:1059-1077` sends `X-Gemini-Key`, `X-AI-Provider`, `X-OpenAI-*`. The dual-family contract is documented at `App.jsx:1628-1630` (empty model ⇒ header omitted — the comment there is the live documentation; see Insights #5).
- Duplicated inline builders to fold into the shared builder during implementation: `dashboard/src/components/VoiceOverPage.jsx:347-352`, `dashboard/src/components/CreateEditProfileModal.jsx:253-263`.

### BE resolvers — both families already per-job
- `app.py:152-165` `resolve_openai`: per-job `X-OpenAI-Key/Model/Base-Url` with env `OPENAI_*` fallback.
- `resolve_llm` declared at `app.py:180`, consumed at `:2082`, `:5857`, `:5964`, `:6034`, `:6176`, `:6460` — satellite endpoints (layout picker, thumbnail text, SaaS) read `X-LLM-*` only; a job sending only `X-OpenAI-*` is silently ignored there today (accepted D5 tradeoff).
- `ai_backend_available` (`app.py:213-228`) is the single job gate — any one of Gemini key / per-job openai triple / BYOK `X-LLM-*` / server `LLM_*` env starts a job. Unchanged by this feature.

### Job env handover (FRD assumption verified)
- `app.py:2561-2587` builds the job env: `OPENAI_*` at `:2569-2575`, `LLM_*` at `:2591-2594` (derived from per-job headers). `app.py:1912` passes it to the subprocess. `tests/test_no_double_route.py:340-348` pins the contract. Consequence: rerouting an in-job Gemini stage to the unified config = read the job's `OPENAI_*` env, exactly like `main.py` pipeline stages already do.
- Pre-existing gap (FRD follow-up): on redeploy resume the env is rebuilt from `os.environ` only (`app.py:876`) — BYOK headers reach resumed jobs via the env handover, affecting both families today.

### Dispatch layer
- `ai_provider.py:621-649` `create_ai_provider` selects gemini|openai per job; `ai_provider.py:584` `probe_vision_support` is the capability probe precedent the reroutes reuse.
- Pipeline dispatch sites in `main.py`: `:1757` (clip detail), `:2045`, `:2536`, `:2797`, `:2965-3011` (vision pass), `:3167` (deep analysis retry loop). All already dual-provider.
- Satellite dispatch through `llm_client` (layout picker, thumbnail text, SaaS analyze/scripts) — no `get_code_snippet` needed; the split is pinned by `tests/test_no_double_route.py` and the CLAUDE.md "Stage ownership" rule: the side whose base you take owns the stage.

### Reroute target 1 — `hook_grounding.py` (easy, frames input)
- Input is already frames: 3 frames at 1024px from `screencast`/`wide`/`inset` stretches (`sample_times`/`frames_at`). No Files API, no video upload.
- Reads `GEMINI_API_KEY` from env at `:148`; imports `gemini_worker` at `:128`, `:154`. Sole caller `main.py:3700`. Skip semantics (log one line, keep transcript hook) at `:153-156` — the degradation rule for a vision-less unified endpoint.
- Tests stub `_ask_gemini` — the reroute keeps that seam. Reroute shape: read job `OPENAI_*` env via the `ai_provider` path, guard with `probe_vision_support` (`ai_provider.py:584`), skip cleanly when no vision (NFR: degrade, never fail).

### Reroute target 2 — `screencast_layout.py` (medium, whole-video Files API)
- `:186-206` uploads the whole video through the google.genai Files API; `:217-219` defines `WideContentResponse` (per-range `width_fraction`). Runs in-job (reframe_v2 subprocess), so the job env from `app.py:2591-2594` is already present.
- Capability need is vision + structured ranges (FRD D2 evidence `:172-187`). Reroute per D7: read job `OPENAI_*` env; frames-based sampling replaces the Files upload (the `layout_picker` 12-frames-at-1024px pattern — same decision-forcing shape: closed choices, not measurements).

### Reroute target 3 — `editor.py` (medium-hard, dual input mode)
- `VideoEditor.__init__(api_key)` at `:28-29`; model chain `GEMINI_MODEL_EDITOR` → `GEMINI_MODEL` at `:30-32`; Files upload at `:46`; the EditPlan call at `:131-143` sends `contents=[file_obj, prompt]` — the only true video-native prompt in the four targets.
- Effects list at `:221-224`; filter-string response at `:386-389` is text-only — below that line the stage is provider-agnostic.
- Callers all use module-level functions with no key argument: `app.py:3275` (import), `:3550` (`upload_video`), `:3578` (`get_ffmpeg_filter`), `:3583` (`apply_edits`), `:4686` + `:4726-4727` (effects/generate path). The key is sourced inside the module — the reroute changes only the module's provider selection, zero caller changes.
- Reroute per D6 decision shape: when a Gemini key exists keep the File API path; when absent go frames-based (`layout_picker` pattern). Dual-input precedent already in-repo: `main.py:2198` switches on `input_mode == "native_video"`.

### Reroute target 4 — `thumbnail.py` (split: text already dual-path, image is the gate)
- Text side is already the in-repo template for one-stage-two-providers: when `llm_config` is present it dispatches through `llm_client.chat` (`:81-92`, `:132-136`, `:754-758`), else the Gemini `genai` client (`:92`, `:136`, `:760`). `TEXT_MODEL` = `GEMINI_MODEL_THUMBNAIL`, default `gemini-3.7-flash` (`:17`) — deliberately not flash-lite (creative titles).
- Image side is Gemini-hard: `IMAGE_MODEL` default `gemini-3.1-flash-image` (`:18`); `:603` calls `genai` unconditionally; `app.py:6045-6046` hard-gates `/api/thumbnail/generate` on a Gemini key.
- Reroute per D8: try `/v1/images/generations` on the unified endpoint, fall back to Gemini; the hard gate must accept the unified endpoint. Capability note: OpenAI-compatible image endpoints vary — probe or degrade to a clean "image model unavailable" state, mirroring the skip semantics of `hook_grounding.py:153-156`.

### MCP and external callers
- `mcp_server.py:338-345` forwards the caller's `X-LLM-*` BYOK headers through the in-process `ASGITransport` — satellite stages work over MCP unchanged. External API-key/curl callers sending only `X-OpenAI-*` stay ignored by satellites (D5 tradeoff, FRD follow-up: a one-line BE fallback could close it later).

## Code References
- `dashboard/src/App.jsx:246-250` — first-load import site (migration precedent)
- `dashboard/src/App.jsx:311-318` — `llmConfig` state (dies with card)
- `dashboard/src/App.jsx:693` — `llmConfig_v1` encrypted write (unified store scheme)
- `dashboard/src/App.jsx:703` — `openai_*` plaintext write (migrates)
- `dashboard/src/App.jsx:709` — `gemini_key` plaintext write (migrates, per checkpoint)
- `dashboard/src/App.jsx:1059-1077` — `handleProcess` header assembly
- `dashboard/src/App.jsx:1628-1630` — dual-family header contract comment
- `dashboard/src/App.jsx:1667-1670` — server-config badge state
- `dashboard/src/components/LlmProviderCard.jsx:18-22` — presets
- `dashboard/src/components/LlmProviderCard.jsx:58` — model dropdown
- `dashboard/src/components/LlmProviderCard.jsx:199-205` — card affordances
- `dashboard/src/components/LlmProviderCard.jsx:350-352` — sole `X-LLM-*` emitter
- `dashboard/src/components/VoiceOverPage.jsx:347-352` — duplicated inline header builder
- `dashboard/src/components/CreateEditProfileModal.jsx:253-263` — duplicated inline header builder
- `dashboard/src/lib/llm.js:13-15` — shared builder (FRD cites the empty-model rule here; see Insights #5)
- `app.py:152-165` — `resolve_openai`
- `app.py:180` — `resolve_llm` declaration
- `app.py:213-228` — `ai_backend_available` single job gate
- `app.py:876` — resume env rebuild from `os.environ` only
- `app.py:1912` — job env → subprocess
- `app.py:2082`, `app.py:5857`, `app.py:5964`, `app.py:6034`, `app.py:6176`, `app.py:6460` — `resolve_llm` consumers
- `app.py:2442-2444` — `/api/process` consumes both families
- `app.py:2561-2594` — job env build (`OPENAI_*` `:2569-2575`, `LLM_*` `:2591-2594`)
- `app.py:3275`, `app.py:3550`, `app.py:3578`, `app.py:3583`, `app.py:4686`, `app.py:4726-4727` — editor module-level call sites
- `app.py:6045-6046` — hard Gemini gate on `/api/thumbnail/generate`
- `ai_provider.py:584` — `probe_vision_support`
- `ai_provider.py:621-649` — `create_ai_provider` factory
- `main.py:1757`, `main.py:2045`, `main.py:2536`, `main.py:2797`, `main.py:2965-3011`, `main.py:3167` — pipeline dispatch sites
- `main.py:2198` — `input_mode == "native_video"` dual-input precedent
- `main.py:3700` — hook grounding call site
- `hook_grounding.py:124-152` — capability evidence; `:128`, `:154` `gemini_worker` imports; `:148` `GEMINI_API_KEY` env read; `:153-156` skip semantics
- `screencast_layout.py:172-187` — capability evidence; `:186-206` Files API upload; `:217-219` `WideContentResponse`
- `editor.py:28-32` — ctor + model chain; `:46` upload; `:131-143` EditPlan call; `:221-224` effects; `:386-389` filter string
- `thumbnail.py:17-18` — text/image model envs; `:81-92`, `:132-136`, `:754-758` `llm_client` path; `:92`, `:136`, `:760` Gemini text path; `:603` Gemini image call
- `mcp_server.py:338-345` — MCP header forwarding
- `tests/test_no_double_route.py` — ownership split pin; `:340-348` job env contract
- `tests/test_llm_endpoints.py:221` — pins empty-model header behavior of the shared builder

## Integration Points

### Inbound References
- `dashboard/src/App.jsx:1059-1077` — `handleProcess` → `POST /api/process` (both header families)
- `dashboard/src/components/ResultCard.jsx` — → `POST /api/edit` (sends only `X-Gemini-Key` today; must gain dual families)
- `dashboard/src/components/ThumbnailStudio.jsx` — → `/api/thumbnail/*`
- `dashboard/src/components/SaaShortsTab.jsx` — → SaaS endpoints (`X-LLM-*`)
- `dashboard/src/components/VoiceOverPage.jsx` — voiceover caption path (inline builder `:347-352`)
- `dashboard/src/components/CreateEditProfileModal.jsx` — profile save path (inline builder `:253-263`)
- `mcp_server.py:338-345` — MCP tool calls forwarded in-process with caller auth headers

### Outbound Dependencies
- `editor.py:46` — google.genai Files API (video upload)
- `thumbnail.py:603` — google.genai image generation
- `hook_grounding.py:128`, `:154` — `gemini_worker` (shared Gemini call util)
- `screencast_layout.py:186-206` — google.genai Files API (video upload)
- `llm_client.py` — OpenAI-compatible `/v1/chat/completions` (satellites)

### Infrastructure Wiring
- Job env chain: `app.py:2561-2594` builds → `app.py:1912` injects → in-job stages read env; pinned by `tests/test_no_double_route.py:340-348`
- `/api/config` reports both families from `ai_backend_available` (`llmConfigured`/`llmModel`/`llmBaseUrl` + `localLlm` shape)
- CLAUDE.md "Stage ownership" section — merge rule: the side whose base you take owns the stage

## Architecture Insights
1. Config is the seam, not dispatch (D3). The two-dispatcher split is deliberate, test-pinned, and the smallest-blast-radius choice is to unify config resolution + UI only.
2. Dual input-mode is an established pattern: `main.py:2198` switches on `input_mode == "native_video"`. The editor reroute is a second instance of the same pattern, not a new concept.
3. Vision stages are already frame-based except the two Files-API users (`editor.py:46`, `screencast_layout.py:186-206`). For those, reroute = input-mode switch to the `layout_picker` 12-frames pattern; capability is preserved because both stages ask closed-choice questions, not measurements.
4. One emitter per family: `LlmProviderCard.jsx:350-352` is the only `X-LLM-*` source; after unification the shared builder in `lib/llm.js` becomes the single emitter of both families and the two inline builders fold in (FR3).
5. Stale citation found: the FRD cites `dashboard/src/lib/llm.js:13-15` for the empty-model-header rule; the live documentation is the comment at `App.jsx:1628-1630` and the behavioral pin is `tests/test_llm_endpoints.py:221`. Trust test pins over comments when they drift.
6. Migration pattern: one-time first-load import + delete of the legacy key — precedent `App.jsx:246-250`; this feature imports three legacy keys (`llmConfig_v1` marshals into the new store, `openai_*` and `gemini_key` from plaintext).
7. Env namespace law (CLAUDE.md): `LLM_*` configures satellites only; `AI_PROVIDER`/`OPENAI_*`/`GEMINI_*` configure the pipeline only. The rerouted stages cross from the satellite family to the pipeline family — they must read `OPENAI_*` (job env), never `LLM_*`, or `tests/test_no_double_route.py` fires.

## Precedents & Lessons
5 similar past changes analyzed (unified-provider phases and follow-ups).

### Precedent: OpenAI-compatible provider rollout (phases 1-5)
**Commit(s)**: `fde9338` — unified provider phase (2026-08/09); follow-ups `25222f5`, `e96407b`
**Blast radius**: pipeline (`ai_provider.py`, `main.py`), FE cards, MCP — layered in phases, not one commit

**Follow-up fixes**:
- `25222f5`, `e96407b` — post-phase corrections after the initial rollout

**Lessons from docs**:
- `.rpiv/artifacts/handoffs/2026-08-31_05-52-02_connect-llm-provider-phase1.md` — phase-1 provider connect handoff
- `.rpiv/artifacts/handoffs/2026-08-30_14-42-31_openai-compatible-llm-provider-implementation.md` — implementation notes

**Takeaway**: every provider-facing stage shipped with a capability probe or a clean skip path (`probe_vision_support` `ai_provider.py:584`; skip semantics `hook_grounding.py:153-156`) — vision-less endpoints must degrade, never fail. Follow each phase with a fix commit budget.

### Composite Lessons
- Capability-probe before dispatch, always (`fde9338` line of work; `ai_provider.py:584`). Rerouted stages without vision must skip with one log line, mirroring `hook_grounding.py:153-156`.
- Frames beat video for text-producing vision stages: 12 frames at 1024px ≈ 3k tokens regardless of source length (`layout_picker` precedent, CLAUDE.md) — the reroute input mode for `editor.py` and `screencast_layout.py`.
- Phase large provider changes; the family landed as phases 1-5 with follow-up fixes (`25222f5`, `e96407b`) — the four reroutes here should not ship in one phase.

## Historical Context (from `.rpiv/artifacts/`)
- `.rpiv/artifacts/discover/2026-09-09_19-17-18_unified-ai-provider-config.md` — the FRD this research answers (decisions D0-D8, requirements, NFRs)
- `.rpiv/artifacts/handoffs/2026-08-30_14-42-31_openai-compatible-llm-provider-implementation.md` — provider implementation handoff
- `.rpiv/artifacts/handoffs/2026-08-31_05-52-02_connect-llm-provider-phase1.md` — phase-1 connect handoff
- `.rpiv/artifacts/handoffs/2026-09-04_20-47-42_connect-llm-provider-frontend.md` — frontend provider work
- `.rpiv/artifacts/handoffs/2026-09-08_10-57-41_blueprint-candidate-detection-openai-compat.md` — candidate detection on OpenAI-compat endpoints

## Developer Context
**Q (discover: D0 — Intent: single source of truth, BYOK menu preferred): What problem does having two separate AI-provider config paths cause today?**
A: "i just wanna single source of truth, especially in the UI. im prefer the OpenAI / Compatible API BYOK menu. but does if we set via this menu, every feature related to this is well integrated"

**Q (discover: D1 — Satellites must be covered): Satellites read `X-LLM-*` only and ignore the BYOK panel's `X-OpenAI-*` (`app.py:5866-6184`, `:6467`). Cover them?**
A: Cover satellites too — without it "one menu powers everything" is false.

**Q (discover: D2 — Maximal Gemini reroute, capability-bounded): Which Gemini-only stages reroute?**
A: Vision set + editor AND thumbnail image-gen via `/v1/images/generations` with Gemini fallback. SaaS grounded research stays Gemini-only.

**Q (discover: D3 — Keep both dispatch modules): Unify dispatch or config?**
A: Keep modules, unify config — smallest blast radius, split stays test-pinned.

**Q (discover: D4 — Encrypted storage): Plaintext `openai_key` (`App.jsx:709-713`) vs encrypted `llmConfig_v1` (`App.jsx:703`)?**
A: Encrypted (`llmConfig_v1` scheme).

**Q (discover: D5 — Config seam: FE bridge (dual headers)): How does one panel reach both families?**
A: FE bridge — dashboard derives and emits both header families; backend resolvers untouched. Tradeoff: external callers setting only `X-OpenAI-*` still need both families.

**Q (discover: D6 — Unified card affordances): Which affordances survive?**
A: Presets + Test connection + Model dropdown + Server-config badge (all four).

**Q (discover: D7 — Panel 1 deleted, values migrated): Fate of `LlmProviderCard`?**
A: Delete the card and `llmConfig` state; one-time import of `llmConfig_v1` into the unified store, then remove the old key.

**Q (discover: D8 — One-time plaintext import): Existing plaintext `openai_*` users?**
A: One-time import into the encrypted unified store; delete plaintext keys after import.

**Q (`dashboard/src/App.jsx:709` — research checkpoint): `gemini_key` also sits in plaintext localStorage, but FRD requirements 4-5 migrate only `llmConfig_v1` and `openai_*`. Migrate it too?**
A: Migrate `gemini_key` too — same first-load import into the encrypted unified store. `X-Gemini-Key` call sites (e.g. `ResultCard.jsx`) unchanged; only storage moves.

## Related Research
- None yet — first research artifact for this topic.

## Open Questions
None — no items were explicitly deferred during the discover interview.

Carried-forward FRD follow-ups (accepted tradeoffs, not blockers):
- BYOK config dropped on redeploy resume: env rebuilt from `os.environ` only (`app.py:876`) — pre-existing, affects both families today.
- External-caller trap: API-key/MCP/curl callers sending only `X-OpenAI-*` are silently ignored by satellite endpoints (`app.py:5866-6184`) — accepted consequence of D5; one-line BE fallback could close it later.
- Inline duplicated header builders (`VoiceOverPage.jsx:347-352`, `CreateEditProfileModal.jsx:253-263`) — fold into the shared builder during implementation (FR3).
- Long-term consolidation candidate: duplicate server-side resolution `resolve_openai` vs `resolve_llm` (`app.py:152` / `app.py:180`) — deliberately kept by D3.
