---
date: 2026-09-06T04:31:13+0700
author: Yogiswara Utama
commit: de92009
branch: main
repository: openshorts
topic: "Optional Upload-Post API key"
tags: [intent, frd, dashboard, social-publish, upload-post, self-host]
status: ready
last_updated: 2026-09-06T04:31:13+0700
last_updated_by: Yogiswara Utama
---

# FRD: Optional Upload-Post API key

## Summary

Make the Upload-Post API key fully optional on both frontend and backend. On self-host, the key stops blocking video generation entirely; when a server-side key resolves (env var or cloud managed key), the UI never shows missing-key states; keyless publish attempts fail with a structured, machine-readable error that opens a contextual setup modal at the moment of intent; and the standing "Set your Upload-Post API key to use OpenShorts." banner is reworded into a soft, non-blocking suggestion.

## Problem & Intent

Developer's verbatim framing (skill input):

> the UI there is this blocking : Upload-Post API Key — Required to publish your clips to TikTok, Instagram Reels, and YouTube Shorts. Free tier available, no credit card needed. Register at app.upload-post.com / Connect your TikTok, Instagram, or YouTube accounts / Go to API Keys and generate one / Paste it below. **make this optional for frontend and backend**

Intent answer (Step 2, developer's choice): **funnel owner** — success is measured on the product: fewer users abandon at the key wall, more complete the pipeline end-to-end. The wall is factually wrong about what it gates: the probe confirmed `/api/process` never checks the Upload-Post key, yet on self-host a missing key alone blocks generation via `keysMissing` (`dashboard/src/App.jsx:781`, `dashboard/src/App.jsx:842-849`).

## Goals

- The pipeline works keyless from ingest to download: generation, editing, subtitles, and downloads never require an Upload-Post key (self-host).
- When a server-side key resolves (`UPLOAD_POST_API_KEY` env on self-host; managed key for entitled cloud users), the UI shows no missing-key states anywhere.
- Publishing stays fully possible via any of: local BYOK key (Settings), server-resolved key, or cloud managed key.
- Keyless publish attempts produce a clear, structured, actionable error, and the UI answers it with a just-in-time setup modal at the moment of intent.
- The banner and header chip stop claiming the key is required to use OpenShorts; they become a soft attach-rate signal instead.

## Non-Goals

- **No draft queue** — keyless posts are not saved and replayed once a key appears (explicitly rejected during interview).
- **No cloud-flow redesign** — non-entitled cloud publish errors (`402 no_plan` vs `400`) and managed-user empty states stay as-is (scope decision: self-host focus).
- **No change to what publishing physically requires** — an actual post still needs a resolvable key; "optional" means optional-to-configure until the user tries to publish.
- **AI-key gate untouched** — `needsAiBackend` keeps gating generation; the AI key is genuinely required.
- **No server-side storage of BYOK keys** — the local key stays in encrypted browser localStorage.

## Functional Requirements

1. The system SHALL NOT block video generation, editing, subtitle, or download flows when no Upload-Post key is configured: `keysMissing` drops the `!uploadPostKey` term (`dashboard/src/App.jsx:781`), so `handleProcess` (`dashboard/src/App.jsx:842-849`) never opens the key modal for a missing Upload-Post key.
2. The existing setup modal (`dashboard/src/App.jsx:2013-2110`) SHALL fire only for `needsAiBackend`; the title logic collapses (the `'Upload-Post API Key Required'` branch disappears) and the Upload-Post block is removed from that modal.
3. Clicking a publish action with no resolvable key SHALL open a contextual modal carrying the existing step-by-step copy (register at app.upload-post.com → connect accounts → generate key → paste, `dashboard/src/App.jsx:2093-2096`) plus an inline paste field (Enter saves via `setUploadPostKey`); Cancel dismisses without navigation. Wired into all four publish surfaces: `dashboard/src/components/ResultCard.jsx:1017`, `dashboard/src/components/ScheduleWeekModal.jsx`, `dashboard/src/components/ThumbnailStudio.jsx:387` and `:1154-1161`, `dashboard/src/components/SaaShortsTab.jsx:1384-1385`.
4. `GET /api/config` SHALL report `uploadPostConfigured: true` when a server-side Upload-Post key resolves (self-host: `UPLOAD_POST_API_KEY` env set, `app.py:188`; cloud: entitled user with `MANAGED_UPLOAD_POST_API_KEY` set, `app.py:183-185`, `cloud/config.py:228-229`, `cloud/managed_keys.py:24-25`). All frontend gates SHALL consume this flag, following the existing `llmConfigured` pattern: `canPost` in `dashboard/src/components/ResultCard.jsx:651` and `dashboard/src/components/ScheduleWeekModal.jsx:116` treat a server-resolved key as configured.
5. The publish endpoints SHALL return `400` with body `{"error": "upload_post_key_missing", ...}` (human message preserved) when no key resolves: `post_to_socials` (`app.py:4435-4436`), `thumbnail_publish` (`app.py:5264-5265`), `saasshorts_post_to_socials` (`app.py:5601-5602`).
6. `GET /api/social/user` (`app.py:4523-4531`) SHALL return `200` with an empty profiles/connected list instead of `400` when no key resolves on self-host. The same empty-state principle applies to the analytics/scheduled family gated by `_social_analytics_auth` (`app.py:4608-4618`, `app.py:4765-4777`) for the self-host no-key case; cloud non-entitled behavior is unchanged (scope decision).
7. The standing banner (`dashboard/src/App.jsx:1259-1272`) and header chip (`dashboard/src/App.jsx:1239-1253`) SHALL be reworded as a soft, non-blocking suggestion (framing Upload-Post as optional publishing setup, e.g. "Optional: connect Upload-Post in Settings to publish to TikTok/Reels/Shorts"); they SHALL NOT block any interaction and SHALL hide when a local or server-resolved key exists.
8. The Settings Upload-Post section (`dashboard/src/App.jsx:1392-1424`) remains the BYOK setup home, behavior unchanged.
9. The MCP `publish_clip` tool (`mcp_server.py:473-482`) SHALL surface the structured error unchanged in shape (tool error carrying the `400` body).

## Non-Functional Requirements

- **Performance**: no specific constraint — `uploadPostConfigured` is one boolean in an existing `/api/config` payload.
- **Security**: BYOK key stays client-side in encrypted localStorage (`dashboard/src/App.jsx:241-244`, `:639-646`); no new server-side persistence of user keys; key-resolution precedence (header → body → env, `app.py:187-188`) unchanged; the error body must not reveal more than key-present/absent.
- **UX / Accessibility**: the JIT modal reuses the existing `Modal` component (keyboard reachable, focus-trapped as today); the suggestion banner never traps focus or blocks scroll.
- **Reliability**: zero behavior change when a key IS present — publish-with-key works byte-identically to today (regression guard); cloud managed flow untouched; `tests/test_social_tenant_isolation.py` and `tests/test_billing_states.py` keep passing.

## Constraints & Assumptions

- **Scope constraint**: self-host surface only; cloud (billing) flows unchanged (Decision 4).
- **Vendor constraint**: Upload-Post physically requires a key to post — the feature makes configuration optional until publish, not posting keyless.
- **Assumption**: no existing test pins the current no-key `400` raises (`app.py:4436`, `:4531`, `:5265`, `:5602`) — probe found none; research should verify.
- **Assumption**: the frontend never calls `/api/social/user` without a key today (guard at `dashboard/src/App.jsx:745-746`), so the empty-state change mainly benefits MCP/agent callers and the new JIT flow.
- **Assumption**: `README.md:412` documents `UPLOAD_POST_API_KEY` — docs copy may need a one-line "optional" clarification.

## Acceptance Criteria

- [ ] With a valid AI key configured and NO Upload-Post key (localStorage cleared, no env key): submitting a job from the dashboard succeeds — no key modal appears, clips generate and download.
- [ ] `curl -s localhost:8000/api/config | jq .uploadPostConfigured` prints `false` without the env key and `true` with `UPLOAD_POST_API_KEY` set.
- [ ] With `UPLOAD_POST_API_KEY` set and empty localStorage: the ResultCard publish button is enabled and no "configure api key" warning shows (`canPost` true via the server flag, `dashboard/src/components/ResultCard.jsx:651`).
- [ ] Publish click with no resolvable key opens the contextual modal with the 4-step copy and paste field; Cancel dismisses without navigation.
- [ ] `curl -s -X POST localhost:8000/api/social/post -H 'Content-Type: application/json' -d '{"clip_url":"x.mp4","platforms":["tiktok"]}'` returns `400` with a body containing `upload_post_key_missing`.
- [ ] `curl -s localhost:8000/api/social/user` (no key header) returns `200` with an empty profiles list.
- [ ] Banner text no longer says "to use OpenShorts"; wording frames Upload-Post as optional publishing setup; banner hidden when a server key resolves.
- [ ] `cd dashboard && npm run lint && npm run build` exit 0.
- [ ] `python -m pytest tests/test_social_tenant_isolation.py tests/test_billing_states.py` passes (cloud behavior unchanged).

## Recommended Approach

Frontend: drop `!uploadPostKey` from the `keysMissing` OR (`dashboard/src/App.jsx:781`); collapse the existing modal to the AI-key gate; add a contextual publish-setup modal reusing the existing step copy; consume a new `uploadPostConfigured` flag from `GET /api/config` alongside the existing `llmConfigured` pattern. Backend: add the flag to `/api/config`; change the no-key branches of `post_to_socials` / `thumbnail_publish` / `saasshorts_post_to_socials` to return `{"error": "upload_post_key_missing"}`; change the self-host no-key branches of `/api/social/user` (+ analytics/scheduled family) to empty-state `200`s. No schema changes (`api_key` already Optional, `app.py:4415`), no persistence changes.

## Decisions

### Decision 1 — Pipeline works keyless end-to-end
**Question**: Pre-resolved from codebase evidence — on self-host a missing Upload-Post key alone blocks generation (`keysMissing` ORs it in, App.jsx:781; `handleProcess` aborts to the modal, App.jsx:842-849) though `/api/process` never checks the key. Keep as a goal: the pipeline must work keyless from ingest to download?
**Recommended**: Yes — free pipeline.
**Chosen**: Yes — free pipeline.
**Rationale**: evidence: dashboard/src/App.jsx:781 + dashboard/src/App.jsx:842-849 + confirmed (backend never checks the key at /api/process).

### Decision 2 — Server-resolved keys count as configured
**Question**: Pre-resolved from codebase evidence — the backend already posts without any client key when a server-side key resolves (cloud managed key for entitled users, app.py:183-185; `UPLOAD_POST_API_KEY` env on self-host, app.py:188), but every frontend gate reads only localStorage. Keep as a requirement: when a server-side key resolves, the UI must not show missing-key states?
**Recommended**: Yes — report server key via `/api/config` `uploadPostConfigured` flag (same pattern as existing `llmConfigured`).
**Chosen**: Yes — report server key.
**Rationale**: evidence: app.py:183-188 + cloud/config.py:228-229 + confirmed; the wall overstates the requirement in both modes today.

### Decision 3 — "Backend optional" = structured 4xx + empty connection state
**Question**: Pre-resolved from codebase evidence — request models already mark `api_key` Optional (app.py:4415); the requirement lives only in five in-handler 400 raises. Actual posting can never succeed with zero resolvable key. Keep this interpretation: keyless publish attempts fail with a clear, actionable error — and connection checks stop hard-failing?
**Recommended**: Yes — JIT error + empty state, no queueing.
**Chosen**: Yes — JIT error + empty state.
**Rationale**: evidence: app.py:4415/4435-4436/4530-4531/5264-5265/5601-5602 + confirmed; draft-queue variant explicitly rejected.

### Decision 4 — Scope: self-host surface
**Question**: Pre-resolved from codebase evidence — the blocking wall never fires on hosted/cloud (`keysMissing` is self-host-only, App.jsx:769-781); managed cloud users already post keylessly. Which deployment surface does this feature target?
**Recommended**: Self-host focus.
**Chosen**: Self-host focus.
**Rationale**: evidence: dashboard/src/App.jsx:769-781 + confirmed; cloud flows stay as-is.

### Decision 5 — Banner + chip reworded to soft suggestion
**Question**: With generation keyless, the standing banner "Set your Upload-Post API key to use OpenShorts." (App.jsx:1259-1272) and the header chip "Upload-Post API Key Missing" (App.jsx:1239-1253) become factually wrong. What happens to them?
**Recommended**: Remove both (zero-friction, at the cost of the attach-rate signal).
**Chosen**: Reword to soft, non-blocking suggestion.
**Rationale**: developer diverged from recommendation — preserving the self-host social-attach signal outweighs the small always-visible surface; discovery also remains at publish buttons (ResultCard.jsx:253-255).

### Decision 6 — Structured error code
**Question**: Keyless publish attempts must fail with a clear error (Decision 3). What response shape does the backend use?
**Recommended**: Structured code — `{"error": "upload_post_key_missing"}` on the three publish 400s.
**Chosen**: Structured code.
**Rationale**: deterministic frontend mapping (no string matching on copy) and machine-readable reason for MCP/agent callers (mcp_server.py:473-482).

### Decision 7 — JIT contextual setup modal
**Question**: When the user clicks Publish with no key configured, what do they see?
**Recommended**: Contextual modal with the existing step-by-step copy + inline paste field.
**Chosen**: Contextual modal.
**Rationale**: the exact copy from the input survives (App.jsx:2093-2096), re-contextualized from blocking wall to moment-of-intent setup; Cancel keeps the user on the card.

### Decision 8 — Old modal becomes AI-key only
**Question**: The current blocking modal (App.jsx:2013-2110) carries both the AI-key setup and the Upload-Post block. What is its fate once generation is keyless?
**Recommended**: AI-key only — modal fires for `needsAiBackend` alone; Upload-Post block moves out (Settings + JIT modal own it).
**Chosen**: AI-key only.
**Rationale**: the AI key is genuinely required to run the pipeline, so its gate stays; Upload-Post setup now has two better homes.

## Open Questions

- None — every node resolved during the interview; nothing was deferred.

## Suggested Follow-ups

- Cloud non-entitled users get a plain `400` on publish while the analytics gate returns `402 no_plan` — inconsistent error semantics across the social surface (`app.py:4610-4618` vs `app.py:4435-4436`); out of scope today.
- `upload_post_key()` can return `None` for entitled cloud users when `MANAGED_UPLOAD_POST_API_KEY` is unset, after `ensure_profile` already ran with an empty `Apikey` header (`app.py:184`, `cloud/managed_keys.py:24-25`, `cloud/social_profiles.py:22-23`) — latent ordering issue worth a look.
- No test pins any of the current no-key `400` raises (`app.py:4436`, `:4531`, `:5265`, `:5602`) — regression coverage lands with this feature's changes.
- `fetchUserProfiles` alerts "No profiles found for this API Key." in non-silent mode (`dashboard/src/App.jsx:756-760`) — candidate for the same JIT treatment later.
- `README.md:412` documents `UPLOAD_POST_API_KEY` — verify docs frame it as optional after this change.

## References

- Skill input: free-text feature description (quoted in Problem & Intent).
- Probe evidence: `dashboard/src/App.jsx`, `dashboard/src/components/{ResultCard,ScheduleWeekModal,ThumbnailStudio,SaaShortsTab}.jsx`, `app.py`, `cloud/{managed_keys,config,social_profiles}.py`, `mcp_server.py`, `tests/test_social_tenant_isolation.py`, `tests/test_billing_states.py`.
