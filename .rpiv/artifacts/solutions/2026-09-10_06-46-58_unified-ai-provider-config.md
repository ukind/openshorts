---
date: 2026-09-10T06:46:58+0700
author: Yogiswara Utama
commit: e5ffe7c
branch: main
repository: openshorts
topic: "Unified AI provider config — single source of truth BYOK panel"
confidence: high
complexity: medium
status: ready
verdict: pass
tags: [solutions, ai-provider, llm-client, byok, frontend, editor, thumbnail, hook-grounding, screencast]
last_updated: 2026-09-10T06:46:58+0700
last_updated_by: Yogiswara Utama
---

# Solution Analysis: Unified AI provider config — single source of truth BYOK panel

**Date**: 2026-09-10T06:46:58+0700
**Author**: Yogiswara Utama
**Commit**: e5ffe7c
**Branch**: main
**Repository**: openshorts

## Research Question
"i just wanna single source of truth, especially in the UI. im prefer the OpenAI / Compatible API BYOK menu. but does if we set via this menu, every feature related to this is well integrated" (discover D0). One menu must power every AI feature except live web search. Today the answer is no: the BYOK panel feeds only the `ai_provider` pipeline family, satellites read `X-LLM-*` only, and four stages are Gemini-only.

## Summary
**Problem**: Two backend config families (`ai_provider.py` pipeline vs `llm_client.py` satellites) and four Gemini-only stages mean one BYOK menu does not integrate everything. Setting an OpenAI-compatible endpoint leaves editor effects, thumbnail image generation, hook grounding, and screencast layout detection dead or degraded.
**Recommended**: Option 1 — FE dual-header bridge + full reroutes, shipped in 5 phases. Phase 1 equals Option 4; Option 2's header-only BE fallback is a cheap optional add-on after phase 1.
**Effort**: Med-High (~6-10 days; ~380-580 changed lines + ~250-400 test lines across ~11 files)
**Confidence**: High

## Problem Statement

**Requirements:**
- One unified BYOK menu (the OpenAI / Compatible API card) is the single source of truth in the UI (D0).
- Satellites (layout picker, thumbnail text, SaaS analyze/scripts) are covered by it (D1).
- Maximal reroute: the four Gemini-only stages gain the unified endpoint; SaaS grounded research stays Gemini-only (D2).
- Keep both dispatch modules; unify config only (D3).
- Encrypted storage for all provider keys in the browser (D4).
- FE bridge: the dashboard derives and emits both header families (D5).
- Card affordances survive: presets, Test connection, model dropdown, server-config badge (D6).
- `LlmProviderCard.jsx` is deleted; its values migrate (D7).
- Plaintext keys migrate once into the encrypted store, then are deleted (D8).

**Constraints:**
- Hard: env namespace law — `LLM_*` configures satellites only; `AI_PROVIDER`/`OPENAI_*`/`GEMINI_*` configure the pipeline only. Rerouted stages read `OPENAI_*`, never `LLM_*` (CLAUDE.md; `tests/test_no_double_route.py`).
- Hard: stage ownership is test-pinned; dispatch never changes, only config and input-mode (`tests/test_no_double_route.py`, 15 tests).
- Hard: billing pin — under cloud billing, `resolve_llm` returns `None` (`app.py:193-194`) and `resolve_gemini` serves the managed key (`app.py:143-150`). Cloud behavior must not change.
- Hard: rerouted vision stages degrade, never fail (one log line + clean skip, `hook_grounding.py:148-151` shape).
- Soft: phased delivery per the `fde9338` precedent, with a fix-commit budget after each phase.

**Success criteria:**
- A user who sets only an OpenAI-compatible key in one card can run: pipeline, satellite text tasks, and (post-reroute) hook grounding, screencast layout, editor effects, and thumbnail image generation.
- Every Gemini-only surface either works on the unified endpoint or degrades with a visible/logged reason.
- All pins stay green: `tests/test_no_double_route.py`, `tests/test_llm_endpoints.py`, `tests/test_ai_provider.py`, `tests/test_hook_grounding.py`.
- No new Python dependencies.

## Current State

