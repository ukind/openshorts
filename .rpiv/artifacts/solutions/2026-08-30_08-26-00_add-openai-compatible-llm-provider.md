---
date: 2026-08-30T08:26:00+0700
author: Yogiswara Utama
commit: 071c4c3
branch: main
repository: openshorts
topic: "Add OpenAI-compatible third-party LLM endpoint option alongside Gemini (not a replacement)"
confidence: high
complexity: medium
status: ready
verdict: pass
tags: [solutions, llm-provider, llm_client, gemini, openai-compat, minimax, openrouter]
last_updated: 2026-08-30T08:26:00+0700
last_updated_by: Yogiswara Utama
---

# Solution Analysis: Add OpenAI-compatible third-party LLM endpoint option alongside Gemini

**Date**: 2026-08-30T08:26:00+0700
**Author**: Yogiswara Utama
**Commit**: 071c4c3
**Branch**: main
**Repository**: openshorts

## Research Question

User: "can we replace it with other multi model like minimax m3 or any open ai compatible endpoint that multi model?" — then corrected scope: **"i dont wanna replace gemini api, instead add options for third parties open ai compatible endpoint to be used. not solely google api"**, with the selection criterion **"i need robust solution"**.

So: keep Google Gemini as the unchanged default, and add the ability to route the AI calls to any OpenAI-compatible chat-completions endpoint (MiniMax M3, OpenRouter, local vLLM, …). Robustness — graceful degradation, preserved observability, no regression on the Gemini path — is the dominant criterion.

## Summary

**Problem**: Every AI call in the pipeline goes through Google's `google-genai` SDK with a single provider (Gemini), keyed by `GEMINI_API_KEY` / `X-Gemini-Key`. Users want provider optionality without giving up Gemini.
**Recommended**: Option 1 — thin global provider switch (in-house `llm_client.py`, Gemini default unchanged, OpenAI-compatible backend opt-in via `LLM_BASE_URL`/`LLM_API_KEY` + per-task model envs).
**Effort**: Medium (~4-5 days)
**Confidence**: High

## Problem Statement

**Requirements:**
- Gemini remains the default; zero behavior change when no third-party endpoint is configured.
- Any OpenAI-compatible chat-completions endpoint can be selected (MiniMax M3, OpenRouter, vLLM, …) — multi-vendor, not a new single-vendor lock-in.
- Structured output (the pipeline depends on validated Pydantic schemas) keeps working, with graceful degradation when a provider lacks strict json_schema.
- Blocked-content and transient-retry semantics survive (they drive alerts, webhooks, and user-facing messages).
- Self-host BYOK works for the third-party endpoint the way `X-Gemini-Key` works today.

**Constraints:**
- Hard: cloud/managed mode keeps working exactly as today (minute-based metering, managed keys) — no billing changes in phase 1.
- Hard: the 4 test suites pinning retry/blocked behavior stay green unmodified on the Gemini path.
- Soft: no new heavy dependencies (deploy weight matters; `requirements.txt:10` pins `google-genai==1.75.0`, CI installs it explicitly at `.github/workflows/ci.yml:21,25`).
- Soft: subprocess/resume system only restores process-level env (`app.py:783-801` stores no credentials) — provider config must be env-shaped, not per-request, to survive resumes.

**Success criteria:**
- Setting `LLM_BASE_URL`+`LLM_API_KEY`+`LLM_MODEL` on a self-host instance produces identical clips through, e.g., MiniMax M3, with cost estimates marked `price_estimated` when the model is unknown to the price table.
- Unsetting them must make the code path byte-identical to today's Gemini path.
- A provider outage maps to the existing transient-retry ladder; a provider policy refusal maps to the existing blocked-content ladder (alert class "blocked content (user video)", not an outage alert).

## Current State

**Capability classes of the 7 genai-using files** (the deciding axis — portability differs per class):

