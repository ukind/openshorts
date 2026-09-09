---
date: 2026-09-08T09:13:45+0700
author: Yogiswara Utama
commit: 51c1861
branch: main
repository: openshorts
topic: "candidate detection works on OpenAI-compatible endpoints (Ollama Cloud)"
tags: [research, codebase, ai-provider, openai-compatible, ollama, candidate-detection, cheap-events, vision, deep-analysis, silent-video]
status: ready
last_updated: 2026-09-08T09:13:45+0700
last_updated_by: Yogiswara Utama
---

# Research: candidate detection works on OpenAI-compatible endpoints (Ollama Cloud)

## Research Question

Chained from `.rpiv/artifacts/discover/2026-09-08_08-39-20_candidate-detection-openai-compat.md`. Verify with live evidence and bound the four fixes so the candidate-detection panel's promises hold under `AI_PROVIDER=openai` (the OpenAI-compatible chat-completions protocol — e.g. Ollama Cloud, LM Studio, local Ollama — never a claim about OpenAI-the-vendor):

1. Silent-video path through the provider abstraction (no Gemini key required, no raw `genai.Client`).
2. One-shot image-capability check for text-only models: one warning, zero frame calls after it.
3. `temperature` / `max_tokens` / `timeout` forwarded by `create_ai_provider` to both provider clients.
4. Cheap-signal merge also runs for videos ≤ 120 s.

Gemini path must stay byte-identical (suite baseline 889 passed / 10 pre-existing env failures; `tests/test_no_double_route.py` pins).

## Summary