**Existing implementation:**
- Two dispatch modules: `ai_provider.create_ai_provider` (`ai_provider.py:621-649`, pipeline) and `llm_client` (satellites). Split is deliberate; `tests/test_no_double_route.py` pins it.
- Per-job resolvers: `resolve_openai` (`app.py:152-165`), `resolve_llm` (`app.py:180-207`, a two-leg chain: header triple `:188-194`, then env `:206`). `resolve_llm` has **8 call sites**: `app.py:226` (job gate), `:2444` (`/api/process`), `:2098` (`/api/llm/test`), `:5866`, `:5971`, `:6056` (thumbnail endpoints), `:6183`, `:6467` (SaaS).
- Job gate: `ai_backend_available` (`app.py:213-228`) accepts either family.
- Job env handover: `app.py:2561-2594` builds the subprocess env (`OPENAI_*` `:2572-2586`, `LLM_*` `:2591-2593`), injected at `app.py:1907-1914`; pinned by `tests/test_no_double_route.py:319-357`.
- Capability probe: `probe_vision_support` (`ai_provider.py:584`), 3 production callers (`main.py:2135`, `:2549`, `:3013`), pinned by 10 assertions in `tests/test_ai_provider.py:256-328`.
- FE: `handleProcess` (`App.jsx:1059-1077`) already emits both families; `llmHeaders()` (`dashboard/src/lib/llm.js:16-24`) is the sole literal `X-LLM-*` emitter (literals at `:21-22`), with ~8 call sites across 4 files. The empty-model rule is documented at `lib/llm.js:13-15`.
- Four Gemini-only stages: `hook_grounding.py` (frames input, in-job), `screencast_layout.py` (Files API upload `:186-197`, in-job), `editor.py` (Files API + video-native EditPlan `:130-147`, request-scoped), `thumbnail.py` image path (`genai` at `:602-611`, request-scoped; gate `app.py:6052-6054`).

**Relevant patterns:**
- Dual input-mode: `main.py:1629` (`"native_video"`), `main.py:2198` — the editor reroute is a second instance.
- Key-based dual dispatch inside one stage: `thumbnail.py:81` + `:132-137` (text side) — the template for the image reroute.
- Request-scoped dual dispatch in an endpoint: caption dual-dispatch `app.py:6786-6807` — the template for editor/thumbnail reroutes (they are request-scoped endpoints, NOT in-job stages).
- One-time import + delete of a legacy localStorage key: `App.jsx:229-250` (`gemini_key` → `geminiKey_v1`; already shipped at HEAD).
- Degrade-never-fail skip: `hook_grounding.py:148-151`.
- Phased provider rollout with follow-up fixes: `fde9338`, `25222f5`, `e96407b`.

**Integration points:**
- `dashboard/src/App.jsx:1631-1639` + `:5` — the only `LlmProviderCard` references; zero test references.
- `dashboard/src/App.jsx:246-249`, `:311-318`, `:697-724` — plaintext `openai_*` init/state/persistence; `:703` writes `llmConfig_v1`.
- `dashboard/src/App.jsx:913-917` — `providerCfg`/`llmActive`/`needsAiBackend`; `providerCfg` feeds `SaaShortsTab` (`:1937`) and `App.jsx:2116`, so the `{baseUrl, apiKey, model}` shape must survive the store merge.
- `dashboard/src/components/ResultCard.jsx:294` — sends only `X-Gemini-Key` today.
- `dashboard/src/components/VoiceOverPage.jsx:346-353`, `CreateEditProfileModal.jsx:253-263` — duplicated inline header builders to fold.
- `mcp_server.py:57-61`, `:338-345` — `_FORWARD_HEADERS` already allowlists both header families; forwarded in-process. Zero MCP changes needed for the FE bridge.
- Resume env rebuild: `app.py:1002-1004` — `os.environ` only; BYOK headers are dropped on redeploy resume (pre-existing, both families, unchanged by every option here).

