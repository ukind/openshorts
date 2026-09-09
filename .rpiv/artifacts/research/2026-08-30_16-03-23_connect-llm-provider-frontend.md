---
date: 2026-08-30T16:03:23+0700
author: Yogiswara Utama
commit: 35e9d7e
branch: main
repository: openshorts
topic: "Connect the OpenAI-compatible LLM provider backend to the dashboard frontend"
tags: [research, codebase, llm-provider, dashboard, byok, openai-compatible, frontend]
status: ready
last_updated: 2026-08-30T16:03:23+0700
last_updated_by: Yogiswara Utama
---

# Research: Connect the OpenAI-compatible LLM provider backend to the dashboard frontend

## Research Question

> previously i add open ai compatible provider beside gemini api key. but its only in the backend the UI or the front end is not yet added. now connect the BE and the FE especially for my previous changes. use pire browser to validate your changes end to end between the front end and the backend

## Summary

The backend work (commits `30ce67e` → `35e9d7e`) is **complete and correct**. Every call site the
design classified as reroutable — class A (text + strict schema) and class B (image-in frames) — is
already wired through `llm_client.chat()`. Nothing on the backend was missed.

The frontend was **deliberately** left out. Design decision Q3
(`.rpiv/artifacts/designs/2026-08-30_08-58-35_openai-compatible-llm-provider.md:78`, approved at
`:2417`) lists under Non-Goals: *"Dashboard UI: no provider fields, no key inputs (header keys die on
resume — the UI would advertise a flaky setting)."* **This research reverses that decision** at the
developer's direction (see Developer Context).

The reversal is defensible on the repo's own precedent: `X-Gemini-Key` has the *identical*
resume-loss property — stated verbatim in the `resolve_llm` docstring (`app.py:152-154`) and
established by commit `900dc44` — and it has shipped with a full UI since day one
(`KeyInput.jsx`, `App.jsx:1294`). The objection applies equally to a feature already in production.

Three facts shape the whole frontend design:

1. **The provider covers 5 of 6 LLM-aware endpoints, and only part of the app.**
   `POST /api/thumbnail/generate` calls `resolve_gemini` and hard-fails without a Gemini key
   (`app.py:5004-5006`) because image generation is Gemini-only. Everything `ResultCard.jsx` drives
   (`/api/edit`, `/api/effects/generate`, `/api/hook`, `/api/translate`, `/api/subtitle`) is
   Gemini-pinned by design. An LLM-only user gets working clip scoring, titles, descriptions and
   AI-Shorts analysis — and hard errors everywhere else.
2. **The dashboard is blind to server state.** `GET /api/config` (`app.py:1868-1875`) returns four
   fields and says nothing about the LLM. The UI cannot distinguish "server has `LLM_*` env, no user
   input needed" from "user must supply something".
3. **Two silent-failure modes the UI must catch.** `config_from` ignores the pair unless base URL
   *and* key are both present (`tests/test_llm_client.py:425`), and a server configured with base+key
   but no model goes **inert with only a console warning** (`tests/test_llm_client.py:442`).

`mcp_server.py:53-55` already forwards `x-llm-base-url`/`x-llm-key`/`x-llm-model` verbatim — it is a
working reference implementation of exactly the contract the dashboard must now speak.

## Detailed Findings

### Backend completeness sweep — nothing left to reroute

Cross-referencing the design's capability table against live `llm_client.chat` call sites:

| Class | Design says | Live rerouted sites | Status |
|---|---|---|---|
| A. Text + strict schema | reroutes | `main.py:1457-1459,1537`; `thumbnail.py:133,188,264,439,758`; `saasshorts.py:363,554` | complete |
| B. Image-in (frames) | reroutes | `layout_picker.py:147,171` | complete |
| C. Full-video upload | Gemini-pinned | `editor.py:131,140,221,386`; `main.py:1735`; `screencast_layout.py:199` | correctly excluded |
| D. Image generation | Gemini-pinned | `thumbnail.py:603` | correctly excluded |
| E. Google-Search grounding | Gemini-pinned | `saasshorts.py:105` | correctly excluded |

Classes C/D/E are not oversights — an OpenAI-compatible `/v1/chat/completions` endpoint cannot accept
a video file upload, cannot render an image, and has no Google Search grounding. The exclusions are
physical, not editorial.

### The provider resolution contract

`resolve_llm(request, task=None)` (`app.py:143-169`) mirrors `resolve_gemini` (`app.py:124-140`):

- `BILLING_ENABLED` → returns `None` unconditionally. **Cloud is Gemini-pinned; the UI must never
  offer LLM BYOK when billing is on.**
