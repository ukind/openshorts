---
date: 2026-09-04T10:12:16+0700
author: Yogiswara Utama
commit: 35e9d7e
branch: main
repository: openshorts
topic: "Connect the OpenAI-compatible LLM provider to the dashboard frontend"
tags: [intent, frd, llm-provider, dashboard, byok, openai-compatible, frontend, settings, pire-browser]
status: ready
last_updated: 2026-09-04T10:12:16+0700
last_updated_by: Yogiswara Utama
---

# FRD: Connect the OpenAI-compatible LLM provider to the dashboard frontend

## Summary

Connect the OpenAI-compatible LLM provider (backend, shipped commits `30ce67e` through `35e9d7e`) to the dashboard frontend. A new AI Provider card in self-host Settings collects an endpoint URL, API key and model. The card stores the triple encrypted in the browser and sends it as the `X-LLM-*` header triple. The app front gate widens from "has a Gemini key" to "has any AI backend". Validation runs end-to-end through pire-browser against a local Python stub endpoint.

## Problem & Intent

The developer (maintainer) added an OpenAI-compatible LLM provider to the backend beside the existing Gemini API key. The frontend has no UI for it. Today, the developer cannot select or configure the new provider through the dashboard. The UI locks the developer to Gemini. Success looks like: the developer configures any OpenAI-compatible endpoint (Ollama, OpenRouter, vLLM) through Settings, tests the connection, runs generation jobs through it, and the app stops demanding a Gemini key.

## Goals

- Collect endpoint URL, API key and model in the dashboard Settings and send them as the `X-LLM-*` header triple.
- Report a server-side `LLM_*` configuration to the UI so no key input is demanded when the server already has one.
- Unblock the app for a user who has only an OpenAI-compatible provider and no Gemini key.
- Label the Gemini-only action (thumbnail image generation) inline rather than blocking the whole tab.
- Validate the full BE to FE journey end-to-end through pire-browser.

## Non-Goals

- Server-side persistence of provider config. Self-host has no auth (`app.py:106-121`), so a write endpoint would be world-writable.
- Centralizing BYOK headers in `apiFetch`. That would send the provider key on uploads and social posting.
- Any cloud or billing provider surface. `resolve_llm` returns `None` under `BILLING_ENABLED` (`app.py:158`).
- Fixing the unbounded `_clients` cache in `llm_client.py:130-147`. Pre-existing and orthogonal to this feature.

## Functional Requirements

1. The system SHALL provide an AI Provider settings card with three required fields: endpoint URL, API key and model. The card SHALL offer quick-fill presets for Ollama Cloud, local Ollama and OpenRouter.
2. The system SHALL refuse to save a half-configured provider. All three fields must be present (`llm_client.config_from` returns `None` for base and key with no model, `llm_client.py:150-178`).
3. The system SHALL store the provider triple as one encrypted blob in `localStorage` under `llmConfig_v1`. The system SHALL clear the stored blob when all three fields are empty.
4. The system SHALL send the `X-LLM-*` header triple on `/api/process`, Thumbnail Studio and AI Shorts requests. Each component builds its own headers beside the existing `X-Gemini-Key`, not inside `apiFetch`.
5. The system SHALL omit `X-LLM-Model` when the model field is blank. A whitespace-only model falls back to the server env chain (`tests/test_llm_client.py:707`).
6. The system SHALL expose `llmConfigured`, `llmModel` and `llmBaseUrl` through `/api/config` and `useAuth()`. The API key is never reported.
7. The system SHALL provide a `POST /api/llm/test` endpoint (self-host only) that validates a provider with one minimal live call. The endpoint returns `{ok, model, latencyMs}`.
8. The system SHALL widen the app front gate from "has a Gemini key" to "has any AI backend". The required-keys banner, header badge and modal copy must reflect Gemini or provider.
9. The system SHALL keep Thumbnail Studio usable on an LLM-only setup. Only the image-generation step carries a Gemini-specific inline note, not a whole-tab block.
10. The entire provider surface SHALL be absent under `BILLING_ENABLED`. `/api/config` reports all three fields off and `/api/llm/test` returns 404.
11. The system SHALL migrate the Gemini key from plaintext `localStorage` (`gemini_key`) to the encrypted `ENC:` convention (`geminiKey_v1`). The migration is one-way and runs once per browser.