| Class | Calls | Sites | OpenAI-compat viability |
|---|---|---|---|
| **A. Text + strict schema** | viral clips score/detail, thumbnail titles/concepts/description, SaaS analyze/scripts | `main.py:1512-1639` (score `:1561-1563`, detail `:1587-1588`), `thumbnail.py:117,166,234,406,717`, `saasshorts.py:105,357,539` | Yes — `response_format: json_schema` where supported; repo already has a 3-strategy fallback ladder (`gemini_worker.py:461-482`: `strict-json` / `json-text-recovery` / `structured-schema`) |
| **B. Image-in (frames)** | layout picker (12 frames), thumbnail concepts (10 frames) | `layout_picker.py:139-167`, `thumbnail.py:47-50` (`_frame_parts`), `screencast_layout.py:167-211` | Yes — standard `image_url` data-URL parts |
| **C. Full-video upload** | silent-video analysis, editor effects | `main.py:1650-1732` (upload `:1665`, call `:1698-1699`), `editor.py:36` (`upload_video`), `editor.py:131,140,221,386` | **No standard equivalent** — degrade to frame sampling (precedent: `layout_picker.sample_frames`, and the comment at `thumbnail.py:19-21` explaining why frames beat video) |
| **D. Image generation** | thumbnail image render | `thumbnail.py:16-17` (`IMAGE_MODEL`), `:543-591` (`_generate_one`), call `:569` | **Not chat completions** — provider image APIs differ; keep on Gemini (or fal.ai, already integrated at `saasshorts.py:575,649`) in phase 1 |
| **E. Google-Search grounding** | SaaS online research | `saasshorts.py:108-110` (`types.Tool(google_search=…)`), `:114-124` (parses `grounding_metadata`) | **No equivalent** — this one call stays Gemini-pinned |

**Key/env plumbing (the integration surface):**
- `app.py:124` `resolve_gemini` (header read `:137` → env fallback `:140`); self-host 400 on missing key `app.py:198`; a second BYOK path reads the key from the request body at `app.py:2695` (`/api/edit`).
- Managed/cloud: `app.py:876-879` injects `managed_keys.gemini_key()` into the subprocess env; `cloud/managed_keys.py:3,20-21`; `cloud/config.py:224-225` (`MANAGED_GEMINI_API_KEY`); docker-compose.cloud.yml:46.
- Subprocess: `app.py:2196-2198` spawns `main.py` with env override. Resume: `app.py:783-801` deliberately persists no credentials — BYOK header keys are already lost on resume and fall back to env at `app.py:876`; `.instance`/drain rebuild env from `os.environ` only (`app.py:601,610-611`).
- Six more `resolve_gemini` call sites beyond /api/process: `app.py:2695` (edit), `:3658` (effects), `:4762/:4864/:4943/:5071` (thumbnail analyze/titles/generate/describe), `:5271` (saasshorts analyze).
- MCP forwards `x-gemini-key` verbatim (`mcp_server.py:53`); no MCP tool schema exposes model names. MCP docs instruct `X-Gemini-Key` (`skills/openshorts/reference.md:5-6`).