All four defects are confirmed in live code, and each fix site is now mapped to verified line numbers (the FRD's cites had drifted; see the correction table in Detailed Findings). The load-bearing discoveries:

- **Silent video**: `get_visual_clips` (`main.py:2888`) is a Gemini island — key wall at `main.py:2893-2904`, raw `genai.Client` at `main.py:2905`. The `_no_genai` canary patches the **class**, so it catches `GeminiProvider.__init__` too: the fix must remove the wall AND the raw construction. The deep scan already demonstrates Gemini-native-without-raw-genai (upload via `_prov.client`, `main.py:2197`).
- **Capability probe**: must be a module-level cache in `ai_provider.py` keyed by `(provider_type, model_name, base_url, api_key)` — per-instance caching cannot work because the provider is rebuilt per score batch (`main.py:2502` inside the batch loop). One process per job (`app.py:2560` spawns `main.py` per job) makes module state = job state. The gate needs **two** sites because deep runs *before* scoring (`main.py:2095` vs `main.py:2493`): one before deep frame extraction (`main.py:2120`), one before the vision loop (`main.py:2574`).
- **Params**: all 9 `create_ai_provider` call sites pass kwargs to the **factory**; zero pass them per-call — so the call-time kwargs layer (`ai_provider.py:305-310`, `:327-329`) is dead for production and every OpenAI-compatible request runs hardcoded 0.7/4096/no-timeout today. `GeminiProvider.generate_content` reads no kwargs at all, so store-without-apply leaves Gemini byte-identical.
- **Cheap events ≤120s**: move exactly `main.py:2069-2091` above the whole-clip shortcut at `main.py:1957`; the short-video test pin stays green because it passes no `video_path`. Refactor trap: moving the whole surrounding `try` block instead would silently enable deep for 61-120 s videos.
- **Warning surface**: the `[toggles]`/`[cheap]` lines the FRD modeled after are **parent-process prints that never reach the job log** — only child (`main.py`) stdout lands in `jobs[job_id]['logs']` (`app.py:1890`). One `print` in `main.py` satisfies self-host; cloud needs one unanchored rule in `log_view.py:15-25` (developer approved).

## Detailed Findings

### Verified line-number corrections (cite these, not the FRD's)

| FRD / brief said | Verified at 51c1861 |
|---|---|
| `semantic_analyzer.py:311-315` no-vision no-op | `cloud/semantic_analyzer.py:233-245` (233-238 silent no-signals; 240-245 except + ⚠️ print); 311-315 is JSON template text inside `_build_multimodal_prompt` |
| `main.py:2113` `_deep_nframes` | `main.py:2109` |
| `main.py:2138` deep frames extract | `main.py:2120` (initial), `main.py:2255` (native-failure fallback) |
| `main.py:2566` vision gate | `main.py:2513` |
| `main.py:2577-2584` vision 6-frame call | `main.py:2574-2580` |
| `main.py:2097` deep gate | `main.py:2095` |
| `ai_provider.py:296-300` param defaults | `ai_provider.py:305-310` (params dict), `:327-329` (timeout) |
| `ai_provider.py:464-465` / `:467-472` factory branches | `ai_provider.py:466-468` (gemini), `:469-472` (openai, base_url read at 471) |
| `tests/test_no_double_route.py:73-77` `_no_genai` | `tests/test_no_double_route.py:67-72` |
| `tests/...:131-150` short-video pin | `tests/test_no_double_route.py:145-158` (call at 153, asserts 156-157) |
| `tests/...:98-101` `fake_create` | `tests/test_no_double_route.py:122-125` |
| `tests/...:196-230` `TestAppLayerEnv` | `tests/test_no_double_route.py:255-281` (asserts 287-295) |
| `app.py:2710` DEEP_AI_PROVIDER | `app.py:2710` write; resolution `app.py:2702-2709` |

`dashboard/src/App.jsx` carries parse_partial graph ranges (1800, 2047, 2461) — all panel-copy claims below rest on direct source reads.

### Silent-video path (FR1)

- Sole caller: `main.py:3548` (`else` branch of `transcript is not None`, `main.py:3542-3548`). `transcript` becomes `None` via `NoAudioError` at `main.py:3511-3516` or the sparse-speech gate `speech_is_sparse` (`main.py:2880-2885`, floors `MIN_SPEECH_WORDS=8` / `MIN_SPEECH_WORDS_PER_MIN=5` at `main.py:2876-2877`) applied at `main.py:3518-3524` — a talking video with almost no words also takes the "silent" path.
- Key wall: `api_key = os.getenv("GEMINI_API_KEY")` at `main.py:2893`; no key → `import llm_client` (`main.py:2895`), two message variants (`main.py:2896-2903`), `return None` (`main.py:2904`). Entry print at `main.py:2892` says "analyzing with {AI_PROVIDER.title()} vision" — self-contradictory on an OpenAI-compatible job, which then dies with `RuntimeError("Clip detection failed — OpenAI did not return usable clips")` (`main.py:3550-3556`, label from `_err_provider`/`_err_label` at `main.py:3553-3554`) even though the real blocker was the Gemini wall one frame up.
- Raw client body: `genai.Client(api_key=...)` at `main.py:2905` (module import `main.py:21`); File API upload + poll to 180 s (`main.py:2911-2924`); prompt from `gemini_worker.VISUAL_PROMPT_TEMPLATE` (`gemini_worker.py:88-109`) formatted at `main.py:2936-2940`; direct call **without** `response_schema` at `main.py:2943-2948`; free-JSON parse at `main.py:2950` where `cost_analysis` is hardcoded `None` (the silent path contributes no cost data today); `clip_quality.validate_clips` at `main.py:2953`, ≥1 s clamp + `iou_dedup(0.7)` at `main.py:2963-2964`; return `{"shorts": clean}` at `main.py:2966-2974`; `GeminiBlockedError` re-raised at `main.py:2975-2977`; generic exception → `None` at `main.py:2978-2980`; `finally` deletes the upload at `main.py:2981-2986`.
- Caller contract to preserve (`main.py:3550-3577`): non-empty `shorts` or `RuntimeError` (`main.py:3555-3556`); `clips_data['transcript']` stub at `main.py:3566`; metadata write `main.py:3572-3577`.
- Rewrite reuses: `extract_frames_from_window` (`cloud/semantic_analyzer.py:41-176`, `[]`-on-error at `:173-175`; import shim `HAS_SEMANTIC_ANALYZER` `main.py:45-47`, stub `main.py:54-55` — the silent path must degrade like the deep gate does when it's false); the deep scan's full-window frame precedent (`main.py:2120`, count at `main.py:2109`); the image_url message contract (built `main.py:2258-2263` and `cloud/semantic_analyzer.py:199-215`; forwarded verbatim by `OpenAICompatibleProvider` `ai_provider.py:291-297`; converted to `Part.from_bytes` by `GeminiProvider` `ai_provider.py:118-142`, remote URLs rejected `:143-151`); provider resolution precedent `main.py:1921-1933`.
- Gemini-native-without-raw-genai precedent: deep uploads through the provider object — `_prov.client.files.upload` (`main.py:2197`), `_prov.client.models.generate_content` (`main.py:2218-2219`) — because `GeminiProvider.__init__` exposes `.client`/`.genai_types`/`.model_name` (`ai_provider.py:73-94`).
- Canary verdict: `_no_genai` (`tests/test_no_double_route.py:67-72`) patches `google.genai.Client`; it intercepts `main.py:2905` but never fires because (a) no test drives the silent path (both pipeline tests pass a transcript, `:129-146` and `:148-158`), and (b) the wall returns before construction. It ALSO trips on `GeminiProvider.__init__` (`ai_provider.py:79-87`) — so the fix must route through `create_ai_provider` (no Gemini client constructed on an OpenAI-compatible silent job), and the new acceptance test can install `_no_genai` and pass.

### Image-capability probe (FR2)

- Probe shape: one `generate_content` call with `schema=None` (avoids the `response_format` branch entirely, `ai_provider.py:314-326` + retry `:377-385`) and `messages=[{"role":"user","content":[text part, one tiny image_url part]}]` — the exact format `analyze_semantic_window` builds (`cloud/semantic_analyzer.py:199-215`, call `:221-224`). Pass small per-call `max_tokens` (~16) and `timeout`; per-call kwargs already work (`ai_provider.py:305-310`, `:327-329`).
- Wire evidence (verified, not assumed): Ollama Cloud returns `HTTP 400 {"error":"this model does not support image input"}` for an `image_url` part on a text-only model; LM Studio's documented text is "Model does not support images. Please use a model that does" — classify by message substring, not status code.
- Verdict classification, tri-state, transient-FIRST (reusing the exact token list `ai_provider.py:420-424`): final message matches a transient token and no image phrase → `unknown` (proceed as today); matches image-support phrase → `no_vision`; 200 → `vision_ok`. On the verified Ollama 400 the existing handler raises `AIProviderError` on attempt 1 (no retry storm: not transient, not the response_format case `:410-417`). Trap: an older server returning **500** with the image-rejection body retried 3× (~15 s backoff, `:437-439`) — the probe's own small `timeout` kwarg mitigates.
- Accepted risk: a server that accepts the image part and ignores it returns 200 → false `vision_ok`; the per-window failure block (`cloud/semantic_analyzer.py:240-245`) stays as backstop. FRD does not require catching this.
- Cache: module-level dict in `ai_provider.py`, key `(provider_type, model_name, base_url, api_key)`. Why: `main.py:2502` sits inside the score batch loop (`main.py:2493`, `SCORE_BATCH = 8` at `main.py:2443`) — instance-level caching would probe per batch. Census of factory sites per job: `main.py:1758`, `:1771`, `:1834`, `:1865` (VOD metadata), `:2002` (whole-clip), `:2167` (deep), `:2502` (score), `:2757` (detail), `:3093` (subtitle polish). One process per job (`app.py:2560` `cmd = [sys.executable, "-u", "main.py"]`) makes module state job-scoped.
- Gate placement — two sites, both BEFORE frame extraction (the acceptance criterion counts zero frame calls): deep gate at `main.py:2095` / extraction `main.py:2120` (deep runs before scoring, so "pre-pass after the score loop" is too late for deep); vision gate at `main.py:2513` / extraction `main.py:2574-2580` / call `main.py:2589-2594`. Today's cost of no gate: per window, 6-frame cv2 extraction + one LLM round trip + one ⚠️ print (`cloud/semantic_analyzer.py:240-245`), plus a fully silent empty-signals path when the response lacks `signals` (`:233-238`).
- Deep-provider identity: `X-Deep-Provider` (`app.py:2701-2709`) can select a different family/model than the job (`_deep_config()`, `main.py:1612-1647`; instance at `main.py:2167`). Identity-keyed cache → at most two entries per job; when deep == job identity the second lookup is a dict hit. Restrict the probe to `OpenAICompatibleProvider` — gemini deep means `input_mode: "native_video"` (`main.py:1631`, `:1645`), always vision-capable.
- Warning surface: only child stdout enters `jobs[job_id]['logs']` (`app.py:1890`; capture `app.py:1908-1919`, marker filter `:1856-1890`, timestamp prefix included). `_visible_logs` (`app.py:1833-1843`): raw when `BILLING_ENABLED` off (`app.py:93`) or `DEBUG_LOGS` on (`app.py:115`); otherwise the strict whitelist `log_view.py:15-25` ("Anything not matched by a rule is hidden", `log_view.py:5-6`; verbatim rule `^`-anchored at `:18-19` so subprocess timestamps defeat it — the warning rule must be unanchored). Parent-side `[toggles]`/`[deep-env]`/`[cheap]` prints (`app.py:2718-2721`) go to the uvicorn console only, never the job log — the FRD's comparison model was wrong.

### Parameter passthrough (FR3)

- Factory: `create_ai_provider` (`ai_provider.py:450-474`) — gemini branch constructs `GeminiProvider(model, api_key)` (`:466-468`), openai branch reads only `base_url` (`:469-472`). Both constructors have no sampling slots (`AIProvider.__init__` `:38-40`; `GeminiProvider.__init__` `:73-94`; `OpenAICompatibleProvider.__init__` `:260-282`). No other construction sites exist repo-wide (only class defs + factory calls).
- Effective behavior today: params dict `ai_provider.py:305-310` — `temperature = kwargs.get("temperature", 0.7)`, `max_tokens = kwargs.get("max_tokens", 4096)`; timeout only `"timeout" in kwargs` (`:327-329`). Repo-wide grep: **zero** production calls pass per-call kwargs (all 11 `generate_content` call sites pass at most prompt/schema/messages). So the effective OpenAI-compatible request is always 0.7 / 4096 / no timeout key; `AI_TEMPERATURE`/`AI_MAX_TOKENS`/`AI_TIMEOUT` (`main.py:115-117`, eager aliases under the stale-at-import warning `main.py:109`) have zero effect on OpenAI-compatible jobs, and `AI_TIMEOUT` at every site.
- Site-by-site deltas after merge (factory → constructor storage; openai-compatible jobs): score `main.py:2502` and detail `main.py:2757` (env values / default 0.7-4096 / 30 s); whole-clip `main.py:2002` (`min(30, AI_TIMEOUT)` cap becomes real); deep frames `main.py:2167` (intended 0.2 / 3000 / 90 finally apply); subtitle polish `main.py:3093` (0.25 / 5000→9000 escalation at `main.py:3122` stops being dead); VOD `main.py:1758`, `:1771` (0.2 / 1200 / 60), `:1834` (0.3 / 700 / 60), `:1865` (0.4 / 900 / 60).
- Gemini byte-identity condition: `GeminiProvider.generate_content` (`ai_provider.py:96-250`) reads **no** kwargs; config carries only `response_mime_type` + optional `response_schema` (`:189-195`); SDK call `:200-204`. `GenerateContentConfig` has no timeout field and the client is built at `__init__` — timeout has no natural Gemini slot. Store-at-`__init__`-without-apply = Gemini wire behavior byte-identical. **Developer decision: this is the chosen reading.**
- Deep native-video path bypasses `generate_content` entirely (inline config at `main.py:2218`, call `:2219`) — stays byte-identical under factory-only change; making deep's 0.2/3000 reach the native path needs an edit at `main.py:2218` (out of plain FR3 scope, flag for design).
- Params survive the `response_format` retry: `params.pop("response_format")` at `ai_provider.py:380`, re-send `:383` — temperature/max_tokens/timeout stay in `params`.
- Test machinery: `fake_create` (`tests/test_no_double_route.py:122-125`) swallows kwargs and records only `(provider_type, model_name)` — the forwarding test needs new capture (extend the record to include `kw`, or stub `ai_provider.GeminiProvider` / `ai_provider.OpenAICompatibleProvider` constructors; stubbing also avoids the lazy package imports at `ai_provider.py:76-77`, `:265`). `cloud/semantic_analyzer.py:16` imports but never calls the factory; `mcp_server.py` never imports it (`:54-61` headers only); thumbnail/editor/saasshorts/layout_picker/hook_grounding/gemini_worker/cloud/game_analyzer use native genai clients, untouched by FR3.

### Cheap events ≤120 s (FR4)

- Two disjoint paths in `get_viral_clips` (`main.py:1913`): ≤120 s returns from the whole-clip branch (`main.py:1957-2052`) before `cheap_events = []` is ever assigned (`main.py:2069`); the guard `'cheap_events' in locals()` at `main.py:1991` is always False, its exception twin at `main.py:1994` sets the same, and the payload at `main.py:1995` sends `"none"` in both fields.
- Move exactly `main.py:2069-2091` (init, import at `:2072`, toggle at `:2073`, guard `if cheap_enabled and video_path and os.path.exists(video_path)` at `:2074`, call `:2075-2079`, log `:2080-2091` including `📡 Cheap events: N detected` at `:2090`) to before `main.py:1957`. Nothing in `1957-2069` depends on extraction not having run; the only short-path consumer is the `:1991` guard. All five long-path consumers (`main.py:2102-2103`/`:2122` deep peaks+timeline, `:2329` candidate merge, `:2470-2473` score timeline, `:2566-2571` vision peaks, `:2715-2732` detail enrichment) stay ordered.
- Error isolation: today the block sits inside `try:` (`main.py:2071`) with the handler printing "⚠️ Cheap event seeding skipped" at `main.py:2424`; relocated, it must carry equivalent protection (it sits outside the branch's own `try`). `extract_cheap_events` (`cheap_events.py:477-519`) already swallows per-signal failures (`:485-506`); transcript events always (`:484-488`), scene `:490-494`, audio `:496-500`, visual `:502-506`, dedupe 0.35 s (`:508-518`). Provider-neutral, no LLM.
- Test pin: `tests/test_no_double_route.py:145-158` calls `get_viral_clips(_transcript(20.0), 20.0)` with no `video_path` → the `:2074` guard keeps extraction a no-op → `_wc_ev` stays `"none"` → pin stays green unchanged.
- **Developer decision — hint fields**: split per type like the long path (audio events → `audio_events`, scene cuts → `scene_boundaries`), not one combined string in both. The detail prompt's SCENE-CUT ALIGNMENT rule reads `scene_boundaries` (`gemini_worker.py:352-357`) and would misfire on audio spikes.
- Second dead block (flagged, out of scope per decision): the deep gate `float(video_duration) > 60` at `main.py:2095` can never be False — short videos returned at `main.py:2019`/`:2052` first. Deep never runs ≤120 s on either provider while the panel copy promises "watches the whole video" (`dashboard/src/App.jsx:2237`). Keep off; do NOT move the whole seeding `try` body (`main.py:2071-2424`) or deep silently turns on for 61-120 s.
- Adjacent artifact (record, out of scope): `_wc_active_weights = None` defined only in the short branch (`main.py:1970`) but referenced by the deep block (`main.py:2134`) — `NameError` on the long path, swallowed at `main.py:2135-2136`, dropping the whole game-profile context for deep.
- Acceptance-wording correction: the FRD's "emits the cheap-events merge into the score prompt" cannot hold on short videos — there is no score prompt there; the merge lands in the detail payload fields at `main.py:1991-2001` formatted into `DETAIL_PROMPT_TEMPLATE` (`main.py:1996-2001`).

### Panel copy ↔ code contract (no UI change)

- Card 1 toggles (`dashboard/src/App.jsx:2192-2213`): chain checkbox → `X-Enable-Scene/Audio/Visual` headers (`App.jsx:1063-1065`) → `app.py:2662-2664` → effective `:2673-2675` → env `:2689-2692` → `main.py:122-124` → `cheap_events.py:490-506`. Holds on both providers; after FR4 it must fire on ≤120 s too.
- Card 2 vision (`App.jsx:2214-2226`): "6 frames (3 uniform + 3 around peaks)" verified — `extract_frames_from_window(..., num_frames=6, peak_times=...)` (`main.py:2574-2580`), split math `peak_needed = num_frames // 2` (`cloud/semantic_analyzer.py:100-101`), peak priority scream > sudden_loudness > visual_activity > scene_change > laughter (`main.py:2566-2571`), 0.7 s dedupe + clamp + backfill (`cloud/semantic_analyzer.py:97-116`). Precision: "3 uniform + up to 3 peak". Expansion copy ↔ shortlist target `max(8, min(25, dur // 45 + 6))` (`main.py:2656-2663`). Display mismatch (cosmetic): `[toggles]` prints fixed `candidates='15'|'10'` (`app.py:2718`) matching neither formula.
- Card 3 deep (`App.jsx:2227-2252`): "With Gemini it actually views the footage; with OpenAI-compatible models it inspects a frame sample" verified — gemini `input_mode: native_video` (proxy encode `main.py:2170-2185`, upload `:2197`, poll `:2204`, inline schema call `:2218-2219`, cleanup `:2238`); openai 12 frames over full duration (`main.py:2109`, `:2120`, fallback `:2255`, prompt `:2141`). "Strongest finds always among final clips": pool priority `main.py:2355-2366` + final-selection bypass `main.py:2770-2814` (assembly `:2811`). Selector "same as job"/"Gemini"/"OpenAI" (`App.jsx:2244-2246`, state `:283`, header `:1071`, form `:1103`) → `app.py:2702-2711` → `_deep_config` `main.py:1615-1641`; "same as job" on an OpenAI-compatible job produces env identical to picking "OpenAI".
- ≤120 s: vision and deep are no-ops on both providers today (early return precedes both gates) — the panel over-promises on short inputs; FR4 repairs only the cheap-signals third.

### Bonus findings (out of scope, record for planner)

- **Deep costs are dead code**: `main.py:2291` appends to `costs` before `costs = []` at `main.py:2425` → `UnboundLocalError`, swallowed at `main.py:2292-2293`. Deep never contributes to job cost totals. The FR2 probe must not repeat this: follow the sink-drain pattern (`cloud/semantic_analyzer.py:316-333` → drain at `main.py:2678`) or discard the probe's cost; do not append into `costs` outside its lifetime, and don't pollute the Gemini control run's total.
- `apply_game_profile_scoring` no-op fallback (`main.py:73-82`): without profile weights vision changes nothing but still costs calls — makes the vision toggle partly cosmetic on both providers (already in FRD follow-ups).

## Code References

- `ai_provider.py:450-474` — `create_ai_provider`; kwargs dropped (gemini `:466-468`, openai `:469-472`, base_url `:471`)
- `ai_provider.py:305-310` — OpenAI-compatible params dict with hardcoded fallbacks 0.7/4096
- `ai_provider.py:327-329` — per-call timeout conditional
- `ai_provider.py:314-326` — response_format construction when schema present
- `ai_provider.py:377-385` — LM-Studio no-schema retry (pops response_format, re-sends params)
- `ai_provider.py:420-424` — transient token list (probe verdict must reuse)
- `ai_provider.py:426-439` — raise / backoff
- `ai_provider.py:96-250` — `GeminiProvider.generate_content`; config `:189-195`, call `:200-204`, cost `:217`; reads no kwargs
- `ai_provider.py:73-94` — `GeminiProvider.__init__` (exposes `.client`, `.genai_types`); canary-sensitive `:79-87`
- `ai_provider.py:260-282` — `OpenAICompatibleProvider.__init__`
- `ai_provider.py:291-297` — messages forwarded verbatim
- `main.py:2888` / `:2893-2904` / `:2905` — `get_visual_clips` / key wall / raw `genai.Client`
- `main.py:2911-2924`, `:2936-2950`, `:2953-2986` — silent-path upload, call+parse (cost None), validate/dedup/return/teardown
- `main.py:3542-3548` — transcript branch; `:3548` sole silent caller; `:3550-3556` RuntimeError
- `main.py:1957-2052` — ≤120 s whole-clip branch; dead guard `:1991`, twin `:1994`, payload `:1995-2001`, detail call `:2002-2003`
- `main.py:2069-2091` — cheap-events block to relocate (guard `:2074`)
- `main.py:2095` — deep gate (`> 60` conjunct dead); `:2098` `_is_gemini` from `_deep_config().input_mode`
- `main.py:2109`, `:2120`, `:2255` — deep frame count / extraction / fallback
- `main.py:2167` — deep factory call (0.2/3000/90, dropped today)
- `main.py:2197`, `:2218-2219`, `:2238` — native upload / inline config call / cleanup (Gemini-without-raw-genai precedent)
- `main.py:2258-2263`, `:2265-2287` — image_url deep call / no-schema retry
- `main.py:2291-2293` — dead deep-cost append (cautionary)
- `main.py:2425`, `:2504-2507`, `:2678`, `:2761-2763`, `:2828-2837` — cost accumulation perimeter
- `main.py:2493`, `:2502-2503` — score batch loop / factory + call (vision reuses instance `:2589-2594`)
- `main.py:2513`, `:2555`, `:2566-2571`, `:2574-2580`, `:2587` — vision gate / loop / peaks / extraction / frames check
- `main.py:2656-2663` — vision shortlist expansion
- `main.py:2757-2759` — detail factory + call
- `main.py:3091-3097`, `:3122` — subtitle polish attempts (escalation currently dead)
- `main.py:115-117`, `:109` — AI_TEMPERATURE/AI_MAX_TOKENS/AI_TIMEOUT eager aliases
- `main.py:1921-1933` — provider/head resolution precedent
- `main.py:86-117` — lazy `_get_*` getters + eager aliases
- `main.py:1612-1647` — `_deep_config` (same/gemini/openai branches)
- `cloud/semantic_analyzer.py:41-176` — `extract_frames_from_window` (signature load-bearing; `[]` on error `:173-175`)
- `cloud/semantic_analyzer.py:178-225` — `analyze_semantic_window`; content build `:199-215`; call `:221-224`
- `cloud/semantic_analyzer.py:233-245` — silent no-signals + per-window ⚠️ failure block
- `cloud/semantic_analyzer.py:316-333` — cost sink / `_record_semantic_cost` / `drain_semantic_costs`
- `cheap_events.py:477-519` — `extract_cheap_events`; `:522-645` `events_to_candidate_windows`
- `app.py:152-178` — header resolution (openai triple, AI provider)
- `app.py:2560-2587` — job env writer (both families); `:2560` per-job subprocess
- `app.py:2661-2695` — toggle headers → effective → ENABLE_* env; deep-auto-off-vision `:2682-2684`
- `app.py:2701-2717` — deep env (resolution `:2702-2709`, DEEP_AI_PROVIDER `:2710`, mirrors `:2711-2717`)
- `app.py:2718-2721` — `[toggles]`/`[deep-env]`/`[cheap]` parent-only prints
- `app.py:1833-1843` — `_visible_logs`; `:1856-1891` — stdout capture → job log (`:1890`) + console
- `app.py:93`, `:115` — BILLING_ENABLED / DEBUG_LOGS
- `log_view.py:5-6`, `:15-25` — strict whitelist rules (unanchored rule needed for child prints)
- `tests/test_no_double_route.py:67-72` — `_no_genai` (class patch; catches `GeminiProvider.__init__`)
- `tests/test_no_double_route.py:122-125` — `fake_create` (swallows kwargs)
- `tests/test_no_double_route.py:129-146`, `:148-158` — two-pass / short-video pins
- `tests/test_no_double_route.py:255-281` — `TestAppLayerEnv` (asserts `:287-295`; adding env keys safe, removing/renaming pinned keys breaks)
- `dashboard/src/App.jsx:2192-2252` — the three cards; `:2224` frame copy; `:2237` deep copy; `:2244-2246` selector; `:1063-1071` headers
- `gemini_worker.py:88-109`, `:268-313`, `:352-357`, `:480`, `:530-557` — VISUAL prompt / SCORE template / SCENE-CUT rule / GeminiBlockedError / cost calc
- `mcp_server.py:54-61` — X-LLM/X-AI headers only; never imports the factory

## Integration Points

### Inbound References
- `main.py:3548` — the only caller of `get_visual_clips` (inside `main()` pipeline flow; no test reaches it today)
- `main.py:2120` / `:2255` (deep) and `:2574-2580` (vision) — the two existing consumers of `extract_frames_from_window`; the silent path becomes the third
- 9 factory call sites listed above — all flow through `create_ai_provider`; FR3 changes every one's OpenAI-compatible behavior
- Dashboard headers (`dashboard/src/App.jsx:1063-1071`) → `app.py` env writer → subprocess env → `main.py` module constants — the only configuration path; the probe needs no new env keys

### Outbound Dependencies
- `ai_provider.py` → `openai` SDK (`:265`) and `google-genai` (`:76-77`), lazy imports (stub-based tests avoid them)
- `cloud/semantic_analyzer.py:16` → `ai_provider` (types + factory import, factory never called there)
- `main.py:2895` → `llm_client` (the wall only — removed by FR1; the `_no_genai`/`_canary_llm_client` semantics must hold)
- `main.py:21` → `google.genai` (raw client at `:2905` — removed by FR1)

### Infrastructure Wiring
- Per-job subprocess: `app.py:2560` `cmd = [sys.executable, "-u", "main.py"]` → module state in `main.py`/`ai_provider.py` is job-scoped (cache home rationale)
- Env writer `app.py:2560-2587` carries BOTH key families (pinned `tests/test_no_double_route.py:255-281` — add-safe, remove/rename-breaks)
- Log pipeline: child stdout → `enqueue_output` (`app.py:1856-1891`) → `jobs[job_id]['logs']` (`:1890`) → `_visible_logs` (`:1833-1843`) → `log_view.py` whitelist (cloud) or raw (self-host)
- Deep overrides: `X-Deep-Provider` → `DEEP_AI_PROVIDER`/`DEEP_OPENAI_*` (`app.py:2701-2717`) → `_deep_config` (`main.py:1612-1647`)

## Architecture Insights

- **One process per job** is the invariant that makes a module-level identity-keyed probe cache correct; per-instance caching structurally cannot satisfy "once per job" (`main.py:2502` rebuilds per batch).
- **`extract_frames_from_window` serves three masters** (deep `:2120`/`:2255`, vision `:2574`, future silent path); its signature and `[]`-on-error contract are load-bearing for all four fixes.
- **`create_ai_provider` is the single choke point**: FR3 is a precondition for FR1's parameter fidelity (the new silent-path call's params would be dropped without it — exactly as deep's already are at `main.py:2167`).
- **Deep runs before scoring** (`main.py:2095` vs `:2493`): any "pre-pass after the score loop" design is too late for deep; the capability gate needs two call sites sharing one cached verdict.
- **`_is_gemini` comes from `_deep_config()["input_mode"]`, not `_get_ai_provider()`** (`main.py:2098`) — deep can override the job provider; hang provider-specific behavior on the config, not the env.
- **Cost accounting perimeter**: `costs = []` (`main.py:2425`) → aggregate (`:2828-2837`); vision uses the sink-drain pattern (`cloud/semantic_analyzer.py:316-333` → `main.py:2678`). The dead deep append (`main.py:2291`) is the standing example of getting this wrong.
- **Parent vs child print surfaces**: `[toggles]`/`[cheap]` are parent-console-only; the job log is child stdout only. Any user-visible warning must be printed by `main.py`.
- **Env namespace rule** (CLAUDE.md): `LLM_*` satellites, `AI_PROVIDER`/`OPENAI_*`/`GEMINI_*` pipeline; the fixes add no env keys (probe is runtime state).

## Precedents & Lessons

5 similar past changes analyzed.

### Precedent: ai_provider abstraction + multimodal candidate detection lands
**Commit(s)**: `9f280ca` — "feat: game profiles, multimodal candidate detection and clip quality pipeline" (2026-09-02); brought into this fork by merge `d450d08` (2026-09-07)
**Blast radius**: 20 files across 5 layers (7,900 insertions) — `ai_provider.py` (NEW), `cheap_events.py` (NEW), `clip_quality.py` (NEW), `cloud/semantic_analyzer.py` (NEW), `main.py` (+1,891), `app.py` (+2,438 churn), 6 new test files
**Follow-up fixes**:
- `d450d08` (2026-09-07) — merge-commit gap fixes: `game_profiles` missing from `USER_OWNED_TABLES` (GDPR), log-prefix assertion, `game_profile_json="{}"` stub
**Takeaway**: four of the five target files have exactly ONE commit of history — institutional memory lives in `main.py`'s older threads and the plan docs, not these files.

### Precedent: routing a Gemini-native pass through any OpenAI-compatible server (closest prior art for FR1)
**Commit(s)**: `c0da654` — "feat(llm): run the moment picker on any OpenAI-compatible server (Ollama, vLLM, LM Studio)" (2026-09-01)
**Blast radius**: 9 files across 4 layers (`llm_backend.py` NEW + `main.py` reroute + `app.py`/UI + tests)
**Follow-up fixes**:
- `25222f5` (2026-09-02) — job-start Gemini-key gate accepted a configured local LLM (4-line `app.py` fix)
- `56707b7` (2026-09-05) — provider-only jobs died at launch on `None` `GEMINI_API_KEY` (same bug class, twice in four days)
**Takeaway**: the env/job gate breaks, not the LLM code — audit every `GEMINI_API_KEY` guard when routing `get_visual_clips`, and add a "no Gemini key configured" test case for the silent path.

### Precedent: the documented decision this FRD overturns
**Commit(s)**: `738d52b` (2026-07-21) silent-video origin + `2e9d6a8` (2026-08-25) sparse-speech gate — both direct `genai.Client` in `main.py`
**Blast radius**: `2e9d6a8`: 2 files (+`tests/test_sparse_speech.py` NEW); `738d52b`: 2 files
**Follow-up fixes**:
- `8fb5e3b` (2026-08-21) — editor UI assumed a transcript exists on wordless jobs
**Lessons from docs**:
- `.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md:2176` — capability matrix verbatim: "Silent-video clip detection (vision) | no — Gemini-only | the endpoint cannot watch video"; same doc `:68`, `:2304` class silent-video under "Stays Gemini"
**Takeaway**: the Gemini pin was deliberate 2026-08-30 policy (wrong belief about video ingestion), not an oversight — update the capability matrix, README "Still needed for silent videos" clause, and related docs in the SAME change or the docs will lie.

### Precedent: capability surfaced as UI copy, never probed (closest prior art for FR2)
**Commit(s)**: `3c655af` (2026-09-02) provider-neutral deep copy; `f83d555`/`448d042`/`a30694e`/`94d8659` (2026-09-05) AI Provider card + connection probe + headers/gates
**Follow-up fixes**:
- `56707b7` (2026-09-05) — gate crash on None key landed the same day as the phases
**Takeaway**: text-only degradation was handled by copy ("auto-disables vision"), never by probing — the one-shot probe is new behavior; the per-window ⚠️ print it replaces is the known cost.

### Precedent: parameter forwarding has a proven pattern in `llm_client` (closest prior art for FR3)
**Commit(s)**: `30ce67e` (2026-08-30) `llm_client.py` NEW; `da48ff2` (2026-08-30) 847-line contract tests NEW; `ec60f4f` (2026-08-30) routing across 5 files
**Follow-up fixes**: none — feature + contract tests landed as one unit and held
**Lessons from docs**:
- `.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md:507-510`, `:2000-2005` — conditional forwarding (`if temperature is not None`) pinned by a recording fake (`seen["max_tokens"]`)
- Same doc `:2337` — review near-miss: broad `except (..., ValueError)` swallowed `GeminiBlockedError` (a ValueError subclass)
**Takeaway**: pin forwarding with a recording fake; route provider refusals explicitly before any broad exception tuple.

### Composite Lessons
- **Job gates break, not LLM code** (`25222f5`, `56707b7`) — the single most likely regression for FR1.
- **`tests/test_no_double_route.py` is the hard boundary** (`d450d08`; FRD acceptance) — `genai.Client` unconstructed and `llm_client.chat` uncalled on the openai path, including from any new silent-path code.
- **Merges drop half a fix** (`25222f5`) — after touching `app.py` gating, grep both `AI_PROVIDER`/`OPENAI_*` and `GEMINI_API_KEY` readers.
- **Broad exception tuples hide provider failures** (plan `:2337`) — the probe must classify explicitly (transient-first), never degrade silently.
- **Docs update in the same change** — capability matrix + README silent-video clause + deep copy references.

## Historical Context (from `.rpiv/artifacts/`)
- `.rpiv/artifacts/discover/2026-09-08_08-39-20_candidate-detection-openai-compat.md` — source FRD: four gaps, acceptance criteria, live-run gates (its line cites superseded by this doc's correction table)
- `.rpiv/artifacts/plans/2026-09-06_14-41-23_marsic-fork-parity-merge.md` — merge plan: M4 gate retarget (`:581-596`), "Stays Gemini" list incl. silent-video (`:1823`), verification notes (`:629`, `:637`), README clause (`:1936`)
- `.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md` — provider plan: capability matrix (`:2176`), llm_client forwarding pattern (`:507-510`, `:2000-2005`), get_viral_clips pin (`:1613`), GeminiBlockedError near-miss (`:2337`)
- `.rpiv/artifacts/discover/2026-09-06_05-40-55_marsic-fork-parity-merge.md` — stage-ownership discovery (ai_provider vs llm_client)
- `.rpiv/artifacts/research/2026-09-06_06-09-59_marsic-fork-parity-merge.md` — merge research (stage-ownership context)

## Developer Context
**Q (discover: FRD target: verify + fix, not build)**: The candidate-detection panel, toggles, and copy already exist in the tree (`dashboard/src/App.jsx:2192-2260`) — what should this FRD cover?
A: Verify + fix compat gaps; UI stays untouched.

**Q (discover: Provider terminology)**: Does `AI_PROVIDER=openai` mean an OpenAI-compatible endpoint like Ollama Cloud?
A: Yes — the OpenAI-compatible chat-completions protocol; endpoint from `OPENAI_BASE_URL`, model from `OPENAI_MODEL`, key optional.

**Q (discover: Cheap signals stay as-is)**: The three cheap signals are provider-neutral local computations entering identical prompt text for both providers. Keep as-is?
A: No code change. Developer delegated the judgment; live-run acceptance criterion added.

**Q (discover: Deep-scan frame fallback stays as-is)**: Deep scan under OpenAI-compatible inspects 12 sampled frames; Gemini keeps native video. Keep as-is?
A: No code change. Same delegated judgment; live-run acceptance criterion added.

**Q (discover: Scope: four gaps in, two out)**: Which probe-found gaps become requirements?
A: Silent-video wall, no-vision warning, honor timeout/params, short-video signals. Cost accounting and schema-400 resilience excluded.

**Q (discover: Acceptance endpoints)**: Which OpenAI-compatible endpoint(s) must the acceptance runs use?
A: Ollama Cloud + local.

**Q (discover: Gemini must not regress)**: Must the fixes leave the existing Gemini path untouched?
A: Yes — suite baseline and `tests/test_no_double_route.py` pins are acceptance criteria.

**Q (`main.py:1991-2001`): On short videos the cheap-signal hints land in the detail request's two text fields; long videos fill them separately (audio `main.py:2719`, scene `:2732`). Combined string in both fields, or split per type?**
A: Split per type — audio events under `audio_events`, scene cuts under `scene_boundaries`, matching the long path (the detail prompt's SCENE-CUT rule reads `scene_boundaries` and would misfire on audio spikes).

**Q (`main.py:2095`): Deep never runs ≤120 s (early return precedes the gate; the `> 60` conjunct is dead code) while panel copy promises deep "watches the whole video". Extend deep to 61-120 s?**
A: Keep deep off ≤120 s — status quo; flag the dead gate and the copy gap. Moving only `main.py:2069-2091` (not the whole seeding `try`) is mandatory to preserve this.

**Q (`ai_provider.py:189-195`): Forwarded params — store on both clients but apply only on OpenAI-compatible (Gemini byte-identical), or apply on Gemini too (all Gemini requests change sampling)?**
A: Store both, apply OpenAI-compatible only. Gemini requests stay byte-identical; the acceptance test "forwards to the client constructor" still passes.

**Q (`app.py:2719-2721` / `log_view.py:15-25`): The warning's visibility — `[toggles]`/`[cheap]` never reach the job log (parent prints); on cloud a whitelist hides child prints. Self-host only, or cloud too?**
A: Cloud too — the warning must be visible in the dashboard job log everywhere: one unanchored rule in `log_view.py:_RULES` (server-side file; dashboard files untouched). One `print` in `main.py` is the emission point.

## Related Research
- `.rpiv/artifacts/research/2026-08-30_16-03-23_connect-llm-provider-frontend.md` — earlier research on connecting the LLM provider frontend
- `.rpiv/artifacts/research/2026-09-04_20-47-42_connect-llm-provider-frontend.md` — follow-up research on the same provider-frontend surface

## Open Questions

- None — the developer deferred nothing explicitly (FRD carried forward verbatim; all four checkpoint questions resolved above).
- Deferred to design stage (not developer questions): the probe's own timeout value (mitigates the 500-with-image-text retry trap); whether the false-`vision_ok` case (server accepts and ignores images) gets any handling beyond the existing per-window backstop; whether deep's native-video inline config (`main.py:2218`) should also receive the stored deep params (outside plain FR3).