## Non-Functional Requirements

- **Performance**: `POST /api/llm/test` must fail fast. A black-holed URL returns within 10 seconds, not the 300-second read timeout the real client uses (`llm_client.py:130`). The probe uses a one-off short-timeout client (connect 5 s, read 20 s), not the shared `_clients` cache.
- **Security**: The provider triple is browser-stored and header-carried, matching the `X-Gemini-Key` precedent. Self-host has no auth, so a server-side write endpoint is excluded. The API key never appears in `/api/config` or `/api/llm/test` responses. `POST /api/llm/test` shares the metadata-probe rate limiter (15 per hour, `app.py:260`).
- **UX / Accessibility**: The card follows the ElevenLabs and fal.ai card markup pattern in `App.jsx`. A server-configured install shows a collapsed status block with an "override with my own endpoint" button. The form stays open if the browser already has a saved override.
- **Reliability**: `probe()` raises exactly what `chat()` would raise (`LlmError`, `LlmTransientError`, `GeminiBlockedError`). A green Test connection means the next job resolves the same way. Provider failures surface the provider own error text verbatim.

## Constraints & Assumptions

- The backend is complete (commits `30ce67e` through `35e9d7e`). This is frontend-only work plus two small backend additions: the `/api/config` fields and the `/api/llm/test` endpoint.
- Self-host has no authentication (`app.py:106-121`). This constrains all persistence decisions.
- The 2026-08-30 plan and design (decisions D1 through D9) are the baseline. This FRD adopts them with one delta: pire-browser e2e validation.
- Dev stack: `./dev.sh` starts `uvicorn` on port 8000 and Vite on port 5173. Vite proxies `/api` to the backend (`vite.config.js:33-39`).
- The pire-browser e2e test uses a local Python stub (`http.server` on `localhost:11434` returning valid chat-completions JSON). No real provider account is needed.
- Phases 1 through 5 merge as one unit. Precedent: commits `29681ba` (fal.ai) and `03477d4` (ElevenLabs) ship backend and frontend together.

## Acceptance Criteria

- [ ] Running `cd dashboard && npm run build` exits 0.
- [ ] Running `./.venv/Scripts/python.exe -m pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` exits 0.
- [ ] `grep -rn "X-LLM" dashboard/src/` returns matches in `App.jsx`, `LlmProviderCard.jsx`, `ThumbnailStudio.jsx`, `SaaShortsTab.jsx` and `lib/llm.js`.
- [ ] `grep -c "/api/llm/test" app.py` returns at least 1.
- [ ] `curl localhost:8000/api/config` with no `LLM_*` env returns `llmConfigured:false`, `llmModel:null`, `llmBaseUrl:null`.
- [ ] `curl localhost:8000/api/config` with `LLM_BASE_URL` plus `LLM_API_KEY` plus `LLM_MODEL` set returns `llmConfigured:true`, model and base URL reported. A grep for the key value finds nothing.
- [ ] pire-browser: open `http://localhost:5173`, navigate to Settings, see the AI Provider card beside the Gemini key card.
- [ ] pire-browser: fill the three fields (`http://localhost:11434/v1`, any key, `stub-model`), click "Test connection" and see a success response with a `latencyMs`.
- [ ] pire-browser: click Save, reload the page, and the three values persist (encrypted under `llmConfig_v1` in `localStorage`).
- [ ] pire-browser: submit a clip generation job and confirm the outgoing `/api/process` request carries `X-LLM-Base-Url`, `X-LLM-Key` and `X-LLM-Model` headers.
- [ ] pire-browser: with only the provider configured (no Gemini key), the "Required API keys missing" banner does not demand a Gemini key.
- [ ] pire-browser: Thumbnail Studio opens and is usable. Only the image-generation step shows a Gemini-specific note.
- [ ] Restart with `BILLING_ENABLED=1`: `POST /api/llm/test` returns 404 and `/api/config` reports `llmConfigured:false`.