**Observability & safety semantics that must survive any provider:**
- Blocked-content ladder: `gemini_worker.py:362-391` (`GeminiBlockedError`, blocked finish-reason set, two user-facing message shapes) → `main.py:1456,1468-1469` (never retried) → `main.py:1502-1509` (drop single window / recursive bisect) → `main.py:1622-1625` (propagate) → `main.py:1883-1888` (terminal "no usable clips" error). Thumbnail has its own variant (`thumbnail.py:580,591,635-647,650,660-661`) surfaced in `ThumbnailStudio.jsx:904`.
- Transient-retry ladder: `main.py:1442-1481`, token list at `:1471-1476` is Google-error-shaped (`503|UNAVAILABLE|429|RESOURCE_EXHAUSTED|500|INTERNAL|overloaded|Deadline|…`) — needs OpenAI-shaped additions (429, `rate_limit`, `timeout`, `5xx`).
- Alert classification: `cloud/alerts.py:58-66` — "blocked content (user video)" must stay ordered before the generic `"gemini"` branch; a third-party failure needs its own class or a generalized one. Called from `app.py:1322`; also feeds the Telegram high-failure-rate alert (`cloud/alerts.py:155`, `app.py:1273-1286`).
- Log redaction: `app.py:1628-1636` `_SENSITIVE_LOG_RE` matches `gemini|openai|anthropic|flash|model|token|thinking|cost` — a new provider brand string must be added or it leaks into cloud logs; cloud users see only `log_view.friendly_logs` whitelist (`app.py:1639-1656`).
- Webhooks export the job error text (last 500 chars) to third parties: `app.py:1414-1421`.
- Cost analysis: `gemini_worker.py:412-440` reads Gemini `usage_metadata` fields; `clip_selection.py:9-26` `MODEL_PRICES` is Gemini-only, **but unknown models already degrade gracefully** — `(0.50, 3.00)` + `price_estimated=True` (`gemini_worker.py:416-419`). Metering is minute-based, never token-based (`cloud/metering.py:341`, `cloud/config.py:69-79`), so third-party tokens need no metering in cloud mode.
- Dashboard: key in localStorage `gemini_key` (`App.jsx:211`, written at `:574`, stored plain by design), header send `:790-792`, key-required modal `App.jsx:1926-1982`, a second key read path in `ResultCard.jsx:284-291`, per-tab gates in `SaaShortsTab.jsx:46-47,203-204` and `ThumbnailStudio.jsx:76-77` etc. Marketing/SEO surface: ~22 strings in `seo/pages.js`, plus `seo/data.js`, `landing-fallback.js`, `Landing.jsx`, `PricingPage.jsx`, `PricingSection.jsx:98,135,160`, legal sub-processor lists (`seo/legal.js:110,346,572,705`), `index.html` JSON-LD (`:90,101,176,184,200,216`).
- Tests pinning the above: `test_gemini_retry.py:105-146`, `test_gemini_block_split.py:24-46`, `test_alert_classify.py:25-26,37-40`, `test_layout_picker.py:73-110`, `test_clip_selection.py:92-96`, `test_generation_controls.py:49-52`, `test_mcp_endpoint.py:136-138`, `test_agent_uploads.py:17`, `test_process_handover.py:43`, `test_short_source_gate.py:27`.

## Solution Options

### Option 1: Global provider switch — thin in-house adapter
**How it works:**
One new module `llm_client.py` (~250 lines, httpx — already a dependency) exposing the narrow surface the codebase actually uses: `chat(prompt, schema, images=[]) -> (validated_obj, cost)` plus a blocked-content exception. Two backends: google-genai (default, code path unchanged when `LLM_BASE_URL`/`LLM_API_KEY` unset) and OpenAI-compatible chat-completions. Per-task model envs: `LLM_MODEL` (default fallback), `LLM_MODEL_THUMBNAIL`, `LLM_MODEL_EDITOR`, `LLM_MODEL_SAAS`, mirroring the existing `GEMINI_MODEL*` chain (`editor.py:29-34`, `thumbnail.py:16-17`, `saasshorts.py:42`). Map provider refusals into `GeminiBlockedError`-equivalent semantics and provider transients into the existing retry ladder; map `usage.prompt_tokens/completion_tokens` into `_calculate_cost_analysis`; unknown models ride the existing `price_estimated=True` fallback.

**Pros:**
- Default path stays byte-identical → the robustness requirement is structural, not tested-in. The four pinned test suites keep guarding the Gemini path untouched.
- Reuses in-repo patterns: env-chain model selection, the 3-strategy JSON ladder (`gemini_worker.py:461-482`), price fallback (`gemini_worker.py:416-419`), a second-provider precedent (fal.ai BYOK, `saasshorts.py:575,649`).
- Any vendor, one protocol: MiniMax M3 (OpenAI Chat Completions compatible — platform.minimax.io/docs/api-reference/text-chat-openai, accessed 2026-08-30), OpenRouter, vLLM, or Gemini's own OpenAI layer as an escape hatch.
- Small diff, no new dependencies, no SDK churn; deploy weight unchanged.

**Cons:**
- We own the compat mapping (blocked reasons, error strings, usage fields) — provider quirks are our code, not a library's.
- Chat-completions protocol only covers classes A/B; C and D need explicit policy (below).

**Complexity:** Medium (~4-5 days)
- Files to create: 1 (`llm_client.py`, ~250 lines) + 1 test file
- Files to modify: ~10 (`main.py`, `gemini_worker.py`, `layout_picker.py`, `screencast_layout.py`, `thumbnail.py` (text calls only), `saasshorts.py` (2 of 3 calls), `app.py` (resolve chain + env plumbing + log redaction), `cloud/alerts.py`, `README.md`)
- Risk level: Low-Medium (default-path invariance is the safety net)

