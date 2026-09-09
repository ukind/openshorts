---
date: 2026-09-04T20:47:42+0700
author: Yogiswara Utama
commit: 35e9d7e
branch: main
repository: openshorts
topic: "Connect the OpenAI-compatible LLM provider to the dashboard frontend"
tags: [research, codebase, llm-provider, dashboard, byok, openai-compatible, frontend, settings, api-config, probe-endpoint, pire-browser]
status: ready
last_updated: 2026-09-04T20:47:42+0700
last_updated_by: Yogiswara Utama
---

# Research: Connect the OpenAI-compatible LLM provider to the dashboard frontend

## Research Question

Connect the OpenAI-compatible LLM provider (backend, shipped commits `30ce67e`–`35e9d7e`) to the dashboard frontend: a new AI Provider settings card in self-host Settings collects an endpoint URL, API key and model; the triple is stored encrypted in the browser and sent as the `X-LLM-*` header triple; the app front gate widens from "has a Gemini key" to "has any AI backend"; validation runs end-to-end through pire-browser against a local Python stub endpoint.

> **Graph-freshness caveat:** the codebase knowledge graph was indexed 2026-08-30T05:06:48Z (pre-LLM-provider commits). Every backend LLM surface (`llm_client.py`, `app.py` LLM additions, `cloud/alerts.py`, `mcp_server.py`, `saasshorts.py`) and every cited dashboard/test file is `metadata_changed` or `not_tracked` in the graph. All `file:line` citations below come from **live-source reads** by analysis agents (grep/read), not the graph. Re-index after the feature lands.

## Summary

The backend LLM **core** is shipped: `resolve_llm` (`app.py:143`) plus six call sites, `llm_client.py` (`config_from`, `active_config`, `_post`, `chat`), and the `LLM_ENDPOINT_HINT` string. The frontend has **zero** provider code (`llmConfig`/`llmHeaders`/`llmConfigured`/`lib/llm.js`/`LlmProviderCard.jsx` do not exist).

The single load-bearing finding: the FRD's "two small backend additions" — the `/api/config` LLM fields (`_env_llm_config`) and the `POST /api/llm/test` probe endpoint (`probe()`, `_require_usable()`, a one-off short-timeout client) plus `tests/test_llm_endpoints.py` — are **NOT in the tree at HEAD `35e9d7e`**, despite a 2026-08-31 handoff reporting them complete and tested. The plan artifact's checked `[x]` boxes describe lost code. Phase 1 §1+§2 must be implemented fresh.

Three corrections to the received framing: (1) `/api/saasshorts/generate` is fal.ai+ElevenLabs-pinned, **not** Gemini-pinned — the feared SaaS generate seam does not exist; (2) ResultCard's "six Gemini-pinned endpoints" is actually **two** (`/api/effects/generate`, `/api/edit`), and its 400 never fires because the client-side gate at `ResultCard.jsx:288` throws first; (3) the metering probe is unreachable on self-host (`reserve_process_minutes` returns early at `app.py:285-286`), so the rate-limiter bucket sharing is safe.

Four design decisions were resolved at checkpoint (see Developer Context): headers-only e2e stub; retry the `/api/config` fetch on failure; harden the `decrypt` migration guard against key-rotation garbage; keep the probe client at connect 5s/read 20s and clarify the NFR text.

## Detailed Findings

### Backend LLM capability layer (shipped) and what is NOT shipped

- `resolve_llm(request, task)` at `app.py:143-169` is the resolver: under `BILLING_ENABLED` it returns `None` (cloud stays Gemini-pinned); otherwise `llm_client.config_from(X-LLM-Base-Url, X-LLM-Key, task, X-LLM-Model)` then falls back to `active_config(task)`. A guarded `import llm_client` makes a missing SDK a gate, not a 500 (`app.py:156-159`). Header config does **not** survive a redeploy resume (the manifest rebuilds env from `os.environ` only; docstring cites `app.py:876`).
- Six live `resolve_llm` call sites: `app.py:2112` `/api/process` (`task=None`), `app.py:4816` `/api/thumbnail/analyze` (`task="thumbnail"`), `app.py:4921` `/api/thumbnail/titles`, `app.py:5006` `/api/thumbnail/generate`, `app.py:5133` `/api/thumbnail/describe`, `app.py:5337` `/api/saasshorts/analyze` (`task="saas"`). Each is followed by a two-branch raise: self-host `400 LLM_ENDPOINT_HINT`, cloud `gemini_missing_error()`.
- `llm_client.config_from` (`llm_client.py:150-178`) returns `None` unless **both** base and key are present (a header key must never travel to an env-configured base_url); an explicit model wins over the `LLM_MODEL_<TASK>` → `LLM_MODEL` env chain; with no model anywhere it warns once and returns `None` (backend inert). `active_config` (`llm_client.py:181-185`) is the env path.
- `llm_client.chat` (`llm_client.py:356-409`) validates the URL is a full `http(s)://` URL (`:364-368`) and that a model is set (`:369-373`); builds `Authorization: Bearer <key>` (`:409`); runs a `response_format` ladder (json_schema → json_object → bare). `LlmConfig.api_key` is `field(repr=False)` (`llm_client.py:92`), so reprs/tracebacks exclude the key.
- **NOT shipped (Phase 1 §1+§2 deliverables, confirmed absent by repo sweep):** `POST /api/llm/test`, `probe()`, `_require_usable()`, a one-off short-timeout probe client, `_env_llm_config`, the `llmConfigured`/`llmModel`/`llmBaseUrl` `/api/config` fields, `dashboard/src/lib/llm.js`, `dashboard/src/components/LlmProviderCard.jsx`, `tests/test_llm_endpoints.py`. `get_config` (`app.py:1871-1874`) returns exactly four fields today (`youtubeUrlEnabled`, `billingEnabled`, `googleAuthEnabled`, `jobRetentionSeconds`). A `grep` for `api/llm` in `app.py` returns nothing.