### Corrections to the research doc (verified at HEAD `e5ffe7c` by the fit agents)
1. `LlmProviderCard.jsx:350-352` does not exist. The card is 212 lines and emits via `llmHeaders(form)` at `:58` (Test connection, `:55-71`). The sole literal `X-LLM-*` emitter is `lib/llm.js:21-22`.
2. `LlmProviderCard` has ZERO test references (research claimed one stale test reference; not reproducible — matches were in `.rpiv/` docs only).
3. The `gemini_key` plaintext import ALREADY shipped at HEAD (`App.jsx:229-250`); the writer is `geminiKey_v1` (`App.jsx:693`). Remaining imports: `llmConfig_v1` (write `:703`) and the `openai_*` triple (reads `:246-249`, writes `:709-712`).
4. `resolve_llm` consumers drifted: 8 live sites at `app.py:226, 2098, 2444, 5866, 5971, 6056, 6183, 6467` (research listed 6 stale numbers).
5. Resume env rebuild lives at `app.py:1002-1004`; `app.py:876` is shutdown code.
6. Thumbnail gate is `app.py:6052-6054` (billing branch at `:6058+` must stay intact); not `:6045-6046`.
7. `editor.py` and `thumbnail.py` are request-scoped endpoints, not in-job subprocess stages. Their reroute key source is `resolve_openai` in-request (precedent `app.py:6786-6807`), not the job env. Only `hook_grounding.py` and `screencast_layout.py` run in-job and read the job `OPENAI_*` env.
8. The empty-model header rule has NO live test pin: `tests/test_llm_endpoints.py:221-225` pins the BYOK header leg of `ai_backend_available`; `tests/test_llm_client.py:707` is stale (pins alert classification at `:700-712`). The rule survives only as documentation at `lib/llm.js:13-15`.
9. FE verification reality: `dashboard/` has no test runner at all (`package.json` scripts are dev/build/lint/preview; zero `*.test.*` files). BE tests pin raw header dicts, so a pure FE change cannot break them.

## Solution Options

### Option 1: FE dual-header bridge + full reroutes
**How it works:**
One unified OpenAI/Compatible card derives both header families from the encrypted store: `X-OpenAI-*` for the pipeline and derived `X-LLM-*` for satellites. The four Gemini-only stages gain the unified endpoint: the two in-job stages read the job `OPENAI_*` env through the `ai_provider` path with a `probe_vision_support` guard; the two request-scoped endpoints resolve via `resolve_openai`. Dispatch never changes (D3). Delivered in phases.

**Pros:**
- Every load-bearing seam already ships and is test-pinned: dual emission (`App.jsx:1059-1077`), job env (`app.py:2561-2594`), probe (`ai_provider.py:584`), dual input-mode (`main.py:2198`), key-based dual dispatch (`thumbnail.py:81`, `:132-137`), request-scoped dual dispatch (`app.py:6786-6807`).
- Satisfies D1 AND D2: after phase 5, "one menu powers everything" is true except SaaS grounded research (accepted by D2 itself).
- Zero new dependencies; zero MCP changes; cloud behavior unchanged (billing pins untouched).
- 11 FE emission points converge to 1 shared builder.

**Cons:**
- Largest diff of the shippable options: ~380-580 lines across ~11 files, plus ~250-400 test lines.
- The `/v1/images/generations` client is net-new surface with no in-repo precedent and real capability variance across OpenAI-compatible endpoints.
- FE work has no automated safety net (no FE test harness exists).

**Complexity:** Med-High (~6-10 days)
- Files to create: 0-1 (~0-80 lines; image client helper may live in `thumbnail.py`)
- Files to modify: 10, delete 1 (`LlmProviderCard.jsx`, −212 lines); ~380-580 changed lines + tests
- Risk level: Med (variant constraints: rerouted stages read `OPENAI_*` only; gate relaxation keeps the billing branch)

### Option 2: Server-side resolver shim (header-only trigger)
**How it works:**
The dashboard emits ONE family (`X-OpenAI-*`). A third leg inside `resolve_llm` (`app.py:180-207`) derives satellite config from `X-OpenAI-*` headers when `X-LLM-*` is absent. The shared FE base and the four reroutes are identical to Option 1. Closes the external-caller trap: MCP/API-key/curl callers sending only `X-OpenAI-*` stop being ignored by satellites, with zero MCP changes.

**Pros:**
- Cheapest possible BE delta: ~25-45 lines in one function; all 8 `resolve_llm` call sites adopt automatically; `resolve_llm` is already a two-leg chain, so a third leg is idiomatic.
- `tests/test_no_double_route.py` holds by letter and spirit (dispatch stays `llm_client.chat`; canaries patch `llm_client`/`ai_provider`/`genai.Client`, none trip).
- Fixes a real trap the FE bridge leaves open (external callers).