- Self-host: the header triple `X-LLM-Base-Url` + `X-LLM-Key` wins when **both** are present
  (a header key must never travel to an env-configured base URL), `X-LLM-Model` optional.
- Otherwise falls back to `llm_client.active_config(task)` reading `LLM_*` env.
- Model chain is `LLM_MODEL_<TASK> or LLM_MODEL`, never `GEMINI_MODEL*` (`tests/test_llm_client.py:456-470`).

### Endpoint gate patterns — one outlier

Five endpoints share an identical gate that accepts *either* backend:

```
api_key = await resolve_gemini(request)
llm_cfg = await resolve_llm(request, task=...)
if not api_key and llm_cfg is None:
    if not BILLING_ENABLED:
        raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
    raise gemini_missing_error()
```

`POST /api/thumbnail/generate` (`app.py:5004-5006`) inverts it — `resolve_gemini` first, hard fail if
absent, `resolve_llm` only afterwards for the text portion. This is the single endpoint where an
LLM-only user cannot proceed, and the UI must say so.

### Frontend: four key-consuming components, two header patterns

`ThumbnailStudio.jsx:73-77` is the cleanest template and the one to model after:

```
export default function ThumbnailStudio({ geminiApiKey, ... }) {
  const keyHeader = geminiApiKey ? { 'X-Gemini-Key': geminiApiKey } : {};
  const needsKey = !geminiApiKey && !managed;
```

One prop in, one header object, one capability flag — reused across all four `/api/thumbnail/*`
calls. `SaaShortsTab.jsx:46-47` follows the same shape. `App.jsx:792` builds the header inline.
`ResultCard.jsx:284` is the outlier: it bypasses React state and re-reads
`localStorage.getItem('gemini_key')` directly.

**`ResultCard.jsx` is out of scope for this work.** Its six endpoints (`:294,326,373,434,519,556,604`)
are all class C Gemini-pinned — none appears in the `resolve_llm` consumer list. It needs no LLM
headers at all; it only needs its existing "needs a Gemini key" message to stay accurate.

### Storage convention and the plaintext inconsistency

`encrypt()`/`decrypt()` (`App.jsx:35-46`, `:48-62`) are XOR-with-static-salt plus base64, prefixed
`ENC:` — obfuscation, not real encryption, and self-documented as such. Three keys use it under
versioned names (`uploadPostKey_v3`, `elevenLabsKey_v1`, `falKey_v1`); `gemini_key` alone is stored
plaintext (`App.jsx:574`). No commit explains the exception — it predates the helper.

### `/api/config` is the capability channel

`AuthContext.jsx` is the sole consumer of `GET /api/config`, holding it in a `config` state object
initialised `{ billingEnabled: false, googleAuthEnabled: false }` and exposing values through
`useAuth()`. `App.jsx:201` destructures `billingEnabled`, `jobRetentionSeconds` and friends. Commit
`d0e1f5a` established the precedent for adding a field end-to-end.

`LlmConfig` declares `api_key: str = field(repr=False)` (`llm_client.py:90-93`) and
`test_api_key_never_appears_in_repr` (`tests/test_llm_client.py:485-488`) pins it — so reporting
`llmConfigured` and the model name is safe, but the endpoint must never echo the key.

### Error surfacing — a genuine gap

Every failure message from `llm_client` is namespaced with the literal prefix `"LLM provider"`
(`llm_client.py:144,240,242,256,259,265,285,291,300,309,447,452`), split across `LlmError`
(permanent: 401/403/404/402/bad request/truncated) and `LlmTransientError` (429/408/5xx/timeout/empty).

`cloud/alerts.py::_classify_failure` matches that prefix and returns `"llm provider"` — but that path
is **cloud-only operator alerting** (Telegram), and its own comment notes the BILLING env sweep means
a managed job can never emit it. **There is no user-facing UI path for provider errors today.**

Two concrete display risks:

- `apiFetch` converts **every** HTTP 402 into a `QuotaError` (`lib/api.js`), which the UI renders as
  an OpenShorts top-up prompt. Any 402-shaped provider failure surfaced through that wrapper would be
  mis-rendered as a billing problem.
- `apiJson` populates `ApiError.detail` **only when FastAPI's `detail` is a string**; object details
  are silently dropped. `LLM_ENDPOINT_HINT` is a string (safe), but `gemini_missing_error()`'s 402
  returns an object (`{error, message}`) that would be lost.

Self-host job errors reach the UI raw — `log_view.friendly_logs` is a cloud-only whitelist filter, so
self-hosters see the full `"LLM provider ..."` text already.

## Code References