## Recommended Approach

Implement the 2026-08-30 plan five phases as one unit: backend capability channel plus probe endpoint, browser state plus migration, AI Provider settings card, app gate widening, and per-feature capability split. Add pire-browser as the e2e validation harness against a local Python stub endpoint. The plan artifact at `.rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md` is the implementation baseline.

## Decisions

### Adopt existing plan with pire-browser delta

**Question**: The probe found a complete, triaged plan for exactly this FE work from 2026-08-30. None of it shipped. How should this FRD treat it?
**Recommended**: Execute existing plan
**Chosen**: Adopt plan with deltas
**Rationale**: The plan five phases and nine design decisions (D1 through D9) are the baseline. The one delta is pire-browser e2e validation, which the plan covers only with curl and DevTools.

### Provider config storage: browser plus headers

**Question**: From the probe I inferred the provider triple stays browser-stored (encrypted `localStorage` `llmConfig_v1`) and travels per-request as the `X-LLM-*` header triple. Design D1 rejected server-side persistence. Keep this, or change it?
**Recommended**: Keep browser plus headers
**Chosen**: Keep browser plus headers
**Rationale**: evidence: `app.py:106-121` (self-host has no auth, server-side write would be world-writable) plus D1 confirmed. Matches `X-Gemini-Key` precedent (`App.jsx:211,792`).

### FE scope: full plan scope

**Question**: The plan scope covers the settings card and the app-wide gate change. Keep that full scope, or trim it?
**Recommended**: Full plan scope
**Chosen**: Full plan scope
**Rationale**: A minimal card-plus-headers scope leaves the "Required API keys missing" banner still demanding Gemini. An LLM-only user sees a block despite a working provider. The gate widening is necessary for the feature to unblock the app.

### E2E validation: full pire-browser journey

**Question**: Your input adds pire-browser e2e validation. What should that cover?
**Recommended**: Full browser journey
**Chosen**: Full browser journey
**Rationale**: The plan manual verification uses curl and DevTools only. A pire-browser journey exercises the real Settings card flow, Test connection and a generation request. This covers the full BE to FE seam.

### E2E stub: local Python stub

**Question**: The pire-browser e2e journey needs an OpenAI-compatible endpoint. What should serve as that endpoint?
**Recommended**: Python stub
**Chosen**: Python stub
**Rationale**: The plan manual-verification section already ships a 10-line `http.server` stub on `localhost:11434`. Zero deps, zero network. It stands in for the local-Ollama preset the card ships with.

## Open Questions

None. All branches resolved during the interview.

## Suggested Follow-ups

- The unbounded `_clients` cache in `llm_client.py:130-147` grows per base URL. Pre-existing, already reachable via the six live endpoints, and orthogonal to this feature.
- `cloud/alerts.py::_classify_failure` was corrected twice in production (`77905a9`, `730f7de`). The `"LLM provider"` prefix strings must stay verbatim.
- `ResultCard.jsx` has six Gemini-pinned endpoints (class C) that the plan deliberately does not touch.

## References

- Input: "previously i add open ai compatible provider beside gemini api key. but its only in the backend the UI or the front end is not yet added. now connect the BE and the FE especially for my previous changes. use pire browser to validate your changes end to end between the front end and the backend"
- Parent design: `.rpiv/artifacts/designs/2026-08-30_16-58-07_connect-llm-provider-frontend.md`
- Implementation plan: `.rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md`
- Backend commits: `30ce67e`, `ec60f4f`, `e96407b`, `da48ff2`, `35e9d7e`