**Cons:**
- The trigger shape decides everything: header-only firing is clean; reusing `resolve_openai` wholesale (inheriting its env leg, `app.py:160-165`) silently reconfigures every self-host deployment running `AI_PROVIDER=openai` and breaks the namespace law — **that variant is a blocker**.
- Keyless servers need a direct `LlmConfig` build (`config_from` drops keyless endpoints, `llm_client.py:182-183`); the `Authorization: Bearer ` + empty string at `llm_client.chat:434` is tolerated by Ollama/vLLM but unverified elsewhere.
- Inherits key-mixing (header key can pair with env base) and changes the `/api/llm/test` contract for `X-OpenAI-*`-only requests.
- The MCP headline benefit has zero test coverage today — it needs the repo's first MCP forwarding test.

**Complexity:** Med (same as Option 1 + a near-neutral delta: +~30-45 BE lines, −~15 FE lines)
- Files to create: 0; Files to modify: 11 (+`app.py`); Risk level: Med (variant-dependent)

### Option 3: Server-owned config store
**How it works:**
BYOK config becomes server state: per-user table in cloud mode, global JSON store in self-host, read/written through new authenticated endpoints. Job env is built from the store at submit; headers become optional overrides. The reroutes are unchanged from Option 1.

**Pros:**
- Real side benefits, verified: fixes the redeploy-resume gap exactly at `app.py:1002-1014`; MCP callers inherit the owner's stored config through existing identity resolution (`cloud/auth.py:109-112`) with no header plumbing.
- Per-user tables, dual-mode stores, authenticated writes, and erasure wiring all have house precedent (`cloud/models.py`, `local_game_profiles.py:50-70`, `cloud/api_keys.py`, `USER_OWNED_TABLES`).

**Cons:**
- **Blocking (integration-risk):** two independent hard seams. (a) Reversible secret-at-rest storage has zero precedent — the repo only has sha256 for revocable mint-once tokens, no crypto dependency in any requirements file, and the FE "encryption" is XOR+base64 obfuscation (`App.jsx:44-53`). Stored provider keys are a honeypot class of asset the DB never held. (b) Startup ordering: `_resume_interrupted_jobs()` runs at `app.py:1729` before the DB engine exists (`:1740`); fixing it needs reorder/lazy-init/defer-to-scan-loop.
- Billing pin conflict is a product decision (`resolve_llm` returns `None` under billing, `app.py:193-194`).
- Largest surface: ~5-7 new files, ~10-12 modified, ~350-550 new + ~400-600 modified lines — 2-3× Option 1 — plus a mandatory auth/encryption/resume test matrix and no FE harness for the localStorage→server migration.

**Complexity:** High (~2-3× Option 1; realistically a phase-2 target after a header bridge ships)
- Files to create: 5-7 (~350-550 lines); Files to modify: 10-12 (~400-600 lines); Risk level: High

### Option 4: UI-only bridge, defer reroutes
**How it works:**
Unified card + dual headers + full FE cleanup (delete `LlmProviderCard`, import `llmConfig_v1` + `openai_*`, fold both inline builders). Satellites gain coverage via derived `X-LLM-*`. The four Gemini-only stages keep requiring a Gemini key. Defers D2 rather than executing it. This is exactly Option 1's phase 1.

**Pros:**
- Smallest diff: 6 files modified, 1 deleted (−212), ~200-300 touched lines, zero BE changes, zero test-breakage risk (no FE harness; BE tests pin raw headers only).
- Clean deletion verified: `LlmProviderCard` has 2 references (`App.jsx:5`, `:1631-1639`) and zero test references; `ThumbnailStudio`/`SaaShortsTab` need zero changes if the `llmHeaders(cfg)` signature holds.

**Cons:**
- Leaves the headline promise false. With only an OpenAI-compatible key set: `/api/edit` dies with a visible "Gemini API Key is missing" (`ResultCard.jsx:292`), thumbnail image stays gated (`app.py:6052-6054`), hook grounding silently skips (`hook_grounding.py:148-152`), screencast detection silently degrades (`screencast_layout.py:195-212`). D2 stays fully unexecuted — the state discover declared unacceptable.
- Three real hazards even in this small scope: the `openai_*` write-back at `App.jsx:709-712` resurrects plaintext keys after import unless removed in the same change; the `llmConfig` state is replaceable, not deletable (`providerCfg` at `App.jsx:913-915` feeds six consumers); the empty-model escape hatch dies if the unified store always carries a model.

