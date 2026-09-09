---
date: 2026-09-06T06:09:59+0700
author: Yogiswara Utama
commit: de92009
branch: main
repository: openshorts
topic: "Merge Marsic1/openshorts fork for full feature parity — conflict anatomy, stage ownership, unified gate, env surface, infra, dashboard seam, tests"
tags: [research, codebase, merge, llm-client, ai-provider, llm-backend-deletion, gate-predicate, env-namespaces, compose, remotion, dashboard, tests]
status: ready
last_updated: 2026-09-06T06:09:59+0700
last_updated_by: Yogiswara Utama
---

# Research: Merge Marsic1/openshorts fork for full feature parity

## Research Question

Developer's framing (via discover): "i just discover another fork of my current project … it have massive additional feature. my idea is i wanna do pull that to my current branch. and it will conflict. and i will resolved it one by one. is this approach sound? … i wanna keep my idea and their idea." The discover stage answered the approach question (yes, with take-theirs-then-reapply) and fixed the FRD; this research answers what the planner must know to execute it without re-discovery: per-file conflict anatomy, the two-LLM-system reconciliation, the unified gate, the env surface, infra slices, the dashboard seam, and how the test suites compose.

## Summary

The merge is **take-theirs as base for every conflicted file, with a precise re-apply list for our smaller deltas**. Seven files conflict textually (the FRD's six, with `remotion/package-lock.json` counted separately from `remotion/package.json`). The real hazards are the ones git will not flag:

1. **A silent rename**: we renamed `dashboard/src/lib/analytics.js` → `panel.js`; they modified it in place. The merge keeps only `panel.js`, and their `App.jsx:33` import must be repointed during the App.jsx resolution or the vite build fails.
2. **Two LLM systems, one env namespace**: their `llm_backend.py` reads the same `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` triple our `llm_client.py` reads, with a looser activation rule. Deleting `llm_backend.py` (12 references across 4 files) is what makes the namespaces disjoint; its surviving semantics (the gate exemption, `/api/config` `localLlm`) re-target `llm_client` through one new predicate, `ai_backend_available()`.
3. **The FRD's Remotion claim is factually wrong**: their manifest is not a superset — it drops `zod`. Their build only compiles because zod hoists transitively from `@remotion/media`'s runtime deps. The correct resolution is the **union** manifest, and the regenerated lockfile must be **committed** because their `render-service/Dockerfile` now runs `npm ci` against it.
4. **Stage-ownership flips delete our `main.py` llm seam** — their `_run_gemini_stage`/`_run_stage_split` are production-dead in their own tree (only tests reach them; live stages call `create_ai_provider` directly), so the flip is safe, but our tests pinning the `llm=` seam must port into a boundary-contract test.

All four open tradeoffs were decided at the developer checkpoint (recorded in Developer Context): provider error semantics → theirs wholesale; compose `env_file` → `required: false`; keyless local-LLM strictness → accept + document; history nav → union rule.

## Detailed Findings

### 1. `app.py` conflict anatomy (ours 6,053 lines @ de92009; theirs 7,980 @ b1f5350)

Per-hunk re-apply table — our hunk → their-side anchor → action (all lines re-verified by the analysis pass):

| Our hunk | Our lines | Their-side anchor | Action |
|---|---|---|---|
| `resolve_llm(request, task)` | `app.py:143-169` | after `resolve_ai_provider` return at `../openshorts-marsic/app.py:178`, before `resolve_upload_post` at :181 | insert verbatim (reads `X-LLM-*` at `app.py:163-166`; billing pin `app.py:156-157` stays) |
| `LLM_ENDPOINT_HINT` constant | `app.py:230-232` | after `gemini_missing_error()` ends at `../openshorts-marsic/app.py:236` | insert verbatim |
| `_env_llm_config()` helper | `app.py:1870-1889` | after `/health/ready` ends at `../openshorts-marsic/app.py:1979`, before `@app.get("/api/config")` at :1981 | insert verbatim (self-contains billing pin `app.py:1879-1880` + task loop `app.py:1885-1888`) |
| `/api/config` LLM keys | `app.py:1894,1903-1905` | inside their `get_config` dict `../openshorts-marsic/app.py:1983-1991` | add keys alongside their `localLlm` (:1990) — both fed from `_env_llm_config()` (see §4) |
| `POST /api/llm/test` probe | `app.py:1909-1950` | after their `get_config` ends at `../openshorts-marsic/app.py:1991` | insert verbatim; their `_check_probe_rate` already exists at `../openshorts-marsic/app.py:264` |
| `llm_cfg = await resolve_llm(request)` | `app.py:2189` | after `resolve_openai` call at `../openshorts-marsic/app.py:2309` | insert one line; their `(gemini_key, gemini_model)` 2-tuple unpack at :2308 wins over our scalar form (`app.py:2188`) — 41 commits depend on it |
| Job-start gate | `app.py:2190-2193` | supersedes `../openshorts-marsic/app.py:2323-2324` | replace `llm_backend.active() and not BILLING_ENABLED` with the unified predicate; keep their `provider == "gemini"` conditioning; self-host raise becomes `LLM_ENDPOINT_HINT`, billing raise stays `gemini_missing_error()` |
| env-writer `if api_key:` guard | `app.py:2315-2318` | — | **not re-applied** — their writer is None-safe by construction (`env["GEMINI_API_KEY"] = gemini_key or ""` at `../openshorts-marsic/app.py:2444`; `if gemini_key:` guard at :2439-2440) |
| `LLM_*` job-env forward | `app.py:2319-2324` | after their provider print at `../openshorts-marsic/app.py:2449`, before `layout_env` at :2451-2454 | insert as `if llm_cfg is not None:` block; independent of their `AI_PROVIDER`/`OPENAI_*` branch (:2433-2449) |
| billing `LLM_*` prefix sweep | `app.py:2325-2331` | appended as `elif BILLING_ENABLED:` on the forward block | keep the prefix-scan form (`app.py:2330-2331`) — their `env = os.environ.copy()` (`../openshorts-marsic/app.py:2423`) inherits stray server `LLM_*` |

Hard dependency: their `import llm_backend` (`../openshorts-marsic/app.py:2`), the gate (:2323), and `"localLlm"` (:1990) all reference the deleted module — rewire all three in one change or the file fails at import.

**Resolver inventory (merged file):**

| Resolver | Where | Headers | Env | Returns |
|---|---|---|---|---|
| `resolve_gemini` (ours) | `app.py:124-140` | `X-Gemini-Key` (:137) | `GEMINI_API_KEY` (:140); billing managed key (:132-136) | `Optional[str]` |
| `resolve_llm` (ours) | `app.py:143-169` | `X-LLM-Base-Url/-Key/-Model` (:163-166) | via `llm_client.active_config` (`llm_client.py:196-200`) | `Optional[LlmConfig]` |
| `resolve_gemini` (theirs) | `../openshorts-marsic/app.py:138-150` | `X-Gemini-Model/-Key` (:139-140) | `GEMINI_MODEL` (:142), `GEMINI_API_KEY` (:149) | `(key, model)` 2-tuple |
| `resolve_openai` (theirs) | `../openshorts-marsic/app.py:153-167` | `X-OpenAI-Key/-Model/-Base-Url|-URL` (:157-160) | `OPENAI_API_KEY/MODEL/BASE_URL` (:161-163) | `(key, model, base_url)` — never None; defaults `gpt-4o-mini` / `https://api.openai.com/v1`; key optional |
| `resolve_ai_provider` (theirs) | `../openshorts-marsic/app.py:169-178` | `X-AI-Provider` (:171) | `AI_PROVIDER` (:172, default gemini) | 6-tuple (provider, gemini_key, openai_key, openai_model, openai_base, gemini_model) |

Merged callers: `resolve_llm` threads into `/api/llm/test`, `/api/process`, and the satellite endpoints (thumbnail analyze/titles/generate/describe + saas analyze — their routes at `../openshorts-marsic/app.py:5704,5809,5875,6016,6296`; add `task="thumbnail"`/`task="saas"` per our `app.py:4896,5417`). `resolve_ai_provider`: exactly one caller — `/api/voiceover/captions` (`../openshorts-marsic/app.py:6601`, call 6609-6610). `resolve_openai`: `/api/process` (:2309) + `/api/game-profiles/analyze` (:7966); `/api/openai/models` (:7903-7930) reads the same headers directly. Their fork is mid-migration on `resolve_gemini`'s tuple signature: game-profiles defensively unpacks (`_gr[0] if isinstance(_gr, tuple) else _gr`, :7971-7978) while `/api/edit` (:3332) and `/api/effects/generate` (:4492) bind the raw return.

Regions needing no action (their code stands): deep-env block `../openshorts-marsic/app.py:2540-2568`, game-profile env :2657-2662, provider-branched writer :2433-2449.

### 2. `main.py` stage ownership (our 11 hunks, all from commit `ec60f4f`)

Ground truth: our `_run_gemini_stage` is 5-arg with `llm=None` (`main.py:1443`) + `llm_client.chat` branch (`main.py:1455-1459`) + distinct `LlmTransientError`/`LlmError` handlers (`main.py:1478-1490`); their version is 4-arg branching on `llm_backend.active()` (`../openshorts-marsic/main.py:1565,1574,1582-1583`). **Critical reachability fact: their `_run_gemini_stage` is called only by `_run_stage_split` (`../openshorts-marsic/main.py:1914`), which has no production caller** — live score/detail call `create_ai_provider` directly (:2528-2529, :2783-2785). Only tests reach the dead chain.

Hunk verdicts: **delete** hunks 1-9 (the `llm=` param, chat branch, error handlers, `_run_stage_split` threading, `active_config()` pick at `main.py:1537-1550`, score/detail `llm=` passes, `price_estimated` flag, the `except (LlmError, LlmTransientError)` print+raise at `main.py:1656-1664`); **rewrite** hunk 0 (imports: take their block `../openshorts-marsic/main.py:25-29` minus `import llm_backend`; drop our `import llm_client` — the three satellite modules import it locally) and hunk 10 (silent-video error message keeps our longer text but the gate `llm_backend.active()` at `../openshorts-marsic/main.py:2921` becomes `llm_client.active_config() is not None`; the vision call stays Gemini-native, :2928-2967).

**No-double-route confirmed**: post-merge dispatch map — score :2528, whole-clip detail :2028, detail :2783, VOD metadata :1773/:1786/:1849/:1880, deep scan :2193, vision/semantic weighting :2615-2619 (reuses score instance), caption-emoji :3116-3120 all route `create_ai_provider`; silent-video vision and hook grounding (:3648-3649, Gemini-only by design) stay native; thumbnail (`thumbnail.py:133,188,264,439,758`), saas (`saasshorts.py:363,554`), layout pick (`layout_picker.py:171`) stay `llm_client`. With `LLM_*` complete AND `AI_PROVIDER=openai`, each stage reads exactly one dispatch expression and if/else arms are mutually exclusive — two backends per job, never two calls per stage.

Error-semantics delta on the flipped stages (developer decision: **accept theirs wholesale**):
- Exception taxonomy collapses to one `AIProviderError` (`../openshorts-marsic/ai_provider.py:26-32`) with string-sniff retry classification (:420-424) — coarser than our structured HTTP-status mapping (`llm_client.py:204+`).
- `GeminiBlockedError` is wrapped by `except Exception` (`../openshorts-marsic/ai_provider.py:223,235-239`), so `get_viral_clips`' catch (`../openshorts-marsic/main.py:2886`) can never fire from provider stages; batch bisect survives only in the dead `_run_stage_split`.
- Stage failures print `❌` and `return None` (:2891-2893); the job fails later with the generic line at :3579-3580.
- `OpenAICompatibleProvider` returns `cost_analysis: None` (`../openshorts-marsic/ai_provider.py:371-374`) — provider jobs lose per-call cost totals; the Gemini arm keeps real costs (:217-222).
- `AI_TIMEOUT` default 30 s (`../openshorts-marsic/main.py:118`, capped `min(30, AI_TIMEOUT)` at :2028) vs our 300 s read timeout (`llm_client.py:130`).
- Temperature default 0.7 (`../openshorts-marsic/main.py:116`) vs our caller-controlled default-unset.

### 3. Deleting `llm_backend.py` — consumer port list

Complete grep of both trees (ours: zero hits at HEAD and at merge-base):

| Reference | Disposition |
|---|---|
| `../openshorts-marsic/main.py:29` (import) | delete |
| `../openshorts-marsic/main.py:1569-1583` (`_run_gemini_stage` local mode) | delete (production-dead) |
| `../openshorts-marsic/main.py:1937` (`return 3 if llm_backend.active() else 8` in `score_batch_size()`) | delete function + tests; `SCORE_BATCH = 8` is hardcoded at :2469 and `score_batch_size()` has no production caller. Optional wire-in: read `LLM_SCORE_BATCH` at :2469 to preserve the small-context behavior their CLAUDE.md:145-147 promises — default is drop |
| `../openshorts-marsic/main.py:2921` (silent-video gate) | rewrite → `llm_client.active_config() is not None` |
| `../openshorts-marsic/app.py:2` (import) | delete |
| `../openshorts-marsic/app.py:1990` (`"localLlm": llm_backend.describe()`) | port onto `_env_llm_config()` (see §4) |
| `../openshorts-marsic/app.py:2323` (job gate) | rewrite → unified predicate |
| `../openshorts-marsic/CLAUDE.md:140-151`, `README.md:129,253,382-401,554-564`, `.env.example:70-79`, `dashboard/src/App.jsx:840` (comment) | rewrite |
| `../openshorts-marsic/hook_grounding.py:17-19` | docstring-only mention; leave |
| `../openshorts-marsic/tests/test_llm_backend.py` (151 lines) | delete; port :40-56 onto the predicate (see §9) |

Module shape needs no porting: `generate_json -> (dict, cost)` mirrored our tuple; its only caller dies. Their stages consume the dict shape `{"response": ..., "cost_analysis": ...}` (`../openshorts-marsic/ai_provider.py:219-222,371-374`).

Env disposition: `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` stay ours (`llm_client.py:176-201`) with tightened semantics (empty key deactivates; no model means inactive with one warning); `LLM_PROVIDER` deleted (no reader left; `AI_PROVIDER` replaces the concept); `LLM_TIMEOUT` deleted (replaced by `AI_TIMEOUT`, 600 s → 30 s default — slow local models must raise `AI_TIMEOUT`); `LLM_SCORE_BATCH` dead unless wired; `OLLAMA_CONTEXT_LENGTH` never had a code reader (doc-only).

**Migration scenario** (their user's `.env`: `LLM_BASE_URL` set, `LLM_PROVIDER=openai`, no key): `LLM_PROVIDER` reads nothing; `llm_client` does not activate (`config_from` returns None on empty key, `llm_client.py:175-178`, pinned by `tests/test_llm_client.py:427`); gate case AI_PROVIDER unset → rejected with hint; gate case `AI_PROVIDER=openai` → passes but stages read `OPENAI_*` (`../openshorts-marsic/main.py:94-108`) and hit `https://api.openai.com/v1` with an empty key → 401. **Working migration**: `AI_PROVIDER=openai` + `OPENAI_BASE_URL=http://host.docker.internal:11434/v1` + `OPENAI_MODEL=<model>` + optional `OPENAI_API_KEY=ollama`; separately `LLM_API_KEY=<any>` + `LLM_MODEL` re-enables llm_client satellites.

Dependency: `openai==3.3.0` is in their `requirements.txt:15`, absent from ours (21 lines) — arrives via auto-merge. `OpenAICompatibleProvider.__init__` does `import openai` (`../openshorts-marsic/ai_provider.py:265`) and the error path imports `BadRequestError` (:412).

### 4. Unified `ai_backend_available()` predicate

Lives in `app.py` beside the resolvers (after the re-applied `resolve_llm`) — **not** in `llm_client.py` (the provider leg needs request/header context, and `llm_client` is deliberately env-only and import-light, `tests/test_llm_endpoints.py:7-8`); must not import `llm_backend`. Signature shape: async, takes the request, keyword options `gemini_key` and `provider` so `/api/process` reuses already-resolved values instead of re-hitting the user DB (`app.py:133` / `../openshorts-marsic/app.py:145`). Four legs, flat OR:

1. `bool(gemini_key)` — covers header, env, and billing-managed keys via their `resolve_gemini` (`../openshorts-marsic/app.py:147-150`).
2. `provider == "openai"` — per-job provider leg; valid because `resolve_openai` always yields a usable base (:163) and keys are optional (:154-156). Preserves their `25222f5` semantics (an openai-provider job is never Gemini-gated, :2323).
3. `await resolve_llm(request) is not None` — per-request BYOK `X-LLM-*`; None under billing (`app.py:156-157`).
4. `_env_llm_config() is not None` — server `LLM_*` env; already billing-pinned (`app.py:1879-1880`), which inherits the `and not BILLING_ENABLED` half of their old gate rather than re-implementing it.

Call site 1 — job launch: their gate at `../openshorts-marsic/app.py:2323-2324` becomes the predicate call inside their existing `provider == "gemini" and not gemini_key` condition, with our `LLM_ENDPOINT_HINT` (self-host) / `gemini_missing_error()` (billing) split (`app.py:2192-2193`). Legs 1-2 are redundant at this site but load-bearing at site 2 and for future callers.

Call site 2 — `/api/config`: `get_config` gains `request: Request` (FastAPI injects; no route change) **or** reports from the env leg alone — the env-only leg is correct there because BYOK headers are per-job, never config state (their dashboard carries per-job headers, `../openshorts-marsic/dashboard/src/App.jsx:978-990`).

**`/api/config` key reconciliation** — the two keys answer different questions and both dashboards consume them by name; keep both, feed both from `_env_llm_config()`: our `llmConfigured`/`llmModel`/`llmBaseUrl` (`app.py:1903-1905` → `dashboard/src/contexts/AuthContext.jsx:152-154` → `dashboard/src/App.jsx:779` `llmActive`) and their `localLlm` (`../openshorts-marsic/app.py:1990` → `../openshorts-marsic/dashboard/src/contexts/AuthContext.jsx:138` → `../openshorts-marsic/dashboard/src/App.jsx:841` `geminiOk`), preserving their exact `describe()` dict shape `{"provider","model","baseUrl"}` (asserted verbatim at `../openshorts-marsic/tests/test_llm_backend.py:49-52`) and their `None if BILLING_ENABLED` guard.

**Accepted strictness change** (documented in merge note + CLAUDE.md + `.env.example`): a keyless local server on bare `LLM_BASE_URL` reports `llmConfigured: false` post-merge (`config_from` requires base+key, `llm_client.py:169-174`); migration is one line (`LLM_API_KEY=<any placeholder>`) or the `AI_PROVIDER=openai` + `OPENAI_BASE_URL` channel, which accepts keyless natively.

### 5. Env-gating surface

Their resolution model is three layers per request — header > form/body > server env (`../openshorts-marsic/app.py:2292-2300`) — materialized as a per-job env block (:2432-2470, :2525-2568) that the child reads at import (`../openshorts-marsic/main.py:87-128`, eager aliases flagged "stale if env changes per job") or lazily (`_deep_config()` :1627-1656). Ours is two layers (`x-llm-*` headers or env, `llm_client.py:165-201`, forwarded per job `app.py:2319-2324`, swept under billing `app.py:2325-2331`).

Features gated by form/header/dashboard toggle, **not** env: game profile (`game_profile_id` form `../openshorts-marsic/app.py:2221`, `X-Game-Profile-Id` :2240-2242, JSON body :2271-2272; env `GAME_PROFILE_ID` :2659-2661 is only the child transport, read at `../openshorts-marsic/main.py:3569`), deep selectors (`deep_provider`/`deep_gemini_model`/`deep_openai_model` forms :2230-2232, `X-Deep-Provider` :2549), per-job provider (`ai_provider` form :2222, `X-AI-Provider` :2292), the seven toggles (`X-Enable-*` :2510-2516, forms :2223-2229), `X-Target-Clips` :2545, voiceover request fields (`voice_provider`, `voice_prompt`, `voice_preset_id`, `elevenlabs_voice_id`, `language`, `style`, :6639-6647; ElevenLabs is header-BYOK `X-ElevenLabs-Key` :6658-6660, :5135-5139 — no env var). `USER_ID` is a per-job metering transport (:2664-2665), never deployment config. The four detection toggles have **no server-env fallback** on the server path — they default ON from header/form (:2521-2523); only the child `main.py` reads their env names (:123-126).

`.env.example` section plan (take ours as base; lines 1-51 identical both sides):

1. Base shared block — keep.
2. Thumbnail Studio (`GEMINI_MODEL_THUMBNAIL`, `GEMINI_IMAGE_MODEL`, ours :55-56) — keep; note their `cloud/game_analyzer.py:16` also reads `GEMINI_MODEL_THUMBNAIL` (one var, two readers, same meaning).
3. Our third-party LLM section (`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL/LLM_MODEL_THUMBNAIL/LLM_MODEL_SAAS`, ours :58-68) — keep verbatim.
4. NEW: video-pipeline provider block — `AI_PROVIDER`, `GEMINI_MODEL`, `OPENAI_MODEL`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AI_TEMPERATURE`, `AI_MAX_TOKENS`, `AI_TIMEOUT` (from their :52-62); document `GEMINI_API_KEY`+`OPENAI_API_KEY`, **not** `AI_API_KEY` (near-dead: its only reader is the eager alias `../openshorts-marsic/main.py:114`).
5. NEW: deep analysis — `DEEP_AI_PROVIDER`, `DEEP_GEMINI_MODEL`, `DEEP_GEMINI_API_KEY`, `DEEP_OPENAI_MODEL`, `DEEP_OPENAI_API_KEY`, `DEEP_OPENAI_BASE_URL`, `ENABLE_DEEP_ANALYSIS` (per-job writes at `../openshorts-marsic/app.py:2558-2565`; server env is only the CLI default).
6. NEW: detection toggles — `ENABLE_SCENE_DETECTION/AUDIO_EVENTS/CHEAP_VISUAL/VISION_ANALYSIS/SUBTITLE_ENHANCEMENT/CAPTION_EMOJIS`, `TARGET_CLIPS` (layering note: dashboard writes job env :2537-2548; these set CLI/deployment defaults).
7. NEW: voiceover TTS — `TTS_DEVICE`, `TTS_LANGUAGE`, `TTS_ATTN_IMPLEMENTATION`, `VOICEOVER_GAME_VOLUME`, `VOICEOVER_VOICE_VOLUME`, `VOICEOVER_DUCK_THRESHOLD`, `VOICEOVER_DUCK_RATIO`, `VOICEOVER_PRESETS_DIR`; note ElevenLabs needs the header, not env.
8. NEW: proxy/ops — `PAID_PROXY_DAILY_MB`, `PROXY_DRAIN_SECONDS` (`../openshorts-marsic/app.py:696`; resumed-job pop at :953), `HOOK_GROUNDING`, `HOOK_GROUNDING_FRAMES`, `HOOK_GROUNDING_MIN_SHARE`, `HOOK_GROUNDING_WIDTH` (`../openshorts-marsic/hook_grounding.py:28-35`).
9. DROP their moment-picker block (their :70-79) — collides with section 3 on the same three names for a different stage. Never document `GAME_PROFILE_ID`/`USER_ID`.

Default drift to fix in the merged tree: `../openshorts-marsic/ai_provider.py:467` defaults `gemini-2.5-flash` while every other `GEMINI_MODEL` reader defaults `gemini-3.1-flash-lite` (:92, :112, :143, :2438, :2445, layout_picker :143, hook_grounding :131); `ai_provider.py:470` defaults `gpt-4` vs `gpt-4o-mini` elsewhere.

Collision checks (verified): `OPENAI_*` — zero readers on our side, stays theirs. `LLM_MODEL_` prefix — ours-only (dynamic read, `llm_client.py:181`). Core `LLM_*` triple — the one semantic collision, resolved by deletion (our job-env forward also writes `LLM_MODEL` at `app.py:2324`, which would flip their backend if both survived). `GEMINI_*` — neutral shared ground.

### 6. Module ownership map (standing rule: the side whose base you take owns the stage)

Overlap of the two changed-file sets on our satellite surface: `saasshorts.py` and `ffmpeg_utils.py` only. `thumbnail.py`, `layout_picker.py`, `mcp_server.py`, `llm_client.py` were never touched by them.

| Module | Ruling |
|---|---|
| `llm_client.py` | ours — owns the `LLM_*` namespace and all five satellite stages |
| `thumbnail.py` (:17 model, chat :133/:188/:264/:439/:758) | ours, clean merge |
| `layout_picker.py` (active_config :147, chat :171) | ours, clean merge |
| `mcp_server.py` | ours, but **extend the allowlist** (`mcp_server.py:53-55`) with their per-request headers — `x-ai-provider`, `x-openai-key`, `x-openai-model`, `x-openai-base-url`, `x-game-profile-id`, `x-enable-*`, `x-target-clips`, `x-deep-provider`, `x-elevenlabs-key` (`../openshorts-marsic/app.py:157-160,2240,2510-2516,2545,2549,5135`) — or their features lose BYOK through MCP. Their allowlist (`../openshorts-marsic/mcp_server.py:53`): `authorization, x-api-key, x-gemini-key, x-upload-post-key` |
| `saasshorts.py` | ours keeps the llm_client reroute (:362-363, :549); theirs adds `mark_ai_generated` import + call — **both hunks keep**, textually disjoint |
| `llm_backend.py` | the one real conflict — deleted, readers re-target llm_client |
| `ai_provider.py` | theirs — video pipeline, deep analysis, semantic scoring, voiceover captions |
| `cloud/game_analyzer.py` | theirs; reads `GEMINI_MODEL_THUMBNAIL`/`GEMINI_MODEL` (:16) and `OPENAI_MODEL`/`OPENAI_BASE_URL` (:90,:93) — no collision with our `LLM_MODEL_*` task chain |
| `cloud/semantic_analyzer.py` | theirs (`from ai_provider import ...` :16, no direct env reads) |
| `voiceover.py`, `voice_presets.py`, `hook_grounding.py`, `cheap_events.py`, `clip_quality.py`, `cloud/proxy_ledger.py` | theirs, self-contained |

### 7. docker-compose GPU flip

Compose delta (ours `docker-compose.yml:1-42` vs theirs `:1-60`): theirs adds `build.args.GPU: "1"` (:3-6), `env_file: [.env]` (:8-9), `TZ` (:11), backend `gpus: all` (:14), `hf-cache` volume (:15-22, :59-60), renderer `NVIDIA_*` env (:52-53) + `gpus: all` (:54). `docker-compose.e2e.local.yml` and `docker-compose.cloud.yml` are **byte-identical** both sides (verified). Dockerfile deltas: qwen-tts in the GPU pip block (`../openshorts-marsic/Dockerfile:34-35`), `ARG GPU=0` defaults (:30, :63), conditional sox (:66-69), `fonts-symbola` (:60). `requirements.txt` delta = `openai==3.3.0`.

Flip checklist (merged file): GPU arg → `"0"` or drop the `args:` block; delete `gpus: all` at backend (:14) and renderer (:54); keep `hf-cache` (Whisper/ASR cache on CPU too; the ~4 GB Qwen model never downloads on CPU because the package is absent); `NVIDIA_*` env optional cleanup (inert without the runtime). If `gpus: all` survives: `docker compose config` still validates but `up` fails at the daemon (`could not select device driver "" with capabilities: [[gpu]]`) → failed Coolify deploy.

Nothing needs NVIDIA: the renderer's NVENC path is a stub (logs and retries with identical args; `../openshorts-marsic/render-service/src/render-worker.ts`; backend sends `'useNvenc': True` at `../openshorts-marsic/app.py:3271`, functionally ignored). qwen-tts CPU degradation verified graceful end-to-end: package absent on CPU build → `QwenLocalTTS.available()` False via ImportError (`../openshorts-marsic/voiceover.py:345-351`) → `qwen_status()` hint (:400-408) → `/api/voiceover/tts/status` (`../openshorts-marsic/app.py:6550-6553`) → pipeline endpoint 400s `voice_provider=="qwen"` (:6661-6664) → UI defaults ElevenLabs (`../openshorts-marsic/dashboard/src/components/VoiceOverPage.jsx:142`, fetch-catch default :213).

**`env_file` decision (checkpoint)**: change `env_file: [.env]` (:8-9) to `required: false` (compose ≥2.24) — injects when present, no failure on fresh clones; `docker compose config` acceptance criterion holds either way.

### 8. Remotion manifest + lockfile

Manifest delta (verified exact): ours adds `zod: ^4.3.6` (post-fork commit `f3cd74d`); theirs adds `@remotion/animated-emoji: 4.0.447` and **drops zod**. All `@remotion/*` + `remotion` pins identical at 4.0.447 both sides; scripts/devDeps identical.

Import inventory: `remotion/src/lib/types.ts:1` imports `zod` — **byte-identical file in both trees**, schemas exported and consumed at `Root.tsx:5,:80` both sides. Their `src/compositions/Subtitles.tsx:13` imports `@remotion/animated-emoji` (rendered :273-292). Why their zod-less manifest compiles today: zod 4.3.6 lives in the **runtime dependencies of `@remotion/media` 4.0.447** only (remotion core/cli/player carry it in devDependencies, which never install for consumers) — npm hoists it and the bare `from "zod"` resolves accidentally. Fragile by construction.

Correct resolution: **union manifest** (theirs + `zod ^4.3.6` re-added). Taking their manifest verbatim works only via the transitive hoist; taking their `Subtitles.tsx` without the animated-emoji line fails `tsc` (TS2307, not installed in our node_modules). **The regenerated `remotion/package-lock.json` must be committed**: their `render-service/Dockerfile` now COPYs it and runs `npm ci --legacy-peer-deps` (hard-fails on missing/stale lockfile) — generate with the same flag. Same for `render-service/package-lock.json`. Their `render-service/package.json` also pins `@remotion/bundler/renderer/remotion` to exact 4.0.447 (ours `^4.0.0`) — take theirs for coherence. Their render-service Dockerfile adds emoji/symbola fonts + `fc-cache`, and downloads the animated-emoji asset pack at **build time** (network dependency, `|| true` tolerated); `server.ts` adds permissive CORS for `/output`.

`remotion/src` file delta since base: `Root.tsx` (theirs only — async `calculateMetadata` fixing 30 s truncation), `lib/fonts.ts` (theirs only — Anton font-face + Liberation stacks), `compositions/Subtitles.tsx` (theirs only — animated emoji, marginV positioning), `public/fonts/Anton-Regular.ttf` (theirs only, new binary — required at runtime by `staticFile()`, tsc will NOT catch it missing), `lib/types.ts` identical, tsconfig/.gitignore identical. Only `package.json` + lockfile are real hand-conflicts. `render-service` is a separate npm project declaring its own zod (used at `render-service/src/server.ts:3,:26,:69`).

### 9. Test suites

Inventory: ours 56 files in flat `tests/`, theirs 64 — 54 shared basenames identical, ours-only `test_llm_client.py` (917 lines) + `test_llm_endpoints.py` (156), theirs-only the 10 new files. `tests/conftest.py` byte-identical both sides (path shim + `BILLING_ENABLED=0` pin, `tests/conftest.py:5,11`). Only shared file that differs: `tests/test_ffmpeg_utils.py` (theirs 145 lines vs ours 98 — adds `AI_DISCLOSURE`/`mark_ai_generated` coverage) → take theirs.

CPU-only CI status of their new files: all fine except — `test_game_profiles.py` uses `@pytest.mark.asyncio` on 7 tests and **CI installs no pytest-asyncio** (`ci.yml:24-26`) → add the plugin or rewrite with `asyncio.run`; `test_proxy_ledger.py` skips without yt-dlp (CI behavior today, keep the `importorskip` guard); `test_semantic_integration.py` self-skips when heavy imports fail. `test_local_game_profiles.py:32-33` sets `LOCAL_PROFILES_DIR` in setUp — **dead code**: the module constants are fixed at import (`../openshorts-marsic/cloud/local_game_profiles.py:18-19`) and `LocalGameProfileRepository()` mkdirs `./profiles/` in CWD (:65-66); `test_game_profile_update_http.py` patches the constants correctly (:64-73). Order hazards: `test_game_profile_update_http.py:32` sets `LOCAL_GAMEPROFILES=1` at module import with no restore.

Import-order: post-merge, our tests still import cleanly in minimal CI — `test_llm_client.py` imports only httpx/pytest/json/pydantic/llm_client/gemini_worker at module level (:13-19) and reaches `main` via `pytest.importorskip("main")` (:515-516); `test_llm_endpoints.py` imports `app` (:11) and recovers once app.py drops `import llm_backend`. Their `main.py` top-level imports `ai_provider, hook_grounding, clip_quality, llm_backend` (:25-29) — all import-light, one dies. `cloud.semantic_analyzer` import is try/except-guarded in their main (:40-47). YOLO/MediaPipe at import exists on BOTH sides (ours `main.py:86,90-91`; theirs :183,:187-188) — hazard unchanged.

**Gate-test port** (their commit `25222f5` ships **no test** — it is a 1-file +4/-1 gate hunk; the tests live in `../openshorts-marsic/tests/test_llm_backend.py`): port :40-56 into `tests/test_llm_endpoints.py` (which already has the TestClient fixture :21-22, the env clean-slate autouse fixture :25-30, TestConfigFields :32-71, probe suite :73-152): (1) no backend → `llmConfigured is False`, `localLlm is None`, `/api/process` 400s; (2) full triple → both keys truthy with the exact describe shape; add counter-case base-URL-only → False (pins the strictness change); (3) their `LLM_PROVIDER=gemini` override test **cannot port** (no reader post-merge) → replace with `AI_PROVIDER=gemini` + full `LLM_*` → still True; (4) `AI_PROVIDER=openai` + `OPENAI_*` triple, no Gemini key → True. Extend `_clean_slate` (:26-27) with `AI_PROVIDER, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL, GEMINI_API_KEY, GEMINI_MODEL, LLM_PROVIDER`.

**No-double-route boundary test** (new `tests/test_no_double_route.py` or appended to `test_llm_client.py`): config = `AI_PROVIDER=openai` AND full `LLM_*` AND `OPENAI_*` set. Video side: patch `ai_provider.create_ai_provider` (`../openshorts-marsic/ai_provider.py:450`) with a counting fake returning the dict shape; canary `llm_client.chat`/`active_config` to raise; drive `main.get_viral_clips` (importorskip pattern) with `video_duration > 120` and `<= 120` (whole-clip shortcut at `../openshorts-marsic/main.py:1983`); cover whole-clip detail :2028, score :2528, detail :2783 (+ optionally VOD :1773-1890, deep :2193, enhance :3116); assert provider_type == "openai", zero llm_client calls, `google.genai.Client` never constructed (reuse the `_no_genai` canary `tests/test_llm_client.py:722-728`). Satellite side: patch `llm_client.chat` with a counting fake; canary `create_ai_provider`; drive `thumbnail.analyze_video_for_titles` (`thumbnail.py:63` → chat :133/:188), `refine_titles` (:223→:264), `plan_thumbnail_concepts` (:398→:439), `saasshorts.analyze_saas` (:261→:363), `layout_picker.pick` (:131, stub `sample_frames` per `tests/test_llm_client.py:744-745`); pass `llm_config=` for thumbnail/saas, env for layout. Optional app-layer: post a job with both chains set, assert child env carries `AI_PROVIDER=openai`+`OPENAI_*` (`../openshorts-marsic/app.py:2433-2437`) and `LLM_*` (`app.py:2319-2324`) — one system does not erase the other.

**Break-list**: `../openshorts-marsic/tests/test_llm_backend.py` dies with the module (:40-56 port as above; :60-120 drop — `tests/test_llm_client.py` already pins schema/ladder/blocked/transient/probe at :67-83, :148-232, :250-309, :314-339, :820-870; :125-141 superseded by the boundary test; :143-151 pins the dead `score_batch_size` knob). Ours: `tests/test_llm_client.py:507-712` pins the `llm=` seam and `LLM_*`-env-driven `get_viral_clips` — port into the boundary test (their `get_viral_clips` reads `AI_PROVIDER`, `../openshorts-marsic/main.py:1947-1960`, and never calls llm_client). `tests/test_llm_endpoints.py` stays green iff merged app.py keeps `llmConfigured/llmModel/llmBaseUrl` (:33-64), `/api/llm/test` (:73-135), `LLM_ENDPOINT_HINT` (`app.py:230`), `_probe_times` (`app.py:238`). Every their-tree test importing `app` or `main` recovers without edits once the two modules drop the import.

`gemini_worker.py` union dependency (ours 550 lines, theirs 668): merged module must keep every symbol the suites import — `ScoreResponse`, `DetailResponse`, `GeminiBlockedError`, `_parse_json_response_text`, `_get_response_text`, `raise_if_blocked`, `_calculate_cost_analysis`, `GroundedHook`, `GROUNDED_HOOK_PROMPT`, `DETAIL_PROMPT_TEMPLATE`. Boundary tests must patch `create_ai_provider` (never construct the real openai client — package is lazy-imported).

### 10. Dashboard `App.jsx` seam

**Merge geometry**: 7 textual conflicts (merge-tree `442a367a`): `.env.example`, `CLAUDE.md`, `app.py`, `dashboard/src/App.jsx`, `main.py`, `remotion/package.json`, `remotion/package-lock.json`. AuthContext **auto-merges cleanly** carrying both flag sets (`llmConfigured/llmModel/llmBaseUrl` + `localLlm`, with `track` from `../lib/panel`). `vite.config.js` byte-identical. `INCLUDED_TOOL_TABS`/`ADVANCED_TOOL_TABS`/`TOOL_NAMES` byte-identical.

**Rename bomb**: base `dashboard/src/lib/analytics.js`; we renamed to `panel.js`, they modified in place. Merged tree keeps `panel.js` (their comment edits land inside it), but their `App.jsx:33` imports `'./lib/analytics'` — repoint to `'./lib/panel'` during resolution or vite fails module-not-found. Their other importers (AccountPage :5, PricingSection :5, TopUpModal :4, AuthContext :10) auto-resolve.

**localStorage ownership**: our migration is one-way (`geminiKey_v1` read `dashboard/src/App.jsx:226` → plaintext `gemini_key` fallback :233 → delete :237), so their `App.jsx:217` read returns null post-merge — safe. Two dangers: their persistence effect `if (apiKey) localStorage.setItem('gemini_key', apiKey)` (`../openshorts-marsic/App.jsx:642`) **must be dropped** (would resurrect plaintext keys on every mount); their `CreateEditProfileModal.jsx:247` reads `gemini_key` directly from storage for the game-profile analyze header (:251-263) — returns `''` post-migration → patch to receive `apiKey` via props. Clean adds from theirs: `openai_key/openai_model/openai_base_url/ai_provider/gemini_model`, `enable_*` ×7, `target_clips`, `deep_provider`, `selected_game_profile`, tutorial keys (`os_clip_tutorial`, `os_show_clip_tutorial`, `os_welcomed`), voiceover keys (`openshorts_voiceover_session`, `vo_last_voice_preset`, `vo_last_style_preset`), subtitle keys (`openshorts_subtitle_presets/_defaults`, `openshorts_recent_emojis`). All read at `../openshorts-marsic/App.jsx:218-258`, written in one effect :639-657 — self-contained, no collisions with ours.

**State disposition**: `apiKey` → ours (encrypted init `App.jsx:225-242`, persistence :623-627; drop their :217 + :642). `llmConfig` → ours unchanged. `aiProvider/openaiKey/openaiModel/openaiBaseUrl/geminiModel` + dropdown state → theirs (semantically overlaps `llmConfig` but keys/headers/resolvers are disjoint — two surfaces coexist per FRD). `targetClips` → **real semantic conflict**: ours derives from form data (`App.jsx:873`), theirs keeps persistent state feeding `X-Target-Clips` (:978) + body (:1016) → take theirs, delete our `data.targetClips` path. `tutorialPhase`, `voiceoverSource/voiceoverMounted`, game-profiles state, deep/toggles → theirs. `providerCfg/llmActive/needsAiBackend` → ours; `geminiOk` (`../openshorts-marsic/App.jsx:841`) drops in favor of the unified gate.

**Gate reconciliation** (merged): `needsAiBackend = !apiKey && !(llmConfigComplete(providerCfg) || llmConfigured || localLlm)` — keeps our banner/MediaInput-blocking behavior (`App.jsx:1260,1247-1269`) and their provider-only flow; route their blocked-UI sites (`../openshorts-marsic/App.jsx:1416-1438,:2561+`) through it.

**Headers**: disjoint families — ours `X-LLM-*` via `llmHeaders(providerCfg)` (`dashboard/src/lib/llm.js:16-24`) at 8 send sites (App :865-868, LlmProviderCard :58, SaaShortsTab :218, ThumbnailStudio :174/:221/:245/:295/:366); theirs `X-AI-Provider` + `X-OpenAI-*` + `X-Enable-*` + `X-Deep-Provider` + `X-Game-Profile-Id` + `X-Target-Clips` + `X-Gemini-Model` (`../openshorts-marsic/App.jsx:977-990`, modal :251-263, VoiceOverPage :347-352). Only `X-Gemini-Key` is shared, both sourced from the same `apiKey` state. Merged handleProcess spreads both objects into one headers literal — no clobbering; each backend resolver ignores the other family. Their body payloads (`cheapToggles` :1006-1015, `targetPayload` :1016, profile payload :941-944) mirror the headers; `../openshorts-marsic/app.py:2312-2317` accepts either channel.

**Components**: clean adds from theirs — `GameProfilesPage.jsx` (mount `../openshorts-marsic/App.jsx:1977`), `VoiceOverPage.jsx` (:1987-1991, kept-mounted via `voiceoverMounted`), `VoiceStylePresetsPage.jsx` (:2008), `CreateEditProfileModal.jsx` (:27), `ClipTutorial.jsx` (:2705-2713), `InvoicesCard.jsx` (inside AccountPage :189, `has_billing_account` guard). Ours-only: `LlmProviderCard.jsx` (mount `App.jsx:1375-1382`), `lib/llm.js`. Both-changed: App.jsx (manual), AuthContext + AccountPage + ResultCard (auto-merge), MediaInput/KeyInput/Landing/Legal/main.jsx/… (theirs wins clean; KeyInput's new model props are backward-compatible with our 2-prop mount `App.jsx:1371`), ThumbnailStudio + SaaShortsTab (ours wins clean — llmHeaders).

**Routing/nav**: extend our navItems (`App.jsx:968-975`) with their tabs — history becomes **unconditional** per the union rule (billing mode keeps our `billingEnabled && isSignedIn` gate; self-host unconditional, per `../openshorts-marsic/App.jsx:1125-1128`), plus `voiceover` :1127, `voice-style-presets` :1128, `game-profiles` :1129; add their mount blocks beside ours (`App.jsx:1556-1694`); wrap our `goToTab` (`App.jsx:990`) with their tutorial lock (`../openshorts-marsic/App.jsx:1129-1131`); session-restore persists activeTab both sides. Legal/Invoices/Tutorial land without router changes (`main.jsx` theirs wins clean).

**dashboard/package.json**: add `@remotion/animated-emoji ^4.0.447` (theirs; animated-emoji re-burn needs it); **keep our `zod ^4.3.6`** (theirs is a ^3.24.0 downgrade; no their-side dashboard code imports zod directly).

### Ordered App.jsx resolution (the merge recipe, as facts)

1. Take their file as base. 2. Repoint `./lib/analytics` → `./lib/panel` (:33). 3. Swap apiKey init/persistence to ours (delete their :217 initializer and :642 plaintext write). 4. Unify the gate to `needsAiBackend` incl. `localLlm`; delete `geminiOk`. 5. Merge header objects in handleProcess; resolve `targetClips` to their persistent state. 6. Mount our `LlmProviderCard` beside their provider UI (KeyInput with model props `../openshorts-marsic/App.jsx:1540`, provider selector :2068, Phase-4 toggles :2092). 7. Props-patch `CreateEditProfileModal` for the Gemini key. 8. Extend navItems + mount blocks + tutorial lock. (`/api/config` returning both key families is the app.py-side twin of steps 4+6.)

## Code References

Jump table for the planner (repo-relative; `../openshorts-marsic/…` = their tree @ b1f5350):

- `app.py:124-140` — our `resolve_gemini` (billing-managed branch :132-136)
- `app.py:143-169` — our `resolve_llm`; header reads :163-166; billing pin :156-157
- `app.py:230-232` — `LLM_ENDPOINT_HINT`
- `app.py:1870-1889` — `_env_llm_config()` (billing pin :1879-1880, task loop :1885-1888)
- `app.py:1892-1906` — `/api/config`; keys :1903-1905
- `app.py:1909-1950` — `POST /api/llm/test`
- `app.py:2188-2193` — job-start gate + hint/error split
- `app.py:2314-2331` — job-env block (guard :2317-2318, forward :2319-2324, sweep :2325-2331)
- `app.py:4896,5001,5086,5213,5417` — satellite endpoints carrying `task=` (their-side routes :5704/:5809/:5875/:6016/:6296)
- `../openshorts-marsic/app.py:138-178` — their resolver trio (openai :153-167, ai_provider :169-178, 6-tuple :178)
- `../openshorts-marsic/app.py:225-236` — `gemini_missing_error()`
- `../openshorts-marsic/app.py:242-264` — probe rate-limit (`_probe_times`, `_check_probe_rate`)
- `../openshorts-marsic/app.py:1983-1991` — their `/api/config` (`localLlm` :1990)
- `../openshorts-marsic/app.py:2208-2324` — `process_endpoint` (unpack :2308, resolve_openai :2309, gate :2320-2324)
- `../openshorts-marsic/app.py:2433-2449` — provider-branched env writer (None-safe :2439-2448)
- `../openshorts-marsic/app.py:2510-2568` — toggle/deep env blocks
- `../openshorts-marsic/app.py:2657-2665` — `GAME_PROFILE_ID`/`USER_ID` transports
- `../openshorts-marsic/app.py:6601-6664` — voiceover captions (resolve_ai_provider caller) + qwen 400 guard
- `../openshorts-marsic/app.py:7903-7980` — `/api/openai/models` + `/api/game-profiles/analyze`
- `../openshorts-marsic/app.py:2` — `import llm_backend` (delete)
- `main.py:1443-1490` — our `_run_gemini_stage` llm seam (delete)
- `main.py:1505-1550` — `_run_stage_split` llm threading + `active_config` pick (delete)
- `main.py:1583-1611,1635-1664,1690-1695` — score/detail passes, cost flag, error print, silent-video message
- `../openshorts-marsic/main.py:25-29` — import block (minus llm_backend)
- `../openshorts-marsic/main.py:1565-1618` — their 4-arg `_run_gemini_stage` (production-dead)
- `../openshorts-marsic/main.py:1900-1937` — their `_run_stage_split` + dead `score_batch_size`
- `../openshorts-marsic/main.py:1947-1967` — their provider resolution in `get_viral_clips`
- `../openshorts-marsic/main.py:2469` — hardcoded `SCORE_BATCH = 8`
- `../openshorts-marsic/main.py:2519-2536,2783-2785,2028-2029,1773-1890,2193,3116-3120` — live provider call sites
- `../openshorts-marsic/main.py:2886-2893` — blocked catch (unreachable from provider stages) + return-None failure
- `../openshorts-marsic/main.py:2921-2967` — silent-video gate + native vision call
- `../openshorts-marsic/main.py:87-128` — env readers (eager aliases flagged stale) + toggle env :123-127
- `../openshorts-marsic/ai_provider.py:26-32,197-243,331-436,450-472` — AIProviderError, Gemini retry ladder, OpenAI retry ladder, factory + default drift :467/:470
- `../openshorts-marsic/ai_provider.py:265,277,371-374,412` — lazy `import openai`, key placeholder, cost None, BadRequestError
- `../openshorts-marsic/llm_backend.py:37-64,74-78,102-165` — provider/base_url/model/active/describe, "ollama" placeholder, generate_json (deleted with module)
- `llm_client.py:165-201` — `config_from` both-present rule + `active_config` + model chain
- `llm_client.py:130,329-363,391-459,461-478,820-870` — read timeout, cost, chat ladder, probe
- `thumbnail.py:63,133,188,223,264,398,439,705,758` — llm_client call sites (ours, unchanged)
- `saasshorts.py:261,362-363,549,554` — reroute + their AI-Act marking (both keep)
- `layout_picker.py:131,146-171` — pick + active_config/chat
- `mcp_server.py:53-55` — header allowlist to extend
- `../openshorts-marsic/docker-compose.yml:3-22,52-60` — GPU arg, env_file, gpus, hf-cache
- `../openshorts-marsic/Dockerfile:22-36,60-69,122-126` — GPU-gated blocks
- `../openshorts-marsic/voiceover.py:319-328,345-351,359-362,400-408,495-498` — device pick, availability, status, mixing env
- `remotion/package.json:12-20` vs `../openshorts-marsic/remotion/package.json:12-20` — zod/animated-emoji delta
- `remotion/src/lib/types.ts:1`, `remotion/src/Root.tsx:5,80`, `../openshorts-marsic/remotion/src/compositions/Subtitles.tsx:13,273-292` — zod + animated-emoji consumers
- `../openshorts-marsic/render-service/Dockerfile` — npm ci + lockfile COPY + build-time emoji download
- `dashboard/src/App.jsx:225-242,262-281,623-658,776-780,865-868,968-975,1247-1269,1375-1382,1697` — our init/persist/gate/headers/nav/mounts
- `dashboard/src/lib/llm.js:10-24` — `llmConfigComplete`, `llmHeaders`
- `dashboard/src/contexts/AuthContext.jsx:152-164` — merged flag sets (auto-merges)
- `../openshorts-marsic/dashboard/src/App.jsx:33,206-284,639-657,841,977-990,1125-1131,1977-2008,2705-2713` — their import, state, persistence, gate, headers, nav, mounts, tutorial
- `../openshorts-marsic/dashboard/src/components/CreateEditProfileModal.jsx:247,251-263` — direct gemini_key read (props patch)
- `tests/conftest.py:5,11`; `tests/test_llm_client.py:13-19,507-712,722-728,744-766,820-870`; `tests/test_llm_endpoints.py:21-152`
- `../openshorts-marsic/tests/test_llm_backend.py:14,17-21,40-56,58-125,125-151` — port source (file deleted)
- `../openshorts-marsic/tests/test_game_profiles.py:20+`; `test_game_profile_update_http.py:32,64-73`; `test_local_game_profiles.py:28-48`; `../openshorts-marsic/ci.yml:24-28` — asyncio-mark + import-env + CI pip gaps

## Integration Points

### Inbound References
- `app.py:2189` + 5 satellite endpoints — feed `resolve_llm` → `llm_client` (satellite BYOK path)
- `../openshorts-marsic/app.py:2309,6609,7966` — feed `resolve_openai`/`resolve_ai_provider` (pipeline provider path)
- `../openshorts-marsic/main.py:2528,2783,2028,2193,3116` — pipeline stages → `create_ai_provider`
- `thumbnail.py/saasshorts.py/layout_picker.py` — satellite stages → `llm_client.chat`
- `../openshorts-marsic/dashboard/src/App.jsx:978-990` — per-job provider headers; `dashboard/src/App.jsx:865-868` — per-job `X-LLM-*`
- `mcp_server.py:54-55` — MCP BYOK forward for our headers; their headers need allowlisting (§6)

### Outbound Dependencies
- `llm_client.py:88` → `gemini_worker` (`GeminiBlockedError`, `_parse_json_response_text`); `clip_selection.lookup_model_prices` for cost
- `../openshorts-marsic/ai_provider.py:16-23,265,412` → `gemini_worker` pydantic models + lazy `openai` SDK
- `../openshorts-marsic/main.py:183-193` → YOLO/MediaPipe at import (both sides)
- `requirements.txt:15` (theirs) → `openai==3.3.0` (new, arrives via auto-merge)

### Infrastructure Wiring
- Job env handoff: `../openshorts-marsic/app.py:2432-2470` (provider block), `:2525-2568` (toggles/deep/target), our `app.py:2319-2331` (`LLM_*` forward + billing sweep) — both families coexist in one child env
- Resume/drain/heartbeat untouched (base infra identical; `PROXY_URL` pops at `../openshorts-marsic/app.py:2426` fresh + `:953` resumed)
- Compose: flip checklist (§7); `env_file` → `required: false`; `hf-cache` kept
- Remotion: union manifest + regenerated-and-committed lockfiles (`npm ci --legacy-peer-deps` flag parity with their Dockerfile)

## Architecture Insights

- **Stage ownership as the reconciliation unit** — "the side whose base you take owns the stage" resolves the two-LLM-system collision without unification: theirs owns the video pipeline (ai_provider), ours owns satellite text stages + MCP BYOK (llm_client). Both job-env families forward per job; the child picks per stage. The only construct that could double-route (our `_run_gemini_stage` llm branch) is deleted.
- **Their fork's provider plumbing is three-layer (header > form/body > env) and materializes per-job env for the child**; ours is two-layer and forwards only the `LLM_*` triple. The merged gate must evaluate request-level legs, not just env.
- **`llm_client`'s inert-when-half-configured contract** (base+key+model, None otherwise, one warning) is the safety property the merged system keeps; their `llm_backend` activated on base URL alone — the strictness change is accepted and documented.
- **Production-dead code is the safe deletion zone**: their `_run_gemini_stage`/`_run_stage_split`/`score_batch_size` have no production callers; `AI_API_KEY` has one eager-alias reader; `OLLAMA_CONTEXT_LENGTH` has zero readers.
- **Error-classification strings are API**: `cloud/alerts._classify_failure` keys on "llm provider"; `GeminiBlockedError` drives never-retry/bisect/alert ladders. Satellite-side strings must stay byte-identical (unchanged files, so they do). Pipeline-side loss of the blocked/bisect ladder is accepted (checkpoint).
- **Silent renames beat textual conflicts**: rename+modify resolves cleanly and hides a build-breaking import.
- **Transitive-hoisted dependencies are latent breakage**: a manifest that compiles only because a peer hoists a package breaks on the next bump — declare what you import.

## Precedents & Lessons

5 similar past changes analyzed.

### Precedent: upstream merge silently dropped half a feature
**Commit(s)**: `0f326fd` — "Merge upstream/main (mutonby) into squashed history" (2026-09-02), ~100+ files
**Follow-up fixes**: `25222f5` — "fix(merge): job-start Gemini-key gate accepts a configured local LLM (llm_backend) as upstream did" (2026-09-02), 1 file +4/−1. The gate from one side landed, the other fork's half dropped.
**Takeaway**: This merge fixes that same gate line a **third** time (ours `56707b7`, theirs `25222f5`, now `ai_backend_available()`). Replace it with the predicate — do not hand-resolve.

### Precedent: our LLM backend feature series (additive pattern)
**Commit(s)**: `30ce67e` (llm_client.py +454), `ec60f4f` (routing, 5 files +317/−124), `da48ff2` (tests +847), `e96407b` (MCP BYOK + alerts)
**Lessons from docs**: `.rpiv/artifacts/designs/2026-08-30_08-58-35_openai-compatible-llm-provider.md` — "the Gemini code at each branch stays verbatim"; paired-commit rule (redaction + provider strings ship together: `29fed21`→`8160fc6`); alert-class ordering fixed twice in production (`77905a9`, `730f7de`); body-key BYOK was a security hole closed in `6ec6935` — do not extend it.
**Takeaway**: Keep `llm_client` exception classes and error strings byte-identical (they are — the module is untouched by them).

### Precedent: second LLM system rotted in five days
**Commit(s)**: `c0da654` — "feat(llm): run the moment picker on any OpenAI-compatible server" (2026-09-01, 9 files, +165 llm_backend.py)
**Takeaway**: A duplicate transport with no production caller is deletion work, not adaptation — matches FRD #4.

### Precedent: wide-gate frontend exposed a latent launch crash
**Commit(s)**: `f83d555`→`79817ac` Phases 1-5 (2026-09-05); follow-ups `56707b7` (provider-only None-env crash, latent since `d5121ed`), `de92009` (adjacent loudnorm fix)
**Lessons from docs**: `.rpiv/artifacts/validation/2026-09-05_15-07-32_connect-llm-provider-frontend.md` — five provider-only/gate personas to test; pinned check `grep -c 'startswith("LLM_")' app.py → 2`.
**Takeaway**: Re-run those personas post-merge and re-derive pinned counts — their `AI_PROVIDER`/`OPENAI_*` namespace makes the "2" stale.

### Precedent: compose churn
**Commit(s)**: ours `a5649a3`→`2a0e5e0` (last 2026-07-14); theirs `f89a7eb` (2026-09-02, GPU + hf-cache)
**Takeaway**: FRD #7 holds; the flip is mechanical (§7) and Coolify has no NVIDIA runtime.

### Composite Lessons
- Gate predicates are the merge's real hazard: one line, fixed three times, dropped once (`0f326fd`→`25222f5`).
- Invisible semantic collisions beat visible conflicts: audit stage routing after resolution, not conflict text.
- Latent assumptions surface when gates widen — e2e a provider-only job and a no-keys job post-merge, not only Gemini.
- Error-class strings and exception types are load-bearing contracts downstream.
- Pinned verification counts go stale when env namespaces grow — re-derive during validation.

## Historical Context (from `.rpiv/artifacts/`)

- `.rpiv/artifacts/discover/2026-09-06_05-40-55_marsic-fork-parity-merge.md` — FRD: goals, 11 requirements, decisions, acceptance criteria for this merge
- `.rpiv/artifacts/research/2026-09-04_20-47-42_connect-llm-provider-frontend.md` — research behind our AI Provider card + BYOK headers
- `.rpiv/artifacts/designs/2026-08-30_08-58-35_openai-compatible-llm-provider.md` — design of `llm_client.py` (contract, error taxonomy, paired-commit precedents)
- `.rpiv/artifacts/validation/2026-09-05_15-07-32_connect-llm-provider-frontend.md` — provider-only persona validation + pinned counts

## Developer Context

**Q (discover: Conflict-resolution tactic): Per conflicted file (6 total), whose version is the base?**
A: Take theirs, re-apply ours. (Evidence: `git merge-tree` dry run + diffstat, their hunks 10-30× ours.)

**Q (discover: Delete Marsic1's dead llm_backend.py): Their `llm_backend.py` is a third LLM system only reachable from tests — throw it away during the merge?**
A: Delete it. (Analyzer verified only caller chain `_run_stage_split` has no production callers; their features use `ai_provider.py`.)

**Q (discover: Unified job-start gate): One rule for starting a job (Gemini key OR any configured OpenAI-compatible endpoint — Ollama Cloud included)?**
A: Gemini OR any endpoint — single predicate `ai_backend_available()`; prevents the crash returning via either path; Ollama Cloud must work.

**Q (discover: Parity scope): Full parity = all 41 commits' features land, config-gated (proxy spend and GPU stay off until enabled)?**
A: All features, config-gated. Merged ≠ enabled in prod.

**Q (discover: LLM reconciliation boundary (advisor-delegated)): How do the two surviving AI systems coexist?**
A: Divide by stage, advisor-hardened ("A+"): `ai_provider.py` owns video-pipeline stages; `llm_client.py` owns satellite text stages (thumbnail/SaaS/layout) + MCP BYOK. Single gate predicate; no-double-route contract test; boundary written into CLAUDE.md; env namespaces disjoint after `llm_backend.py` deletion; short re-apply list in `app.py`.

**Q (discover: Merge mechanics): How does the merge physically land?**
A: Merge commit on branch `merge/marsic-parity`, verify, fast-forward `main`.

**Q (discover: GPU default in compose): Their compose ships `GPU: "1"` + `gpus: all` — what does the merged default do on your CPU Coolify host?**
A: CPU default, GPU opt-in via build-arg.

**Q (discover: Remotion conflict): `package.json`/lock conflict (add/add) — take their manifest and regenerate the lock?**
A: Take theirs + regen lock. **Research correction**: their manifest drops `zod` (not a superset) — resolve to the union (theirs + `zod ^4.3.6`) and **commit** the regenerated lockfile (their render-service Dockerfile `npm ci`s it).

**Q (discover: Marsic remote persistence): Keep Marsic1 configured as a permanent git remote?**
A: "use my git" — no permanent remote; already-fetched `refs/remotes/marsic/main` used for this merge.

**Q (`../openshorts-marsic/main.py:2891-2893`): Taking their video-pipeline stages drops batch bisect on blocked content, error-reason propagation, per-call cost dicts, and the 300 s read timeout. Accept theirs wholesale, or port minimal error surfacing?**
A: Accept theirs wholesale. Stage ownership = their base owns the stage; unification is an explicit non-goal.

**Q (`../openshorts-marsic/docker-compose.yml:8-9`): `env_file: [.env]` fails `docker compose config` on fresh clones without a `.env` — keep, drop, or soften?**
A: `required: false` — load when present, ignore when absent.

**Q (`llm_client.py:169-193` vs `../openshorts-marsic/llm_backend.py:55-57`): Their keyless bare-`LLM_BASE_URL` mode goes inert under llm_client's base+key+model rule. Accept strictness or tolerate keyless?**
A: Accept + document — migration note: set `LLM_API_KEY` to any placeholder (e.g. `ollama`), or use `AI_PROVIDER=openai` + `OPENAI_BASE_URL` (keyless-native).

**Q (`dashboard/src/App.jsx:968` vs `../openshorts-marsic/App.jsx:1125-1126`): History tab gating — billing-gated (ours) or unconditional (theirs)?**
A: Union rule — billing mode keeps the signed-in gate; self-host (no billing) shows history unconditionally.

## Related Research

- `.rpiv/artifacts/research/2026-09-04_20-47-42_connect-llm-provider-frontend.md` — the llm_client frontend feature this merge must preserve

## Open Questions

None — all checkpoint questions resolved. (Carried from discover: "None — no deferred items.")
