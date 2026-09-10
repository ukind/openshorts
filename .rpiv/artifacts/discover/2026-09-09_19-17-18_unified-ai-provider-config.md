---
date: 2026-09-09T19:17:18+0700
author: Yogiswara Utama
commit: e5ffe7c
branch: main
repository: openshorts
topic: "Unified AI Provider Config"
tags: [intent, frd, ai-provider, llm-client, dashboard-settings, byok, reroute]
status: ready
last_updated: 2026-09-09T19:17:18+0700
last_updated_by: Yogiswara Utama
---

# FRD: Unified AI Provider Config

## Summary

Replace the two AI-provider surfaces in the dashboard settings sidebar with ONE unified OpenAI-compatible panel (the "OpenAI / Compatible API BYOK" card, absorbing the "AI Provider" card's affordances). The unified panel's single endpoint triple feeds every LLM stage: pipeline stages via `X-OpenAI-*` as today, satellite stages via derived `X-LLM-*` headers, and four formerly Gemini-only stages via targeted backend reroutes. Keys move to encrypted localStorage.

## Problem & Intent

The settings sidebar has two provider cards that serve the same fundamental purpose:

- **"AI Provider"** (`LlmProviderCard.jsx`) — "Any OpenAI-compatible endpoint (Ollama, OpenRouter, vLLM, …) can power clip analysis, titles and descriptions — a Gemini key stays optional."
- **"OpenAI / Compatible API" BYOK** (inline in `App.jsx`) — "when set, these override server env OPENAI_MODEL / OPENAI_BASE_URL / OPENAI_API_KEY."

Developer's words, verbatim:

> "i just wanna single source of truth, especially in the UI. im prefer the OpenAI / Compatible API BYOK menu. but does if we set via this menu, every feature related to this is well integrated"

The probe answered that question: **no, not today.** The two cards map 1:1 to two backend config families — the BYOK card feeds only the `ai_provider` family (pipeline); satellites (layout picker, thumbnail text, SaaS) read `X-LLM-*` only and silently ignore `X-OpenAI-*` (`app.py:5866-6184`, `:6467`); five stages are Gemini-only. This feature makes "set one menu" integrate everything except live web search.

## Goals

- Exactly one AI-provider card in the settings sidebar — single source of truth, especially in the UI.
- Setting only the unified panel powers: pipeline stages (clip score/detail, deep analysis, vision pass, transcript polish, VOD metadata, voiceover captions, game profiles), satellite stages (layout picker, thumbnail titles/refine/description/concepts, SaaS analyze/scripts), and the rerouted Gemini-only stages (hook grounding, screencast width detection, editor effects via frames, thumbnail image generation via `/v1/images/generations` when the endpoint supports it).
- No plaintext API keys in localStorage.
- Invisible migration: existing users keep their configured endpoint and key with zero re-entry.

## Non-Goals

- **Merging the dispatch systems.** `ai_provider.py` and `llm_client.py` stay separate; `tests/test_no_double_route.py` keeps pinning ownership (CLAUDE.md standing rule: the side whose base you take owns the stage).
- **Backend resolver changes.** `resolve_openai` / `resolve_llm` / `resolve_gemini` (`app.py:137-207`) and the two header families stay as-is. Unification lives in the dashboard.
- **Rerouting SaaS grounded web research** (`saasshorts.py:49-60`). It needs live Google Search grounding; the OpenAI-compatible standard has no equivalent.
- **Fixing the external-caller trap.** curl / MCP / API-key callers sending only `X-OpenAI-*` will still be ignored by satellite endpoints — accepted consequence of the FE-bridge decision (D5).
- **Fixing the resume caveat.** BYOK values dropped on redeploy resume (`app.py:876` rebuilds env from `os.environ`) is pre-existing behavior for both families — follow-up.
- **Changing MCP or server env semantics.** `mcp_server.py:60-67` forwarding and `LLM_*` / `OPENAI_*` / `AI_PROVIDER` env behavior stay untouched.

## Functional Requirements

1. The settings sidebar SHALL show exactly one AI-provider card: API key, model (with dropdown), base URL — plus endpoint presets, a "Test connection" button, the model dropdown fetch, and a "Configured on the server" badge.
2. The unified card's values SHALL persist encrypted with the same encryption scheme as `llmConfig_v1` (`App.jsx:703`) **under a new storage key** (e.g. `aiProviderConfig_v1`). The legacy keys `llmConfig_v1` and `openai_key` / `openai_model` / `openai_base_url` (`App.jsx:246-250`, `:709-713`) are import sources only (FR4/FR5) and SHALL NOT remain after first load.
3. Every FE call site that triggers an LLM stage SHALL attach the unified triple as BOTH header families: `X-AI-Provider` + `X-OpenAI-Key/Model/Base-Url`, and derived `X-LLM-Base-Url` / `X-LLM-Key` / `X-LLM-Model` (model header omitted when empty, per the existing rule `lib/llm.js:13-15`). Call sites: `handleProcess` (`App.jsx:1059-1077`), `ResultCard.jsx` (`/api/edit` — today sends only `X-Gemini-Key`), `ThumbnailStudio.jsx`, `SaaShortsTab.jsx`, `VoiceOverPage.jsx`, `CreateEditProfileModal.jsx`, the LLM test call. The inline duplicated builders (`VoiceOverPage.jsx:347-352`, `CreateEditProfileModal.jsx:253-263`) fold into one shared helper.
4. `LlmProviderCard.jsx` and the `llmConfig` state SHALL be deleted. On first load after upgrade, existing `llmConfig_v1` values import into the unified store once, then the old key is removed.
5. Existing plaintext `openai_*` values SHALL import into the encrypted unified store on first load and be deleted from localStorage.
6. Editor effects (`/api/edit`, `/api/effects/generate`) SHALL accept the unified config and use a frames-based analysis path (sampled frames as images, the `layout_picker` pattern) when no Gemini key is present. The Gemini File API video path stays when a Gemini key exists.
7. Hook grounding (`hook_grounding.py`) and screencast width detection (`screencast_layout.py`) SHALL use the job's OpenAI-compatible env (already handed to the subprocess as `OPENAI_*`, `app.py:2569-2575`) for their vision calls when Gemini is absent; skip gracefully when the configured model lacks vision (preserve the existing skip semantics, `hook_grounding.py:153-156`).
8. Thumbnail image generation SHALL try `/v1/images/generations` on the unified endpoint first and fall back to the Gemini image model. The hard Gemini gate on `/api/thumbnail/generate` (`app.py:6045-6046`) SHALL accept a unified endpoint as an alternative.
9. `/api/config` SHALL report the server's OpenAI-family env state (e.g. `openaiConfigured` / `openaiModel`) alongside the existing `llmConfigured` family, so the badge reflects the server pipeline env. Read-only reporting — no resolver changes.
10. "Test connection" SHALL validate the unified triple via `POST /api/llm/test` using the derived `X-LLM-*` headers; the model dropdown SHALL list models via `GET /api/openai/models?base_url=…` using the card's own values (both endpoints already work with those inputs).

## Non-Functional Requirements

- **Performance**: no added server round-trips — derivation is client-side. Frames-based editor analysis costs frame sampling comparable to `layout_picker` (12 frames, not video upload).
- **Security**: keys live only in the browser, encrypted; sent per request, never stored server-side (existing contract preserved). BYOK values never logged.
- **UX / Accessibility**: one card, no duplicated mental model; migration invisible; degradation (no vision / no image endpoint) skips or falls back rather than surfacing errors.
- **Reliability**: Gemini remains fallback for image generation; vision-less models cause a clean skip of rerouted stages, never a failed job; `ai_backend_available()` job gate semantics unchanged (`app.py:213-228`).

## Constraints & Assumptions

- `tests/test_no_double_route.py` MUST keep passing — dispatch ownership unchanged.
- Header families and resolver functions on the wire stay backwards compatible: existing clients sending only one family keep working exactly as today.
- Assumption: the per-job provider toggle ("AI provider for this job", `App.jsx:2171-2185`, sets `X-AI-Provider`) stays as the pipeline-provider selector (gemini vs unified endpoint); satellites follow the endpoint triple regardless of the toggle value.
- The job subprocess env handover already carries `OPENAI_*` (`app.py:2569-2575`) and the `X-LLM-*` triple as `LLM_*` env (`app.py:2591-2594`) — assumption: the derived `X-LLM-*` headers on `/api/process` therefore reach the layout picker inside jobs with no further change (verify in research).
- Assumption: user endpoints may lack vision or image generation; every rerouted stage must degrade, not fail.
- Constraint: the rerouted Gemini-only modules split across two contexts — in-job subprocess (env-based: hook grounding, screencast detection) and direct API endpoints (header-based: `/api/edit`, `/api/thumbnail/generate`). Both must read the unified config.

## Acceptance Criteria

- [ ] Settings sidebar shows exactly ONE provider card; `grep -r "LlmProviderCard" dashboard/src` returns no references.
- [ ] With NO Gemini key, NO server `LLM_*`/`OPENAI_*` env, and only the unified panel set to a local endpoint: a full `/api/process` job completes and the layout-picker log line shows the unified endpoint made the choice (satellite coverage through derived headers).
- [ ] Thumbnail Studio titles generate via the unified endpoint with no Gemini key present.
- [ ] `/api/edit` produces an effects plan with only the unified panel set (frames path), no `X-Gemini-Key` attached.
- [ ] Thumbnail image generation succeeds via `/v1/images/generations` on an endpoint that implements it, and falls back to Gemini (or skips with a clear log line) when it does not.
- [ ] After first load post-upgrade: `openai_key` / `openai_model` / `openai_base_url` and `llmConfig_v1` are gone from localStorage, and the unified card still shows the imported values.
- [ ] Hook grounding runs its frame call against the unified endpoint when `GEMINI_API_KEY` is absent (log line), or skips cleanly with a non-vision model.
- [ ] `npm run lint` (strict, `--max-warnings 0`) passes; `pytest tests/test_no_double_route.py` passes.

## Recommended Approach

FE-only unification at the dashboard layer: one encrypted store + one shared header builder emitting `X-OpenAI-*` and derived `X-LLM-*` on every LLM-carrying call site; delete `LlmProviderCard` with one-time migrations from both legacy stores; plus targeted backend reroutes in the four Gemini-only modules (editor frames path; hook grounding + screencast detection reading job `OPENAI_*` env; thumbnail image-gen with `/v1/images/generations` first) and a read-only `openaiConfigured` addition to `/api/config`. No resolver or dispatch changes.

## Decisions

### D0 — Intent: single source of truth, BYOK menu preferred
**Question**: What problem does having two separate AI-provider config paths cause today, and who hits it? (intent — open)
**Recommended**: n/a — intent question
**Chosen**: "i just wanna single source of truth, especially in the UI. im prefer the OpenAI / Compatible API BYOK menu. but does if we set via this menu, every feature related to this is well integrated"
**Rationale**: developer's own framing; the feature exists to make the answer to that last clause "yes".

### D1 — Satellites must be covered
**Question**: The satellite stages read X-LLM-* only and silently ignore the BYOK panel's X-OpenAI-* headers (`app.py:5866-6184`, `:6467`). Keep this for the feature, or change it as part of the work?
**Recommended**: Cover satellites too
**Chosen**: Cover satellites too
**Rationale**: evidence: `app.py:5866-6184`, `app.py:6467` + confirmed — without it "one menu powers everything" is false.

### D2 — Maximal Gemini reroute, capability-bounded
**Question**: Which Gemini-only stages get rerouted to the unified OpenAI-compatible config? (developer asked first: "what does it need the gemini model in the first place? what model capabilities needed")
**Recommended**: Vision set + editor
**Chosen**: Vision set + editor **and** thumbnail image-gen via `/v1/images/generations` with Gemini fallback. SaaS grounded research stays Gemini-only.
**Rationale**: capability taxonomy from source — hook grounding + screencast detection need only vision (`hook_grounding.py:124-152`, `screencast_layout.py:172-187`); editor effects degrade to frames (`editor.py:29-34` File API → frames, the `layout_picker` pattern); image-gen is endpoint-dependent; search grounding (`saasshorts.py:60`) has no OpenAI-compatible equivalent.

### D3 — Keep both dispatch modules
**Question**: The two-dispatcher split (`ai_provider.py` vs `llm_client.py`) is deliberate, pinned by `tests/test_no_double_route.py` and the CLAUDE.md rule. Keep the two modules and unify only config resolution + UI?
**Recommended**: Keep modules, unify config
**Chosen**: Keep modules, unify config
**Rationale**: evidence: `tests/test_no_double_route.py`, CLAUDE.md "Stage ownership" section + confirmed — smallest blast radius.

### D4 — Encrypted storage
**Question**: Panel 1 encrypts its key blob (`App.jsx:703`) while the surviving Panel 2 stores `openai_key` in plaintext (`App.jsx:709-713`). Which storage does the unified panel use?
**Recommended**: Encrypted
**Chosen**: Encrypted (`llmConfig_v1` scheme)
**Rationale**: evidence: `App.jsx:703` vs `App.jsx:709-713` + confirmed.

### D5 — Config seam: FE bridge (dual headers)
**Question**: How does the unified panel's config reach the satellite stages — where does the single source of truth live?
**Recommended**: BE resolver fallback (`resolve_llm` honors `X-OpenAI-*` when `X-LLM-*` absent)
**Chosen**: FE bridge — the dashboard derives and emits both header families from the one panel; backend resolvers untouched
**Rationale**: developer prioritizes UI-level single source of truth ("especially in the UI"); accepted tradeoff: external callers (curl/MCP/API-key) setting only `X-OpenAI-*` still need both families.

### D6 — Unified card affordances
**Question**: Which affordances does the unified panel keep? (multi-select)
**Recommended**: all four listed
**Chosen**: Presets + Test connection + Model dropdown + Server-config badge
**Rationale**: evidence: `LlmProviderCard.jsx:18-22`, `:58`, `:199-205`; `App.jsx:1667-1670` + multi-select picked all.

### D7 — Panel 1 deleted, values migrated
**Question**: What happens to LlmProviderCard (the "AI Provider" card with encrypted `llmConfig_v1` storage)?
**Recommended**: Delete + migrate
**Chosen**: Delete `LlmProviderCard.jsx` and `llmConfig` state; one-time import of `llmConfig_v1` into the unified store, then remove the old key
**Rationale**: evidence: `App.jsx:311-318`, `App.jsx:703` + confirmed — cleanest UI, zero re-entry.

### D8 — One-time plaintext import
**Question**: Existing users have Panel 2's key in plaintext localStorage today. Migrate it into the encrypted unified store automatically?
**Recommended**: One-time import
**Chosen**: One-time import; delete plaintext keys after import
**Rationale**: evidence: `App.jsx:246-250`, `App.jsx:709-713` + confirmed.

## Open Questions

None — no items were explicitly deferred during the interview.

## Suggested Follow-ups

- BYOK config dropped on redeploy resume: env rebuilt from `os.environ` only (`app.py:876`), headers reach jobs only via the env handover (`app.py:2591-2594`) — affects both families today, pre-existing.
- External-caller trap: API-key/MCP/curl callers sending only `X-OpenAI-*` are silently ignored by satellite endpoints (`app.py:5866-6184`) — accepted consequence of D5; a one-line BE fallback would close it later if wanted.
- Inline duplicated header builders in `VoiceOverPage.jsx:347-352` and `CreateEditProfileModal.jsx:253-263` — fold into the shared builder during implementation (FR3).
- Long-term consolidation candidate: duplicate server-side resolution `resolve_openai` vs `resolve_llm` (`app.py:152` / `app.py:180`) — deliberately kept by D3.

## References

- Input: free-text feature description (this session's `/skill:discover` invocation).
- Probed source: `dashboard/src/components/LlmProviderCard.jsx`, `dashboard/src/App.jsx`, `dashboard/src/lib/llm.js`, `dashboard/src/components/ResultCard.jsx`, `dashboard/src/components/ThumbnailStudio.jsx`, `dashboard/src/components/SaaShortsTab.jsx`, `dashboard/src/components/VoiceOverPage.jsx`, `dashboard/src/components/CreateEditProfileModal.jsx`, `app.py`, `ai_provider.py`, `llm_client.py`, `layout_picker.py`, `thumbnail.py`, `hook_grounding.py`, `screencast_layout.py`, `editor.py`, `saasshorts.py`, `mcp_server.py`, `tests/test_no_double_route.py`.
- `CLAUDE.md` — "Stage ownership: `ai_provider.py` vs `llm_client.py`" section.