**Complexity:** Low (~2-3 days, 1 phase)
- Files to create: 0; Files to modify: 6, delete 1; Risk level: Low

## Comparison

| Criteria | Opt 1: FE bridge + reroutes | Opt 2: BE resolver shim | Opt 3: Server store | Opt 4: UI-only |
|----------|----------|----------|----------|----------|
| Complexity | Med-High | Med | High | Low |
| Codebase fit | High | High (header-only variant) | Low-Med | High |
| Risk | Med | Med (env-leg variant = blocker) | High | Low |
| "One menu powers everything" | Yes (all but SaaS research) | Yes (same) | Yes | No — 4+ Gemini-only surfaces remain |
| New dependencies | 0 | 0 | 1 (crypto) | 0 |
| Effort | ~6-10 d, 5 phases | ≈ Opt 1 (±0 delta) | 2-3× Opt 1 | ~2-3 d, 1 phase |

## Recommendation

**Selected:** Option 1 — FE dual-header bridge + full reroutes, in 5 phases.

**Rationale:**
- It is the only clearing candidate that satisfies both D1 and D2, so the user's actual question ("does every feature integrate?") ends with "yes" instead of "mostly".
- Every seam it needs already ships and is test-pinned (see Current State); the reroutes are second instances of established in-repo patterns, not new concepts.
- Zero new dependencies and zero MCP changes; cloud/billing behavior untouched; degradation semantics already exist as a template.
- It strictly contains Option 4 (its phase 1) and composes with Option 2 (its header-only fallback is a +25-45-line add-on usable later without disturbing pins).

**Why not alternatives:**
- Option 2: not chosen as the primary because the FE bridge is needed regardless (the UI must have one card), and the BE fallback addresses a tradeoff (external callers) that D5 explicitly accepted. Recommended as an add-on after phase 1 — header-only trigger ONLY; never the env-leg variant (it silently reconfigures existing self-host deployments and breaks the namespace law).
- Option 3: fails the fit filter on integration-risk — no precedent for reversible secret storage plus a startup-ordering seam on the resume path. Revisit later as a phase-2 evolution once the header bridge has shipped.
- Option 4: clears the filter mechanically but under-delivers the requirements (D2 deferred); adopted as Option 1's phase 1 rather than an endpoint.

**Trade-offs:**
- Accepting a larger, phased diff (~380-580 lines over 5 phases) in exchange for full integration.
- Accepting one net-new integration surface (`/v1/images/generations`, capability variance) in exchange for thumbnail image coverage; mitigated by probe + clean-unavailable state + Gemini fallback (D2).
- Accepting that SaaS grounded research stays Gemini-only (D2's own boundary) and that the redeploy-resume BYOK gap remains (pre-existing, both families).

**Implementation approach:**
1. Phase 1 — Unified card + FE bridge (D5-D8): unified encrypted store; one-time import of `llmConfig_v1` (`:703`) and the `openai_*` triple (`:246-249`/`:709-712`) with the plaintext writers removed in the same change; delete `LlmProviderCard.jsx`; re-home presets/Test/badge into the OpenAI card; replace `llmConfig` state with a `providerCfg`-compatible derived view; fold the two inline builders into `lib/llm.js` and extend it to a dual-family builder; give `ResultCard.jsx:294` the derived families. ~150-250 lines, 6 files + 1 deletion. Ship alone; it is independently valuable.
2. Phase 2 — `hook_grounding.py` reroute: swap the client inside `_ask_gemini` (`:124-142`), key from job `OPENAI_*` via the `ai_provider` path, `probe_vision_support` guard, keep the skip shape (`:148-151`). Sole caller `main.py:3700`; zero caller changes. ~30-50 lines + 2 tests.
3. Phase 3 — `screencast_layout.py` reroute: replace the Files upload (`:186-197`) with `layout_picker`-style 12-frames-at-1024px sampling; keep degrade-to-`[]` (`:210-212`). ~60-100 lines + 2-3 tests.
4. Phase 4 — `editor.py` dual input-mode: Gemini Files API when a key resolves, else frames via request-scoped `resolve_openai` (caption precedent `app.py:6786-6807`); fork the effects call (`:220-227`); the filter-string call (`:385-392`) is text-only. Zero caller changes. ~80-120 lines + 3-4 tests.
5. Phase 5 — `thumbnail.py` image reroute + gate relax: `/v1/images/generations` path next to the `genai` call (`:602-611`) with Gemini fallback and a clean-unavailable state; relax the gate (`app.py:6052-6054`) keeping the billing branch (`:6058+`). ~60-100 lines + 3-4 tests. Optional same-phase add-on: Option 2's header-only fallback in `resolve_llm` (+ its 2 tests, including the repo's first MCP forwarding test).