### The `POST /api/llm/test` probe endpoint (Phase 1 §2)

- **Rate limiter.** `_check_probe_rate(user_id)` (`app.py:259-266`) keys `_probe_times` (`app.py:238`) by `str(user_id)`; `PROBES_PER_HOUR = 15` (`app.py:239`); raises 429 at the cap. The only existing caller passes `user.id` at `app.py:318`, but `reserve_process_minutes` returns early on self-host (`if not BILLING_ENABLED: return ...` at `app.py:285-286`), so that call is unreachable there. Reusing the limiter with `request.client.host` on self-host **cannot** starve metadata probing (the metering path never runs). Forking is mechanically equal to reuse; the `_analytics_times` precedent (`app.py:4514`) guards two *simultaneously-active* consumers, which does not apply. `request.client` can be `None` — guard it (`request.client.host if request.client else "unknown"`, house precedent at `app.py:2213`); the limiter ignores `X-Forwarded-For`, so all clients behind one NAT share one 15/hour bucket.
- **Status-code contract.** `apiFetch` (`dashboard/src/lib/api.js:22-37`) intercepts HTTP **402 → `QuotaError`** (`:29-35`) **before** `apiJson` (`:54-63`) sees the body, so a provider's own 402 is never surfaced as a top-up prompt from this endpoint. `apiJson` throws `ApiError(status, detail, raw)` for non-ok, keeping `JSON.parse(text).detail` only when it is a string. The endpoint must answer: **404** (billing), **429** (limiter), **400** (no config resolves → `LLM_ENDPOINT_HINT` at `app.py:230-232`; malformed URL / missing model), **502** (upstream rejection / transient / blocked). **Never 402.** The plan maps any `LlmError` whose message `startswith("LLM provider")` to 502 and lifts the two prefix-free validation messages (malformed URL `llm_client.py:365-368`, missing model `:370-373`) to 400.
- **The discriminator must use `startswith`, not `in`.** Every `_post`-family message starts with the literal `"LLM provider"` (`llm_client.py:240,243,257,260,266,286,292,301,310,448,453` + `_http_client` `:144`). Provider error text is interpolated *after* the prefix (`:260-261`), so a provider body can contain the substring mid-message. `startswith` is immune; `in` would flip a genuine upstream failure to 400. `cloud/alerts.py::_classify_failure` (`:62-63`) uses `in` and checks it *first* (before the proxy-hint check at `:64`) — that ordering is what keeps a provider 402 classified as "llm provider", not "proxy". Do **not** reword any `_post` message or the two lifted messages (the plan's do-not-reword row pins this).
- **One-off probe client (required, not stylistic).** `_http_client(base_url)` (`llm_client.py:137-147`) hardcodes `timeout=_TIMEOUT` (`:130`, `read=300.0`) and caches by `base_url` alone with no timeout parameter. A connect-5/read-20 probe cannot ride it without a signature + cache-key change; without the change the first probe and the first real `chat()` call would fight over one cached client with incompatible timeouts. The plan's separate uncached `_probe_client` is required. `_post` accepts any client (`:232`), so the POST + error-mapping logic is fully reusable (no fork of `_post`). Both are synchronous, so the endpoint must offload to a thread (`run_in_executor`, precedent at `app.py:325-330`).
- **Key-echo.** The key lives only in the request header (`Authorization: Bearer <key>`, `:409`). `_err_fields` (`:189-205`) and `_err_detail` (`:207-213`) read the *response* body (message/code/type + `text[:400]`), never request headers; `_post` truncates to `[:300]` (`:256-261`). Our code cannot format the key into `detail`. The only leak path is provider-controlled echo (a base URL whose error body repeats the received `Authorization` header) — on self-host BYOK the requester supplied the key, so an echo returns it to its owner. The residual exposure is the reviewed SSRF row: unauthenticated reach + 300 chars of verbatim upstream error text forms an internal-network oracle, rate-capped but not reach-capped.
- **`task=None` trap.** `config_from` reads `LLM_MODEL_<TASK>` only when a task is named (`:171-172`); the existing `task=None` caller sits at `app.py:2112`. The probe must loop `("thumbnail","saas")` (plan) so a server with only `LLM_MODEL_THUMBNAIL` resolves on the first iteration; calling `resolve_llm(request)` with `task=None` would report `llmConfigured:true` via `_env_llm_config` but 400 the probe.
- **Lost-work detail to re-incorporate.** The 2026-08-31 handoff records a `_redact()` closure at the `app.py` endpoint boundary that strips the key from provider error text before echoing `detail`. Phase 1 §2 must re-add this belt-and-suspenders measure when re-implemented.

### `/api/config` capability channel, AuthContext, and the app gate (Phase 1 §1 + Phase 2 + Phase 4)

- **`/api/config` is additive.** `d0e1f5a` (2026-08-20) is the one-commit end-to-end path for adding a `/api/config` field: endpoint → `AuthContext.jsx` → `useAuth()` destructure. The three LLM fields must stay additive. `MediaInput.jsx:52` is a second bare `/api/config` consumer but checks `r.ok` and reads only `youtubeUrlEnabled` — it needs no edit.
- **AuthContext fetch is bare and one-shot.** `AuthContext.jsx:104` does a bare `fetch(getApiUrl('/api/config'))` (not `apiFetch`, no `res.ok` check). On success `setConfig(cfg)` (`:105`). On **any** failure the catch at `:110` swallows it; `config` keeps its 2-key init (`:18`) and `setLoading(false)` runs unconditionally (`:111`). Three failure modes (network refusal, non-JSON body, HTTP-error-with-JSON) all yield `loading=false` + empty config. The Phase 2 passthrough `llmConfigured: !!config.llmConfigured` (`:138`) then reads false forever.
- **Phase 4 gate needs an error-state guard (decision).** The plan's `keysMissing = ... && !authLoading` covers only the slow path (`loading=true`). On a failed fetch, `llmConfigured` is false forever, so the badge (`App.jsx:1162`), banner (`:1183`) and modal trigger (`:774-776`) fire **permanently** on an env-configured server — the review's flash bug becomes a permanent state. Also: no fetch timeout exists, so a hang keeps `loading=true` and the gate suppressed for the session. Decision: **retry the fetch** (1-2 retries with backoff). The fetch runs once per mount (`:113`, stable callback deps).
- **Badge/banner/modal copy surfaces (no line-number divergence).** The plan's `1169-1175` (badge span) and `1189-1195` (banner span) ARE the live line numbers of the copy spans; `1162`/`1183` are the surrounding block conditions. Badge block `App.jsx:1162-1178`; banner block `1183-1205`; modal `1925-1960`; modal trigger `774-776` (`showKeyModal` set only here). `aiBackendMissing = 12` is arithmetically sound (12 fence lines) but **format-fragile** — count after prettier. Two strings sit inside the `keysMissing` blocks but outside every fence: the banner lead-in at `App.jsx:1188` ("Required API keys missing.") and the badge `title` at `:1166` — FR 8 covers banner+badge copy, so these should join Phase 4.
- **Header build spread-merge fits.** `App.jsx:792` is `const headers = apiKey ? { 'X-Gemini-Key': apiKey } : {};` with later property mutations (`Content-Type` at `:809`/`:820`); the FormData branch (`:828-835`) leaves it untouched; single consumption at `apiFetch('/api/process', { headers, body })` (`:838`). The plan replacement `{ ...(apiKey ? {...} : {}), ...llmHeaders(llmConfig) }` is a `const` object literal — property mutation stays legal, and `apiFetch` wraps it with `new Headers(...)` (`api.js:16-19`). Zero downstream edits. `App.jsx:792` is the only `X-Gemini-Key` build site in the file.

### Per-feature capability split (Phase 5)

- **Complete `resolve_llm` map (live).** Six sites (`app.py:2112,4816,4921,5006,5133,5337`). Endpoints with **no** `resolve_llm`: `/api/saasshorts/generate` (`:5770`), `/api/saasshorts/actor-upload` (`:5407`), `/api/saasshorts/actor-options` (`:5445`), `/api/saasshorts/post` (`:5514`), `/api/thumbnail/upload` (`:4715`), `/api/thumbnail/publish` (`:5167`), `/api/edit` (`:2739`), `/api/effects/generate` (`:3705`), `/api/subtitle` (`:3822`), `/api/subtitle/remove` (`:4029`), `/api/hook` (`:4098`), `/api/translate` (`:4243`). Companion `resolve_gemini` sites (`app.py:124-140`): `/api/process:2111`, `/api/edit:2748`, `/api/effects/generate:3711`, `/api/thumbnail/analyze:4815`, `/api/thumbnail/titles:4920`, `/api/thumbnail/generate:5003`, `/api/thumbnail/describe:5132`, `/api/saasshorts/analyze:5336`.
- **SaaS generate is NOT Gemini-pinned (correction).** `/api/saasshorts/generate` (`app.py:5770-5933`) takes only `X-Fal-Key` + `X-ElevenLabs-Key` (`:5775-5776`), gates on those two (`:5782-5785`), and `generate_full_video` (`saasshorts.py:1323`) is a media pipeline with no genai/Gemini after that line. The feared seam does not exist. The FRD claim "AI Shorts text stages route to the LLM provider" survives: `analyze_saas(..., llm_config=llm)` (`app.py:5367-5368`) and `generate_scripts(..., llm_config=llm)` (`:5380-5382`) route to the provider. **One residual seam:** grounded web research `research_saas_online` is Gemini-only and silently skipped (`app.py:5359-5366` runs it only `if gemini_key`); an LLM-only analyze succeeds with reduced research depth and no signal. Phase 5 §2 as written is sufficient; the only optional addition is an analyze-step note for the skipped research.
- **SaaShortsTab call-site census.** `geminiHeader`/`needsGeminiKey` (`SaaShortsTab.jsx:46-47`) have four uses each: `:46`, `:47`, the analyze guard `:203-206`, and the analyze spread `:216`. The generate path (`:291`, `:338`) sends only `X-Fal-Key`+`X-ElevenLabs-Key`; no AI header rename is needed there.
- **Thumbnail census is correct.** `keyHeader` (`ThumbnailStudio.jsx:76`) appears on exactly six lines: the definition plus five spreads (`:172` analyze, `:218` manual title, `:241` refine, `:288` generate, `:358` describe). Upload (`:136`) and publish (`:397`) carry no AI header, and their backends need none (`/api/thumbnail/upload` `app.py:4715-4759` saves the file + local Whisper; `/api/thumbnail/publish` `app.py:5167-5216` needs only the Upload-Post pair). After rename, `grep -c "aiHeaders"` = 6. `needsKey` lives on six lines (`:77` def, `:155`, `:268`, `:347`, `:495`, `:507`); Phase 5 replaces all six with `needsAiBackend` and adds `needsGeminiForImages` at five sites. The `/api/thumbnail/generate` Gemini hard-requirement (`app.py:5003-5005`, *before* `resolve_llm` at `:5006`) justifies `needsGeminiForImages`.
- **ResultCard correction.** The "six Gemini-pinned endpoints" premise overstates: live code pins **two** — `/api/effects/generate` (`app.py:3705`, Gemini-only `:3711-3714`) and `/api/edit` (`app.py:2739`, body `api_key` or Gemini `:2748-2751`; the frontend sends no body key). `/api/subtitle/remove` (`:4029`) is a file swap with no `resolve_gemini`; `/api/subtitle`, `/api/hook`, `/api/translate`, `/api/social/post` all work without a Gemini key. So an LLM-only user loses exactly one affordance: AI Edit. The backend `400 "Missing X-Gemini-Key"` is **unreachable** from `handleAutoEdit` because the client-side gate at `ResultCard.jsx:288` (`if (!apiKey && !isManaged)`) throws first at `:289` ("Gemini API Key is missing. Please set it in Settings.") before any network call. The error renders inline (`:866-870`) and auto-clears after 5s (`:359`). The affordance is not silent — it fails fast with a truthful Gemini-naming message. The plan's non-touch stance stays defensible; the only change worth considering is a copy tweak at `:289` to state an AI provider cannot serve clip AI-edit.

### Gemini-key migration (Phase 2)

- **`decrypt` contract.** `decrypt` (`App.jsx:48-67`) never throws: falsy → `''` (`:49`); only `ENC:`-prefixed strings enter the branch (`:50`); `atob(raw)` on invalid base64 throws and is caught → `''` (`:54`, `:59-61`); a valid-base64-but-corrupt payload → `atob` succeeds → XOR returns a non-empty **garbage** string (`:55-57`); a no-prefix value returns verbatim (`:66`, plaintext passthrough). `encrypt` catches its own failure and returns plaintext (`:42-44`).
- **Migration guard must harden (decision).** The plan's `if (decrypted) return decrypted` covers only the empty case, not the garbage case. A `VITE_ENCRYPTION_KEY` rotation turns every stored blob into the garbage case → the garbage becomes the key, is sent as `X-Gemini-Key`, and is re-encrypted on save. Decision: **harden the guard** — validate the decrypted value (round-trip re-encrypt-and-compare, or a non-empty alphanumeric check) before adopting; fall through to the legacy `gemini_key` migration on mismatch.
- **`gemini_key` reader sweep.** Exactly three readers in `dashboard/src`: `App.jsx:211` (state init read), `App.jsx:574` (persistence write), `ResultCard.jsx:284` (dead fallback). `KeyInput.jsx` is clean (takes `savedKey` as a prop, touches no storage; `:4`, save handler `:15-19`). The ResultCard fallback is dead in practice (App passes `geminiApiKey={apiKey}` seeded from the same storage). The plan deletes the ResultCard fallback in the same phase as the migration — **required**, because after migration `removeItem`s `gemini_key`, that fallback reads `null`. Post-change `grep` arithmetic: 2 hits (one `getItem` + one `removeItem` in the initializer; the `:574` write is rewritten to `geminiKey_v1`).
- **`llmConfig_v1` blob is safe.** The initializer wraps `JSON.parse(decrypt(stored))` in try/catch and returns the empty triple in the catch. Every corrupt path lands in the catch by design (invalid base64 → `''` → `JSON.parse('')` throws; valid-base64 garbage → `JSON.parse` throws; truncated write; parsed `null`/scalar → destructuring throws). The catch is designed recovery, not a hazard.
- **No data-preserving localStorage migration has ever shipped (precedent).** The only key-change on record, `d60c376`, renamed `uploadPostKey_v2`→`uploadPostKey_v3` and chose forced re-entry over migration. The `gemini_key`→`geminiKey_v1` one-way move is untested territory.

### Test + e2e harness

- **`probe()` does not exist** in `llm_client.py` (definitions end at `chat` `:356`); it is a plan addition. For `probe()` to succeed: it calls `_require_usable(config)` then a one-off `_probe_client(base_url)`; `_post` sends POST `/chat/completions`, on `<400` returns `resp.json()` (non-JSON → `LlmTransientError` `:264-266`); `probe()` discards the body and returns `None` when `_post` does not raise. So the **minimum stub response is any 2xx with a JSON-parseable body** — the plan stub is more than sufficient; `Content-Type` is decorative. The `choices` shape matters only on the `chat()` path (which "Test connection" never takes); usage is optional on both paths (`_cost_from_usage` returns `None` for non-dict usage `:320`; pin `tests/test_llm_client.py:123-127`).
- **Path join.** The stub binds `('',11434)` and its `do_POST` ignores `self.path`, so the join cannot mismatch. The live join produces `/v1/chat/completions` (pinned at `tests/test_llm_client.py:70-71`); the scheme check accepts `http://` (`llm_client.py:364`).
- **E2E stub decision (headers-only).** The FRD `/api/process` bullet routes through `resolve_llm` → `chat()` with a pydantic schema → `_parse_json_response_text(text)` (`llm_client.py:443-447`) fails on the body `'ok'` (not JSON) → `LlmTransientError`. Decision: keep the simple stub; inspect the outgoing `/api/process` request headers via pire-browser and accept the job fails at the LLM step (the bullet only asks to confirm headers). No schema-valid stub needed.
- **Orchestration.** `dev.sh` starts three services (backend 8000, renderer 3100, frontend 5173) and **no stub**. `dev.sh:240` sets `VITE_PROXY_TARGET=http://127.0.0.1:8000` for the frontend itself, so the journey needs **no manual proxy variable** (the default `http://backend:8000` is a docker-compose service name that does not resolve on the host — only matters for hand-run Vite). The stub on 11434 is a fourth process with no owner in `dev.sh` (absent from the stop loop `dev.sh:173-175`); run `./dev.sh --no-renderer` in one shell + the `http.server` one-liner in a second shell; killing the stub is manual. No pire/playwright/e2e test wiring exists anywhere (`docker-compose.e2e.local.yml` is a compose file, not a harness).
- **`conftest.py` is 11 lines, no fixtures.** `sys.path.insert(0, <repo root>)` at `tests/conftest.py:5` makes `import app` work from any CWD; `os.environ["BILLING_ENABLED"]="0"` at `:11` runs before any app import (`app.py` freezes the flag at import, `load_dotenv` never overrides an existing variable — comment at `:7-9`). The `_clean_slate` fixture is planned **inside the new test file**, not conftest.
- **Subprocess billing test survives with two fragilities.** The script sets four env vars before `import app`; `load_dotenv()` (`app.py:32`) defaults to `override=False` (pre-set vars win); `BILLING_ENABLED` reads "1" (`app.py:91`); the billing branch imports the `cloud` package (`app.py:103-106`). Fragility 1: **CWD dependence** — conftest patches `sys.path` in the pytest process only; a `python -c` child gets no patch and puts CWD at `sys.path[0]`; the sketch passes no `cwd` → passes only when pytest runs from the repo root. Fix: pass `cwd=os.path.dirname(app_module.__file__)` or prepend a `sys.path` insert in the script. Fragility 2: **cloud deps** — a venv without `requirements-billing.txt` makes the child die on ImportError.
- **In-process module needs no isolation.** Importing `app` in-process is established practice (`tests/test_mcp_endpoint.py:14-15`, `tests/test_social_tenant_isolation.py:24`). Import-time side effects are two `makedirs` (`app.py:37-38`) + stdio UTF-8 reconfigure (`app.py:80-87`), both harmless. Under `TestClient` every request shares host key `"testclient"`; the planned `TestConnectionCheck` posts ~10 times (under 15); the autouse `_clean_slate` clears `_probe_times` per test; billing is off so `reserve_process_minutes` returns early. The subprocess is needed for the billing class only. `Lifespan` never runs under `TestClient` without a `with` block (`app` is `FastAPI(lifespan=lifespan)` at `app.py:1602`), so no disk scan / startup tasks — both the planned fixture and subprocess script rely on this.
- **Acceptance command runnable.** `pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` runs both in one process; `test_llm_client.py` is app-free at module scope (imports httpx/pytest/json/pydantic/llm_client/gemini_worker) and lazily `importorskip("main")` (`:515-516`, pulls cv2/ultralytics/torch/mediapipe — seconds, not failures). The two modules share no mutable state that collides.

## Code References

- `app.py:143-169` — `resolve_llm(request, task)`: BYOK header triple → `config_from` → `active_config`; `None` under billing.
- `app.py:230-232` — `LLM_ENDPOINT_HINT` string (the 400 detail for "no AI backend").
- `app.py:238-266` — `_probe_times` + `PROBES_PER_HOUR=15` + `_check_probe_rate(user_id)`.
- `app.py:285-286` — `reserve_process_minutes` early return on self-host (metering probe unreachable).
- `app.py:318` — sole existing `_check_probe_rate` caller (`user.id`, cloud-only).
- `app.py:1871-1874` — `get_config()` returns exactly 4 fields today (no LLM fields).
- `app.py:2112,4816,4921,5006,5133,5337` — the six `resolve_llm` call sites.
- `app.py:5003-5005` — `/api/thumbnail/generate` Gemini hard-requirement (before `resolve_llm`).
- `app.py:5359-5366` — `research_saas_online` Gemini-only silent skip in SaaS analyze.
- `app.py:5770-5785` — `/api/saasshorts/generate` is fal.ai+ElevenLabs-pinned (no Gemini).
- `llm_client.py:92` — `LlmConfig.api_key` is `field(repr=False)`.
- `llm_client.py:130-147` — `_TIMEOUT` (read=300s) + `_clients` cache + `_http_client` (no timeout override).
- `llm_client.py:150-178` — `config_from`: base+key required; model env chain; warns + `None` if no model.
- `llm_client.py:181-185` — `active_config` (env path).
- `llm_client.py:232-266` — `_post`: POST `/chat/completions`; blocked/transient/rejected mapping; `"LLM provider"` prefix.
- `llm_client.py:356-409` — `chat`: URL + model validation; `response_format` ladder; `Authorization: Bearer <key>`.
- `llm_client.py:443-447` — `_parse_json_response_text` fails on non-JSON (the e2e stub decision).
- `dashboard/src/lib/api.js:22-37` — `apiFetch`: 402→`QuotaError` before body seen; adds bearer.
- `dashboard/src/lib/api.js:54-63` — `apiJson`: `ApiError(status, detail, raw)` for non-ok.
- `dashboard/src/contexts/AuthContext.jsx:18,104-111,138` — config state, bare fetch, catch+setLoading, value passthrough.
- `dashboard/src/App.jsx:33-67` — `ENCRYPTION_PREFIX`, `encrypt`, `decrypt` (garbage-case at `:55-57`).
- `dashboard/src/App.jsx:211,574` — `gemini_key` plaintext read/write.
- `dashboard/src/App.jsx:708,774-776,792,838,1162-1178,1183-1205,1925-1960` — gate, modal trigger, header build, fetch, badge, banner, modal.
- `dashboard/src/components/SaaShortsTab.jsx:46-47,203-206,216,291,338` — Gemini header + guard + generate path.
- `dashboard/src/components/ThumbnailStudio.jsx:76-77,172,218,241,288,358,495,507` — `keyHeader`/`needsKey` + five spreads + gate.
- `dashboard/src/components/ResultCard.jsx:280-363,288-289,866-870` — `handleAutoEdit`, client-side gate, inline error.
- `dashboard/src/components/MediaInput.jsx:52-55` — second `/api/config` consumer (additive, no edit).
- `dashboard/src/components/KeyInput.jsx:4,15-19` — clean: `savedKey` prop, no storage.
- `cloud/alerts.py:62-64` — `_classify_failure`: `in` check first (prefix ordering).
- `tests/conftest.py:5,11` — `sys.path` + `BILLING_ENABLED=0` (no fixtures).
- `tests/test_llm_client.py:70-71,123-127,515-516,707` — path-join pin, garbage-usage pin, lazy `main` import, whitespace-model pin.
- `dashboard/vite.config.js:8,22-29` — proxy target + `/api` entry (FRD's `:33-39` does not exist).

## Integration Points

### Inbound References
- `app.py:143-169` (`resolve_llm`) — reads the `X-LLM-Base-Url` + `X-LLM-Key` (+ optional `X-LLM-Model`) header triple from the six call sites; the frontend `X-LLM-*` triple (built per-component beside `X-Gemini-Key`, never in `apiFetch`) is the inbound source.
- `app.py:1871-1874` (`get_config`) — two inbound consumers: `AuthContext.jsx:104` (bare fetch → `useAuth()`) and `MediaInput.jsx:52` (additive, reads only `youtubeUrlEnabled`).
- `mcp_server.py` `_FORWARD_HEADERS` — already forwards `x-llm-base-url`/`x-llm-key`/`x-llm-model` (added in `e96407b`); the MCP path is a live reference implementation of the exact header triple the dashboard must speak.

### Outbound Dependencies
- `llm_client.py:150-178` (`config_from`) — the contract the frontend header triple must satisfy (base+key both present, or `None`).
- `llm_client.py:356-409` (`chat`) — the actual OpenAI-compatible call; `_http_client` (`:137`) is the shared 300s client the probe must NOT reuse.
- `cloud/alerts.py:62-63` (`_classify_failure`) — downstream alert classification keyed on the `"LLM provider"` substring; the endpoint's 502 `detail` must keep the prefix so alerts classify correctly.

### Infrastructure Wiring
- `app.py:238-266` — rate limiter (`_probe_times` / `_check_probe_rate`) the `/api/llm/test` endpoint reuses (self-host, keyed by `request.client.host`).
- `app.py:32,91,103-106` — import-time `load_dotenv()` + `BILLING_ENABLED` freeze + cloud import; the subprocess billing test must set env before `import app`.
- `dashboard/vite.config.js:8,22-29` — dev proxy; `dev.sh:240` sets `VITE_PROXY_TARGET` so the e2e journey needs no manual var.
- `tests/conftest.py:5,11` — `sys.path` + `BILLING_ENABLED=0`; the new test module imports `app` in-process (no isolation needed) except the billing subprocess class.

## Architecture Insights

- **The BYOK pattern is per-request headers, not centralized.** `apiFetch` deliberately does NOT carry BYOK keys (Non-Goal: would send the provider key on uploads and social posting). Each feature component builds its own `X-Gemini-Key` + `X-LLM-*` headers at the point of the AI-bearing call. The new `X-LLM-*` triple must follow this — built per-component, never in `apiFetch`.
- **Self-host has no auth (`app.py:106-121`), which constrains all persistence.** Provider config stays browser-stored (encrypted `localStorage` `llmConfig_v1`) and travels per-request — matching the `X-Gemini-Key` precedent. A server-side write endpoint would be world-writable and is excluded.
- **Capability is split per feature, not per app.** The gate widens from "has a Gemini key" to "has any AI backend" (`llmConfigComplete(llmConfig) || llmConfigured`), but per-feature Gemini-only steps (thumbnail image generation, ResultCard AI-edit) get inline notes, not whole-tab blocks. The classifier routes face-less shots but the provider only serves text stages; image generation stays Gemini-pinned.
- **The `X-LLM-*` triple crosses the job subprocess boundary and dies on resume** (exactly like `X-Gemini-Key`); the `resolve_llm` docstring states this. Do not copy fal.ai's plumbing for a key that needs to survive a job — it doesn't.
- **Error classes are flat and decide the catch map.** `LlmError(Exception)`, `LlmTransientError(Exception)` (not a subclass), `GeminiBlockedError(ValueError)` (not a subclass). The plan catches `LlmError` with the `startswith("LLM provider")` discriminator (→400 if absent, →502 if present); `LlmTransientError`/`GeminiBlockedError` reach the unconditional 502 first. A blocked message without the prefix still answers 502 correctly.

## Precedents & Lessons

4 similar past changes analyzed.

### Precedent: ElevenLabs BYOK card — backend and frontend in one commit
**Commit(s)**: `03477d4` — "Add ElevenLabs voice dubbing and improve project setup" (2026-01-23)
**Blast radius**: 9 files across 3 layers
- backend/ — `app.py` (+118), `translate.py` (new, +239), `subtitles.py` (+58)
- frontend/ — `dashboard/src/App.jsx` (+64), `TranslateModal.jsx` (new, +158), `ResultCard.jsx` (+88)
- docs/ — `CLAUDE.md`, `README.md`, `LICENSE`

Introduced `elevenLabsKey` state, `X-ElevenLabs-Key`, and `elevenLabsKey_v1` under `encrypt()`. **Origin of the `_v1` versioned-encrypted-key convention.**
**Follow-up fixes**: none (`git log -- TranslateModal.jsx` shows only `03477d4` and a restyle).
**Takeaway**: the house pattern for a new BYOK key is one commit carrying backend module + endpoint wiring + the App.jsx card + the `_v1` encrypted key + the header.

### Precedent: fal.ai BYOK card — backend and frontend in one commit
**Commit(s)**: `29681ba` — "added video ugc generator" (2026-03-15)
**Blast radius**: 5 files across 3 layers
- backend/ — `app.py` (+271), `saasshorts.py` (new, +1369), `requirements.txt`
- frontend/ — `dashboard/src/App.jsx` (+78), `SaaShortsTab.jsx` (new, +1194)

Introduced `falKey`, `X-Fal-Key`, `falKey_v1`, and rewired the existing `X-Gemini-Key`/`X-ElevenLabs-Key` construction.
**Follow-up fixes**: `37bd3b0` (2026-03-20) "Add API key modal when Gemini key is missing" (+66 App.jsx) — key-missing UX gap closed 5 days later; `d128fca` (voice-state reset); `86117d3` (low-cost mode).
**Takeaway**: the card pattern is reusable, but do not copy its plumbing — the `X-LLM-*` triple crosses the job subprocess boundary and dies on resume (`app.py:876`), exactly like `X-Gemini-Key`.

### Precedent: `X-Gemini-Key` BYOK header, plaintext storage, and the only key-rename on record
**Commit(s)**: `d5121ed` (2025-12-19) introduced `gemini_key` plaintext + `X-Gemini-Key`; `4fce681`/`c40e5aa` (2025-12-20) "addded encryption" added `encrypt()`/`decrypt()` + versioned keys but left `gemini_key` plaintext; `d60c376` (2025-12-20) renamed `uploadPostKey_v2`→`uploadPostKey_v3` ("Changed key to force re-login or fresh start if needed") — did NOT read the old key.
**Follow-up fixes** (after `05578c5` "hosted paid mode", 2026-07-14): `eb7504f`, `0311b61`, `54bb701` (managed-user BYOK gaps within 3 days); `6ec6935` closed body-key BYOK (keys travel as headers only).
**Takeaway**: no data-preserving localStorage migration has ever shipped. The `gemini_key`→`geminiKey_v1` one-way move is untested territory, and `ResultCard.jsx:284` reads `gemini_key` directly — update that read in the same slice or it strands.

### Precedent: the LLM backend series (`30ce67e`→`35e9d7e`)
**Commit(s)** (all 2026-08-30): `30ce67e` (`llm_client.py` new, +454); `ec60f4f` "route clip scoring, layout picks and thumbnail/saas text" (5 files, +317/−124); `e96407b` (classify provider errors + forward BYOK headers through MCP); `da48ff2` (`tests/test_llm_client.py` new, +847); `35e9d7e` (docs).
**Blast radius**: backend + tests + docs. **Zero frontend files** — the anomaly vs `03477d4`/`29681ba`.
**Follow-up fixes**: none in git (it is HEAD). But the follow-on work exists in artifacts only: the 2026-08-31 handoff reports Phase 1 complete (`/api/llm/test`, `probe()`, `_require_usable()`, the `/api/config` fields, `tests/test_llm_endpoints.py` +216) and states "Nothing is committed to git yet." The current tree has none of it; the plan's 8 checked `[x]` boxes describe lost code.
**Takeaway**: treat the backend status channel + probe endpoint as NOT shipped regardless of checked plan boxes; re-verify `grep -c "/api/llm/test" app.py`; commit each phase before the session ends.

### Composite Lessons
- **Unverified artifact state is the top risk** (`30ce67e`→`35e9d7e` + the 2026-08-31 handoff). The Phase 1 implementation was never committed and is absent at HEAD, while the plan + handoff report it complete and tested. Re-verify against the live tree before planning; commit each phase at session end.
- **BYOK keys ship backend and frontend in one unit** (`03477d4`, `29681ba`). The plan's "Phases 1–5 merge as one unit" follows this; do not merge a partial phase set.
- **Every new key surface produced a managed-user follow-up fix within days** (`eb7504f`, `0311b61`, `54bb701` after `05578c5`; `6ec6935` closed body-key BYOK). Gate every new card + header on `!billingEnabled` in the same commit; send keys as headers only.
- **Key-safety details must ship with the feature**: redaction at the endpoint boundary (the lost `_redact()` closure), no `X-LLM-Model: ""` (`tests/test_llm_client.py:707`), base+key only as a pair, never echo the key from `/api/config`, and the probe must not use the shared 300s client (`llm_client.py:130-147`).
- **`d0e1f5a` is the template for a `/api/config` field addition** (one commit: endpoint → `AuthContext.jsx` → `useAuth()`); `e96407b` shows every new BYOK header must also land in `mcp_server._FORWARD_HEADERS`.

## Historical Context (from `.rpiv/artifacts/`)
- `.rpiv/artifacts/discover/2026-09-04_10-12-16_connect-llm-provider-frontend.md` — the FRD: requirements, acceptance criteria, the pire-browser delta.
- `.rpiv/artifacts/designs/2026-08-30_16-58-07_connect-llm-provider-frontend.md` — the parent design, decisions D1–D9.
- `.rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md` — the 5-phase implementation plan with code + a 41-finding self-review (the implementation baseline; its checked boxes describe lost code).
- `.rpiv/artifacts/handoffs/2026-08-31_05-52-02_connect-llm-provider-phase1.md` — reports Phase 1 complete but uncommitted; records the `_redact()` closure detail to re-incorporate.
- `.rpiv/artifacts/research/2026-08-30_16-03-23_connect-llm-provider-frontend.md` — prior research; notes the `_v1` convention origin and that fal.ai is the wrong plumbing precedent.

## Developer Context

**Q (discover: Adopt existing plan with pire-browser delta)**: The probe found a complete, triaged plan for this FE work from 2026-08-30. None of it shipped. How should this FRD treat it?
A: Adopt plan with deltas — the plan's five phases and decisions D1–D9 are the baseline; the one delta is pire-browser e2e validation.

**Q (discover: Provider config storage: browser plus headers)**: Keep browser-stored encrypted `localStorage` `llmConfig_v1` traveling per-request as the `X-LLM-*` triple, or change it?
A: Keep browser plus headers — `app.py:106-121` (self-host has no auth); matches `X-Gemini-Key` precedent (`App.jsx:211,792`).

**Q (discover: FE scope: full plan scope)**: Keep the full plan scope (settings card + app-wide gate change)?
A: Full plan scope — gate widening is necessary; an LLM-only user sees a block despite a working provider otherwise.

**Q (discover: E2E validation: full pire-browser journey)**: What should pire-browser e2e cover?
A: Full browser journey — the real Settings card flow, Test connection, and a generation request.

**Q (discover: E2E stub: local Python stub)**: What serves as the OpenAI-compatible endpoint?
A: Python stub — a 10-line `http.server` stub on `localhost:11434`. Zero deps, zero network.

**Q (`llm_client.py:443-447`): How should the e2e stub serve the `/api/process` bullet (a real job fails the LLM step on the simple stub body)?**
A: Headers-only stub — keep the simple `{choices:[{message:{content:'ok'},...}]}` stub, inspect the outgoing `/api/process` request headers via pire-browser, and accept the job fails at the LLM step. The FRD bullet only asks to confirm the headers are present.

**Q (`AuthContext.jsx:104,110-111`): How should Phase 4 handle a `/api/config` fetch failure (leaves `loading=false` + empty config → permanent banner)?**
A: Retry the fetch — add 1-2 retries with backoff to the AuthContext config fetch; recovers transient failures, keeps single-shot semantics minimal.

**Q (`App.jsx:55-57`): Harden the `decrypt` migration guard against key-rotation garbage?**
A: Harden the guard — validate the decrypted value (round-trip re-encrypt-and-compare or a non-empty alphanumeric check) before adopting; fall through to the legacy `gemini_key` migration on mismatch.

**Q (`llm_client.py:130`): Probe one-off client timeout policy (connect 5s/read 20s vs the 10s NFR)?**
A: Keep connect 5s / read 20s; clarify the NFR text to name the silent-host 20s case (the "black-holed" wording is the connect-phase case, satisfied at 5s).

## Related Research
- `.rpiv/artifacts/research/2026-08-30_16-03-23_connect-llm-provider-frontend.md` — prior research on the same feature surface (backend-first; notes the `_v1` convention origin and the fal.ai plumbing caveat).

## Open Questions

None unresolved from the discover artifact. Two narrow items remain as recommendations for the planner, not blockers:
- The banner lead-in at `App.jsx:1188` ("Required API keys missing.") and the badge `title` at `:1166` sit inside the `keysMissing` blocks but outside every Phase 4 fence; FR 8 covers banner+badge copy, so they should join Phase 4.
- The SaaS analyze grounded-research step (`research_saas_online`, `app.py:5359-5366`) is Gemini-only and silently skipped for an LLM-only user; an optional analyze-step note would surface the reduced research depth.