### Option 2: LiteLLM SDK as the single client
**How it works:**
Replace `genai.Client` call sites with `litellm.completion()` / `image_generation()`; provider/model strings (`gemini/...` default, `openai/minimax-m3`, …) select the backend. LiteLLM translates response_format, retries, and cost tracking across ~100 providers (docs.litellm.ai/docs/image_generation, /docs/providers/minimax, accessed 2026-08-30).

**Pros:**
- Broadest provider coverage in one dependency; image generation included (the only option covering class D out of the box).
- Built-in cost tracking and fallbacks replace hand-rolled `MODEL_PRICES` lookup and parts of the retry ladder.

**Cons:**
- It is a **replacement shaped as an addition**: every call site is re-plumbed through a new dependency, so the Gemini path is no longer byte-identical — the exact regression risk the user's "robust" criterion exists to avoid.
- Heavy dependency; adds import weight to the standalone subprocess worker (`gemini_worker.py:484-546`) and to the 5-minute deploy builds; SDK churn becomes an operational risk.
- MiniMax via LiteLLM routes through the **Anthropic-spec** endpoint (docs.litellm.ai/docs/providers/minimax) — different semantics from the OpenAI-compat path, an abstraction quirk we'd debug blind.
- The Google-specific behaviors this repo carefully built (Files API video upload, thinking-config branching at `gemini_worker.py:442-459`, blocked-finish-reason mapping) become translation-layer surprises.

**Complexity:** Medium-High (~5-7 days incl. re-baselining the pinned test suites)
- Files to modify: ~12 + requirements/CI
- Risk level: Medium-High (single point of failure moves from Google to a third-party SDK)

### Option 3: Per-task provider matrix
**How it works:**
Every Gemini touchpoint gains its own provider triple (`CLIPS_PROVIDER/MODEL/BASE_URL`, `THUMB_TEXT_PROVIDER/…`, etc., defaulting to Gemini), extending the existing per-task model envs pattern to full provider config; dashboard advanced options expose per-task selection (precedent: layout options in `MediaInput.jsx`).

**Pros:**
- Maximum optionality — e.g., MiniMax for clip detection, Gemini for thumbnails, simultaneously.
- Follows an existing config pattern (`GEMINI_MODEL_THUMBNAIL`, `GEMINI_IMAGE_MODEL`, `GEMINI_MODEL_EDITOR`, `GEMINI_MODEL_SAAS`).

**Cons:**
- Config surface multiplies N tasks × provider triple; key resolution (which key for which task? per-task BYOK?) complicates `resolve_gemini` (9 call sites), the subprocess env injection (`app.py:876-879, 2196-2198`), and worsens the resume-system credential gap (`app.py:783-801` — already loses BYOK keys).
- Verification becomes combinatorial: every task × provider pair is a failure mode. More switches is the opposite of robust.
- 90% of the value is captured by Option 1's per-task **model** envs pointing at one shared endpoint.

**Complexity:** High (~7-10 days)
- Risk level: Medium-High

### Option 4: MiniMax M3 direct wiring (baseline)
**How it works:**
`LLM_PROVIDER=minimax` branches to MiniMax's endpoint (OpenAI SDK compatible — platform.minimax.io/docs/api-reference/text-openai-api) with a MiniMax key env; everything else unchanged.

**Pros:**
- Smallest diff; MiniMax M3 is a strong target (1M context, native multimodality — minimax.io/models/text/m3, accessed 2026-08-30; tool use documented at …/text-m3-function-call).
- MiniMax additionally offers file management, image t2i/i2i APIs, and self-host SGLang guides — a full ecosystem if the user later wants more.

**Cons:**
- **Reproduces the problem it solves**: swaps single-vendor Google lock-in for single-vendor MiniMax lock-in. The user explicitly asked for "options … not solely" one provider.
- The next vendor needs another branch — N vendors = N integrations.
- MiniMax-specific gaps are unverified on their own chat endpoint: strict `json_schema` support (tool use is documented; response_format json_schema is not — marked UNKNOWN), image input on M3 chat (marketing says native multimodality; endpoint behavior unverified). EvoLink (third-party) claims image/video/PDF input at ~$0.49/1M input (evolink.ai/minimax-m3) but that is an aggregator, not the source.