Deploy note: phases 2-5 are independent commits; batch each with its tests per the deploy-handover guidance in CLAUDE.md. Before any code edit: re-index (the graph index at 2026-09-08 predates HEAD) and re-run `detect_changes`.

**Integration points:**
- `dashboard/src/lib/llm.js:16-24` — extend to the dual-family builder; keep the empty-model rule (`:13-15`); literals stay only here.
- `dashboard/src/App.jsx:1631-1639`/`:5` — replace the card mount/import; `:246-249`, `:311-318`, `:697-724` — store init, migration, persistence.
- `dashboard/src/App.jsx:1059-1077` — `handleProcess` keeps its shape; values come from one store.
- `dashboard/src/components/ResultCard.jsx:294` — gains derived families.
- `hook_grounding.py:124-152`, `screencast_layout.py:175-212`, `editor.py:28-65`/`:130-147`/`:220-227`, `thumbnail.py:602-611`, `app.py:6052-6054` — the four reroutes + gate.
- `app.py:180-207` — optional Option 2 add-on (header-only trigger; direct `LlmConfig` build for keyless endpoints).

**Patterns to follow:**
- Capability probe before dispatch: `ai_provider.py:584`.
- Dual input-mode: `main.py:2198`. Key-based dual dispatch: `thumbnail.py:81`/`:132-137`. Request-scoped dual dispatch: `app.py:6786-6807`.
- One-time import + delete: `App.jsx:229-250`.
- Degrade-never-fail: `hook_grounding.py:148-151`.
- Phased rollout with a fix-commit budget: `fde9338` → `25222f5`/`e96407b`.

**Risks:**
- Env namespace breach (rerouted stage reads `LLM_*`): mitigate with per-stage pins; `tests/test_no_double_route.py` must stay green untouched.
- Plaintext resurrection after import: remove the `openai_*` writers (`App.jsx:709-712`) in the same change as the import.
- `providerCfg` shape break (6 consumers incl. `SaaShortsTab:1937`): keep `{baseUrl, apiKey, model}` as the derived satellite view.
- Image-endpoint variance: probe, degrade to a clean "image model unavailable" state, keep the Gemini fallback.
- Silent vision degradations: keep the one-log-line contract; a UI tell is optional later scope.
- No FE test harness: grep-checklist + manual QA (below); adding a harness is out of scope for this feature.

## Scope Boundaries
- Building: one unified card emitting both families; migration of two legacy key families; four stage reroutes; gate relaxation; optional header-only BE fallback.
- NOT building: server-side config state (Option 3), dispatch-module changes (D3 forbids), SaaS grounded research reroute (D2), MCP changes, a fix for the redeploy-resume BYOK gap (pre-existing follow-up), an FE test harness.

## Testing Strategy

**Unit tests:**
- Hook grounding (2): skip without vision after probe `no_vision`; skip with no provider. Keep the `_ask_gemini` stub seam (`tests/test_hook_grounding.py:91, :124, :133`) — dispatch the OpenAI arm inside `_ask_gemini`, or add a parallel `_ask_openai` stub set.
- Screencast (2-3): frames path returns ranges through an OpenAI-compat stub; degrade to `[]` on provider failure.
- Editor (3-4): mode selection (Gemini key → Files API; absent + OpenAI → frames); EditPlan JSON on the compat stub; text-only filter string on the compat stub.
- Thumbnail image (3-4): `/v1/images/generations` success; refusal falls back to Gemini; clean-unavailable state; gate accepts the unified config at `app.py:6052-6054`.
- If the Option 2 add-on lands (7-9 tests, ~120-180 lines): `X-OpenAI-*`-only reaches `/api/thumbnail/analyze` and `/api/saasshorts/analyze`; `X-LLM-*` beats the fallback; keyless base-url-only pinned; billing keeps satellites Gemini-pinned; `X-OpenAI-*`-only `/api/process` carries derived `LLM_*` into the job env (mirror `tests/test_no_double_route.py:340-348`); first-ever MCP forwarding test.