- `llm_client.py:90-93` — `LlmConfig` dataclass, `api_key` marked `repr=False`
- `llm_client.py:96-104` — `LlmError` / `LlmTransientError` semantics
- `llm_client.py:150-178` — `config_from`, requires base+key pair
- `llm_client.py:181-186` — `active_config`, env + per-task model chain
- `llm_client.py:356-454` — `chat()` funnel, 42 inbound refs
- `app.py:124-140` — `resolve_gemini`
- `app.py:143-169` — `resolve_llm`, header triple + billing gate
- `app.py:216-227` — `gemini_missing_error()`, 402 object vs 400 string
- `app.py:230-232` — `LLM_ENDPOINT_HINT`
- `app.py:1868-1875` — `GET /api/config`, the 4-field capability payload
- `app.py:5004-5006` — the thumbnail-generate Gemini hard requirement
- `mcp_server.py:53-55` — `_FORWARD_HEADERS`, working reference for the header contract
- `cloud/alerts.py` — `_classify_failure`, `"llm provider"` class (cloud-only)
- `dashboard/src/App.jsx:35-62` — `encrypt`/`decrypt`
- `dashboard/src/App.jsx:211,574` — `gemini_key` state + plaintext persistence
- `dashboard/src/App.jsx:708` — `keysMissing` gate
- `dashboard/src/App.jsx:792` — header construction for `/api/process`
- `dashboard/src/App.jsx:1294` — `<KeyInput>` mount
- `dashboard/src/App.jsx:1928-1990` — required-keys modal
- `dashboard/src/components/KeyInput.jsx` — hardcoded single-provider component
- `dashboard/src/components/ThumbnailStudio.jsx:73-77` — best template
- `dashboard/src/components/SaaShortsTab.jsx:43-47` — same pattern
- `dashboard/src/components/ResultCard.jsx:284-291` — localStorage bypass (out of scope)
- `dashboard/src/lib/api.js` — `apiFetch`/`apiJson`, `QuotaError`, `ApiError`
- `dashboard/src/contexts/AuthContext.jsx` — sole `/api/config` consumer
- `tests/test_llm_client.py:425,442,456-470,485-488,707` — the pinned contract

## Integration Points

### Inbound References — the 6 `resolve_llm` consumers

| Endpoint | Route | Call | Task | Gate |
|---|---|---|---|---|
| `POST /api/process` | `app.py:2091` | `2112` | — | either backend; 400 at `2115` |
| `POST /api/thumbnail/analyze` | `4806` | `4816` | thumbnail | either; 400 at `4819` |
| `POST /api/thumbnail/titles` | `4914` | `4921` | thumbnail | either; 400 at `4924` |
| `POST /api/thumbnail/generate` | `4984` | `5006` | thumbnail | **Gemini required** at `5004` |
| `POST /api/thumbnail/describe` | `5126` | `5133` | thumbnail | either; 400 at `5136` |
| `POST /api/saasshorts/analyze` | `5330` | `5337` | saas | either; 400 at `5340` |

Frontend callers: `App.jsx:792` → `/api/process`; `ThumbnailStudio.jsx:170,214,237,286,354` → all
four thumbnail endpoints; `SaaShortsTab.jsx:212` → saasshorts analyze.

### Outbound Dependencies

- `llm_client.py` → `httpx` (`_http_client:137-147`, per-base-url client cache, `_TIMEOUT:130`)
- `llm_client.py` → `gemini_worker.GeminiBlockedError` — reused so the existing blocked/bisect ladder keeps working
- `app.py:159-169` → guarded `import llm_client` (gate, never a 500)

### Infrastructure Wiring

- `.env.example:64-68` — `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MODEL_THUMBNAIL`, `LLM_MODEL_SAAS`
- `app.py` `/api/process` — injects header config into the job subprocess env; lost on resume (`app.py:876`)
- `mcp_server.py:53-55` — MCP header forwarding, already live
- `dashboard/vite.config.js` — dev proxy `/api` → `VITE_PROXY_TARGET` (default `http://backend:8000`)
- `dev.sh` — backend `:8000`, frontend `:5173`, renderer `:3100`

## Architecture Insights

- **Resolver family symmetry.** `resolve_gemini` / `resolve_llm` / `resolve_upload_post` share one
  shape: billing branch first (managed key or `None`), then header, then env. Any new resolver should
  follow it, and the UI should assume "cloud = no BYOK" universally.
- **Cloud is Gemini-pinned by construction.** `resolve_llm` returns `None` under billing *and* a
  prefix sweep strips `LLM_*` from job envs. The UI must gate the entire provider surface on
  `!billingEnabled`.