**Complexity:** Low-Medium (~2-3 days)
- Risk level: Medium (vendor-shaped unknowns)

## Comparison

| Criteria | 1. Thin switch | 2. LiteLLM | 3. Task matrix | 4. MiniMax direct |
|----------|--------------|------------|----------------|-------------------|
| Capability-coverage (A/B/C/D/E) | A,B ✓ · C degrade · D stays Gemini · E pinned Gemini | A,B,D ✓ · C partial via passthroughs · E ✗ | A,B ✓ · C degrade · D stays · E pinned | A,B likely ✓ (unverified) · C ✗ · D bespoke · E ✗ |
| Precedent-fit | High (env chains, JSON ladder, fal precedent) | Low (no LiteLLM in repo) | Medium (extends model-env pattern) | Low (new branch pattern) |
| Integration-risk | Low-Med (default path invariant) | Medium-High (all sites re-plumbed) | Medium-High (config combinatorics, resume gaps) | Medium (vendor unknowns) |
| Migration-cost (additive) | Low-Med (~10 files, 1 new module) | Med-High (+dep, CI, re-baseline tests) | High (N×config plumbing + dashboard UX) | Low (~2-3 files) |
| Verification-cost | Medium (add mock-endpoint tests; existing suites untouched) | High (re-baseline 4 pinned suites) | High (combinatorial) | Medium |
| Approach-shape robustness | **Highest** — opt-in, protocol-level, vendor-neutral | Medium — powerful but couples to SDK | Low-Med — many switches | Low — one vendor |

## Recommendation

**(A) ≥1 candidate clears the fit filter.**

**Selected:** Option 1 — Global provider switch (thin in-house adapter), with per-task model envs, phased so C/D/E stay Gemini-pinned.

**Rationale:**
- "Robust" is satisfied structurally: with `LLM_BASE_URL` unset the code path is byte-identical to today, so the default cannot regress — the four pinned test suites keep guarding it unmodified.
- One protocol (OpenAI chat-completions) yields every vendor the user named (MiniMax M3, OpenRouter, any OpenAI-compatible endpoint) with no per-vendor code — unlike Option 4, and without the per-provider semantics risk of LiteLLM's translation layer.
- The codebase already contains every degradation mechanism the second backend needs: 3-strategy JSON ladder (`gemini_worker.py:461-482`), unknown-model price fallback (`gemini_worker.py:416-419`), silent no-op degradation in optional paths (`layout_picker.py:168-170`, `screencast_layout.py:209-211`), and a second-provider BYOK precedent (fal.ai).
- The audit surfaced the exact hidden surfaces a naive patch would miss: log redaction (`app.py:1628-1636`), webhook error export (`app.py:1414-1421`), alert ordering (`cloud/alerts.py:58-66`), Google-shaped retry tokens (`main.py:1471-1476`), the body-key BYOK path (`app.py:2695`), and resume credential loss (`app.py:783-801`). Option 1's single seam addresses all of them in one place.