**Integration tests:**
- Existing pins must stay green with zero modification: `tests/test_no_double_route.py` (15 tests, incl. env contract `:319-357`), `tests/test_llm_endpoints.py` (26), `tests/test_ai_provider.py` (37, incl. probe `:256-328`), `tests/test_llm_client.py` (56; resolver untouched).
- Known hazard if the env-leg variant were ever tried: `tests/test_llm_endpoints.py:26-28` clears `LLM_*` but not `OPENAI_*`, and `test_no_config_is_a_400` (`:97`) flips. The header-only trigger removes the hazard by construction.

**Manual verification:**
- [ ] Fresh browser profile seeded with all legacy keys (`llmConfig_v1`, `openai_*`, `geminiKey_v1`): import happens once, plaintext keys are removed, a reload does not resurrect them.
- [ ] A job starts with only the unified card set (no Gemini key): pipeline + satellite text + (post-reroute) hook grounding, screencast, editor, thumbnail image all work or degrade with a logged reason.
- [ ] Empty-model path: with the model field cleared, satellites still fall back to the server `LLM_MODEL` (the `lib/llm.js:13-15` rule — currently documentation-only; verify by hand).
- [ ] `npm run lint` (`--max-warnings 0`) and `npm run build` pass.
- [ ] Grep checklist: `LlmProviderCard` = 0 refs in `dashboard/src`; literal `X-LLM` only in `lib/llm.js`; `llmHeaders` call sites ≈ 9-10 after the fold.
- [ ] Cloud mode smoke: billing pin intact — `providerCfg` stays empty, managed key still serves.

## Open Questions
**Resolved during research:**
- Is the `gemini_key` migration still pending? No — already shipped at HEAD (`App.jsx:229-250`); only `llmConfig_v1` + `openai_*` remain.
- What is the real `LlmProviderCard` blast radius? Two references (`App.jsx:5`, `:1631-1639`), zero test references.
- Do all four reroutes read the job env? No — only the two in-job stages; `editor.py`/`thumbnail.py` are request-scoped and resolve via `resolve_openai` in-request (precedent `app.py:6786-6807`).
- Does MCP need changes? No for Options 1/4 (`mcp_server.py:57-61` already forwards both families); the add-on's MCP benefit needs its first test.

**Requires user input:**
- Fate of the empty-model escape hatch: the unified card always carries a model, which kills the "use server satellite default" path. Default assumption for planning: keep an empty/"server default" option in the unified card.
- Timing of the Option 2 add-on (BE header-only fallback): ship with phase 1 or as its own follow-up commit. Default: follow-up commit after phase 1 stabilizes.
- `/v1/images/generations` variance across compatible endpoints: assume probe + clean-unavailable + Gemini fallback (D2 already decided the fallback).

**Blockers:**
- None.

## References
- `.rpiv/artifacts/research/2026-09-09_20-00-08_unified-ai-provider-config.md` — the research map this analysis verifies and corrects (see Corrections above).
- `.rpiv/artifacts/discover/2026-09-09_19-17-18_unified-ai-provider-config.md` — FRD with decisions D0-D8.
- `.rpiv/artifacts/handoffs/2026-08-30_14-42-31_openai-compatible-llm-provider-implementation.md` — provider implementation handoff.
- `.rpiv/artifacts/handoffs/2026-08-31_05-52-02_connect-llm-provider-phase1.md` — phase-1 connect handoff.
- `app.py:152-228` — resolvers + job gate; `app.py:2561-2594` + `:1907-1914` — job env handover; `app.py:6786-6807` — request-scoped dual-dispatch precedent.
- `ai_provider.py:584`, `:621-649` — capability probe + provider factory.
- `dashboard/src/lib/llm.js:16-24` — shared builder; `dashboard/src/App.jsx:1059-1077` — dual-family emission.
- `tests/test_no_double_route.py` — ownership + env pins; `tests/test_ai_provider.py:256-328` — probe pins.
- CLAUDE.md — "Stage ownership: ai_provider.py vs llm_client.py" and the env namespace law.