- **Fail-inert, not fail-loud.** A half-configured backend stays `None` rather than erroring, so a
  broken setup looks like "Gemini is being used" rather than a visible failure. The UI is the only
  place this can be surfaced to a self-hoster.
- **Per-component header objects are the house style.** All four components build their own; none
  relies on `apiFetch` for BYOK headers. `apiFetch` deliberately carries only the bearer token.
- **`"LLM provider"` is a reserved string** (`llm_client.py:74`) load-bearing for alert
  classification. Never reword those messages without updating `cloud/alerts.py` and its tests.
- **Body-key BYOK is a closed security surface** (precedent `6ec6935`). The provider config must
  travel as headers only — never in a request body.

## Precedents & Lessons

3 similar past changes analyzed.

### Precedent: fal.ai BYOK key added end-to-end
**Commit(s)**: `29681ba` — "added video ugc generator"
**Blast radius**: 5 files across 3 layers
  backend/ — `app.py` (+271), `saasshorts.py` (+1369), `requirements.txt`
  frontend/ — `dashboard/src/App.jsx` (+78), `dashboard/src/components/SaaShortsTab.jsx` (+1194)

**Takeaway**: backend and frontend for a new BYOK key landed in ONE commit — the repo has no
precedent for shipping a key backend-first, which is exactly the gap this work closes.

### Precedent: ElevenLabs BYOK key
**Commit(s)**: `03477d4` — "Add ElevenLabs voice dubbing and improve project setup"; later restyled by `aa676a7`
**Takeaway**: same one-commit shape; the `_v1` versioned-encrypted-key convention originates here.

### Precedent: adding a field to `/api/config`
**Commit(s)**: `d0e1f5a` — "raise the self-host job retention default to 24h and surface it in the UI"
**Takeaway**: the established path is endpoint → `AuthContext` → `useAuth()` destructure; follow it
verbatim for `llmConfigured`.

### Precedent: the `keysMissing` gate
**Commit(s)**: `05578c5` — "feat: add hosted paid mode (managed keys, Stripe trial, metering)"
**Takeaway**: the gate was last touched to make it self-host-only (`!billingEnabled && ...`). Widening
it again must preserve that billing guard.

### Composite Lessons

- BYOK keys ship backend+frontend together in this repo (`29681ba`, `03477d4`) — the current
  backend-only state is the anomaly, not the pattern.
- Per-request keys dying on resume is **accepted precedent**, not a blocker (`900dc44`, accepted for
  `X-Gemini-Key`); the design's objection to a UI applies equally to a shipped feature.
- Alert-class ordering was corrected twice in production (`77905a9`, `730f7de`) — do not reorder
  `_classify_failure` branches.
- Redaction/provider strings must ship in the same commit as the feature (`29fed21`→`8160fc6`).
- fal.ai is the **wrong** key-plumbing precedent — it never crosses the subprocess boundary, whereas
  LLM config does.

## Historical Context (from `.rpiv/artifacts/`)

- `.rpiv/artifacts/solutions/2026-08-30_08-26-00_add-openai-compatible-llm-provider.md` — option analysis for the provider
- `.rpiv/artifacts/designs/2026-08-30_08-58-35_openai-compatible-llm-provider.md` — capability classes A–E, decision Q3 (no UI), Non-Goals at `:78`
- `.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md` — phased backend plan, 21 review findings, 3 deferred
- `.rpiv/artifacts/handoffs/2026-08-30_14-42-31_openai-compatible-llm-provider-implementation.md` — implementation handoff
- `.rpiv/artifacts/validation/2026-08-30_15-13-19_openai-compatible-third-party-llm-endpoint-alongside-gemini-additive.md` — backend validation report

## Developer Context

**Q (`designs/2026-08-30_08-58-35...md:78`, `:2417` vs `app.py:152-154`): The design explicitly ruled out a dashboard UI because header config dies on redeploy resume. `X-Gemini-Key` has the identical property and has always had a UI. How far should the frontend go?**
A: **Full BYOK — status display plus `X-LLM-*` header inputs.** Decision Q3 is reversed. The UI collects base URL, API key and model, and sends the triple. Accepts the same resume-loss the Gemini key already has.

**Q (`app.py:5004-5006`, `ResultCard.jsx:294-604`): An LLM-only user gets working clip scoring/titles/describe/AI-Shorts but broken thumbnails, auto-edit, hooks and translate. What should the dashboard do?**
A: **Let them in, and label what needs Gemini.** One global "has an AI backend" gate so the app unblocks; the genuinely Gemini-only actions show an inline "needs a Gemini key" note. No hidden dead ends, no greyed-out buttons.