**Why not alternatives:**
- Option 2 (LiteLLM): fails the additive requirement — it re-plumbs the Gemini path through a new dependency; the regression surface is the whole pipeline. Its one unique edge (unified image-gen) is not needed in phase 1 since thumbnails stay on Gemini.
- Option 3 (matrix): the per-task flexibility is real but niche; it multiplies failure modes and worsens the resume-key gap. Model envs in Option 1 capture most of it; per-task providers can be a later, additive extension (provider-prefixed model strings).
- Option 4 (MiniMax direct): recreates single-vendor lock-in, which the user explicitly rejected ("not solely google api" — swapping the vendor doesn't create an option).

**Trade-offs:**
- We own the OpenAI-compat mapping (error strings, blocked reasons, usage fields) instead of a library — bounded, ~60 lines, and testable against a mock endpoint.
- Class C (full-video upload) degrades to frame sampling for third-party providers — acceptable because the repo's own measurements (`thumbnail.py:19-21`, CLAUDE.md layout-picker notes) already established frames-over-video as the better cost/quality trade.
- Class D (thumbnail image-gen) and class E (Google-Search grounding) stay Gemini-only in phase 1 — a provider without a Gemini key keeps thumbnail image rendering unavailable, same as today's behavior without a key.

**Implementation approach:**
1. **`llm_client.py`** — provider-neutral `chat()` with schema, images, cost, blocked-error mapping; google-genai backend extracted verbatim from current inline code; OpenAI backend (httpx): `response_format json_schema` → on failure fall back to the existing `json-text-recovery` strategy; map `finish_reason`/HTTP 4xx policy errors → blocked ladder; map 429/5xx/timeout → transient ladder (`main.py:1471-1476` + OpenAI-shaped tokens); usage mapping into `cost_analysis` with `price_estimated=True` for unknown models.
2. **Call-site rewiring (classes A/B only)** — clips score/detail (`main.py:1561-1563,1587-1588`), layout picker, screencast, thumbnail text calls, SaaS analyze + scripts (not the grounded `research_saas_online`). Per-task model envs `LLM_MODEL*` with `GEMINI_MODEL*` fallback semantics preserved.
3. **Plumbing** — `resolve_llm()` alongside `resolve_gemini` (`app.py:124`): `X-LLM-Base-Url`/`X-LLM-Key` headers (BYOK) → env fallback; extend the body-key path (`app.py:2695`) symmetrically; subprocess env passthrough (`app.py:876-879, 2196-2198`); add provider brand to `_SENSITIVE_LOG_RE` (`app.py:1628-1636`); generalize the alert class (keep "blocked content" ordering, add provider-neutral "llm provider" class) (`cloud/alerts.py:58-66`); keep cloud/managed mode pinned to Gemini (managed_keys unchanged).
4. **Tests** — mock OpenAI-compat server (aiohttp/starlette TestClient) exercising: schema-conformant happy path, json_schema-unsupported fallback, policy-refusal → blocked ladder (no retry), 429/500 → retry ladder, unknown model → `price_estimated`; all four existing pinned suites run green unmodified (default-path invariance).
5. **Docs** — README env table (currently `GEMINI_API_KEY` is listed client-side only, `README.md:409`), `skills/openshorts/reference.md:5-6` (MCP BYOK contract), `.env.example` (which today lacks the GEMINI rows entirely — `.env.example:55-56`). Marketing/SEO copy intentionally untouched in phase 1 (22+ strings name Gemini; that is a product decision, not an engineering one).

**Integration points:**
- `llm_client.py` (new) — the single seam.
- `app.py:124-140, 876-879, 2196-2198, 2695` — key/env resolution chain.
- `cloud/alerts.py:58-66`, `app.py:1628-1636, 1414-1421` — observability.
- `main.py:1471-1476` — retry token list.
- `README.md:409`, `.env.example`, `skills/openshorts/reference.md` — docs.

**Patterns to follow:**
- Env-chain model selection: `editor.py:29-34`.
- Strategy ladder for schema strictness: `gemini_worker.py:461-482`.
- Silent degradation in optional paths: `layout_picker.py:168-170`.
- Second-provider BYOK: `saasshorts.py:575,649` (fal).

**Risks:**
- Provider X's chat endpoint lacks strict json_schema (MiniMax M3 unverified) → mitigation: the json-text-recovery strategy already exists; assert-tested against a mock before trusting a vendor.
- Provider image-input support varies (M3 "native multimodality" is marketing-claimed, endpoint-unverified) → mitigation: capability probe at first use; layout/screencast already degrade to "none" on failure.
- Third-party error strings polluting webhooks/alerts → mitigation: map at the seam (`llm_client`), extend `_SENSITIVE_LOG_RE` and the alert classifier in the same change.
- BYOK third-party keys lost on resume (same as `X-Gemini-Key` today, `app.py:783-801`) → mitigation: document; optionally persist a provider name (not the key) in the manifest later.

## Scope Boundaries

- **Building**: one OpenAI-compatible alternative backend for classes A (text+schema) and B (image-in) across clips/layout/screencast/thumbnail-text/SaaS-text; BYOK + env plumbing; observability mapping; tests.
- **NOT doing (phase 1)**: thumbnail image generation routing (stays Gemini), silent-video full-video upload (stays Gemini or frame-samples later), SaaS grounded research (stays Gemini), cloud/managed mode changes (stays Gemini-managed), dashboard provider-picker UI beyond optional base-url/key fields, LiteLLM, marketing/SEO copy changes, token-based metering.

## Testing Strategy

**Unit tests:**
- `llm_client`: schema happy path; json_schema-unsupported → recovery ladder; policy refusal → blocked error, never retried; 429/500/timeout → retried per ladder; usage → cost_analysis with `price_estimated` on unknown model; Gemini backend passthrough identity (unset env → genai path, no httpx call).
- Alert classifier: third-party block message → "blocked content (user video)" (ordering intact); third-party 500 → new provider class, not "gemini".

**Integration tests:**
- Mock OpenAI-compat endpoint: full `get_viral_clips` flow returns identical-shaped shorts through the second backend; layout picker with a vision-less mock degrades to "none".
- Default-path invariance: existing suites (`test_gemini_retry`, `test_gemini_block_split`, `test_alert_classify`, `test_clip_selection`) green with zero modifications.
- Subprocess: `main.py` spawned with `LLM_*` env reaches the second backend; resume rebuild picks it up from process env.

**Manual verification:**
- [ ] Self-host with `LLM_BASE_URL` → MiniMax M3 produces clips end-to-end.
- [ ] Same instance with env unset → Gemini path, logs byte-identical to pre-change.
- [ ] Cloud/managed job → Gemini-managed, no behavior change.
- [ ] Webhook payload on provider failure reads sensibly (no raw brand strings leaking).

## Open Questions

**Resolved during research:**
- Is the user asking for a replacement? No — additive option alongside Gemini (user correction at Step-3 checkpoint).
- Does anything force a new dependency? No — httpx is already a dependency; the needed protocol is chat-completions JSON over HTTPS.
- Do unknown models break cost analysis? No — `price_estimated=True` fallback already exists (`gemini_worker.py:416-419`).
- Does cloud metering need changes? No — metering is minute-based, never token-based (`cloud/metering.py:341`).

**Requires user input:**
- MiniMax vs OpenRouter vs both as the *documented* first-class third-party target for docs/examples — default assumption: MiniMax M3 (user-named), with OpenRouter noted as the zero-config multi-model alternative.
- Whether the dashboard should grow a provider-picker UI in phase 1 — default assumption: no (env-config only), matching the self-host audience.

**Blockers:**
- MiniMax M3 chat endpoint: strict `json_schema` support and image-input behavior are UNVERIFIED on platform.minimax.io docs (tool use is documented; response_format is not). Unblocking: one spike call against the endpoint before finalizing the recovery-ladder defaults. Not a design blocker — the fallback ladder covers both outcomes.

## References

- MiniMax M3 model page (multimodal, 1M context): https://www.minimax.io/models/text/m3 — accessed 2026-08-30
- MiniMax OpenAI-compatible Chat Completions: https://platform.minimax.io/docs/api-reference/text-chat-openai — accessed 2026-08-30
- MiniMax docs index (file mgmt, image t2i/i2i, self-host, tool use): https://platform.minimax.io/docs/llms.txt — accessed 2026-08-30
- MiniMax via EvoLink aggregator (image/video/PDF input claim, ~$0.49/1M): https://evolink.ai/minimax-m3 — accessed 2026-08-30 (aggregator claim, not primary)
- Gemini OpenAI compatibility (escape hatch): https://ai.google.dev/gemini-api/docs/openai — accessed 2026-08-30; structured-output gap reported: https://discuss.ai.google.dev/t/structured-output-not-working-via-the-openai-compatible-layer/108341/1
- OpenRouter structured outputs: https://openrouter.ai/docs/guides/features/structured-outputs — accessed 2026-08-30
- OpenRouter Unified Image API (2026-06-23): https://openrouter.ai/blog/announcements/image-api/ — accessed 2026-08-30
- LiteLLM image generations + MiniMax provider: https://docs.litellm.ai/docs/image_generation , https://docs.litellm.ai/docs/providers/minimax — accessed 2026-08-30
- Codebase audit: 74-item touchpoint verification (this session, codebase-analyzer agent) — corrections and gaps folded into "Current State" above
- `CLAUDE.md:7,132-135` — canonical model/env documentation (GEMINI_MODEL chain, layout-picker frames-not-video rationale)