**Q (`App.jsx:792`, `ThumbnailStudio.jsx:76`, `SaaShortsTab.jsx:46`, `ResultCard.jsx:291` vs `lib/api.js`): Where should the `X-LLM-*` headers be attached?**
A: **Per component, following the existing pattern.** Build an `llmHeaders` object next to each existing `geminiHeader`/`keyHeader`. Do NOT centralize in `apiFetch` — that would send the provider key on every request including uploads and social posting.

**Q (`App.jsx:35,574` vs the `_v1`/`_v3` encrypted siblings): How should the three provider values be persisted?**
A: **One encrypted JSON blob under `llmConfig_v1`.** Follows the newer versioned convention and keeps the triple atomic — the backend ignores the pair unless both base URL and key are present (`tests/test_llm_client.py:425`).

**Q (developer follow-up): Is anything else missing for the OpenAI-compatible provider?**
A: Backend sweep confirmed complete — all class A/B sites rerouted; classes C/D/E are physically impossible on a chat-completions endpoint. Remaining gaps are frontend-only, plus three contract items now captured above: `/api/config` LLM fields, base+key pair validation, and surfacing the silently-inert half-configured state.

## Constraints Pinned by Tests

`tests/test_llm_client.py` locks these — the frontend must not contradict them:

- `:425` `test_config_from_requires_both_values` — base-only and key-only both return `None`. **UI must validate the pair.**
- `:442` `test_half_configured_env_is_inert_with_warning` — base+key without a model stays inert, warns once to stdout. **UI should surface this.**
- `:456-470` `test_active_config_reads_env_and_task_chain` — `LLM_MODEL_<TASK>` → `LLM_MODEL` → `None`; never `GEMINI_MODEL*`; invented knobs like `LLM_MODEL_CLIPS` do nothing.
- `:485-488` `test_api_key_never_appears_in_repr` — safe to report config, never the key.
- `:707` `test_explicit_model_header_wins_over_env_chain` — `X-LLM-Model` overrides env; a whitespace-only model falls back to env. **UI must send an omitted model, not `""`.**
- `:805-847` alert-class tests — a provider 402 "insufficient balance" must classify as `"llm provider"`, not `"proxy"`.

## E2E Validation Plan (pire-browser)

pire-browser is installed and functional — persistent Firefox profile at
`C:\Users\utama\AppData\Local\pire-browser\firefox-profiles\Default`, no live session, all policies
permissive by default.

Environment: `./dev.sh` → backend `:8000`, dashboard `:5173`, renderer `:3100`. Vite proxies `/api`
to the backend, so `http://localhost:5173` is same-origin — set `VITE_PROXY_TARGET=http://localhost:8000`
when running natively.

Scenarios to cover, each asserting on both the rendered UI and the request the backend receives:

1. **Nothing configured** — self-host, no Gemini key, no `LLM_*` env. Expect the front door to block
   and the required-keys modal to offer both options.
2. **Server env configured** — `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` set, no Gemini key. Expect
   `/api/config` to report it, the gate to open, and no key inputs demanded.
3. **BYOK via UI** — enter base URL + key + model in the dashboard; assert the outgoing request
   carries `X-LLM-Base-Url` + `X-LLM-Key` + `X-LLM-Model`, and that `/api/process` is accepted.
4. **Half-configured** — base + key, no model. Expect the UI warning, and the backend to stay on Gemini.
5. **Pair validation** — base URL only, then key only. Expect the UI to refuse to save/send.
6. **Gemini-only capability** — LLM configured, no Gemini key. Expect thumbnail *generate* to show
   "needs a Gemini key" while titles/describe work.
7. **Billing on** — the whole provider surface must be absent.
8. **Provider error** — point at an unreachable base URL; expect an `"LLM provider ..."` message to
   reach the UI, and specifically NOT a quota top-up prompt.

A local stub `/v1/chat/completions` server is the cheapest way to drive 3, 4, 6 and 8
deterministically without spending real Ollama/OpenRouter tokens.

## Open Questions

- Should `/api/config` expose the configured **model name** (useful: "AI backend: gpt-oss:120b") or
  only a boolean? `LlmConfig` protects the key but not the model or base URL; a self-hoster seeing
  their own config is harmless, but the field is also served pre-auth.
- Should the UI offer provider **presets** (Ollama Cloud `https://ollama.com/v1`, OpenRouter, local
  Ollama `http://localhost:11434/v1`)? The design notes the user primarily uses Ollama Cloud.
- Should `gemini_key` be migrated from plaintext to `encrypt()` while touching this code, or left
  alone to avoid a migration path for existing users?
- Is a "test connection" button worth it? Nothing on the backend currently validates a provider
  config without running a real job.
