---
date: 2026-09-10T07:09:56+0700
author: Yogiswara Utama
commit: e5ffe7c
branch: main
repository: openshorts
topic: "Unified AI provider config — single source of truth BYOK"
tags: [design, ai-provider, llm-client, byok, frontend, editor, thumbnail, hook-grounding, screencast]
status: ready
parent: .rpiv/artifacts/solutions/2026-09-10_06-46-58_unified-ai-provider-config.md
last_updated: 2026-09-10T09:41:37+0700
last_updated_by: Yogiswara Utama
---

# Design: Unified AI provider config — single source of truth BYOK panel

## Summary

One encrypted dashboard store (`aiProviderConfig_v1`, `{baseUrl, apiKey, model}`) behind one card (`AiProviderCard.jsx`) derives BOTH backend config families per request: `X-AI-Provider` + `X-OpenAI-*` for the pipeline and derived `X-LLM-*` for satellites, built by the single emitter `dashboard/src/lib/llm.js`. Four Gemini-only stages gain OpenAI-compatible arms — the two in-job stages (`hook_grounding.py`, `screencast_layout.py`) read the job `OPENAI_*` env through `ai_provider` with a `probe_vision_support` guard; the two request-scoped endpoints (`/api/edit`, `/api/effects/generate` via `editor.py`; `/api/thumbnail/generate` via `thumbnail.py`) resolve via `resolve_openai`, the image path using `/v1/images/generations`. Dispatch modules stay untouched (D3); cloud/billing behavior stays untouched.

## Requirements

From the FRD (`.rpiv/artifacts/discover/2026-09-09_19-17-18_unified-ai-provider-config.md`) and the solutions artifact:

- Exactly one AI-provider card in the settings sidebar; it is the single source of truth (D0, FR1).
- The unified triple powers pipeline stages (`X-OpenAI-*`), satellite stages (derived `X-LLM-*`), and the four rerouted Gemini-only stages (D1, D2, FR3/6/7/8).
- Keys persist encrypted under a new storage key; legacy `llmConfig_v1` and `openai_*` import once and are removed (D4, D7, D8, FR2/4/5). Plaintext writers are removed in the same change.
- Card affordances survive: presets, Test connection, model dropdown, server-config badge (D6, FR10).
- `LlmProviderCard.jsx` is deleted; its values migrate (D7).
- Every rerouted stage degrades, never fails: one log line + clean skip/fallback (NFR).
- Pins stay green with zero modification to existing tests: `tests/test_no_double_route.py`, `tests/test_ai_provider.py`, `tests/test_llm_client.py` stay untouched; `tests/test_llm_endpoints.py` keeps all 26 existing tests unmodified and additionally gains ONE additive `TestConfigFields` test for the new `/api/config` fields in Slice 1 (Constraints).
- No new Python dependencies; no resolver changes; no MCP changes (D5, Non-Goals).

## Current State Analysis

Two backend config families exist and stay (D3): `ai_provider.create_ai_provider` (`ai_provider.py:621-649`, pipeline, selected by `AI_PROVIDER` + `OPENAI_*`/`GEMINI_*`) vs `llm_client` (satellites, `LLM_*` + `X-LLM-*` headers). The split is pinned by `tests/test_no_double_route.py`.

- Resolvers: `resolve_gemini` (`app.py:137-149`, billing-pinned), `resolve_openai` (`app.py:152-165`, key optional for local servers), `resolve_llm` (`app.py:180-207`, two-leg: `X-LLM-*` headers via `llm_client.config_from` then env; billing → None). Job gate `ai_backend_available` (`app.py:213-228`) accepts any one family.
- Job env handover (`app.py:2561-2594`): `OPENAI_*` at `:2571-2586`, `LLM_*` at `:2591-2593`; injected into the `main.py` subprocess; pinned by `tests/test_no_double_route.py:322-357` (`test_job_env_carries_both_key_families`).
- FE today: `llmConfig` state (`App.jsx:316-319`, encrypted `llmConfig_v1`) feeds `providerCfg` (`App.jsx:913-915`, cloud-gated empty) → `llmHeaders` (`lib/llm.js:16-24`) → 5 satellite call sites. The inline OpenAI card writes plaintext `openai_*` (`App.jsx:246-249` init, `:709-712` persist) feeding `handleProcess` `X-OpenAI-*` (`App.jsx:1074-1077`), `VoiceOverPage` (`:2093-2095` props), `CreateEditProfileModal` (`:2353` props).
- Four Gemini-only stages: `hook_grounding.py:124-152` (`GEMINI_API_KEY` env, frames input, skip semantics `:148-152`); `screencast_layout.py` `detect_content_ranges` (whole-video Files-API upload `:186-197`, degrade to `[]` `:210-212`); `editor.py` (`VideoEditor(api_key)` `:28-34`, Files upload `:46`, EditPlan call `:131-143`; instantiated only at `app.py:3537` and `:4677`); `thumbnail.py` image path (`_generate_one` `:577-625`, `genai` `:603-610`; hard gate `app.py:6052-6055`).
- `/api/process` also accepts `openai_*` from the body when headers are absent (`app.py:2446-2453`); the game-profiles generate path reads body `openai_*` overrides (`app.py:8149-8156`).
- `tests/test_llm_endpoints.py:221-225` pins the BYOK leg of `ai_backend_available`; `tests/test_llm_client.py:707` is stale (the empty-model header rule survives as documentation in `lib/llm.js:9-11`). `dashboard/` has no FE test runner — FE verification is lint + build + grep checklist + manual QA.

### Key Discoveries

- `llm_client.config_from` (`llm_client.py:165-176`) demands base+key (+ resolved model) and is deliberately inert otherwise — a half-configured endpoint can never hijack a working Gemini setup. This makes the FE bridge safe by construction: derived `X-LLM-*` headers only fire when base+key are set.
- `llmConfigComplete` (`lib/llm.js:7-8`) demands all three fields, but the pipeline family tolerates a keyless endpoint (`resolve_openai` docstring) — the unified gate must split these concerns (D10).
- `CreateEditProfileModal.jsx:274-276` persists the plaintext `openai_key` into saved game-profile bodies — the only path where an FE key lands in backend data (D11 cuts it).
- `editor.py` has NO module-level wrappers; every caller goes through `VideoEditor` instantiated at exactly two sites (`app.py:3537`, `:4677`) — the reroute is confined to `editor.py` + the two endpoints' key-resolution blocks (confirmed repo-wide).
- In-repo reroute precedents: in-job vision pass `main.py:3006-3028` (env dispatch → `create_ai_provider("openai", ...)` → `probe_vision_support` → `image_url` frame parts → `schema=`); request-scoped caption dual-dispatch `app.py:6786-6807`; key-based dual dispatch `thumbnail.py:81-92`, `:131-137`; one-time localStorage import `App.jsx:229-250`.
- Slice 4 discovery: `detect_content_ranges` has NO live caller at HEAD — `main.py:1287` calls `reframe_v2.render(...)` without `content_ranges` (the only render call site, verified repo-wide + `git log -S`), so the SCREENCAST/WIDE upgrade is dormant in the live pipeline and the function is test-reachable only. Pre-existing, orthogonal to this design: Slice 4 reroutes the function's internals only, and its manual criteria are module-level rather than end-to-end job claims.
- Precedent lessons (fde9338 → 25222f5/56707b7/e96407b): audit every gate at the app→subprocess boundary each phase (broke 3×); guard every `env[...] = value` write against None; ship a no-double-route canary per rerouted stage; never reword `"LLM provider"` error prefixes (`cloud/alerts.py` classifies on them); purge stale browser provider state; probes must demand JSON replies and classify transient-first.
- Thumbnail `generate_thumbnail` (`thumbnail.py:628-677`) builds one shared `genai.Client` for both the concept (text, already dual via `llm_config`) and image arms; the image call needs `response_modalities=["TEXT","IMAGE"]` which `/v1/images/generations` has no equivalent for — hence D14 (text-prompt-only unified arm).
- `/api/llm/test` (`app.py:2090-2134`) requires base+key (config_from); `/api/openai/models` (`app.py:8088+`) works keyless — the unified Test connection uses the first when a key is set, the second otherwise.

## Scope

### Building

- `dashboard/src/lib/llm.js`: `aiProviderSet` predicate + `openaiHeaders` builder (dual-family single emitter).
- `dashboard/src/components/AiProviderCard.jsx` (NEW): the one card — key/model/baseUrl, presets, Test connection, model dropdown, server-config badge, keyless + server-default-model support (D10).
- `dashboard/src/App.jsx`: `aiProviderConfig_v1` encrypted store + one-time migration from `llmConfig_v1` + `openai_*` (writers removed), card mount swap, `providerCfg`/`llmActive`/`needsAiBackend` from the unified store, `handleProcess` dual-family emission, prop shims for not-yet-folded components.
- `dashboard/src/contexts/AuthContext.jsx` + `app.py` `/api/config` + `tests/test_llm_endpoints.py`: FR9 — three read-only, billing-pinned `openai*` fields reported through the config fetch, exposed on the auth context, pinned by one additive test.
- Delete `dashboard/src/components/LlmProviderCard.jsx`.
- Fold inline builders: `ResultCard.jsx` (gains both families on `/api/edit`), `VoiceOverPage.jsx`, `CreateEditProfileModal.jsx` (drops plaintext key from profile bodies, D11).
- `hook_grounding.py`: OpenAI arm inside `reground` via job `OPENAI_*` env + `probe_vision_support`; `_ask_openai` seam; keep `_ask_gemini` seam and skip semantics. Tests in `tests/test_hook_grounding.py`.
- `screencast_layout.py`: frames-based OpenAI arm in `detect_content_ranges` (12 frames @1024px, `layout_picker.sample_frames`), degrade-to-`[]` unchanged. Tests in `tests/test_screencast_layout.py`.
- `editor.py` + `app.py`: frames-based EditPlan/effects path via `resolve_openai` when no Gemini key; shared `apply_edits`; probe guard; clean error when no vision. Tests NEW `tests/test_editor_frames.py`.
- `thumbnail.py` + `app.py`: `/v1/images/generations` arm (text-prompt only, D14), Gemini fallback + clean-unavailable state; gate accepts unified config, billing branch intact. Tests extend `tests/test_thumbnail_studio.py`.
- No-double-route canaries for the four rerouted stages (extend `tests/test_no_double_route.py` or per-stage files).

### Not Building

- Server-side config state (Option 3) — out.
- Dispatch-module changes (D3 forbids; `tests/test_no_double_route.py` pins).
- SaaS grounded research reroute (D2 boundary: needs live Google Search grounding).
- The Option 2 BE `resolve_llm` header-only fallback (D13 — deferred entirely, can land later standalone).
- MCP changes (`mcp_server.py:57-70` already forwards both families).
- The redeploy-resume BYOK gap (`app.py:1002-1014` env rebuild) — pre-existing, both families.
- An FE test harness (none exists; adding one is separate scope).
- `/v1/images/edits` reference-image support (D14).

## Decisions

### D10 — Unified card gate: baseUrl only (checkpoint 2026-09-10)

**Ambiguity**: What makes the unified card "set"? `llmConfigComplete` demands all three fields (`lib/llm.js:7-8`), but the card now also feeds the pipeline family, which allows a keyless endpoint (`resolve_openai` `app.py:155-165`), and `config_from` makes a half-triple inert, not dangerous (`llm_client.py:165-176`).
**Explored**: (A) all three required (today's gate — kills presets, keyless local, server-default model); (B) baseUrl + model (loses empty-model fallback); (C) baseUrl only.
**Decision**: baseUrl only. `aiProviderSet(cfg) = !!cfg?.baseUrl?.trim()`. Key optional → keyless local endpoints save and power the pipeline; satellites stay inert without a key (`llmHeaders` returns `{}` — safe by construction). Model optional → "server default" state: empty model omits `X-LLM-Model`, server resolves via `LLM_MODEL_<TASK>`/`LLM_MODEL` env (`llm_client.py:171-176`). Persistence gate = `aiProviderSet`. `needsAiBackend` extends with `aiProviderSet(providerCfg)`.

### D11 — Game-profile bodies drop the plaintext key (checkpoint 2026-09-10)

**Ambiguity**: `CreateEditProfileModal.jsx:274-276` writes `openai_key` into saved profile bodies; server reads body overrides at `app.py:8149-8156`.
**Decision**: Stop persisting the key; keep `openai_model`/`openai_base_url` as profile prefs. The key travels per-request via headers from the unified store. Matches the FRD security NFR; the pre-existing server-side key-in-profile-row behavior ends.

### D12 — Keep `llmConfig` prop names (checkpoint 2026-09-10)

**Decision**: `ThumbnailStudio`/`SaaShortsTab` keep receiving `llmConfig={providerCfg}` with the same `{baseUrl, apiKey, model}` shape — zero internal edits in those components. Names are slightly stale; the diff stays minimal.

### D13 — Option 2 shim deferred entirely (checkpoint 2026-09-10)

**Decision**: The BE header-only fallback in `resolve_llm` is NOT part of this design. It can land later as a standalone header-triggered change without disturbing anything here. Never the env-leg variant (would silently reconfigure self-host deployments and break the namespace law).

### D14 — Thumbnail unified arm is text-prompt only (checkpoint 2026-09-10)

**Decision**: `/v1/images/generations` cannot accept reference images. The unified arm generates from the text prompt alone. Reference images (face photo / frame) are honored by the Gemini arm only: refs + Gemini key → Gemini; refs + no Gemini key → generate without refs (logged). No `/v1/images/edits` probing.

### Inherited decisions (D0–D9, from the FRD/research; never re-asked)

D0 single source of truth via the OpenAI/Compatible menu; D1 satellites covered; D2 maximal reroute (vision set + editor + thumbnail image; SaaS research stays Gemini); D3 keep both dispatch modules; D4 encrypted storage; D5 FE bridge (dual headers); D6 all four card affordances; D7 delete `LlmProviderCard` + migrate; D8 one-time plaintext import; D9 (research) `gemini_key` migration already shipped at HEAD — only `llmConfig_v1` + `openai_*` remain.

### Directional confirm (checkpoint 2026-09-10)

`lib/llm.js` is the single emitter of both header families; all ~11 emission sites converge on its builders; inline builders in `VoiceOverPage.jsx:347-352` and `CreateEditProfileModal.jsx:253-263` are deleted; literal `X-LLM` stays only in `lib/llm.js`, `X-OpenAI-*` literals move there too.

## Architecture

### dashboard/src/lib/llm.js — MODIFY

The dual-family single emitter: `aiProviderSet` predicate (D10) + `openaiHeaders` builder alongside the existing `llmConfigComplete`/`llmHeaders`.

```javascript
// The dual-family BYOK header builders — the single emitter of both config
// families (design: unified AI provider config).
//
// Built per call site — the same shape X-Gemini-Key uses — never inside
// apiFetch, which would leak the provider key to uploads and social
// posting (design D1).

// D2: llm_client.config_from goes silently inert on a half-configured
// provider, so the UI must never treat one as ready. All three fields,
// trimmed. Satellites need the complete triple.
export const llmConfigComplete = (llmConfig) =>
  !!(llmConfig?.baseUrl?.trim() && llmConfig?.apiKey?.trim() && llmConfig?.model?.trim());

// D10: the unified card is "set" when the endpoint URL exists. The key is
// optional (OpenAI-compatible local servers need none — resolve_openai
// accepts a keyless triple) and the model is optional ("server default").
export const aiProviderSet = (cfg) => !!cfg?.baseUrl?.trim();

// D9: X-LLM-Model is omitted, never sent empty — a whitespace-only model
// falls back to the server's env chain (tests/test_llm_client.py:707).
// Base + key alone still resolve server-side, so they travel whenever set.
export const llmHeaders = (llmConfig) => {
  const baseUrl = llmConfig?.baseUrl?.trim();
  const apiKey = llmConfig?.apiKey?.trim();
  if (!baseUrl || !apiKey) return {};
  const model = llmConfig?.model?.trim();
  const headers = { 'X-LLM-Base-Url': baseUrl, 'X-LLM-Key': apiKey };
  if (model) headers['X-LLM-Model'] = model;
  return headers;
};

// The pipeline family, derived from the SAME store: resolve_openai (app.py)
// tolerates a missing key and a missing model, so each header rides only
// when its field is set. The base URL is the anchor — without it the job
// would run on the server's env, which is not what a saved card means.
export const openaiHeaders = (cfg) => {
  const baseUrl = cfg?.baseUrl?.trim();
  if (!baseUrl) return {};
  const headers = { 'X-OpenAI-Base-Url': baseUrl };
  const apiKey = cfg?.apiKey?.trim();
  if (apiKey) headers['X-OpenAI-Key'] = apiKey;
  const model = cfg?.model?.trim();
  if (model) headers['X-OpenAI-Model'] = model;
  return headers;
};
```

### dashboard/src/components/AiProviderCard.jsx — NEW

The one card: form (baseUrl/apiKey/model), presets, Test connection (LLM test when keyed, models probe when keyless), model dropdown, server-config badge; saves trimmed to `onConfigSet`; keyless and empty-model are saveable (D10).

```jsx
import React, { useState } from 'react';
import { Bot, Eye, EyeOff, Check, Loader2, AlertTriangle, RotateCcw } from 'lucide-react';
import { apiJson } from '../lib/api';
import { aiProviderSet, llmHeaders } from '../lib/llm';

// The ONE AI-provider card, self-host Settings only (this mount lives in the
// !billingEnabled branch of App.jsx — the surface is structurally absent on
// cloud). Its single {baseUrl, apiKey, model} triple is the store; App.jsx
// derives BOTH header families from it via lib/llm.js — X-OpenAI-* for the
// pipeline, X-LLM-* for the satellites. Nothing here builds headers.
//
// D10: the endpoint URL is the only required field. Local servers (Ollama,
// LM Studio) need no key; an empty model means "the server's default"
// (X-LLM-Model omitted — the server falls back to LLM_MODEL /
// LLM_MODEL_<TASK>; the pipeline falls back to OPENAI_MODEL).
// D6: presets, Test connection, model dropdown and the server badge survive
// from the two cards this one replaces.
//
// Test connection (FR10): with a key, POST /api/llm/test under the derived
// X-LLM-* headers (the real satellite path); keyless, GET /api/openai/models
// (the only meaningful probe of a keyless endpoint). An upstream 400 = this
// config is wrong, anything else (incl. 502 unreachable) = provider trouble —
// never 402.

const PRESETS = [
  { id: 'openai', label: 'openai', baseUrl: 'https://api.openai.com/v1' },
  { id: 'ollama-local', label: 'local ollama', baseUrl: 'http://localhost:11434/v1' },
  { id: 'lmstudio', label: 'lm studio', baseUrl: 'http://host.docker.internal:1234/v1' },
  { id: 'openrouter', label: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1' },
];

export default function AiProviderCard({
  savedConfig, onConfigSet,
  llmConfigured, llmModel, llmBaseUrl,
  openaiConfigured, openaiModel, openaiBaseUrl,
}) {
  const [form, setForm] = useState(() => ({
    baseUrl: savedConfig?.baseUrl || '',
    apiKey: savedConfig?.apiKey || '',
    model: savedConfig?.model || '',
  }));
  const [saved, setSaved] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  // null | {phase:'running'} | {phase:'ok', latencyMs, model} | {phase:'error', kind, detail}
  const [test, setTest] = useState(null);
  const [models, setModels] = useState({ list: [], loading: false, error: null, open: false });
  // The server badge stands in for the form until the user asks for it.
  // Derived, not stored: the config read lands after mount. A saved card IS
  // an override, so with one saved the form is the primary surface. The
  // badge fires on either server half: the satellite env (llmConfigured) or
  // the pipeline env (openaiConfigured).
  const [overrideRequested, setOverrideRequested] = useState(false);
  const serverConfigured = (!!llmConfigured || !!openaiConfigured) && !aiProviderSet(savedConfig);
  const showForm = overrideRequested || !serverConfigured;

  const setField = (name) => (e) => {
    setForm((f) => ({ ...f, [name]: e.target.value }));
    setSaved(false);
  };

  const handleSave = () => {
    if (!aiProviderSet(form)) return;
    // Stored trimmed: the card is the only writer of the config, and both
    // header builders trim anyway — storing clean costs nothing.
    onConfigSet({
      baseUrl: form.baseUrl.trim(),
      apiKey: form.apiKey.trim(),
      model: form.model.trim(),
    });
    setSaved(true);
  };

  const handleClear = () => {
    setForm({ baseUrl: '', apiKey: '', model: '' });
    onConfigSet({ baseUrl: '', apiKey: '', model: '' });
    setSaved(false);
  };

  const handleTest = async () => {
    setTest({ phase: 'running' });
    const cfg = {
      baseUrl: form.baseUrl.trim(),
      apiKey: form.apiKey.trim(),
      model: form.model.trim(),
    };
    try {
      if (cfg.apiKey) {
        // The same derived X-LLM-* headers a real satellite call would carry.
        // An empty model is omitted — the server resolves LLM_MODEL_<TASK>.
        const data = await apiJson('/api/llm/test', { method: 'POST', headers: llmHeaders(cfg) });
        setTest({ phase: 'ok', latencyMs: data.latencyMs, model: data.model });
      } else {
        // Keyless local endpoint: satellites stay inert by design, so probe
        // the pipeline family instead — list models through the backend proxy.
        const res = await fetch(`/api/openai/models?base_url=${encodeURIComponent(cfg.baseUrl)}`);
        if (!res.ok) {
          // Carry the status: /api/openai/models forwards upstream 400s, and
          // a 400 means this config is wrong, not that the provider failed.
          const err = new Error(`${res.status} ${res.statusText}`);
          err.status = res.status;
          throw err;
        }
        const j = await res.json();
        const list = j.data || j.models || j;
        const ids = Array.isArray(list) ? list.map((m) => m.id || m.name || m).filter(Boolean) : [];
        if (ids.length === 0) throw new Error('No models returned');
        setTest({ phase: 'ok', latencyMs: null, model: `${ids.length} models` });
      }
    } catch (e) {
      setTest({
        phase: 'error',
        kind: e?.status === 400 ? 'config' : 'provider',
        detail: e?.detail || e?.message || 'Request failed',
      });
    }
  };

  const fetchModels = async () => {
    setModels((m) => ({ ...m, loading: true, error: null, open: true }));
    const base = form.baseUrl.trim().replace(/\/$/, '');
    try {
      const h = form.apiKey.trim() ? { Authorization: `Bearer ${form.apiKey.trim()}` } : {};
      // First via the backend proxy (avoids CORS on local servers), then direct.
      let res = await fetch(`/api/openai/models?base_url=${encodeURIComponent(base || 'https://api.openai.com/v1')}`, { headers: h });
      if (!res.ok) res = await fetch(`${base}/models`, { headers: h });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const j = await res.json();
      const list = j.data || j.models || j;
      const ids = Array.isArray(list) ? list.map((m) => m.id || m.name || m).filter(Boolean) : [];
      if (ids.length === 0) throw new Error('No models returned');
      setModels({ list: ids, loading: false, error: null, open: true });
    } catch (e) {
      setModels((m) => ({ ...m, loading: false, error: e.message || 'Failed to fetch models' }));
    }
  };

  const canTest = !!form.baseUrl.trim();
  const canSave = aiProviderSet(form);

  return (
    <div className="card p-4 sm:p-6 mb-8 animate-fade">
      <div className="flex items-center gap-3 mb-4">
        <div className="p-2 bg-paper3 rounded-input text-brass">
          <Bot size={18} />
        </div>
        <h2 className="font-display lowercase text-lg text-ink">AI Provider</h2>
        <span className="readout">BYOK</span>
      </div>

      {showForm ? (
        <>
          <p className="text-xs text-muted mb-4 leading-relaxed">
            One endpoint powers every AI feature — clip analysis, titles, layouts, thumbnails, effects, hook grounding.
            Any OpenAI-compatible server works (OpenAI, Ollama, LM Studio, OpenRouter, vLLM, …); a Gemini key stays optional.
            Keys are stored encrypted in your browser and sent per request, never stored server-side.
          </p>

          <div className="flex flex-wrap gap-1.5 mb-4" aria-label="quick fill">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => { setForm((f) => ({ ...f, baseUrl: p.baseUrl })); setSaved(false); }}
                className={`px-3 py-1.5 rounded-input text-xs border transition-colors ${
                  form.baseUrl === p.baseUrl ? 'border-brass text-ink bg-brass/10' : 'border-rule text-muted hover:text-ink'}`}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="space-y-3">
            <div>
              <label className="block text-sm text-muted mb-1" htmlFor="ai-base-url">Endpoint URL</label>
              <input
                id="ai-base-url"
                type="text"
                value={form.baseUrl}
                onChange={setField('baseUrl')}
                placeholder="https://api.openai.com/v1"
                className="input-field font-mono"
                autoComplete="off"
                spellCheck="false"
              />
              <p className="text-micro text-muted mt-1">e.g. <code className="readout">http://host.docker.internal:1234/v1</code> for LM Studio</p>
            </div>
            <div>
              <label className="block text-sm text-muted mb-1" htmlFor="ai-api-key">API Key <span className="text-muted font-normal">(not required for local servers)</span></label>
              <div className="relative">
                <input
                  id="ai-api-key"
                  type={isVisible ? 'text' : 'password'}
                  value={form.apiKey}
                  onChange={setField('apiKey')}
                  placeholder="sk-... (leave empty for local)"
                  className="input-field pr-12 font-mono"
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={() => setIsVisible(!isVisible)}
                  aria-label={isVisible ? 'hide key' : 'show key'}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-ink transition-colors"
                >
                  {isVisible ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>
            <div>
              <label className="block text-sm text-muted mb-1" htmlFor="ai-model">Model</label>
              <div className="flex gap-1.5">
                <input
                  id="ai-model"
                  type="text"
                  value={form.model}
                  onChange={setField('model')}
                  placeholder="gpt-4o-mini"
                  className="input-field font-mono flex-1"
                  autoComplete="off"
                  spellCheck="false"
                  onFocus={() => setModels((m) => ({ ...m, open: true }))}
                  onBlur={() => setTimeout(() => setModels((m) => ({ ...m, open: false })), 150)}
                />
                <button
                  type="button"
                  onClick={fetchModels}
                  disabled={models.loading || !canTest}
                  className="btn-quiet px-2 py-1 text-xs whitespace-nowrap"
                  title="Fetch models from Base URL"
                >
                  {models.loading ? <Loader2 size={12} className="animate-spin" /> : 'Refresh'}
                </button>
              </div>
              {models.open && (
                <div className="mt-1 max-h-48 overflow-y-auto bg-paper border border-rule rounded-input shadow-lg custom-scrollbar">
                  {(() => {
                    const base = models.list.length > 0
                      ? models.list
                      : ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'];
                    return base.map((m) => (
                      <button key={m} type="button" onMouseDown={(e) => e.preventDefault()}
                        onClick={() => { setForm((f) => ({ ...f, model: m })); setSaved(false); setModels((s) => ({ ...s, open: false })); }}
                        className={`w-full text-left px-3 py-2 text-sm font-mono hover:bg-paper3 transition-colors ${form.model === m ? 'bg-paper3 text-brass' : 'text-ink'}`}>{m}</button>
                    ));
                  })()}
                </div>
              )}
              {models.error && <p className="text-micro text-warn mt-1">{models.error}</p>}
              {models.list.length > 0 && <p className="text-micro text-muted mt-1">{models.list.length} models from server — pick or type custom.</p>}
              <p className="text-xs text-muted mt-1">
                Left empty, the server's LLM_MODEL applies when it has one.
              </p>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row gap-2 mt-4">
            <button
              type="button"
              onClick={handleTest}
              disabled={!canTest || test?.phase === 'running'}
              className="btn-quiet py-2 px-4 text-sm disabled:opacity-50"
            >
              {test?.phase === 'running'
                ? <><Loader2 size={14} className="animate-spin" /> testing…</>
                : 'Test connection'}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={!canSave}
              className={saved ? 'badge-ok px-4 cursor-default' : 'btn-primary'}
            >
              {saved ? <><Check size={14} /> Saved</> : 'Save provider'}
            </button>
            {aiProviderSet(savedConfig) && !saved && (
              <button type="button" onClick={handleClear} className="btn-quiet py-2 px-4 text-sm">
                <RotateCcw size={14} className="inline -mt-0.5 mr-1" /> Reset
              </button>
            )}
          </div>

          {test?.phase === 'ok' && (
            <p className="badge-ok mt-3 inline-flex items-center gap-1.5" role="status">
              <Check size={12} /> {test.latencyMs ? `responded in ${test.latencyMs}ms` : 'responded'}{test.model ? ` · ${test.model}` : ''}
            </p>
          )}
          {test?.phase === 'error' && (
            <div className="mt-3 px-3 py-2.5 rounded-input bg-paper3 border border-rule text-xs" role="alert">
              <p className="flex items-center gap-1.5 font-medium text-ink">
                <AlertTriangle size={12} className="text-warn shrink-0" />
                {test.kind === 'config'
                  ? 'This configuration is not usable:'
                  : 'The provider refused the request:'}
              </p>
              <p className="text-muted mt-1 break-words">{test.detail}</p>
            </div>
          )}
        </>
      ) : (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-start gap-2.5 text-sm">
            <span className="w-2 h-2 rounded-full bg-ok shrink-0 mt-1.5" aria-hidden="true" />
            <div>
              <p className="font-medium text-ink">Configured on the server</p>
              <p className="text-muted text-xs mt-0.5 font-mono break-all">{llmBaseUrl || openaiBaseUrl}</p>
              {(llmModel || openaiModel) && (
                <p className="text-muted text-xs">model: <span className="font-mono">{llmModel || openaiModel}</span></p>
              )}
            </div>
          </div>
          <button type="button" onClick={() => setOverrideRequested(true)} className="btn-quiet py-2 px-4 text-sm shrink-0">
            override with my own endpoint
          </button>
        </div>
      )}
    </div>
  );
}
```

### dashboard/src/App.jsx — MODIFY

Store swap (`aiProviderConfig_v1` encrypted), one-time migration importing `llmConfig_v1` + `openai_*` and deleting them (writers `:709-712` removed), `AiProviderCard` mount replacing `LlmProviderCard` + the inline OpenAI card, `providerCfg`/`llmActive`/`needsAiBackend` from the unified store, `handleProcess` emitting both families via `lib/llm.js` builders, prop shims for components folded in Slice 2.

```jsx
[Section A — module scope; new import + const; the LlmProviderCard import (:5) is deleted]
import AiProviderCard from './components/AiProviderCard';
import { aiProviderSet, llmConfigComplete, llmHeaders, openaiHeaders } from './lib/llm';

const AI_PROVIDER_STORE_KEY = 'aiProviderConfig_v1';

[Section B — states; replaces ONLY the openai states at :246-254 (openaiKey, openaiModel,
 openaiBaseUrl, openaiModels, openaiModelsLoading, openaiModelsError, openaiModelDropdownOpen).
 geminiModelDropdownOpen stays untouched at :255.]
  const [geminiModel, setGeminiModel] = useState(localStorage.getItem('gemini_model') || 'gemini-3.1-flash-lite');
  const [aiProvider, setAiProvider] = useState(localStorage.getItem('ai_provider') || 'gemini');

[Section C — store; replaces the llmConfig state block at ~:310-331]
  // --- aiProviderConfig state — the ONE provider store (unified AI provider config) ---
  // One encrypted JSON blob {baseUrl, apiKey, model} under aiProviderConfig_v1.
  // First load migrates the two legacy sources in: the encrypted llmConfig_v1
  // triple (the more deliberate configuration) wins over the plaintext
  // openai_* triple; an existing aiProviderConfig_v1 wins over both. The
  // legacy keys are removed AFTER adoption, adopted or not — the
  // App.jsx:229-250 (gemini_key) pattern. Nothing here blocks booting.
  const [aiProviderConfig, setAiProviderConfig] = useState(() => {
    const empty = { baseUrl: '', apiKey: '', model: '' };
    const shape = (parsed) => ((parsed && typeof parsed === 'object') ? {
      baseUrl: typeof parsed.baseUrl === 'string' ? parsed.baseUrl : '',
      apiKey: typeof parsed.apiKey === 'string' ? parsed.apiKey : '',
      model: typeof parsed.model === 'string' ? parsed.model : '',
    } : null);
    let value = null;
    try {
      const stored = localStorage.getItem(AI_PROVIDER_STORE_KEY);
      if (stored) value = shape(JSON.parse(decrypt(stored)));
    } catch (_) { /* corrupt or key-rotated blob — fall through to migration */ }
    if (!value || !value.baseUrl) {
      try {
        const legacy = localStorage.getItem('llmConfig_v1');
        if (legacy) value = shape(JSON.parse(decrypt(legacy))) || value;
      } catch (_) { /* unreadable legacy blob — ignore */ }
    }
    if (!value || !value.baseUrl) {
      value = {
        ...(value || empty),
        baseUrl: (value && value.baseUrl) || localStorage.getItem('openai_base_url') || '',
        apiKey: (value && value.apiKey) || localStorage.getItem('openai_key') || '',
        model: (value && value.model) || localStorage.getItem('openai_model') || '',
      };
    }
    ['llmConfig_v1', 'openai_key', 'openai_model', 'openai_base_url'].forEach((k) => {
      try { localStorage.removeItem(k); } catch (_) { /* ignore */ }
    });
    return value || empty;
  });

[Section D — persistence; replaces the llmConfig effect at ~:697-705. Inside the big
 persistence effect (~:708+) the three openai_* writer lines (:709, :710, :712) are DELETED and
 openaiKey/openaiModel/openaiBaseUrl leave the deps array; gemini_model / ai_provider / toggles
 writers stay.]
  // --- aiProviderConfig persistence — encrypted JSON, baseUrl-gated (D10) --------
  // A base URL alone is a usable (keyless local) configuration, so that is
  // the persistence gate. Clearing the card removes the blob, so Reset is a
  // real reset across reloads.
  useEffect(() => {
    try {
      if (aiProviderSet(aiProviderConfig)) {
        localStorage.setItem(AI_PROVIDER_STORE_KEY, encrypt(JSON.stringify(aiProviderConfig)));
      } else {
        localStorage.removeItem(AI_PROVIDER_STORE_KEY);
      }
    } catch (_) { /* ignore */ }
  }, [aiProviderConfig]);

[Section E — gates; replaces providerCfg/llmActive/needsAiBackend at ~:913-920]
  const providerCfg = billingEnabled
    ? { baseUrl: '', apiKey: '', model: '' }
    : aiProviderConfig;
  // D10: a base URL alone is a runnable (keyless local) pipeline config.
  const aiProviderActive = aiProviderSet(providerCfg);
  // With no Gemini key and the unified card set, a stale 'gemini' toggle
  // choice would 400 the job on a missing key. The toggle below renders this
  // value too — what it shows is what runs.
  const effectiveProvider = (!apiKey && aiProviderActive && aiProvider === 'gemini') ? 'openai' : aiProvider;
  const llmActive = llmConfigComplete(providerCfg) || !!llmConfigured || !!localLlm;
  const needsAiBackend = !apiKey && !llmActive && !aiProviderActive;

[Section F — handleProcess header assembly; replaces the header block + the four X-OpenAI lines at ~:1053-1077]
      const headers = {
        // One store, both families, one emitter (lib/llm.js): derived
        // X-LLM-* for the satellites (inert without base+key), X-OpenAI-* for
        // the pipeline (key/model omitted when unset). The Gemini key is our
        // encrypted apiKey, attached once here; the family fields below are
        // theirs (X-AI-Provider / toggles / X-Target-Clips / X-Deep-Provider /
        // X-Game-Profile-Id).
        ...llmHeaders(providerCfg),
        ...openaiHeaders(providerCfg),
        ...(apiKey ? { 'X-Gemini-Key': apiKey } : {}),
        'X-AI-Provider': effectiveProvider,
        'X-Enable-Scene': enableScene ? '1' : '0',
        'X-Enable-Audio': enableAudio ? '1' : '0',
        'X-Enable-Visual': enableVisual ? '1' : '0',
        'X-Enable-Vision': enableVision ? '1' : '0',
        'X-Enable-Deep': enableDeep ? '1' : '0',
        'X-Enable-Enhance': enableEnhance ? '1' : '0',
        'X-Enable-Emoji': enableEmoji ? '1' : '0',
        'X-Target-Clips': String(targetClips),
        ...(deepProvider ? { 'X-Deep-Provider': deepProvider } : {}),
        ...(selectedProfileId ? { 'X-Game-Profile-Id': selectedProfileId } : {}),
      };
      if (geminiModel) headers['X-Gemini-Model'] = geminiModel;

[Section G — settings mount; DELETES the LlmProviderCard mount together with its preceding
 comment block (~:1627-1639) AND the entire inline "OpenAI / Compatible API" card block
 (~:1641-1761, through its balanced closing </div>); both become:]
              {/* The ONE provider card (self-host only): its store feeds BOTH
                  header families via lib/llm.js — the pipeline triple and the
                  derived satellite triple (D5, unified). */}
              <AiProviderCard
                savedConfig={aiProviderConfig}
                onConfigSet={setAiProviderConfig}
                llmConfigured={llmConfigured}
                llmModel={llmModel}
                llmBaseUrl={llmBaseUrl}
                openaiConfigured={openaiConfigured}
                openaiModel={openaiModel}
                openaiBaseUrl={openaiBaseUrl}
              />

[Section H — provider toggle (~:2168-2185); the readout and selected-classes switch to
 effectiveProvider; the Gemini button is disabled when no Gemini key exists and the card is set]
                    <span className="readout">{effectiveProvider}</span>
                    <button
                      type="button"
                      onClick={() => setAiProvider('gemini')}
                      disabled={!apiKey && aiProviderActive}
                      title={!apiKey && aiProviderActive ? 'No Gemini key set — using your AI provider' : undefined}
                      className={effectiveProvider === 'gemini' ? 'btn-primary px-3 py-1 text-xs' : 'btn-quiet px-3 py-1 text-xs'}
                    >
                      Gemini
                    </button>
                    <button
                      type="button"
                      onClick={() => setAiProvider('openai')}
                      className={effectiveProvider === 'openai' ? 'btn-primary px-3 py-1 text-xs' : 'btn-quiet px-3 py-1 text-xs'}
                    >
                      OpenAI
                    </button>

[Section I — prop wiring, final state after Slice 2's fold. Slice 1 installed transitional
 shims at the VoiceOverPage mount (~:2093-2095) and the CreateEditProfileModal mount (~:2353)
 (openaiApiKey={providerCfg.apiKey} / openaiModel={providerCfg.model} /
 openaiBaseUrl={providerCfg.baseUrl}); Slice 2 folds those components and replaces the
 shims with the single unified prop. The ResultCard mount (~:2597) gains the same prop
 after elevenLabsKey={elevenLabsKey} (Slice 2). The aiProvider={aiProvider} prop lines
 stay untouched at both mounts:]
                aiProviderConfig={providerCfg}

[Section J — deep-provider readout (~:2249) replaces the openaiModel read with:]
  {providerCfg.model || 'server default'}

[Section K — the useAuth destructure (~:212) gains: openaiConfigured, openaiModel, openaiBaseUrl]
```

### dashboard/src/components/LlmProviderCard.jsx — DELETE

Deleted outright (D7). Two references removed in Slice 1 (`App.jsx:5` import, `:1631-1639` mount). Zero test references.

```text
DELETED. The file is removed (D7). Its only two references — the import at App.jsx:5
and the mount at App.jsx:1631-1639 — are deleted by Sections A and G of the App.jsx
changes above. Zero test references (verified repo-wide: matches were in .rpiv/ docs only).
Its affordances were re-homed into AiProviderCard.jsx: presets, the /api/llm/test
connection flow, the derived server badge, and the show/hide key toggle.
```

### dashboard/src/components/ResultCard.jsx — MODIFY

`handleAutoEdit` gains both derived families on `/api/effects/generate` + `/api/edit` (today sends only `X-Gemini-Key`, `ResultCard.jsx:294`); the "Gemini API Key is missing" pre-check relaxes to "no AI backend at all".

```jsx
// --- imports: add after `import { renderInBrowser } from '../lib/renderInBrowser';` (:13) ---
import { aiProviderSet, llmHeaders, openaiHeaders } from '../lib/llm';

// --- props (:39): insert `aiProviderConfig = {},` after `elevenLabsKey,` ---
export default function ResultCard({ clip, index, jobId, durable, uploadPostKey, uploadUserId, geminiApiKey, elevenLabsKey, aiProviderConfig = {}, isManaged, onPlay, onPause, onBulkSubtitle, clipCount = 1, bulkProgress, initialState = null, onStateChange, connectedPlatforms = null, onConnectSocials, onEditClip = null, onVoiceOver = null, onReframeClip = null }) {

// --- handleAutoEdit: replaces lines 284-294 (the stale "Was:" comment trio through
//     the geminiHeaders line; the new block carries its own comments) ---
            const apiKey = geminiApiKey;

            // Managed (paid) users get the Gemini key resolved server-side. A
            // self-host user is covered by EITHER a Gemini key or the unified
            // AI provider card — a base URL alone is a runnable keyless
            // endpoint (D10), carried by X-OpenAI-*.
            if (!apiKey && !isManaged && !aiProviderSet(aiProviderConfig)) {
                throw new Error("No AI backend: set a Gemini key or an AI provider in Settings.");
            }
            // One store, both families, one emitter (lib/llm.js) — the same
            // spread /api/process sends. X-LLM-* is inert on these endpoints;
            // X-OpenAI-* activates the editor frames path when Slice 5 lands.
            const aiHeaders = {
                ...llmHeaders(aiProviderConfig),
                ...openaiHeaders(aiProviderConfig),
                ...(apiKey ? { 'X-Gemini-Key': apiKey } : {}),
            };

// --- both fetch sites (:301 and :333): `...geminiHeaders` → `...aiHeaders` ---
```

### dashboard/src/components/VoiceOverPage.jsx — MODIFY

`byokHeaders()` (`:346-354`) folds into the shared builders; the unified prop replaces `openaiApiKey/Model/BaseUrl` props.

```jsx
// --- import: add after :8 (`import { apiFetch, apiJson } from '../lib/api';`) ---
import { aiProviderSet, llmHeaders, openaiHeaders } from '../lib/llm';

// --- props: replace ONLY :84-86 (the openai triple) with the unified prop ---
  aiProviderConfig,
// (aiProvider :81, geminiApiKey :82, geminiModel :83 stay untouched)

// --- byokHeaders (replaces :346-354 incl. the closing `};`) ---
  // The caption request's headers. The provider pair (X-AI-Provider +
  // X-Gemini-*) is this page's own toggle; both config families come from
  // the ONE unified store via the lib/llm.js builders — never inline
  // (single-emitter rule). resolve_ai_provider ignores X-LLM-* here; it
  // rides along for uniformity and costs nothing.
  const byokHeaders = () => ({
    'X-AI-Provider': captionProvider,
    ...(geminiApiKey ? { 'X-Gemini-Key': geminiApiKey } : {}),
    ...(geminiModel ? { 'X-Gemini-Model': geminiModel } : {}),
    ...llmHeaders(aiProviderConfig),
    ...openaiHeaders(aiProviderConfig),
  });

// --- generateCaptions openai gate (replaces :362-365 incl. the closing brace) ---
    if (captionProvider === 'openai' && !aiProviderSet(aiProviderConfig)) {
      // D10: a base URL alone is a runnable keyless local endpoint; the old
      // key-only gate blocked exactly those.
      setError('Set your AI provider in Settings first.');
      return;
    }
```

### dashboard/src/components/CreateEditProfileModal.jsx — MODIFY

`handleAiAnalyze` inline builder (`:253-264`) folds into the shared builders; profile save drops `openai_key` from the body (D11); unified prop replaces the triple.

```jsx
// --- import: add after :4 (`import { apiJson } from '../lib/api';`) ---
import { llmHeaders, openaiHeaders } from '../lib/llm';

// --- props (:6-9): replace the triple with the unified prop ---
export default function CreateEditProfileModal({ isOpen, onClose, profile, onSave,
                                                aiProvider, geminiApiKey,
                                                aiProviderConfig }) {
// (deleted: openaiApiKey, openaiModel, openaiBaseUrl)

// --- handleAiAnalyze header block (replaces :253-264, the two-branch if/else;
//     :265+ `const data = await apiJson(...` / `method: 'POST',` / `headers,` stay) ---
      // One spread replaces the old two-branch builder (both branches sent
      // the same headers in different order). The key rides X-OpenAI-Key
      // (resolve_openai), never the body — D11; model/base stay in the body
      // as the server's fallback leg (app.py reads them after the headers).
      const headers = {
        'X-AI-Provider': provider,
        ...(geminiApiKey ? { 'X-Gemini-Key': geminiApiKey } : {}),
        ...llmHeaders(aiProviderConfig),
        ...openaiHeaders(aiProviderConfig),
      };

// --- request body (replaces :274-276; :273 `provider,` stays on disk) ---
          openai_model: aiProviderConfig?.model || undefined,
          openai_base_url: aiProviderConfig?.baseUrl || undefined,
// (deleted: openai_key: openaiApiKey || undefined,)
```

### hook_grounding.py — MODIFY

OpenAI arm inside `reground`: when `GEMINI_API_KEY` is absent, read the job `OPENAI_*` env, build via `ai_provider.create_ai_provider`, guard with `probe_vision_support`, call with frames + `GroundedHook` schema; `_ask_openai` seam beside `_ask_gemini`; skip semantics unchanged.

```python
# --- module docstring, third paragraph (replaces lines 17-19; the rest of the
#     docstring is untouched) ---
Frames need a model that can see: a Gemini key, or this job's
``OPENAI_*`` endpoint when its model passes the vision probe. With neither
the function returns None and the transcript-based hook stands. Never
raises: a hook problem must never cost the clip.

# --- new seams, placed directly after _ask_gemini (after line 141); both
#     mirror _ask_gemini's split-out-for-tests shape ------------------------

def _openai_provider():
    """An OpenAI-compatible provider from the JOB's ``OPENAI_*`` env, or None.

    app.py injects this env into EVERY job — missing values arrive as the
    codebase defaults (``OPENAI_BASE_URL`` falls back to api.openai.com, the
    key to ``""``), so a bare base-URL check would see a "configured"
    endpoint in a job that never set one. A configuration is therefore: a
    key, or a base URL other than the injected default. A model alone is
    never one. Namespace law: pipeline family only — the ``LLM_*`` satellite
    env never reaches this module (pinned by the canary in
    tests/test_hook_grounding.py). Never raises: a missing OpenAI SDK
    degrades to None with one log line (reground's caller is unguarded).
    """
    base = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key and (not base or base.rstrip("/") == "https://api.openai.com/v1"):
        return None
    try:
        import ai_provider
        return ai_provider.create_ai_provider(
            "openai",
            (os.environ.get("OPENAI_MODEL") or "").strip() or None,
            api_key=key or None,
            base_url=base or None,
            temperature=0.2, max_tokens=800, timeout=60)
    except ImportError as e:
        print(f"   ⚠️ Hook grounding: OpenAI SDK unavailable ({e}) — keeping "
              "the transcript hook.")
        return None


def _ask_openai(frames, prompt, provider):
    """The OpenAI-compatible arm of ``_ask_gemini``: the same question, the
    frames as ``image_url`` parts, the SAME ``GroundedHook`` schema (the
    provider sends strict json_schema and retries once without it on local
    servers that lack it). Split out so tests can stub it, like
    ``_ask_gemini``. The provider's model has already passed the vision
    probe."""
    import base64
    import gemini_worker

    content = [{"type": "text", "text": prompt}]
    for b in frames:
        data_url = "data:image/jpeg;base64," + base64.b64encode(b).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    result = provider.generate_content(
        prompt, schema=gemini_worker.GroundedHook,
        messages=[{"role": "user", "content": content}])
    return result["response"] or {}


# --- reground: the gate (replaces lines 148-152) ---------------------------
    api_key = os.getenv("GEMINI_API_KEY")
    provider = None
    if not api_key:
        # Gemini stays preferred; the OpenAI arm is the no-key fallback (D2).
        provider = _openai_provider()
        if provider is None:
            print("   🪝 Hook grounding skipped: needs a Gemini key or an "
                  "OpenAI-compatible endpoint (frames), keeping the "
                  "transcript hook.")
            return None
        import ai_provider
        # One probe per provider identity (cached in ai_provider). "unknown"
        # proceeds — only a confirmed text-only model skips, and the probe
        # cost never enters the job total (OpenAICompatibleProvider).
        if ai_provider.probe_vision_support(provider) == "no_vision":
            print("   🪝 Hook grounding skipped: the selected model cannot "
                  "see images (frames), keeping the transcript hook.")
            return None

# --- reground: the dispatch (replaces line 166) ----------------------------
        answer = (_ask_openai(frames, prompt, provider) if provider is not None
                  else _ask_gemini(frames, prompt, api_key))
```

### tests/test_hook_grounding.py — MODIFY

New stubs/tests for the OpenAI arm: skip without vision after probe `no_vision`; skip with no provider at all; reground succeeds through the OpenAI stub; `_ask_gemini` seam preserved.

```python
# --- imports: add after `import gemini_worker` (:10) ------------------------

import base64
import ai_provider
import llm_client

# --- existing test hardened (replaces :114-118; the blank :119 stays): the
#     OpenAI arm below reads the job OPENAI_* env, so the no-key test must pin
#     the no-backend-at-all skip against whatever the developer's shell has --

def test_without_a_gemini_key_the_transcript_hook_stands(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old" and "hook_grounding" not in clip

# --- new section, inserted after test_empty_answer_keeps_the_old_hook
#     (:130-136), before the `--- the cheap half ---` comment (:139) --------
# --- the OpenAI-compatible arm (no Gemini key) ------------------------------

class _FakeProvider:
    model_name = "qwen2.5vl"


@pytest.fixture
def openai_arm(monkeypatch):
    """No Gemini key; a keyless local endpoint in the job OPENAI_* env; the
    factory, probe and frame extraction stubbed so no network is touched."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5vl")
    made = {}
    prov = _FakeProvider()

    def fake_create(*a, **k):
        made["create_args"] = a
        made["create_kwargs"] = k
        made["provider"] = prov
        return prov

    probes = []

    def fake_probe(p):
        probes.append(p)
        return "vision_ok"

    monkeypatch.setattr(ai_provider, "create_ai_provider", fake_create)
    monkeypatch.setattr(ai_provider, "probe_vision_support", fake_probe)
    monkeypatch.setattr(hg, "frames_at",
                        lambda path, times, width=None: [b"jpg"] * len(times))
    return {"made": made, "probes": probes}


def test_openai_arm_regrounds_the_hook(openai_arm, monkeypatch):
    seen = {}

    def fake(frames, prompt, provider):
        seen.update(frames=frames, prompt=prompt, provider=provider)
        return {"on_screen": "Ollama model list, qwen2.5vl downloading",
                "viral_hook_text": "El modelo local que ve tus clips",
                "video_title_for_youtube_short": "qwen2.5vl, el que sí ve"}

    monkeypatch.setattr(hg, "_ask_openai", fake)
    clip = {"viral_hook_text": "old", "video_title_for_youtube_short": "old title",
            "layout_ranges": SCREEN}

    changed = hg.reground("clip.mp4", clip, {"language": "es", "segments": []}, 0, 30)

    assert clip["viral_hook_text"] == "El modelo local que ve tus clips"
    assert clip["video_title_for_youtube_short"] == "qwen2.5vl, el que sí ve"
    assert clip["hook_grounding"]["before"]["viral_hook_text"] == "old"
    assert changed["on_screen"].startswith("Ollama model list")
    assert len(seen["frames"]) == 3
    made = openai_arm["made"]
    assert made["create_args"][0] == "openai"
    assert made["create_args"][1] == "qwen2.5vl"
    assert made["create_kwargs"]["base_url"] == "http://localhost:11434/v1"
    assert made["create_kwargs"]["api_key"] is None
    assert seen["provider"] is made["provider"]
    assert openai_arm["probes"] == [made["provider"]]   # probed once, the built provider


def test_openai_arm_skips_a_text_only_model(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setattr(ai_provider, "create_ai_provider",
                        lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(ai_provider, "probe_vision_support",
                        lambda p: "no_vision")

    def boom(*a, **k):
        raise AssertionError("a text-only model must never be asked")

    monkeypatch.setattr(hg, "_ask_openai", boom)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old" and "hook_grounding" not in clip


def test_injected_default_base_is_not_a_configuration(monkeypatch):
    # FIX 1 pin: app.py's job-env handover (both provider branches) writes the
    # OPENAI_* defaults — OPENAI_BASE_URL falls back to https://api.openai.com/v1,
    # the key to "" — into EVERY job env. A job gated only by the LLM_* family
    # must skip cleanly: no provider built, no probe, no per-clip calls
    # against the default endpoint.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    def boom(*a, **k):
        raise AssertionError("the injected default must not build a provider")

    monkeypatch.setattr(ai_provider, "create_ai_provider", boom)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old" and "hook_grounding" not in clip


def test_missing_openai_sdk_skips_cleanly(monkeypatch):
    # The openai import happens inside create_ai_provider (ai_provider.py:334)
    # and reground's caller is unguarded (main.py:3700), so a missing SDK must
    # degrade to a skip, never raise out of reground.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    def no_sdk(*a, **k):
        raise ImportError("No module named 'openai'")

    monkeypatch.setattr(ai_provider, "create_ai_provider", no_sdk)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old" and "hook_grounding" not in clip


def test_openai_arm_never_touches_the_satellite_client(openai_arm, monkeypatch):
    # No-double-route canary for this reroute (design ordering constraint):
    # the arm is pipeline-family (ai_provider + job OPENAI_* env); a leak into
    # the satellite client trips loudly. Mirrors _canary_llm_client in
    # tests/test_no_double_route.py, kept here so that file stays unmodified.
    def boom(*a, **k):
        raise AssertionError("hook grounding must not call the satellite client")

    monkeypatch.setattr(llm_client, "chat", boom)
    monkeypatch.setattr(llm_client, "active_config", boom)
    monkeypatch.setattr(hg, "_ask_openai", lambda *a: {
        "on_screen": "x", "viral_hook_text": "new hook",
        "video_title_for_youtube_short": "new title"})
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    changed = hg.reground("clip.mp4", clip, {"segments": []}, 0, 30)
    assert changed is not None
    assert clip["viral_hook_text"] == "new hook"


def test_a_gemini_key_wins_over_the_openai_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(hg, "frames_at", lambda *a, **k: [b"jpg"])
    monkeypatch.setattr(hg, "_ask_gemini", lambda *a: {
        "on_screen": "g", "viral_hook_text": "gemini hook",
        "video_title_for_youtube_short": "t"})

    def boom(*a, **k):
        raise AssertionError("with a Gemini key the OpenAI arm must stay off")

    monkeypatch.setattr(hg, "_ask_openai", boom)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is not None
    assert clip["viral_hook_text"] == "gemini hook"


def test_ask_openai_sends_frames_as_image_parts():
    calls = {}

    class FakeProv:
        def generate_content(self, prompt, schema=None, messages=None, **kw):
            calls.update(prompt=prompt, schema=schema, messages=messages)
            return {"response": {"on_screen": "s", "viral_hook_text": "h",
                                 "video_title_for_youtube_short": "t"},
                    "cost_analysis": None}

    out = hg._ask_openai([b"jpg1", b"jpg2"], "PROMPT", FakeProv())

    assert out["viral_hook_text"] == "h"          # result["response"] — the _ask_gemini shape
    assert calls["schema"] is gemini_worker.GroundedHook
    content = calls["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "PROMPT"}
    assert content[1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(b"jpg1").decode("ascii"))
    assert len(content) == 3                      # one text part + two frames


def test_openai_provider_needs_a_key_or_a_real_base(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert hg._openai_provider() is None          # nothing configured → no arm
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert hg._openai_provider() is None          # the injected default is not one either

    fake = _FakeProvider()
    calls = []

    def fake_create(*a, **k):
        calls.append((a, k))
        return fake

    monkeypatch.setattr(ai_provider, "create_ai_provider", fake_create)

    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")  # keyless local
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5vl")
    prov = hg._openai_provider()
    assert prov is fake
    (a, k), = calls
    assert a[0] == "openai" and a[1] == "qwen2.5vl"
    assert k["base_url"] == "http://localhost:11434/v1"
    assert k["api_key"] is None

    calls.clear()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")  # default + key
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert hg._openai_provider() is fake         # a key on the default endpoint is a config
    (a, k), = calls
    assert k["base_url"] == "https://api.openai.com/v1"
    assert k["api_key"] == "sk-x"
```

### CLAUDE.md — MODIFY

Two shipped claims go stale once OpenAI arms exist. Slice 3: the "Gemini-only (frames)" claim in "Hook grounding for on-screen clips" (sentence swap, replaced by quote; match the quoted text, not the range). Slice 4: the "Gemini reports each range's width_fraction" claim in "SCREENCAST / WIDE Modes" — same treatment, the fragment quoted across its hard wrap at CLAUDE.md:245-246.

```markdown
# --- Slice 3: "Hook grounding for on-screen clips" section -------------------
- old: "Gemini-only (frames): with just a local LLM it logs one line and keeps
  the transcript hook. `HOOK_GROUNDING=0` disables it."
+ new: "Frames need a model that can see: a Gemini key, or the job's `OPENAI_*`
  endpoint when its model passes the vision probe; with neither it logs one
  line and keeps the transcript hook. `HOOK_GROUNDING=0` disables it."

# --- Slice 4: "SCREENCAST / WIDE Modes" bullet -------------------------------
# old (fragment spanning the hard wrap at :245-246, two-space continuation
# indent; the rest of the sentence through "cannot)." at :248 is untouched):
- "Gemini reports each range's
  **width_fraction**, and that is the gate"
+ "Gemini (whole video) or the job's `OPENAI_*` endpoint (12 frames @1024px,
  vision-probe-gated) reports each range's **width_fraction**, and that is
  the gate"
```

### screencast_layout.py — MODIFY

`detect_content_ranges` gains a frames arm: when Gemini is absent but the job `OPENAI_*` env resolves, sample 12 frames @1024px (`layout_picker.sample_frames`), call via `ai_provider` with `WideContentResponse` schema + probe guard; degrade-to-`[]` unchanged.
```python
# --- module docstring, last paragraph (replaces lines 28-29; line 30 closes
#     the docstring) ------------------------------------------------------
# old:
# Off by default (``SCREENCAST_LAYOUT=1``). Needs GEMINI_API_KEY; without one it
# is a silent no-op, like every other optional Gemini path here.
# new:
Off by default (``SCREENCAST_LAYOUT=1``). Needs a Gemini key, or this job's
``OPENAI_*`` endpoint when its model passes the vision probe; with neither it
is a silent no-op, like every other optional path here.


# --- new seams, placed directly after overlapping_width (between line 165
#     and def detect_content_ranges at :167) ------------------------------

def _openai_provider():
    """An OpenAI-compatible provider from the JOB's ``OPENAI_*`` env, or None.

    Same gate as hook_grounding._openai_provider: app.py writes the
    ``OPENAI_*`` defaults into EVERY job env, so a bare base-URL check would
    see a "configured" endpoint in a job that never set one. A configuration
    is: a key, or a base URL other than the injected default. A model alone
    is never one. Namespace law: pipeline family only — the ``LLM_*``
    satellite env never reaches this module (pinned by the canary in
    tests/test_screencast_layout.py). Never raises: a missing OpenAI SDK
    degrades to None with one log line (detect_content_ranges must never
    raise out of its gate).
    """
    base = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key and (not base or base.rstrip("/") == "https://api.openai.com/v1"):
        return None
    try:
        import ai_provider
        return ai_provider.create_ai_provider(
            "openai",
            (os.environ.get("OPENAI_MODEL") or "").strip() or None,
            api_key=key or None,
            base_url=base or None,
            temperature=0.2, max_tokens=3000, timeout=90)
    except ImportError as e:
        print(f"   ⚠️ On-screen check: OpenAI SDK unavailable ({e}) — keeping "
              "face-only routing.")
        return None


def _ask_openai_frames(frames, prompt, provider):
    """The OpenAI-compatible arm of the Files upload: the same question, the
    frames as ``image_url`` parts, the SAME ``WideContentResponse`` schema
    (the provider sends strict json_schema and retries once without it on
    local servers that lack it). Split out so tests can stub it, like
    ``_ask_gemini`` in hook_grounding. The provider's model has already
    passed the vision probe."""
    import base64
    import gemini_worker

    content = [{"type": "text", "text": prompt}]
    for b in frames:
        data_url = "data:image/jpeg;base64," + base64.b64encode(b).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    result = provider.generate_content(
        prompt, schema=gemini_worker.WideContentResponse,
        messages=[{"role": "user", "content": content}])
    return (result["response"] or {}).get("ranges") or []


def _ask_gemini_files(api_key, model_name, video_path, prompt):
    """The Gemini arm, split out UNCHANGED from detect_content_ranges so the
    precedence test can stub it without network — the hook_grounding
    ``_ask_gemini`` shape. Whole-video Files upload, then the
    ``WideContentResponse`` call. Raises on failure; the caller's try turns
    that into []. One accepted log delta on the rare unusable-upload path:
    the ✅ summary line now follows the ⚠️ line (nothing parses these)."""
    from google import genai
    from google.genai import types as genai_types
    import gemini_worker

    client = genai.Client(api_key=api_key)
    file_upload = client.files.upload(file=video_path)
    deadline = time.time() + 180
    while True:
        info = client.files.get(name=file_upload.name)
        state = str(getattr(getattr(info, "state", info), "name", "")).upper()
        if state == "ACTIVE":
            break
        if state == "FAILED" or time.time() > deadline:
            print("   ⚠️ Upload not usable — keeping face-only routing.")
            return []
        time.sleep(2)

    response = client.models.generate_content(
        model=model_name,
        contents=[file_upload, prompt],
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=gemini_worker.WideContentResponse,
        ))
    gemini_worker.raise_if_blocked(response)
    return (json.loads(response.text) or {}).get("ranges") or []


# --- detect_content_ranges: gate + dispatch (replaces lines 173-212, the gate
#     through the except/`return []`; the docstring :168-172 and the ranges
#     gate/sort/summary block :214-234 stay on disk untouched) -------------

    if not ENABLED:
        return []
    api_key = os.getenv("GEMINI_API_KEY")
    provider = None
    if not api_key:
        # Gemini stays preferred; the frames arm is the no-key fallback (D2).
        provider = _openai_provider()
        if provider is None:
            return []
        import ai_provider
        # One probe per provider identity (cached in ai_provider). "unknown"
        # proceeds — only a confirmed text-only model skips, and the probe
        # cost never enters the job total (OpenAICompatibleProvider).
        if ai_provider.probe_vision_support(provider) == "no_vision":
            print("   ⚠️ On-screen check skipped: the selected model cannot "
                  "see images — keeping face-only routing.")
            return []

    import gemini_worker

    print("🔎 Checking for full-width on-screen content…")
    try:
        prompt = gemini_worker.WIDE_CONTENT_PROMPT_TEMPLATE.format(
            video_duration=video_duration)
        if provider is not None:
            # 12 frames at 1024px, not the video: the layout_picker economy
            # (~3k tokens whatever the source length, vs a 1-2 GB Files
            # upload billed at ~300 tokens/s of video).
            import layout_picker
            frames = layout_picker.sample_frames(video_path)
            if not frames:
                print("   ⚠️ No readable frames — keeping face-only routing.")
                return []
            raw = _ask_openai_frames(frames, prompt, provider)
        else:
            model_name = os.environ.get("GEMINI_MODEL") or 'gemini-3.1-flash-lite'
            raw = _ask_gemini_files(api_key, model_name, video_path, prompt)
    except Exception as e:
        print(f"   ⚠️ On-screen check failed ({e}) — keeping face-only routing.")
        return []
```

### tests/test_screencast_layout.py — MODIFY

Frames path returns ranges through an OpenAI-compat stub; degrade to `[]` on provider failure; no-provider stays silent.
Additive only: 10 new OpenAI-arm tests; all 19 existing tests byte-unmodified. No existing test hardening needed — the only existing `detect_content_ranges` caller in tests runs with `ENABLED` False.

```python
# --- imports: extend the header block (after the from screencast_layout
#     import at :2-7) ------------------------------------------------------

import base64

import pytest

import ai_provider
import gemini_worker
import llm_client


# --- new section at end of file -------------------------------------------
# --- the OpenAI-compatible frames arm (no Gemini key) ----------------------

class _FakeProvider:
    model_name = "qwen2.5vl"


@pytest.fixture
def openai_arm(monkeypatch):
    """No Gemini key; a keyless local endpoint in the job OPENAI_* env; the
    factory, probe and frame sampling stubbed so no network or ffmpeg is
    touched."""
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5vl")
    made = {}
    prov = _FakeProvider()

    def fake_create(*a, **k):
        made["create_args"] = a
        made["create_kwargs"] = k
        made["provider"] = prov
        return prov

    probes = []

    def fake_probe(p):
        probes.append(p)
        return "vision_ok"

    monkeypatch.setattr(ai_provider, "create_ai_provider", fake_create)
    monkeypatch.setattr(ai_provider, "probe_vision_support", fake_probe)

    import layout_picker
    sampled = []

    def fake_sample(path, n=None, width=None):
        sampled.append({"path": path, "n": n, "width": width})
        return [b"jpg"] * (n or 12)

    monkeypatch.setattr(layout_picker, "sample_frames", fake_sample)
    return {"made": made, "probes": probes, "sampled": sampled}


def test_openai_arm_returns_gated_ranges(openai_arm, monkeypatch):
    seen = {}

    def fake(frames, prompt, provider):
        seen.update(frames=frames, prompt=prompt, provider=provider)
        return [
            {"start": 0, "end": 20, "what": "spreadsheet", "width_fraction": 0.95},
            {"start": 5, "end": 5.2, "what": "blip", "width_fraction": 0.9},   # < 0.5s
            {"start": 0, "end": 20, "what": "corner ticker", "width_fraction": 0.15},
        ]

    monkeypatch.setattr(screencast_layout, "_ask_openai_frames", fake)

    ranges = screencast_layout.detect_content_ranges("x.mp4", 30)

    # The width/duration gate applies to the frames arm exactly as to Gemini's.
    assert ranges == [(0.0, 20.0, "spreadsheet", 0.95)]
    assert len(seen["frames"]) == 12                # layout_picker's default count
    made = openai_arm["made"]
    assert made["create_args"][0] == "openai"
    assert made["create_args"][1] == "qwen2.5vl"
    assert made["create_kwargs"]["base_url"] == "http://localhost:11434/v1"
    assert made["create_kwargs"]["api_key"] is None
    assert seen["provider"] is made["provider"]
    assert openai_arm["probes"] == [made["provider"]]   # probed once, the built provider
    assert openai_arm["sampled"][0]["path"] == "x.mp4"


def test_openai_arm_skips_a_text_only_model(monkeypatch):
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setattr(ai_provider, "create_ai_provider",
                        lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(ai_provider, "probe_vision_support",
                        lambda p: "no_vision")

    def boom(*a, **k):
        raise AssertionError("a text-only model must never be asked")

    monkeypatch.setattr(screencast_layout, "_ask_openai_frames", boom)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_injected_default_base_is_not_a_configuration(monkeypatch):
    # app.py's job-env handover writes the OPENAI_* defaults — the base falls
    # back to https://api.openai.com/v1, the key to "" — into EVERY job env.
    # A job gated only by the LLM_* family must stay a silent no-op: no
    # provider built, no probe, no frames sampled against the default endpoint.
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    def boom(*a, **k):
        raise AssertionError("the injected default must not build a provider")

    monkeypatch.setattr(ai_provider, "create_ai_provider", boom)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_missing_openai_sdk_skips_cleanly(monkeypatch):
    # The openai import happens inside create_ai_provider, and the contract is
    # never-raise: a missing SDK degrades to [], not a crash.
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    def no_sdk(*a, **k):
        raise ImportError("No module named 'openai'")

    monkeypatch.setattr(ai_provider, "create_ai_provider", no_sdk)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_provider_failure_degrades_to_empty(openai_arm, monkeypatch):
    def broken(frames, prompt, provider):
        raise RuntimeError("boom: endpoint refused")

    monkeypatch.setattr(screencast_layout, "_ask_openai_frames", broken)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_no_readable_frames_degrades_to_empty(openai_arm, monkeypatch):
    import layout_picker
    monkeypatch.setattr(layout_picker, "sample_frames",
                        lambda path, n=None, width=None: [])
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_openai_arm_never_touches_the_satellite_client(openai_arm, monkeypatch):
    # No-double-route canary for this reroute: the arm is pipeline-family
    # (ai_provider + job OPENAI_* env); a leak into the satellite client trips
    # loudly. Kept here so tests/test_no_double_route.py stays byte-unmodified.
    def boom(*a, **k):
        raise AssertionError("screencast detection must not call the satellite client")

    monkeypatch.setattr(llm_client, "chat", boom)
    monkeypatch.setattr(llm_client, "active_config", boom)
    monkeypatch.setattr(screencast_layout, "_ask_openai_frames",
                        lambda *a: [{"start": 0, "end": 9, "what": "slide",
                                     "width_fraction": 0.9}])
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == [
        (0.0, 9.0, "slide", 0.9)]


def test_a_gemini_key_wins_over_the_openai_env(monkeypatch):
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(screencast_layout, "_ask_gemini_files",
                        lambda api_key, model, path, prompt: [
                            {"start": 1, "end": 2, "what": "chart",
                             "width_fraction": 0.8}])

    def boom(*a, **k):
        raise AssertionError("with a Gemini key the frames arm must stay off")

    monkeypatch.setattr(screencast_layout, "_ask_openai_frames", boom)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == [
        (1.0, 2.0, "chart", 0.8)]


def test_ask_openai_frames_sends_frames_as_image_parts():
    calls = {}

    class FakeProv:
        def generate_content(self, prompt, schema=None, messages=None, **kw):
            calls.update(prompt=prompt, schema=schema, messages=messages)
            return {"response": {"ranges": [{"start": 0, "end": 1,
                                             "what": "slide",
                                             "width_fraction": 0.9}]},
                    "cost_analysis": None}

    out = screencast_layout._ask_openai_frames([b"jpg1", b"jpg2"], "PROMPT", FakeProv())

    assert out == [{"start": 0, "end": 1, "what": "slide", "width_fraction": 0.9}]
    assert calls["schema"] is gemini_worker.WideContentResponse
    content = calls["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "PROMPT"}
    assert content[1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(b"jpg1").decode("ascii"))
    assert len(content) == 3                      # one text part + two frames


def test_openai_provider_needs_a_key_or_a_real_base(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert screencast_layout._openai_provider() is None    # nothing configured
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert screencast_layout._openai_provider() is None    # the injected default is not one either

    fake = _FakeProvider()
    calls = []

    def fake_create(*a, **k):
        calls.append((a, k))
        return fake

    monkeypatch.setattr(ai_provider, "create_ai_provider", fake_create)

    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")  # keyless local
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5vl")
    prov = screencast_layout._openai_provider()
    assert prov is fake
    (a, k), = calls
    assert a[0] == "openai" and a[1] == "qwen2.5vl"
    assert k["base_url"] == "http://localhost:11434/v1"
    assert k["api_key"] is None

    calls.clear()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")  # default + key
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert screencast_layout._openai_provider() is fake   # a key on the default endpoint is a config
    (a, k), = calls
    assert k["base_url"] == "https://api.openai.com/v1"
    assert k["api_key"] == "sk-x"
```

### editor.py — MODIFY

Frames-based analysis path: module-level function(s) building a provider from an OpenAI triple, sampling frames, asking for `EditPlan`/effects JSON with probe guard; `VideoEditor` Gemini path unchanged; `apply_edits` shared.

```python
# --- 1. NEW module-level block, inserted after EditPlan (after line 24,
#        before "class VideoEditor:" at :27) --------------------------------


class EffectsSegment(BaseModel):
    """One Remotion effects segment — the frames arm's schema for the JSON
    the Gemini arm parses loosely out of response.text (get_effects_config).
    Field names match the wire keys the renderer reads (camelCase)."""
    startSec: float
    endSec: float
    zoom: float = 1.0
    zoomCenterX: float = 0.5
    zoomCenterY: float = 0.5
    brightness: float = 1.0
    contrast: float = 1.0
    saturate: float = 1.0


class EffectsPlan(BaseModel):
    segments: List[EffectsSegment]


class FilterRepair(BaseModel):
    """The repair round-trip's reply: just the corrected filter string."""
    filter_string: str = ""


def _edit_plan_prompt(duration, width, height, transcript, has_captions):
    """The edit-decision prompt — ONE prompt, two transports: the Gemini Files
    arm (VideoEditor.get_ffmpeg_filter) and the OpenAI-compatible frames arm
    (frames_edit_plan). Extracted verbatim from get_ffmpeg_filter; the
    f-string body is byte-identical, so both arms ask the same question."""
    if width is None or height is None:
        width, height = 1080, 1920
    transcript_text = json.dumps(transcript) if transcript else "Not available."

    caption_rule = ""
    if has_captions:
        caption_rule = (
            "\n        CRITICAL: This video already has burned-in captions and/or a hook text. "
            "Any zoom would crop or shift them off screen. Do NOT use zoom_in, punch_in or zoom_pulse — "
            "only color_pop, bw_moment, flash and vignette are allowed.\n"
        )

    prompt = f"""
        You are a viral short-form video editor (TikTok / Reels / Shorts). You receive a video and its transcript.
        Decide WHERE a small set of tasteful, high-impact effects belongs. You do NOT write FFmpeg —
        you return an edit decision list, and a deterministic renderer applies it safely.

        Video duration: {duration:.1f} seconds. Resolution: {width}x{height}.

        Available effect types:
        - "zoom_in": slow push-in across the segment. Builds tension or focus on a key statement. strength 0.06-0.15, segment 1.5-6s.
        - "punch_in": instant tighter framing for the whole segment. Emphasis, punchlines, "listen to this" moments. strength 0.06-0.15, segment 0.8-5s.
        - "zoom_pulse": quick in-and-out pulse peaking mid-segment. Beat drops, single impactful words. strength 0.04-0.10, segment 0.4-1.2s.
        - "color_pop": richer saturation/contrast. Energy, excitement, product/visual highlights. strength 0.2-1.0.
        - "bw_moment": black & white. Drama, serious quotes, flashbacks.
        - "flash": brief bright flash at segment start. Hard-cut energy, reveals. Use at most 2.
        - "vignette": darkened edges. Focus, intimacy, storytelling moments.

        Rules:
        1. Match effects to the CONTENT: place them on the exact words or moments they emphasize, using the transcript timestamps.
        2. Less is more: 2-6 edits per 30 seconds of video. A random effect is worse than no effect.
        3. Never overlap two zoom-type edits (zoom_in/punch_in/zoom_pulse).
        4. Calm, serious delivery => few or no motion effects. High-energy content => more punch.
        {caption_rule}
        TRANSCRIPT (with timestamps, the context of what is being said):
        {transcript_text}

        Return JSON only:
        {{"edits": [{{"type": "punch_in", "start": 3.2, "end": 5.4, "strength": 0.1, "reason": "punchline"}}]}}
        If no effects genuinely improve the video, return {{"edits": []}}.
        """
    return prompt


def _effects_prompt(duration, fps, width, height, transcript):
    """The Remotion effects prompt — shared by both arms, extracted verbatim
    from get_effects_config (the same one-prompt-two-transports rule)."""
    if width is None or height is None:
        width, height = 1080, 1920
    transcript_text = json.dumps(transcript) if transcript else "Not available."

    prompt = f"""
        You are an expert video editor analyzing a video and its transcript to generate dynamic visual effects for a Remotion-based renderer.

        Video Duration: {duration} seconds.
        Video FPS: {fps}
        Video Resolution: {width}x{height}

        TRANSCRIPT (Context of what is being said):
        {transcript_text}

        Your task is to produce a structured JSON describing time-based effect segments that cover the FULL video duration.

        Each segment has these fields:
        - "startSec" (number): Start time in seconds.
        - "endSec" (number): End time in seconds.
        - "zoom" (number): Zoom level. 1.0 = no zoom, max 1.5. Use subtle values like 1.05-1.2 for most cases.
        - "zoomCenterX" (number): Horizontal focus point for zoom, 0.0 (left) to 1.0 (right). 0.5 = center.
        - "zoomCenterY" (number): Vertical focus point for zoom, 0.0 (top) to 1.0 (bottom). 0.5 = center.
        - "brightness" (number): Brightness multiplier. 1.0 = normal. Range 0.8-1.2.
        - "contrast" (number): Contrast multiplier. 1.0 = normal. Range 0.8-1.3.
        - "saturate" (number): Saturation multiplier. 1.0 = normal. Range 0.8-1.3.

        Instructions:
        1. ANALYZE the video content and transcript to understand mood, pacing, and key moments.
        2. Apply CONTEXTUAL effects aligned with speech and action:
           - Use slow, subtle zooms toward the speaker's face during speaking moments.
           - Emphasize key moments, punchlines, or dramatic beats with slightly stronger zoom or contrast.
           - Keep transitions smooth — avoid jarring jumps between segments.
           - If nothing significant is happening, keep values at defaults (zoom 1.0, all multipliers 1.0).
        3. Segments MUST cover the entire video duration from 0 to {duration} seconds with no gaps.
        4. Prefer fewer, longer segments with gradual changes over many rapid short segments.
        5. Output ONLY valid JSON, no explanations.

        Output format:
        {{
            "segments": [
                {{
                    "startSec": 0,
                    "endSec": 3.5,
                    "zoom": 1.0,
                    "zoomCenterX": 0.5,
                    "zoomCenterY": 0.5,
                    "brightness": 1.0,
                    "contrast": 1.0,
                    "saturate": 1.0
                }}
            ]
        }}
        """
    return prompt


def openai_configured(openai_key, openai_base):
    """Whether a resolved OpenAI triple names a real endpoint.

    resolve_openai (app.py:152-165) fills in defaults — base
    https://api.openai.com/v1, key "" — when neither header nor env carries
    them, so a bare base check would see a "configured" endpoint on a server
    that set nothing. Same gate as the in-job arms (hook_grounding.py,
    screencast_layout.py): a key, or a base URL other than the default. A
    model alone is never one."""
    key = (openai_key or "").strip()
    base = (openai_base or "").strip()
    return bool(key) or bool(base and base.rstrip("/") != "https://api.openai.com/v1")


def openai_vision_arm(openai_key, openai_model, openai_base):
    """Build + vision-probe the request-scoped OpenAI-compatible arm.

    Returns (provider, None) or (None, reason); the caller turns a reason
    into a clean 4xx. Never raises. Namespace law: pipeline family only
    (ai_provider + the resolved OPENAI_* triple) — the LLM_* satellite env
    never reaches this module (canary in tests/test_editor_frames.py)."""
    if not openai_configured(openai_key, openai_base):
        return None, "no OpenAI-compatible endpoint configured"
    try:
        import ai_provider
        provider = ai_provider.create_ai_provider(
            "openai",
            (openai_model or "").strip() or None,
            api_key=(openai_key or "").strip() or None,
            base_url=(openai_base or "").strip(),
            temperature=0.2, max_tokens=3000, timeout=120)
    except ImportError as e:
        return None, f"OpenAI SDK unavailable ({e})"
    # One probe per provider identity (cached in ai_provider). "unknown"
    # proceeds — only a confirmed text-only model refuses.
    if ai_provider.probe_vision_support(provider) == "no_vision":
        return None, ("the selected model cannot see images — set a "
                      "vision-capable model or a Gemini key")
    return provider, None


def _frame_messages(prompt, frames):
    """The clip as chat messages: the prompt plus each frame as an image_url
    part — the in-job vision-pass shape (main.py:3015-3023)."""
    import base64
    content = [{"type": "text", "text": prompt}]
    for b in frames:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,"
                                      + base64.b64encode(b).decode("ascii")}})
    return [{"role": "user", "content": content}]


def frames_edit_plan(provider, video_path, duration, fps=30, width=None,
                     height=None, transcript=None, has_captions=False):
    """The OpenAI-compatible arm of VideoEditor.get_ffmpeg_filter: the same
    prompt and EditPlan schema, 12 frames @1024px (layout_picker's economy —
    ~3k tokens whatever the clip length, vs a Files upload) instead of a
    Gemini upload. Returns the same shape as get_ffmpeg_filter. Degrades on
    unreadable frames or an empty plan: the clip stays untouched."""
    import layout_picker
    frames = layout_picker.sample_frames(video_path)
    if not frames:
        print("⚠️ No readable frames for the edit plan — keeping the clip untouched.")
        return {"filter_string": None, "edits": []}
    if width is None or height is None:
        width, height = 1080, 1920
    prompt = _edit_plan_prompt(duration, width, height, transcript, has_captions)
    print("🤖 Asking the OpenAI-compatible endpoint for an edit decision list...")
    result = provider.generate_content(
        prompt, schema=EditPlan, messages=_frame_messages(prompt, frames))
    raw_edits = (result.get("response") or {}).get("edits") or []
    filter_string, applied = build_filter_string(
        raw_edits, duration=duration, fps=fps, width=width, height=height,
        has_captions=has_captions,
    )
    if not filter_string:
        print("ℹ️ The model suggested no (valid) edits — keeping the clip untouched.")
        return {"filter_string": None, "edits": []}
    print(f"🎯 Applying {len(applied)} edits: "
          + ", ".join(f"{e['type']}@{e['start']:.1f}s" for e in applied))
    return {"filter_string": filter_string, "edits": applied}


def frames_effects_config(provider, video_path, duration, fps=30,
                          width=None, height=None, transcript=None):
    """The OpenAI-compatible arm of VideoEditor.get_effects_config: the same
    prompt, the segments validated against EffectsPlan (chat completions
    reply best under an explicit schema; the Gemini arm keeps its loose
    response.text parse). Returns the {"segments": [...]} shape, or None when
    no frames could be read — get_effects_config's own failure shape."""
    import layout_picker
    frames = layout_picker.sample_frames(video_path)
    if not frames:
        print("⚠️ No readable frames for the effects config.")
        return None
    if width is None or height is None:
        width, height = 1080, 1920
    prompt = _effects_prompt(duration, fps, width, height, transcript)
    print("🤖 Asking the OpenAI-compatible endpoint for a Remotion effects config...")
    result = provider.generate_content(
        prompt, schema=EffectsPlan, messages=_frame_messages(prompt, frames))
    plan = result.get("response") or {}
    return {"segments": plan.get("segments") or []}


def openai_repair_filter(provider, filter_string, error_text, width, height):
    """The OpenAI-compatible arm of VideoEditor._repair_filter: the same
    one-shot repair round-trip, text-only — no frames needed, so the frames
    path keeps the self-repair safety net. Returns the corrected string or
    None; never raises."""
    prompt = f"""
        The following FFmpeg -vf filter string you generated fails to run.

        FILTER:
        {filter_string}

        FFMPEG ERROR:
        {error_text}

        Fix the filter. Keep the same creative intent, obey the same rules as before:
        - exact output resolution {width}x{height} (zoompan must set s={width}x{height}),
        - no bare comparison operators (use between/lt/lte/gt/gte),
        - expression values in single quotes.
        Output JSON only: {{"filter_string": "..."}}
        """
    try:
        result = provider.generate_content(prompt, schema=FilterRepair)
        repaired = (result.get("response") or {}).get("filter_string")
        return repaired if isinstance(repaired, str) and repaired.strip() else None
    except Exception as e:
        print(f"⚠️ Filter self-repair failed: {e}")
        return None

# --- 2. __init__ (replaces lines 28-34) ------------------------------------

    def __init__(self, api_key, openai_provider=None):
        # A Gemini key builds the client. Without one (the OpenAI-compatible
        # frames arm) it stays None and every Gemini-only method is off:
        # upload_video / get_ffmpeg_filter / get_effects_config are simply
        # never called on that path, and _repair_filter's guard routes the
        # repair to the OpenAI arm instead.
        self.client = genai.Client(api_key=api_key) if api_key else None
        self.openai_provider = openai_provider
        self.model_name = (
            os.environ.get("GEMINI_MODEL_EDITOR")
            or os.environ.get("GEMINI_MODEL")
            or "gemini-3.1-flash-lite"
        )

# --- 3. get_ffmpeg_filter (replaces lines 74-113: transcript_text through
#        the prompt f-string's closing quotes; the width/height defaulting
#        at :70-72 and everything from the :114 print on stay on disk) -------

        # One prompt, two transports: shared verbatim with the frames arm
        # (frames_edit_plan) so both arms ask the exact same question.
        prompt = _edit_plan_prompt(duration, width, height, transcript, has_captions)

# --- 4. get_effects_config (replaces lines 168-219) -------------------------

        # One prompt, two transports: shared verbatim with the frames arm
        # (frames_effects_config).
        prompt = _effects_prompt(duration, fps, width, height, transcript)

# --- 5. _repair_filter head (replaces lines 365-369: def, docstring, and
#        the "if not self.client: return None" guard; the prompt from :370
#        on stays on disk) ---------------------------------------------------

    def _repair_filter(self, filter_string, error_text, width, height):
        """One self-repair round-trip: show the model the FFmpeg error and ask
        for a corrected filter string. Returns the new string or None."""
        if not self.client:
            # No Gemini client: the OpenAI-compatible arm repairs text-only.
            if self.openai_provider is not None:
                return openai_repair_filter(
                    self.openai_provider, filter_string, error_text, width, height)
            return None
```

### app.py — MODIFY

Slice 1 — `/api/config`: three read-only, billing-pinned `openai*` fields (FR9) next to the existing `llm*` fields. Slice 5 — `/api/edit` + `/api/effects/generate` key blocks: resolve OpenAI triple in-request (caption precedent `app.py:6786-6807`); branch Gemini Files path vs frames path; clean error when neither. Slice 6 — thumbnail gate `:6052-6055` accepts the unified config; billing branch `:6058+` intact.

```python
# --- Slice 1: /api/config response dict (~app.py:2065-2077) — insert the three
#     openai* fields right after the llmBaseUrl entry (FR9). ---

        "llmConfigured": llm_cfg is not None,
        "llmModel": llm_cfg.model if llm_cfg else None,
        "llmBaseUrl": llm_cfg.base_url if llm_cfg else None,
        # FR9: the server's pipeline-family env (OPENAI_*), read-only — the
        # unified card's badge shows both halves. Presence only, never the
        # key, and billing-pinned like the llm_* fields: a hosted server must
        # not leak its env configuration to cloud clients (the same invariant
        # ai_backend_available's docstring states).
        "openaiConfigured": (not BILLING_ENABLED) and bool(
            (os.environ.get("OPENAI_API_KEY") or "").strip()
            or (os.environ.get("OPENAI_BASE_URL") or "").strip()),
        "openaiModel": None if BILLING_ENABLED else (os.environ.get("OPENAI_MODEL") or None),
        "openaiBaseUrl": None if BILLING_ENABLED else ((os.environ.get("OPENAI_BASE_URL") or "").strip() or None),

# --- Slice 5 (approved 2026-09-10) ------------------------------------------

# --- A. import (replaces line 3275) ----------------------------------------

from editor import (VideoEditor, frames_edit_plan, frames_effects_config,
                    openai_configured, openai_vision_arm)

# --- B. /api/edit gate (replaces lines 3485-3488: "final_api_key = body_key
#        or _gemini_key" through "raise gemini_missing_error()"; the body_key
#        and _gemini_key lines at :3483-3484 stay on disk) --------------------

    final_api_key = body_key or _gemini_key
    openai_key, openai_model, openai_base = resolve_openai(request)
    # Gemini preferred (D2): the frames arm is the no-key fallback, the same
    # precedence as the in-job stages. openai_configured rejects resolve_openai's
    # bare fallbacks (default base + empty key), so an unset server env can
    # never route a request at api.openai.com.
    use_openai = (not final_api_key
                  and openai_configured(openai_key, openai_base))
    openai_prov = None
    if use_openai:
        # Build + probe in the executor: both are blocking network calls and
        # this gate runs on the event loop. A refused arm is a config problem
        # → clean 400 here, where it survives (the big try below only has
        # `except Exception`).
        loop = asyncio.get_event_loop()
        openai_prov, arm_error = await loop.run_in_executor(
            None, openai_vision_arm, openai_key, openai_model, openai_base)
        if openai_prov is None:
            raise HTTPException(status_code=400, detail=arm_error)
    if not final_api_key and not use_openai:
        raise gemini_missing_error()

# --- C. run_edit (:3537 construction; :3549-3550 upload; :3578 plan branch;
#        :3583 apply_edits UNCHANGED — the shared renderer, byte-identical on
#        disk; the frames path reaches it through the same method because
#        VideoEditor now tolerates api_key=None) --------------------------------

            editor = VideoEditor(api_key=final_api_key, openai_provider=openai_prov)

# :3549-3550 — upload becomes Gemini-arm-only:
                # 1. Upload (using safe path) — Gemini arm only. The frames
                #    arm samples 12 frames locally inside frames_edit_plan.
                vid_file = None
                if not use_openai:
                    vid_file = editor.upload_video(safe_input_path)

# :3578 — the plan branches; the prompt and EditPlan schema are shared:
                if use_openai:
                    filter_data = frames_edit_plan(
                        openai_prov, safe_input_path, duration, fps=fps,
                        width=width, height=height, transcript=transcript,
                        has_captions=has_captions)
                else:
                    filter_data = editor.get_ffmpeg_filter(vid_file, duration, fps=fps, width=width, height=height, transcript=transcript, has_captions=has_captions)

# --- D. /api/effects/generate gate (replaces lines 4645-4648:
#        "final_api_key, _gemini_model = await resolve_gemini(request)"
#        through "raise gemini_missing_error()") -------------------------------

    final_api_key, _gemini_model = await resolve_gemini(request)
    openai_key, openai_model, openai_base = resolve_openai(request)
    # Same two-arm gate as /api/edit (Gemini preferred, key-or-real-base).
    use_openai = (not final_api_key
                  and openai_configured(openai_key, openai_base))
    openai_prov = None
    if use_openai:
        loop = asyncio.get_event_loop()
        openai_prov, arm_error = await loop.run_in_executor(
            None, openai_vision_arm, openai_key, openai_model, openai_base)
        if openai_prov is None:
            raise HTTPException(status_code=400, detail=arm_error)
    if not final_api_key and not use_openai:
        raise gemini_missing_error()

# --- E. run_effects_generation (:4677 construction; :4685-4686 upload;
#        :4726-4728 the config branch) -----------------------------------------

            editor = VideoEditor(api_key=final_api_key, openai_provider=openai_prov)

# :4685-4686 — upload becomes Gemini-arm-only:
                # Upload video to Gemini — Files arm only; the frames arm
                # samples locally inside frames_effects_config.
                vid_file = None
                if not use_openai:
                    vid_file = editor.upload_video(safe_input_path)

# :4726-4728 — the config branches; the prompt is shared:
                if use_openai:
                    effects_config = frames_effects_config(
                        openai_prov, safe_input_path, duration, fps=fps,
                        width=width, height=height, transcript=transcript)
                else:
                    effects_config = editor.get_effects_config(
                        vid_file, duration, fps=fps, width=width, height=height, transcript=transcript
                    )

# --- Slice 6 (approved 2026-09-10) -------------------------------------------

# --- F. /api/thumbnail/generate gate (replaces :6053-6056) ---------------------

    api_key, _gemini_model = await resolve_gemini(request)
    openai_key, openai_model, openai_base = resolve_openai(request)
    # Gemini preferred (D2): the unified endpoint is the no-key fallback, the
    # same precedence as every rerouted stage. openai_configured — Slice 5's
    # gate predicate, already imported — rejects resolve_openai's bare
    # fallbacks (default base + empty key), so an unset server env can never
    # point a request at api.openai.com. The not-BILLING leg keeps cloud
    # byte-identical to HEAD: an entitled user always resolves the managed
    # key (so the arm never fires), and an anonymous/free caller with BYOK
    # X-OpenAI-* headers — or a stray server OPENAI_* env — must not reach
    # the unmetered executor. The billing branch below (:6058-6065) is
    # untouched.
    use_openai = ((not BILLING_ENABLED) and (not api_key)
                  and openai_configured(openai_key, openai_base))
    if not api_key and not use_openai:
        raise gemini_missing_error()
    llm_cfg = await resolve_llm(request, task="thumbnail")


# --- G. the executor call (replaces :6110-6118) --------------------------------

        loop = asyncio.get_event_loop()
        # D14: the images arm is text-prompt only; the model default resolves
        # inside thumbnail.py (resolve_openai's "gpt-4o-mini" fallback is a
        # chat model and cannot draw).
        openai_image = ({"api_key": openai_key, "model": openai_model,
                         "base_url": openai_base} if use_openai else None)
        try:
            thumbnails = await loop.run_in_executor(
                None,
                functools.partial(
                    generate_thumbnail, api_key, title, session_id, face_path, bg_path,
                    extra_prompt, count, video_context, burn_text=burn_text,
                    thumbnail_text_hint=text_hint, language=language,
                    frame_reference=frame_reference, llm_config=llm_cfg,
                    openai_image_config=openai_image),
            )
        except Exception as e:
            if use_openai:
                # The clean-unavailable state: a configured endpoint that
                # cannot generate images is a configuration problem, not a
                # server fault. 400 — and the outer HTTPException handler
                # releases the reservation.
                raise HTTPException(status_code=400, detail=(
                    "Image generation is not available on the configured endpoint "
                    f"({e}). Set a Gemini key, or point the AI provider at an "
                    "image-capable model."))
            raise
```


### dashboard/src/contexts/AuthContext.jsx — MODIFY

FR9 exposure: three consts (`openaiConfigured`, `openaiModel`, `openaiBaseUrl`) derived from the `/api/config` fetch, added to the context `value` object next to the `llm*` trio.

```jsx
// --- consts before the `value` object (~AuthContext.jsx:151-155): add after llmBaseUrl ---

  // FR9: the server's pipeline-family env (OPENAI_*), read-only — the other
  // half of the unified card's server badge.
  const openaiConfigured = !!config.openaiConfigured;
  const openaiModel = config.openaiModel || null;
  const openaiBaseUrl = config.openaiBaseUrl || null;

// --- and inside the `value` object (~:163), after llmBaseUrl: ---

    openaiConfigured,
    openaiModel,
    openaiBaseUrl,
```

### tests/test_llm_endpoints.py — MODIFY

One additive `TestConfigFields` test pinning the new `/api/config` openai* fields (env set → true/model/base). All 26 existing tests untouched.

```python
# --- one additive test in class TestConfigFields (after
#     test_configured_server_reports_model_and_base_url); existing tests untouched ---

    def test_config_reports_server_openai_env(self, client, monkeypatch):
        # FR9: /api/config reports the server's OPENAI_* env (presence only)
        # so the unified card's badge can show the pipeline half. Read-only
        # reporting — no resolver is involved.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-server")
        monkeypatch.setenv("OPENAI_MODEL", "server-model")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://server.test/v1")
        cfg = client.get("/api/config").json()
        assert cfg["openaiConfigured"] is True
        assert cfg["openaiModel"] == "server-model"
        assert cfg["openaiBaseUrl"] == "http://server.test/v1"
```
### tests/test_editor_frames.py — NEW

Mode selection (Gemini key → Files; absent + OpenAI → frames); EditPlan JSON on a compat stub; text-only filter repair; clean 4xx when no backend at all.

```python
"""The editor frames arm: the request-scoped OpenAI-compatible path behind
/api/edit and /api/effects/generate when no Gemini key resolves.

conftest.py pins BILLING_ENABLED=0, so the endpoint tests run self-host and
stop exactly at the gate (unknown job_id → the 404 just past it) — no
ffmpeg, no network, no job fixtures.
"""
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
import editor

OPENAI = {"X-OpenAI-Key": "sk-test",
          "X-OpenAI-Model": "qwen2.5vl",
          "X-OpenAI-Base-Url": "http://localhost:11434/v1"}


@pytest.fixture
def client():
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_slate(monkeypatch):
    for k in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
              "GEMINI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(k, raising=False)


class _FakeProvider:
    model_name = "qwen2.5vl"

    def __init__(self, response=None, error=None):
        self._response = response if response is not None else {}
        self._error = error
        self.calls = []

    def generate_content(self, prompt, schema=None, messages=None, **kw):
        self.calls.append({"prompt": prompt, "schema": schema,
                           "messages": messages})
        if self._error is not None:
            raise self._error
        return {"response": self._response, "cost_analysis": None}


def _stub_frames(monkeypatch, frames):
    import layout_picker
    monkeypatch.setattr(layout_picker, "sample_frames",
                        lambda path, n=None, width=None: frames)


def _stub_build(monkeypatch, filter_string, applied):
    monkeypatch.setattr(editor, "build_filter_string",
                        lambda edits, **kw: (filter_string, applied))


# --- the gate predicate ------------------------------------------------------

def test_configured_needs_a_key_or_a_real_base():
    assert editor.openai_configured("", "") is False
    assert editor.openai_configured("", "https://api.openai.com/v1") is False
    assert editor.openai_configured("sk-x", "https://api.openai.com/v1") is True
    assert editor.openai_configured("", "http://localhost:11434/v1") is True


def test_vision_arm_needs_a_configuration():
    out, err = editor.openai_vision_arm("", "m", "https://api.openai.com/v1")
    assert out is None and "no OpenAI-compatible endpoint" in err


def test_vision_arm_builds_and_probes_the_provider(monkeypatch):
    import ai_provider
    made, probes = {}, []
    prov = _FakeProvider()
    monkeypatch.setattr(ai_provider, "create_ai_provider",
                        lambda *a, **k: (made.update(args=a, kwargs=k) or prov))
    monkeypatch.setattr(ai_provider, "probe_vision_support",
                        lambda p: probes.append(p) or "vision_ok")

    out, err = editor.openai_vision_arm("sk-x", "qwen2.5vl", "http://l:1/v1")

    assert out is prov and err is None
    assert made["args"][0] == "openai" and made["args"][1] == "qwen2.5vl"
    assert made["kwargs"]["base_url"] == "http://l:1/v1"
    assert made["kwargs"]["api_key"] == "sk-x"
    assert probes == [prov]


def test_vision_arm_refuses_a_text_only_model(monkeypatch):
    import ai_provider
    monkeypatch.setattr(ai_provider, "create_ai_provider",
                        lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(ai_provider, "probe_vision_support", lambda p: "no_vision")

    out, err = editor.openai_vision_arm("sk-x", "m", "http://l:1/v1")
    assert out is None and "cannot see images" in err


def test_vision_arm_degrades_on_a_missing_sdk(monkeypatch):
    import ai_provider

    def no_sdk(*a, **k):
        raise ImportError("No module named 'openai'")

    monkeypatch.setattr(ai_provider, "create_ai_provider", no_sdk)
    out, err = editor.openai_vision_arm("sk-x", "m", "http://l:1/v1")
    assert out is None and "SDK unavailable" in err


def test_the_arm_never_touches_the_satellite_client(monkeypatch):
    # No-double-route canary for this reroute: the arm is pipeline-family
    # (ai_provider + the resolved OPENAI_* triple); a leak into the satellite
    # client trips loudly. Kept here so tests/test_no_double_route.py stays
    # byte-unmodified.
    import llm_client

    def boom(*a, **k):
        raise AssertionError("the editor frames arm must not call the satellite client")

    monkeypatch.setattr(llm_client, "chat", boom)
    monkeypatch.setattr(llm_client, "active_config", boom)
    _stub_frames(monkeypatch, [b"jpg"])
    _stub_build(monkeypatch, None, [])
    prov = _FakeProvider(response={"edits": []})
    assert editor.frames_edit_plan(prov, "x.mp4", 10.0) == {
        "filter_string": None, "edits": []}


# --- the frames arms ---------------------------------------------------------

def test_frames_edit_plan_builds_the_same_filter_shape(monkeypatch):
    _stub_frames(monkeypatch, [b"jpg"] * 12)
    edits = [{"type": "punch_in", "start": 0.0, "end": 1.0,
              "strength": 0.1, "reason": "hook"}]
    prov = _FakeProvider(response={"edits": edits})
    built = {}

    def fake_build(e, **kw):
        built.update(edits=e, kw=kw)
        return "zoompan=1", edits

    monkeypatch.setattr(editor, "build_filter_string", fake_build)

    data = editor.frames_edit_plan(prov, "x.mp4", 10.0, fps=30, width=1080,
                                   height=1920, transcript=[{"text": "hi"}],
                                   has_captions=True)

    call = prov.calls[0]
    assert call["schema"] is editor.EditPlan
    assert call["prompt"].startswith('\n        You are a viral short-form')
    assert "CRITICAL: This video already has burned-in captions" in call["prompt"]
    content = call["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": call["prompt"]}
    assert len(content) == 13                       # the prompt + 12 frames
    assert content[1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(b"jpg").decode("ascii"))
    assert built["edits"] == edits                  # the model's list, untouched
    assert built["kw"]["has_captions"] is True
    assert data == {"filter_string": "zoompan=1", "edits": edits}


def test_frames_edit_plan_uses_the_shared_prompt_builder(monkeypatch):
    # One prompt, two transports: the frames arm must go through the SAME
    # builder the Gemini arm now uses, not a private copy.
    _stub_frames(monkeypatch, [b"jpg"])
    _stub_build(monkeypatch, None, [])
    prov = _FakeProvider(response={"edits": []})
    transcript = [{"text": "hi", "start": 0.0}]
    editor.frames_edit_plan(prov, "x.mp4", 10.0, fps=30, width=1080,
                            height=1920, transcript=transcript,
                            has_captions=True)
    expected = editor._edit_plan_prompt(10.0, 1080, 1920, transcript, True)
    assert prov.calls[0]["prompt"] == expected


def test_frames_edit_plan_degrades_without_frames(monkeypatch):
    _stub_frames(monkeypatch, [])
    prov = _FakeProvider()
    assert editor.frames_edit_plan(prov, "x.mp4", 10.0) == {
        "filter_string": None, "edits": []}
    assert prov.calls == []                         # never asked the model


def test_frames_edit_plan_empty_plan_keeps_the_clip(monkeypatch):
    _stub_frames(monkeypatch, [b"jpg"])
    _stub_build(monkeypatch, None, [])
    prov = _FakeProvider(response={"edits": []})
    assert editor.frames_edit_plan(prov, "x.mp4", 10.0) == {
        "filter_string": None, "edits": []}


def test_frames_effects_config_returns_segments(monkeypatch):
    _stub_frames(monkeypatch, [b"jpg"] * 12)
    segment = {"startSec": 0, "endSec": 3.5, "zoom": 1.05, "zoomCenterX": 0.5,
               "zoomCenterY": 0.5, "brightness": 1.0, "contrast": 1.0,
               "saturate": 1.0}
    prov = _FakeProvider(response={"segments": [segment]})

    cfg = editor.frames_effects_config(prov, "x.mp4", 10.0, fps=30,
                                       width=1080, height=1920)

    assert prov.calls[0]["schema"] is editor.EffectsPlan
    assert "Remotion-based renderer" in prov.calls[0]["prompt"]
    assert cfg == {"segments": [segment]}
    assert len(prov.calls[0]["messages"][0]["content"]) == 13


def test_frames_effects_config_without_frames_is_none(monkeypatch):
    _stub_frames(monkeypatch, [])
    prov = _FakeProvider()
    assert editor.frames_effects_config(prov, "x.mp4", 10.0) is None
    assert prov.calls == []


# --- the text-only repair ----------------------------------------------------

def test_openai_repair_filter_round_trips():
    prov = _FakeProvider(response={"filter_string": "eq=brightness=1.1"})
    out = editor.openai_repair_filter(prov, "broken", "err", 1080, 1920)
    assert out == "eq=brightness=1.1"
    assert prov.calls[0]["schema"] is editor.FilterRepair
    assert "FFMPEG ERROR" in prov.calls[0]["prompt"]
    assert "s=1080x1920" in prov.calls[0]["prompt"]


def test_openai_repair_filter_returns_none_on_junk():
    prov = _FakeProvider(response={"filter_string": "   "})
    assert editor.openai_repair_filter(prov, "b", "e", 1080, 1920) is None


def test_openai_repair_filter_never_raises():
    prov = _FakeProvider(error=RuntimeError("endpoint down"))
    assert editor.openai_repair_filter(prov, "b", "e", 1080, 1920) is None


# --- VideoEditor construction and repair routing ------------------------------

def test_video_editor_without_a_key_routes_repair_to_the_openai_arm(monkeypatch):
    routed = {}
    monkeypatch.setattr(editor, "openai_repair_filter",
                        lambda p, fs, err, w, h: routed.update(prov=p) or "fixed")
    prov = _FakeProvider()
    ed = editor.VideoEditor(api_key=None, openai_provider=prov)
    assert ed.client is None
    assert ed._repair_filter("broken", "err", 1080, 1920) == "fixed"
    assert routed["prov"] is prov


def test_video_editor_with_a_key_repairs_over_gemini(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a Gemini-keyed editor must not repair over the OpenAI arm")

    monkeypatch.setattr(editor, "openai_repair_filter", boom)
    ed = editor.VideoEditor(api_key="k")
    assert ed.client is not None

    sent = {}

    class _FakeModels:
        def generate_content(self, **kw):
            sent.update(kw)
            return SimpleNamespace(text='{"filter_string": "eq=1"}')

    ed.client = SimpleNamespace(models=_FakeModels())
    assert ed._repair_filter("broken", "err", 1080, 1920) == "eq=1"
    assert "FFMPEG ERROR" in sent["contents"]


def test_video_editor_with_a_key_keeps_its_client():
    ed = editor.VideoEditor(api_key="k")
    assert ed.client is not None and ed.openai_provider is None


# --- the endpoint gates --------------------------------------------------------

class TestEditGate:
    def test_no_backend_at_all_is_a_clean_400(self, client):
        r = client.post("/api/edit", json={"job_id": "nope", "clip_index": 0})
        assert r.status_code == 400
        assert r.json()["detail"] == "Missing X-Gemini-Key header"

    def test_a_refused_frames_arm_is_a_clean_400(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "openai_vision_arm",
                            lambda *a: (None, "the selected model cannot see images"))
        r = client.post("/api/edit", json={"job_id": "nope", "clip_index": 0},
                        headers=OPENAI)
        assert r.status_code == 400
        assert "cannot see images" in r.json()["detail"]

    def test_the_frames_arm_passes_the_gate(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "openai_vision_arm",
                            lambda *a: (_FakeProvider(), None))
        r = client.post("/api/edit", json={"job_id": "nope", "clip_index": 0},
                        headers=OPENAI)
        assert r.status_code == 404                 # past the gate; unknown job

    def test_a_gemini_key_wins_over_the_openai_headers(self, client, monkeypatch):
        def boom(*a):
            raise AssertionError("with a Gemini key the frames arm must stay off")

        monkeypatch.setattr(app_module, "openai_vision_arm", boom)
        r = client.post("/api/edit", json={"job_id": "nope", "clip_index": 0},
                        headers={**OPENAI, "X-Gemini-Key": "k"})
        assert r.status_code == 404                 # Gemini arm, past the gate


class TestEffectsGate:
    def test_no_backend_at_all_is_a_clean_400(self, client):
        r = client.post("/api/effects/generate",
                        json={"job_id": "nope", "clip_index": 0})
        assert r.status_code == 400
        assert r.json()["detail"] == "Missing X-Gemini-Key header"

    def test_a_refused_frames_arm_is_a_clean_400(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "openai_vision_arm",
                            lambda *a: (None, "the selected model cannot see images"))
        r = client.post("/api/effects/generate",
                        json={"job_id": "nope", "clip_index": 0},
                        headers=OPENAI)
        assert r.status_code == 400
        assert "cannot see images" in r.json()["detail"]

    def test_the_frames_arm_passes_the_gate(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "openai_vision_arm",
                            lambda *a: (_FakeProvider(), None))
        r = client.post("/api/effects/generate",
                        json={"job_id": "nope", "clip_index": 0},
                        headers=OPENAI)
        assert r.status_code == 404                 # past the gate; unknown job

    def test_a_gemini_key_wins_over_the_openai_headers(self, client, monkeypatch):
        def boom(*a):
            raise AssertionError("with a Gemini key the frames arm must stay off")

        monkeypatch.setattr(app_module, "openai_vision_arm", boom)
        r = client.post("/api/effects/generate",
                        json={"job_id": "nope", "clip_index": 0},
                        headers={**OPENAI, "X-Gemini-Key": "k"})
        assert r.status_code == 404                 # Gemini arm, past the gate
```

### thumbnail.py — MODIFY

Image reroute: `/v1/images/generations` arm beside the `genai` call (text-prompt only, D14); Gemini fallback; clean-unavailable state when the endpoint lacks image support; reference images Gemini-arm only.

```python
# --- module top: insert right after IMAGE_MODEL (line 18) ---------------------

IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL") or "gemini-3.1-flash-image"
# The OpenAI-compatible images arm (D14). resolve_openai fills an unset model
# with the CHAT default "gpt-4o-mini" (app.py), which cannot draw; that value
# and an empty model both land on this default. An explicitly chosen model
# always wins. An env knob, like IMAGE_MODEL/TEXT_MODEL beside it, because
default image-model names differ per compatible server.
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-1"


# --- plan_thumbnail_concepts: replaces :437-444 (the if/else dispatch); the
#     `try:` at :436, :445 `concepts = _parse_json…`, and the except/return
#     :446-450 stay on disk ------------------------------------------------------

        if llm_config is not None:
            import llm_client
            text, _cost = llm_client.chat(prompt, config=llm_config, json_mode=True)
        elif client is None:
            # Keyless unified endpoint (no Gemini key, no complete satellite
            # triple): the generic fallback concept IS the design — one clean
            # line, not a swallowed AttributeError on client.models.
            print("ℹ️ [Thumbnail] No text backend for concepts — using the generic scene")
            return normalise_concepts([], count, title, thumbnail_text_hint)
        else:
            response = client.models.generate_content(
                model=TEXT_MODEL, contents=[prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json"))
            text = response.text


# --- NEW _image_prompt, inserted after _image_part (after :575, before the
#     def _generate_one at :577) ------------------------------------------------

def _image_prompt(concept, burn_text):
    """The image prompt — ONE prompt, two transports: the Gemini call
    (_generate_one) and the OpenAI-compatible images arm
    (_generate_one_openai). Extracted verbatim from _generate_one. The
    IDENTITY clause is deliberately NOT part of it: reference images are
    Gemini-arm only (D14)."""
    if burn_text:
        pos = concept['text_position']
        share = "45% of the width" if pos in ("left", "right") else "40% of the height"
        text_rule = ("Do NOT render any text, letters, numbers, captions, logos or watermarks anywhere "
                     f"in the image. The {pos} {share} of the frame must be clean, simple negative space "
                     "(plain, dark or softly blurred, no objects, no detail): a headline will be placed "
                     "there afterwards. Put the subject and every object in the remaining part.")
    else:
        text_rule = (f'Render the text "{concept["text"]}" in huge bold condensed sans-serif capitals, '
                     f'{concept["text_color"]} with a thick black outline, on the {concept["text_position"]} '
                     "side, spelled EXACTLY as given. No other text.")
    return f"""Generate a professional YouTube thumbnail, 16:9.

{concept['scene']}

{text_rule}

Style: high contrast, saturated colours, crisp subject separation, cinematic lighting, sharp focus on the subject, readable at 168x94 pixels. No clutter, no small details, no borders, no watermark."""


# --- NEW _generate_one_openai, inserted after _generate_one (after :625,
#     before the def generate_thumbnail at :628) --------------------------------

def _generate_one_openai(openai_image_config, concept, out_path, burn_text):
    """One /v1/images/generations call for one concept; returns the saved
    path or raises. Text-prompt only (D14): this endpoint cannot carry
    reference images, and /v1/images/edits stays unprobed — an endpoint
    without image support surfaces through the caller as the
    clean-unavailable state, which replaces the vision probe the chat arms
    use. No size and no response_format: both are model-specific (gpt-image-1
    rejects response_format; dall-e-3 rejects 1536x1024) and
    finalize_thumbnail cover-crops whatever aspect arrives — one code path
    for every OpenAI-compatible server."""
    import base64
    import httpx

    base = (openai_image_config.get("base_url") or "").rstrip("/")
    headers = {}
    key = (openai_image_config.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    model = (openai_image_config.get("model") or "").strip()
    if model in ("", "gpt-4o-mini"):
        model = OPENAI_IMAGE_MODEL  # resolve_openai's chat default cannot draw
    prompt = _image_prompt(concept, burn_text)
    response = httpx.post(f"{base}/images/generations",
                          json={"model": model, "prompt": prompt, "n": 1},
                          headers=headers, timeout=180)
    response.raise_for_status()
    items = (response.json() or {}).get("data") or []
    item = next((d for d in items
                 if isinstance(d, dict) and (d.get("b64_json") or d.get("url"))), None)
    if item is None:
        raise RuntimeError("the endpoint's reply contained no image data")
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
    else:
        # dall-e style URL reply — fetch the bytes from the provider.
        raw = httpx.get(item["url"], timeout=60, follow_redirects=True).content
    pil = Image.open(io.BytesIO(raw))
    if burn_text:
        pil = burn_thumbnail_text(pil, concept["text"],
                                  concept["text_position"], concept["text_color"])
    return finalize_thumbnail(pil, out_path)


# --- _generate_one: replaces :577-601 (def through the IDENTITY block); the
#     blank :602 and `response = client.models.generate_content(` :603-625
#     stay on disk unchanged ----------------------------------------------------

def _generate_one(client, concept, reference_images, out_path, burn_text):
    """One image call for one concept; returns the saved path or raises."""
    # One prompt, two transports: shared verbatim with the images arm
    # (_generate_one_openai). The IDENTITY clause rides only here —
    # reference images are Gemini-arm only (D14).
    prompt = _image_prompt(concept, burn_text)
    if reference_images:
        prompt += ("\nIDENTITY: the person in the provided photo must appear as EXACTLY the same real person: "
                   "identical face shape, skin, eyes, glasses, facial hair, hairstyle and hair length, age and "
                   "body type. Photorealistic, like a photo of them; do not idealize, slim, rejuvenate or "
                   "stylize them. Expression may change slightly but must stay natural and true to their face.")


# --- generate_thumbnail signature + docstring: replaces :628-637 ---------------

def generate_thumbnail(api_key, title, session_id, face_image_path=None, bg_image_path=None,
                       extra_prompt="", count=3, video_context="", burn_text=True,
                       thumbnail_text_hint="", language="en", frame_reference=None,
                       llm_config=None, openai_image_config=None):
    """
    Generates `count` thumbnails, each from its own concept, in parallel.
    frame_reference: {"path", "face"} from extract_face_frames, used as the
    person reference when the user picked a frame instead of uploading a photo.
    openai_image_config: {"base_url", "api_key", "model"} from resolve_openai
    (app.py) — the /v1/images/generations arm when no Gemini key resolved.
    D14: that arm is text-prompt only; reference images are Gemini-arm only.
    Returns [{"url", "text", "why"}] (only the ones that rendered).
    """


# --- the client + the D14 references gate: :638 replaced; :639-640 stay on
#     disk; :641-644 (blank, the two comment lines, `reference_images = []`)
#     replaced; the three reference-image branches :645-652 stay on disk -------

    client = genai.Client(api_key=api_key) if api_key else None
    if client is None and not openai_image_config:
        raise RuntimeError(
            "No image backend: a Gemini key or an OpenAI-compatible endpoint is required")
    output_dir = os.path.join("output", "thumbnails", session_id)
    os.makedirs(output_dir, exist_ok=True)

    # D14: /v1/images/generations cannot carry reference images — refs are the
    # Gemini arm's privilege, and with no Gemini key they are dropped (logged).
    # Dropped BEFORE the concepts call so has_person stays honest: the concept
    # prompt must not promise a person photo this arm cannot send.
    if client is None and (face_image_path or frame_reference or bg_image_path):
        print("⚠️ [Thumbnail] Reference images are honored by the Gemini arm only — "
              "generating without them on the OpenAI-compatible endpoint")
        face_image_path = None
        frame_reference = None
        bg_image_path = None

    # References travel as immutable byte parts: one PIL Image shared by the
    # worker threads below raced inside the SDK's encoder and every call died.
    reference_images = []


# --- run(): replaces :668-673 (the inner try through the bare `raise`);
#     the blocked-retry block :674-683 and everything after stay on disk -------

            try:
                if client is not None:
                    _generate_one(client, concept, reference_images, out_path, burn_text)
                else:
                    _generate_one_openai(openai_image_config, concept, out_path, burn_text)
                fallback = False
            except RuntimeError as e:
                # The blocked-retry below is the Gemini refusal shape; the
                # images arm has no references to drop, so its failures go
                # straight to the per-concept error return.
                if client is None or "no image" not in str(e):
                    raise
```

### tests/test_thumbnail_studio.py — MODIFY

`/v1/images/generations` success; refusal/absence falls back to Gemini or yields the clean-unavailable state; gate accepts unified config with no Gemini key (billing branch untouched).

```python
# --- imports: extend the header (after `import thumbnail  # noqa: E402`) -------

import base64

import httpx
from fastapi.testclient import TestClient

import app as app_module  # noqa: E402


# --- new section at end of file -------------------------------------------------
# --- the OpenAI-compatible images arm (D14; no Gemini key required) -------------

IMG_CONFIG = {"api_key": "sk-x", "model": "gpt-image-1",
              "base_url": "http://localhost:4891/v1"}

OPENAI_HEADERS = {"X-OpenAI-Key": "sk-test",
                  "X-OpenAI-Model": "qwen2.5vl",
                  "X-OpenAI-Base-Url": "http://localhost:11434/v1"}


def _png_bytes(size=(64, 48)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _b64_png():
    return base64.b64encode(_png_bytes()).decode("ascii")


def _concept():
    return thumbnail.normalise_concepts(
        [{"text": "0 to 10k", "text_position": "right", "text_color": "yellow",
          "scene": "a counter climbing over a desk", "why": "the number is the hook"}],
        1, "How I got 10k subs")[0]


def _stub_images_post(monkeypatch, item):
    calls = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [item]}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers or {}})
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def _boom(msg):
    def _raise(*a, **k):
        raise AssertionError(msg)
    return _raise


def test_generate_one_openai_posts_prompt_and_saves(tmp_path, monkeypatch):
    calls = _stub_images_post(monkeypatch, {"b64_json": _b64_png()})
    out = tmp_path / "t.jpg"
    thumbnail._generate_one_openai(IMG_CONFIG, _concept(), str(out), burn_text=True)

    assert calls[0]["url"] == "http://localhost:4891/v1/images/generations"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-x"
    body = calls[0]["json"]
    assert body["model"] == "gpt-image-1" and body["n"] == 1
    assert "a counter climbing over a desk" in body["prompt"]
    assert "negative space" in body["prompt"]          # the burn_text rule rides along
    with Image.open(out) as saved:
        assert saved.size == (1280, 720)               # finalize cover-cropped the 64x48 reply


def test_generate_one_openai_handles_url_replies(tmp_path, monkeypatch):
    _stub_images_post(monkeypatch, {"url": "http://img.test/x.png"})
    monkeypatch.setattr(httpx, "get",
                        lambda url, timeout=None, follow_redirects=False:
                        type("R", (), {"content": _png_bytes()})())
    out = tmp_path / "t.jpg"
    thumbnail._generate_one_openai(IMG_CONFIG, _concept(), str(out), burn_text=False)
    assert out.stat().st_size > 0


def test_generate_one_openai_raises_without_image_data(monkeypatch):
    _stub_images_post(monkeypatch, {"revised_prompt": "..."})   # no b64_json, no url
    with pytest.raises(RuntimeError, match="no image data"):
        thumbnail._generate_one_openai(IMG_CONFIG, _concept(), "unused.jpg", False)


def test_generate_one_openai_http_errors_propagate(monkeypatch):
    # The per-concept catch in generate_thumbnail turns this into the
    # endpoint's clean-unavailable 400 (app.py) — never a hang, never a 500.
    req = httpx.Request("POST", "http://localhost:4891/v1/images/generations")
    resp = httpx.Response(404, request=req)

    def refused(url, json=None, headers=None, timeout=None):
        resp.raise_for_status()

    monkeypatch.setattr(httpx, "post", refused)
    with pytest.raises(httpx.HTTPStatusError):
        thumbnail._generate_one_openai(IMG_CONFIG, _concept(), "unused.jpg", False)


def test_generate_one_openai_swaps_the_chat_model_default(tmp_path, monkeypatch):
    calls = _stub_images_post(monkeypatch, {"b64_json": _b64_png()})
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)

    def run_with(model, key, name):
        cfg = dict(IMG_CONFIG, model=model, api_key=key)
        thumbnail._generate_one_openai(cfg, _concept(), str(tmp_path / name), False)

    run_with("gpt-4o-mini", "sk-x", "a.jpg")
    assert calls[-1]["json"]["model"] == thumbnail.OPENAI_IMAGE_MODEL
    run_with("", "", "b.jpg")
    assert calls[-1]["json"]["model"] == thumbnail.OPENAI_IMAGE_MODEL
    assert "Authorization" not in calls[-1]["headers"]     # keyless local endpoint
    run_with("dall-e-3", "sk-x", "c.jpg")
    assert calls[-1]["json"]["model"] == "dall-e-3"        # an explicit model wins


def test_generate_thumbnail_runs_the_images_arm_without_a_gemini_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)                        # output/ lands in tmp
    monkeypatch.setattr(thumbnail.genai, "Client", _boom(
        "no Gemini key — the client must not be built"))
    monkeypatch.setattr(thumbnail, "_generate_one", _boom(
        "the Gemini image arm must stay off"))
    monkeypatch.setattr(thumbnail, "plan_thumbnail_concepts",
                        lambda client, title, count, **k:
                        [_concept() for _ in range(count)])
    seen = {}

    def fake_arm(config, concept, out_path, burn_text):
        seen.update(config=config, burn_text=burn_text)
        Image.new("RGB", (64, 48)).save(out_path, "JPEG")
        return out_path

    monkeypatch.setattr(thumbnail, "_generate_one_openai", fake_arm)

    out = thumbnail.generate_thumbnail(None, "T", "sess_img", count=1,
                                       openai_image_config=IMG_CONFIG)

    assert out[0]["url"].startswith("/thumbnails/sess_img/")
    assert seen["config"] is IMG_CONFIG and seen["burn_text"] is True


def test_generate_thumbnail_gemini_key_wins_over_the_images_arm(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class _FakeClient:
        pass

    monkeypatch.setattr(thumbnail.genai, "Client", lambda **k: _FakeClient())
    monkeypatch.setattr(thumbnail, "_generate_one_openai", _boom(
        "with a Gemini key the images arm must stay off"))
    seen = {}

    def fake_gemini(client, concept, refs, out_path, burn_text):
        seen.update(client=client)
        Image.new("RGB", (64, 48)).save(out_path, "JPEG")
        return out_path

    monkeypatch.setattr(thumbnail, "_generate_one", fake_gemini)
    monkeypatch.setattr(thumbnail, "plan_thumbnail_concepts",
                        lambda client, title, count, **k:
                        [_concept() for _ in range(count)])

    out = thumbnail.generate_thumbnail("k", "T", "sess_g", count=1,
                                       openai_image_config=IMG_CONFIG)
    assert out[0]["url"].startswith("/thumbnails/sess_g/")
    assert isinstance(seen["client"], _FakeClient)     # the Gemini arm ran, refs or not


def test_generate_thumbnail_drops_references_on_the_images_arm(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    face = tmp_path / "face.jpg"
    Image.new("RGB", (40, 40)).save(face, "JPEG")
    monkeypatch.setattr(thumbnail.genai, "Client", _boom(
        "no Gemini key — the client must not be built"))
    planned = {}

    def fake_plan(client, title, count, **k):
        planned["has_person"] = k.get("has_person")
        return [_concept() for _ in range(count)]

    monkeypatch.setattr(thumbnail, "plan_thumbnail_concepts", fake_plan)
    monkeypatch.setattr(thumbnail, "_generate_one_openai",
                        lambda config, concept, out_path, burn_text:
                        Image.new("RGB", (64, 48)).save(out_path, "JPEG") or out_path)

    thumbnail.generate_thumbnail(None, "T", "sess_d", face_image_path=str(face),
                                 count=1, openai_image_config=IMG_CONFIG)

    assert planned["has_person"] is False              # the concept prompt stays honest
    assert "honored by the Gemini arm only" in capsys.readouterr().out


def test_generate_thumbnail_without_any_backend_raises(monkeypatch):
    monkeypatch.setattr(thumbnail.genai, "Client", _boom(
        "no backend — the client must not be built"))
    with pytest.raises(RuntimeError, match="No image backend"):
        thumbnail.generate_thumbnail(None, "T", "sess_x", count=1)


def test_the_images_arm_never_touches_the_satellite_client(monkeypatch, tmp_path):
    # No-double-route canary for this reroute (design ordering constraint):
    # the images arm routes the resolved OPENAI_* triple through
    # /v1/images/generations; a leak into the satellite client trips loudly.
    # Kept here so tests/test_no_double_route.py stays byte-unmodified.
    import llm_client

    monkeypatch.setattr(llm_client, "chat", _boom("must not call the satellite client"))
    monkeypatch.setattr(llm_client, "active_config",
                        _boom("must not read the satellite config"))
    monkeypatch.setattr(thumbnail.genai, "Client", _boom(
        "no Gemini key — the client must not be built"))
    monkeypatch.setattr(thumbnail, "_generate_one_openai",
                        lambda config, concept, out_path, burn_text:
                        Image.new("RGB", (64, 48)).save(out_path, "JPEG") or out_path)

    # llm_config=None + no Gemini key: the generic fallback concepts, no
    # llm_client call anywhere on the path.
    out = thumbnail.generate_thumbnail(None, "T", "sess_c", count=1,
                                       openai_image_config=IMG_CONFIG)
    assert out[0]["url"].startswith("/thumbnails/sess_c/")


# --- the /api/thumbnail/generate gate --------------------------------------------

@pytest.fixture
def api_client():
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
              "GEMINI_API_KEY", "GEMINI_MODEL",
              "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_MODEL_THUMBNAIL"):
        monkeypatch.delenv(k, raising=False)


class TestGenerateGate:
    def test_no_backend_at_all_is_a_clean_400(self, api_client, clean_env):
        r = api_client.post("/api/thumbnail/generate",
                            data={"session_id": "nope", "title": "T"})
        assert r.status_code == 400
        assert r.json()["detail"] == "Missing X-Gemini-Key header"

    def test_the_images_endpoint_passes_the_gate(self, api_client, clean_env, monkeypatch):
        captured = {}

        def fake_generate(api_key, title, session_id, face, bg, extra, count,
                          video_context, **kw):
            captured.update(api_key=api_key, count=count, kwargs=kw)
            return [{"url": "/t/1.jpg", "text": "X", "why": ""}]

        monkeypatch.setattr(app_module, "generate_thumbnail", fake_generate)
        r = api_client.post("/api/thumbnail/generate",
                            data={"session_id": "nope", "title": "T", "count": 2},
                            headers=OPENAI_HEADERS)
        assert r.status_code == 200
        assert r.json() == {"thumbnails": [{"url": "/t/1.jpg", "text": "X", "why": ""}]}
        assert captured["api_key"] is None             # no Gemini key anywhere
        assert captured["count"] == 2
        assert captured["kwargs"]["openai_image_config"] == {
            "api_key": "sk-test", "model": "qwen2.5vl",
            "base_url": "http://localhost:11434/v1"}
        assert captured["kwargs"]["llm_config"] is None   # cleaned env → no satellite leg

    def test_a_gemini_key_wins_over_the_openai_headers(self, api_client, clean_env, monkeypatch):
        captured = {}

        def fake_generate(api_key, title, session_id, face, bg, extra, count,
                          video_context, **kw):
            captured.update(api_key=api_key, openai=kw.get("openai_image_config"))
            return [{"url": "/t/1.jpg", "text": "X", "why": ""}]

        monkeypatch.setattr(app_module, "generate_thumbnail", fake_generate)
        r = api_client.post("/api/thumbnail/generate",
                            data={"session_id": "nope", "title": "T"},
                            headers={**OPENAI_HEADERS, "X-Gemini-Key": "k"})
        assert r.status_code == 200
        assert captured["api_key"] == "k"
        assert captured["openai"] is None              # the images arm stayed off

    def test_a_refused_images_endpoint_is_a_clean_400(self, api_client, clean_env, monkeypatch):
        def refused(*a, **kw):
            raise RuntimeError("All thumbnail generations failed. "
                               "Last error: 404 Client Error 'Not Found'")

        monkeypatch.setattr(app_module, "generate_thumbnail", refused)
        r = api_client.post("/api/thumbnail/generate",
                            data={"session_id": "nope", "title": "T"},
                            headers=OPENAI_HEADERS)
        assert r.status_code == 400
        assert "Image generation is not available" in r.json()["detail"]
```

## Slices

### Slice 1: Unified card + encrypted store + migration (FE core)

**Files**: `dashboard/src/lib/llm.js`, `dashboard/src/components/AiProviderCard.jsx`, `dashboard/src/App.jsx`, `dashboard/src/components/LlmProviderCard.jsx` (DELETE), `dashboard/src/contexts/AuthContext.jsx`, `app.py` (/api/config only), `tests/test_llm_endpoints.py` (one additive test)

#### Automated Verification:
- [ ] `cd dashboard && npm run lint` passes (strict, --max-warnings 0)
- [ ] `cd dashboard && npm run build` passes
- [ ] `grep -r "LlmProviderCard" dashboard/src` returns no matches
- [ ] `grep -n "setItem('openai_" dashboard/src/App.jsx` returns no matches (plaintext writers gone; the only remaining openai_* literals are the migration reads inside the store initializer)
- [ ] `grep -nE "headers\['X-OpenAI-" dashboard/src/App.jsx` returns no matches (pipeline emission only via lib/llm.js openaiHeaders)
- [ ] `pytest tests/test_llm_endpoints.py -q` passes, including the additive `test_config_reports_server_openai_env`; all 26 existing tests unmodified and green
- [ ] `pytest tests/test_no_double_route.py -q` passes (env contract untouched)

#### Manual Verification:
- [ ] Fresh browser profile seeded with `llmConfig_v1` + `openai_key`/`openai_model`/`openai_base_url` + `geminiKey_v1`: on first load the card shows the imported values; localStorage no longer contains the legacy keys; a reload does not resurrect them
- [ ] Preset click → Save is enabled with no key and no model; after save + reload the values persist (`aiProviderConfig_v1`); Reset clears across reloads
- [ ] Test connection: keyed config shows latency + model; keyless config lists a model count; an upstream 400 (wrong endpoint config) surfaces as a config-class error; an unreachable URL surfaces as provider-class (502)
- [ ] Model dropdown Refresh lists models from a local endpoint
- [ ] Server badge: with server `OPENAI_*` or `LLM_*` env and no saved card the badge shows; "override with my own endpoint" reveals the form
- [ ] DevTools on POST `/api/process` with only the unified card set (no Gemini key): request carries `X-OpenAI-Base-Url` (+ Key/Model when set), derived `X-LLM-*` when a key is set, and `X-AI-Provider: openai`; the job starts
- [ ] Cloud mode (billingEnabled): the card is absent; `providerCfg` stays the empty triple so migrated values never reach cloud requests or gates; `/api/config` openai* fields read false/null

### Slice 2: FE emission convergence

**Files**: `dashboard/src/App.jsx`, `dashboard/src/components/ResultCard.jsx`, `dashboard/src/components/VoiceOverPage.jsx`, `dashboard/src/components/CreateEditProfileModal.jsx`

#### Automated Verification:
- [ ] `cd dashboard && npm run lint` passes (strict, `--max-warnings 0`)
- [ ] `cd dashboard && npm run build` passes
- [ ] `grep -rn "openaiApiKey" dashboard/src` returns no matches (the dead prop name is fully gone)
- [ ] `grep -rn "openai_key" dashboard/src/components/CreateEditProfileModal.jsx` returns no matches (D11 — the key never rides the body)
- [ ] `grep -rn "headers\['X-OpenAI-\|headers\['X-LLM-" dashboard/src | grep -v "lib/llm.js"` returns no matches (emission-form literals only in the single emitter)
- [ ] `grep -c "openaiHeaders" dashboard/src/components/ResultCard.jsx dashboard/src/components/VoiceOverPage.jsx dashboard/src/components/CreateEditProfileModal.jsx` returns >= 1 for each file
- [ ] `pytest tests/test_no_double_route.py -q` passes (FE-only slice; dispatch contract untouched)

#### Manual Verification:
- [ ] Clip card "auto edit" with only the unified card set (no Gemini key, self-host): DevTools shows `/api/effects/generate` carrying `X-OpenAI-Base-Url` (+ Key/Model when set) and `X-LLM-*` when a key is set; the server still answers the Gemini-missing error until Slice 5 — the header shape is what is verified
- [ ] With no Gemini key, no card, self-host: "auto edit" pre-fires the no-AI-backend error and sends no request
- [ ] VoiceOver, caption provider OpenAI, keyless local card (baseUrl only): caption generation starts (the old key-only gate no longer blocks); with nothing configured, "Set your AI provider in Settings first." shows
- [ ] Game profile "Analyze with AI" from the Clip Generator, with a Gemini key AND the card both set: request carries `X-OpenAI-*` headers, no `openai_key` in the body, `openai_model`/`openai_base_url` still present; analysis succeeds on BOTH provider toggle positions (the modal's own toggle seeds from the app toggle — legacy behavior, unchanged)
- [ ] Game Profiles page's own Create/Edit modal still opens and saves (its Analyze runs on server env as before — the pre-existing no-props mount)
- [ ] Cloud mode: `providerCfg` stays the empty triple, every spread is inert, auto edit behaves exactly as before (managed Gemini)

### Slice 3: hook_grounding.py reroute

**Files**: `hook_grounding.py`, `tests/test_hook_grounding.py`, `CLAUDE.md` (one paragraph)

#### Automated Verification:
- [ ] `pytest tests/test_hook_grounding.py -q` passes — all 12 existing tests (one hardened with OPENAI_* delenv) plus the 8 new OpenAI-arm tests
- [ ] `pytest tests/test_no_double_route.py -q` passes, the file byte-unmodified (the reroute canary lives in `tests/test_hook_grounding.py::test_openai_arm_never_touches_the_satellite_client`)
- [ ] `pytest tests/test_ai_provider.py -q` passes (ai_provider.py untouched)
- [ ] `grep -n "llm_client\|LLM_BASE_URL\|LLM_API_KEY\|LLM_MODEL" hook_grounding.py` returns no matches — env namespace law: the arm reads job `OPENAI_*` only

#### Manual Verification:
- [ ] Self-host job, no GEMINI key, unified card → vision-capable local endpoint (e.g. Ollama `qwen2.5vl`), screencast-heavy source: log shows `Hook regrounded on screen (…)`, clip metadata carries `hook_grounding.before` with the original hook
- [ ] Same job with a text-only model: one `cannot see images` skip line per clip, transcript hook stands, job completes (probe fires once per provider identity — `_VISION_PROBE_CACHE`)
- [ ] Job gated only by `LLM_*` (no Gemini key, no OpenAI triple; the job env carries only app.py's injected `OPENAI_*` defaults): one `needs a Gemini key or an OpenAI-compatible endpoint` skip line per clip, no provider built, no probe, no exception
- [ ] Gemini key AND OPENAI_* both present: grounding runs through Gemini with no probe call (precedence unchanged from HEAD)
- [ ] CLAUDE.md "Hook grounding for on-screen clips" paragraph no longer claims Gemini-only

### Slice 4: screencast_layout.py reroute

**Files**: `screencast_layout.py`, `tests/test_screencast_layout.py`, `CLAUDE.md` (one paragraph — Files addition ratified at the checkpoint, Slice 3 precedent)

#### Automated Verification:
- [ ] `pytest tests/test_screencast_layout.py -q` passes — all 19 existing tests unmodified plus the 10 new OpenAI-arm tests
- [ ] `pytest tests/test_no_double_route.py -q` passes, the file byte-unmodified (the reroute canary lives in `tests/test_screencast_layout.py::test_openai_arm_never_touches_the_satellite_client`)
- [ ] `pytest tests/test_ai_provider.py -q` passes (ai_provider.py untouched)
- [ ] `pytest tests/test_hook_grounding.py -q` passes (sibling in-job arm untouched by this slice)
- [ ] `grep -n "llm_client\|LLM_BASE_URL\|LLM_API_KEY\|LLM_MODEL" screencast_layout.py` returns no matches — env namespace law: the arm reads job `OPENAI_*` only

#### Manual Verification:
- [ ] Module-level run, no GEMINI key, vision-capable local endpoint (`OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_MODEL=qwen2.5vl SCREENCAST_LAYOUT=1 python -c "import screencast_layout; print(screencast_layout.detect_content_ranges('screencast.mp4', 120))"` on a real screencast source): returns gated ranges, log shows the 🔎 line then the 📊 summary
- [ ] Same with a text-only model: one `cannot see images` skip line, `[]`, no frame call (probe fires once per provider identity)
- [ ] Same with only app.py's injected defaults (base `https://api.openai.com/v1`, no key): silent `[]`, no provider built, no probe
- [ ] `GEMINI_API_KEY` set AND `OPENAI_*` set: the Files upload path runs, no probe line in the log (precedence unchanged from HEAD)
- [ ] CLAUDE.md "SCREENCAST / WIDE Modes" bullet no longer credits only Gemini

### Slice 5: editor.py frames path + endpoint resolution

**Files**: `editor.py`, `app.py`, `tests/test_editor_frames.py`

#### Automated Verification:
- [ ] `pytest tests/test_editor_frames.py -q` passes — all new tests green (gate predicate, vision arm, both frames arms, repair routing, the no-double-route canary, both endpoint gates)
- [ ] `pytest tests/test_no_double_route.py -q` passes, the file byte-unmodified (the canary lives in `tests/test_editor_frames.py::test_the_arm_never_touches_the_satellite_client`)
- [ ] `pytest tests/test_ai_provider.py -q` passes (ai_provider.py untouched)
- [ ] `pytest tests/test_edit_builder.py -q` passes (the build_filter_string call contract is unchanged)
- [ ] `pytest tests/test_llm_endpoints.py -q` passes (the existing endpoint surface is untouched)
- [ ] `grep -n "llm_client\|LLM_BASE_URL\|LLM_API_KEY\|LLM_MODEL" editor.py` returns no matches — env namespace law: the arm reads the resolved OPENAI_* triple only
- [ ] `grep -c "openai_provider=openai_prov" app.py` returns 2 (both editor constructions carry the arm)

#### Manual Verification:
- [ ] Self-host, no GEMINI key, unified card → vision-capable local endpoint (e.g. Ollama `qwen2.5vl`): clip card "auto edit" returns an edited clip; log shows `Asking the OpenAI-compatible endpoint for an edit decision list` then `🎯 Applying N edits` (or the no-edits line); DevTools shows `X-OpenAI-*` on `/api/edit`
- [ ] Same with a text-only model: one clean 400 `cannot see images` response, no 500, no edit attempted
- [ ] Nothing configured at all: `/api/edit` answers 400 `Missing X-Gemini-Key header` (unchanged from HEAD)
- [ ] Gemini key AND the card both set: the Files upload path runs (`📤 Uploading … to Gemini` in the log), the frames arm stays off — precedence unchanged from HEAD
- [ ] `/api/effects/generate` with only the card: effects JSON generated (`{"segments": [...]}`), Remotion preview unchanged downstream
- [ ] A model-suggested filter that fails the dry-run gets one text-only repair attempt over the OpenAI arm (`🔧 Self-repaired AI filter passed dry-run.` when it works)
- [ ] Cloud mode: entitlement and metering behavior unchanged (unentitled → 402 at the same place; entitled → managed Gemini)

### Slice 6: thumbnail image reroute + gate relax

**Files**: `thumbnail.py`, `app.py`, `tests/test_thumbnail_studio.py`

#### Automated Verification:
- [ ] `pytest tests/test_thumbnail_studio.py -q` passes — all 8 existing tests byte-unmodified plus the 14 new image-arm tests (10 module + 4 gate)
- [ ] `pytest tests/test_no_double_route.py -q` passes, the file byte-unmodified (the canary lives in `tests/test_thumbnail_studio.py::test_the_images_arm_never_touches_the_satellite_client`)
- [ ] `pytest tests/test_editor_frames.py -q` passes (the shared `openai_configured` predicate is reused, not redefined — `grep -n "def openai_configured" *.py` still returns only `editor.py`)
- [ ] `pytest tests/test_llm_endpoints.py -q` passes (the existing endpoint surface is untouched)
- [ ] `grep -n "LLM_BASE_URL\|LLM_API_KEY\|LLM_MODEL" thumbnail.py` returns no matches — env namespace law: the images arm's config arrives as the resolved `OPENAI_*` triple from `resolve_openai`, never `LLM_*` env (the concept-text `llm_client` delegation is unchanged and does its own env reads)
- [ ] `grep -c "openai_configured" app.py` returns 4 — one import (Slice 5) + the three endpoint gates (`/api/edit`, `/api/effects/generate`, `/api/thumbnail/generate`); no second predicate definition

#### Manual Verification:
- [ ] Self-host, no GEMINI key, unified card → image-capable endpoint (e.g. OpenAI `gpt-image-1`): generate returns thumbnails saved under `output/thumbnails/<session>/`, log shows the `✅ [Thumbnail] Saved:` lines
- [ ] Same with a face/frame reference selected: one `⚠️ [Thumbnail] Reference images are honored by the Gemini arm only` line, thumbnails still generate (no refs), the concept prompt carries no person-photo promise
- [ ] Same with a chat-only endpoint (no `/v1/images/generations`, e.g. bare Ollama): one clean 400 `Image generation is not available on the configured endpoint`, no 500
- [ ] Card model empty and `OPENAI_MODEL=gpt-4o-mini` in env: the images request body carries `gpt-image-1` (or `OPENAI_IMAGE_MODEL`), never the chat default
- [ ] Gemini key AND unified card both set: the Gemini arm runs with refs (IDENTITY clause present, no ⚠️ line) — precedence unchanged from HEAD
- [ ] Nothing configured: 400 `Missing X-Gemini-Key header` — unchanged from HEAD
- [ ] Cloud mode: entitled → managed Gemini arm + reservation commits; free → 403 `plan_required`; anonymous caller with `X-OpenAI-*` headers → 400 at the gate (the `not BILLING_ENABLED` guard) — cloud behavior byte-identical to HEAD
- [ ] Keyless local card (baseUrl only, no key): concepts fall back to the generic scene (one `ℹ️ [Thumbnail] No text backend for concepts` line) — reachable only when the endpoint actually serves images

## Desired End State

A self-host user opens Settings and sees ONE provider card. They click the "local ollama" preset, leave the key empty, pick a model from the dropdown, and save. Every AI feature now runs from that single configuration:

```jsx
// App.jsx — one store, both families, one emission site
const headers = {
  ...llmHeaders(providerCfg),      // X-LLM-Base-Url/Key/Model (model omitted when empty)
  ...openaiHeaders(providerCfg),   // X-OpenAI-Key/Model/Base-Url (key omitted when keyless)
  'X-AI-Provider': aiProvider,     // the job toggle, unchanged
};
```

```python
# hook_grounding.py — inside a job with no GEMINI_API_KEY
provider = _openai_provider()               # from job OPENAI_* env, or None
if provider is None: skip (one log line)
if probe_vision_support(provider) == "no_vision": skip (one log line)
answer = _ask_openai(frames, prompt)        # GroundedHook schema, same shape as _ask_gemini
```

```python
# /api/thumbnail/generate with only the unified endpoint set
key, _ = await resolve_gemini(request)
openai_key, openai_model, openai_base = resolve_openai(request)
if not key and not (openai_key or openai_base):  # gate accepts either
    raise gemini_missing_error()
# concept text via llm_client (existing), image via /v1/images/generations,
# falling back to Gemini image when a key exists
```

## File Map

```text
dashboard/src/lib/llm.js                       # MODIFY — aiProviderSet + openaiHeaders (dual-family single emitter)
dashboard/src/components/AiProviderCard.jsx    # NEW — the one card
dashboard/src/App.jsx                          # MODIFY — store, migration, mount, gates, emission, shims
dashboard/src/components/LlmProviderCard.jsx   # DELETE — D7
dashboard/src/components/ResultCard.jsx        # MODIFY — dual families on /api/edit
dashboard/src/components/VoiceOverPage.jsx     # MODIFY — fold byokHeaders
dashboard/src/components/CreateEditProfileModal.jsx  # MODIFY — fold builder, drop profile key (D11)
hook_grounding.py                              # MODIFY — OpenAI arm (in-job env)
tests/test_hook_grounding.py                   # MODIFY — OpenAI-arm tests
CLAUDE.md                                      # MODIFY — hook-grounding paragraph de-Gemini-only'd (Slice 3); SCREENCAST/WIDE paragraph de-Gemini-only'd (Slice 4)
screencast_layout.py                           # MODIFY — frames arm (in-job env)
tests/test_screencast_layout.py                # MODIFY — frames-arm tests
editor.py                                      # MODIFY — frames analysis path
app.py                                         # MODIFY — /api/config openai* fields (Slice 1); editor endpoints (Slice 5); thumbnail gate (Slice 6)
dashboard/src/contexts/AuthContext.jsx        # MODIFY — expose openaiConfigured/openaiModel/openaiBaseUrl (Slice 1)
tests/test_llm_endpoints.py                   # MODIFY — one additive /api/config test (Slice 1)
tests/test_editor_frames.py                    # NEW — editor reroute tests
thumbnail.py                                   # MODIFY — /v1/images/generations arm
tests/test_thumbnail_studio.py                 # MODIFY — image reroute tests
```

## Ordering Constraints

- Slice 1 → Slice 2 strictly (Slice 2 removes the prop shims Slice 1 installs).
- Slices 3-6 are backend-only and independent of 1-2 (they read job env / request headers that exist today); ordered by ease (hook grounding → screencast → editor → thumbnail) per the solutions artifact.
- Within Slices 3-6: no cross-dependencies; each ships with its tests as one commit (deploy-handover guidance: batch each phase with its tests).
- The four rerouted stages must each add a no-double-route canary before or with their reroute (precedent rule).

## Verification Notes

- `tests/test_no_double_route.py` (15 tests) must stay green UNMODIFIED except for added canaries — dispatch ownership never changes.
- `tests/test_llm_endpoints.py`: all 26 existing tests stay green and unmodified; Slice 1 adds one `TestConfigFields` test pinning the new `/api/config` openai* fields.
- `tests/test_ai_provider.py` (37, probe pins `:256-328`), `tests/test_llm_client.py` (56) stay green untouched.
- Env namespace law: rerouted stages read `OPENAI_*`/job env or `resolve_openai`, never `LLM_*`.
- Billing pin: cloud keeps `resolve_llm → None` (`app.py:193-194`) and the managed Gemini key (`app.py:143-150`); the thumbnail billing branch (`app.py:6058+`) is untouched.
- Migration manual QA: fresh profile seeded with `llmConfig_v1` + `openai_*` + `geminiKey_v1` → import once, plaintext keys gone, reload does not resurrect them (writers removed in the same change).
- FE has no test harness: verify via `npm run lint` (`--max-warnings 0`), `npm run build`, and the grep checklist: `grep -r "LlmProviderCard" dashboard/src` → 0; `grep -n "setItem('openai_" dashboard/src/App.jsx` → 0 (plaintext writers gone; migration reads inside the store initializer are the only remaining openai_* literals); header emission via `headers['X-OpenAI-` / `headers['X-LLM-` appears ONLY in `lib/llm.js` (comments may name the families; emission sites may not) after Slice 2.
- Empty-model path: model field cleared → satellites fall back to server `LLM_MODEL`/`LLM_MODEL_<TASK>` (verify by hand; the rule is documentation-only in `lib/llm.js:9-11`).
- Precedent hazards: guard every `env[...] = value` against None; never reword `"LLM provider"` error prefixes; audit the job gate each phase.
- Cloud-mode smoke: `providerCfg` stays empty under billing; managed key still serves.

## Performance Considerations

- Frames-based vision calls: 3 frames (`hook_grounding`) / 12 frames @1024px (`screencast`, `editor`) ≈ 1-3k tokens per call regardless of source length — the measured `layout_picker` economy; replaces 1-2 GB Files-API uploads on the two whole-video stages.
- No new server round-trips: header derivation is client-side; derivation adds zero latency.
- Thumbnail image: one `httpx` POST per concept in the existing thread pool — same parallelism as the Gemini arm.
- `probe_vision_support` is cached per provider identity (`ai_provider.py` `_VISION_PROBE_CACHE`) — one probe per job, not per clip.

## Migration Notes

- localStorage: `llmConfig_v1` (encrypted JSON) and `openai_key`/`openai_model`/`openai_base_url` (plaintext) are import sources only; both import into `aiProviderConfig_v1` on first load and are removed (`removeItem`) whether adopted or not (pattern `App.jsx:229-250`). The plaintext persistence writers (`App.jsx:709-712`) are deleted in the same change — otherwise the next render resurrects the key.
- Import precedence: if `aiProviderConfig_v1` already exists, it wins; legacy keys are still removed. If both legacy sources exist, the encrypted `llmConfig_v1` triple wins over the plaintext `openai_*` triple (it was the more deliberate configuration).
- Saved game profiles that already contain a plaintext `openai_key` in their body: the FE stops sending it; server body-override keeps working for model/base. No server migration needed (the field simply stops arriving).
- Backend: no schema changes; no persisted-data changes; rollback = revert the commit (legacy localStorage keys are already gone after first load — accepted, same tradeoff as the shipped `gemini_key` migration).

## Pattern References

- `main.py:3006-3028` — in-job OpenAI vision arm: `create_ai_provider("openai", ...)` → `probe_vision_support` → `image_url` frame parts → `schema=` → parse. Model for Slices 3-4.
- `app.py:6786-6807` — request-scoped dual dispatch in an endpoint. Model for Slice 5's endpoint blocks.
- `thumbnail.py:81-92`, `:131-137` — key-based dual dispatch inside one function (`llm_config is not None`). Model for Slice 6's arm selection.
- `App.jsx:229-250` — one-time localStorage import + unconditional delete. Model for Slice 1's migration.
- `hook_grounding.py:148-152` — degrade-never-fail skip shape. Shape kept in Slice 3 (message reworded for the two-arm gate; the arm itself is modeled on `main.py:3006-3028`).
- `tests/test_hook_grounding.py:76-90` — module-seam stubbing (`monkeypatch.setattr(hg, "_ask_gemini", fake)`). Model for reroute tests.
- `tests/test_ai_provider.py:26-46` — fake `openai` module via `sys.modules`. Model for provider stubs.
- `fde9338` → `25222f5`/`56707b7`/`e96407b` — phased provider rollout and its fix lessons (gates, env writes, canaries).

## Developer Context

Checkpoint 2026-09-10 (this session):

- **Q (Directional, batched)**: Follow `lib/llm.js` single-emitter across all emission sites? **A**: Confirmed — single emitter of both families. (Prop-name and profile-persistence directionals were not selected → promoted to one-at-a-time questions below.)
- **Q (D10, card gate)**: What makes the unified card "set"? Evidence `lib/llm.js:7-8`, `app.py:155-165`, `llm_client.py:165-176`. **A**: baseUrl only — keyless and server-default-model survive.
- **Q (D11, profile key)**: Keep persisting `openai_key` into game-profile bodies (`CreateEditProfileModal.jsx:274-276`, server reads at `app.py:8149-8156`)? **A**: Drop the key; headers per-request; model/base stay as prefs.
- **Q (D12, prop names)**: Rename `llmConfig` props after the store swap? Evidence `SaaShortsTab.jsx:218`, `ThumbnailStudio.jsx:174-366`. **A**: Keep the name; zero internal edits.
- **Q (D13, Option 2 shim)**: Include the BE header-only `resolve_llm` fallback? **A**: Defer entirely.
- **Q (D14, thumb refs)**: How does the unified image arm handle reference images (`/v1/images/generations` can't take them; `thumbnail.py:577-610` uses them for identity)? **A**: Text-prompt only; refs stay Gemini-arm-only; refs + no key → generate without (logged).
- **Design summary confirmed** → decomposition into 6 slices **approved** (Slice 1 foundation; 2 FE convergence; 3-6 the four BE reroutes).
- **Slice 3 checkpoint (resume session, 2026-09-10)**: presented the two-arm `reground` — Gemini preferred; job-`OPENAI_*` fallback gated on *key or base ≠ app.py's injected default* (`app.py:2581-2586` writes defaults into every job env, so a bare base check misfires on LLM-only jobs), `probe_vision_support` guard (`no_vision` skips, `unknown` proceeds), narrow `ImportError` degradation (unguarded caller `main.py:3700`), canary colocated in the per-stage test file, `CLAUDE.md` paragraph added to Files. 3 verifier passes disclosed (injected-default gate fix, marker fixes, ImportError guard; final residual — one comment citation — fixed and grep-verified in-session, no 4th dispatch). **A**: Approve.
- **Slice 4 checkpoint (resume session, 2026-09-10)**: presented the two-arm `detect_content_ranges` — Gemini Files preferred; job-`OPENAI_*` frames fallback (key-or-real-base gate, identical semantics to the locked Slice 3 gate), `probe_vision_support` guard, `_ask_gemini_files` extracted unchanged for a network-free precedence test, 10 additive tests with the canary colocated, CLAUDE.md Files addition. Disclosed: 2 verifier passes (pass 1 — three range-grounding flaws in the MODIFY comments, all fixed; pass 2 OK); and the dormant-caller fact — `detect_content_ranges` has no live caller at HEAD (`main.py:1287` calls `reframe_v2.render` without `content_ranges`; verified repo-wide + git `-S`), so this slice changes no wiring and its manual criteria are module-level. **A**: Approve (CLAUDE.md Files addition ratified with it).
- **Slice 5 checkpoint (resume session, 2026-09-10)**: presented the two-arm endpoint gates (Gemini preferred; `resolve_openai` + `openai_configured` key-or-real-base — the same semantics as the locked Slice 3/4 in-job gate; executor build+probe so the blocking network never touches the event loop; clean 400 raised before `/api/edit`'s except-Exception-only `try`), the shared prompt builders (byte-identical extractions from `get_ffmpeg_filter`/`get_effects_config`), `apply_edits` shared via `VideoEditor(api_key=None, openai_provider=…)` with the text-only `openai_repair_filter` fallback inside `_repair_filter`, and the 25-test file with the canary colocated. Verifier 1 pass, VERDICT OK (three dispatch attempts: two Pi-runner model-admission RPC timeouts — `router/auto`, `zai/glm-5.3` — success on `claude/sonnet`; the developer asked for a root-cause investigation before Slice 6 and plans to remove the claude runner). **A**: Approve (the stale CLAUDE.md "Stays Gemini: … editor effects" line and the `/api/effects` docstring were noted and left untouched per the locked file list).
- **Slice 6 checkpoint (resume session, 2026-09-10)**: presented the two-arm thumbnail path — the `/v1/images/generations` text-prompt-only arm (D14) with `_image_prompt` shared by both arms (the Slice 5 one-prompt-two-transports pattern), refs dropped BEFORE the concepts call so `has_person` stays honest, `OPENAI_IMAGE_MODEL` env knob + the `gpt-4o-mini` chat-default swap (resolve_openai's fallback cannot draw), no `size`/`response_format` in the POST (model-specific; `finalize_thumbnail` cover-crops whatever arrives), the gate relaxing via Slice 5's `openai_configured` with a `not BILLING_ENABLED` leg keeping cloud byte-identical to HEAD (an anonymous BYOK caller or a stray server `OPENAI_*` env cannot reach the unmetered executor), the clean-400 unavailable state with the reservation released by the existing handler, +14 tests with the canary colocated. Verifier 1 pass, VERDICT OK (3 annotation nits fixed in place: the IMAGE_MODEL insert anchor is `:18`, the plan-dispatch stay-region is `:445` + `:446-450`, the existing-test count is 8 not 9; dispatch clean on `zai-paasv2/glm-5.3-flash` after the worker.js admission-window patch). The interactive ask tool timed out twice (120 s, 900 s), so the checkpoint was ratified in chat. The stale CLAUDE.md "Stays Gemini: image gen" line noted and left untouched per the locked file list (Slice 5 precedent). **A**: Approve.

## Design History

- Slice 1: Unified card + encrypted store + migration (FE core) — approved as generated (3 verifier passes; final VERDICT OK after in-place fixes: store-key const, section ranges, emission-form criteria, err.status on keyless test, billing-pinned openai* fields, artifact bookkeeping)
- Slice 2: FE emission convergence — approved as generated (strictly mechanical: mounts keep `aiProvider={aiProvider}`, the effectiveProvider seed substitution was offered and declined at the checkpoint; 3 verifier rounds — MODIFY range-drift fixes, body-fence duplicate-key fix; final VERDICT OK)
- Slice 3: hook_grounding.py reroute — approved as generated (3 verifier passes: pass 1 caught the injected-default gate flaw — app.py writes OPENAI_* defaults into every job env, fixed with key-or-real-base gate + pins; pass 2 the ImportError hole — narrow `except ImportError` keeps never-raises with the unguarded main.py:3700 caller, plus marker fixes; pass 3 OK except one comment citation, fixed and grep-verified in-session. CLAUDE.md paragraph added to Files; canary colocated in tests/test_hook_grounding.py so tests/test_no_double_route.py stays byte-unmodified)
- Slice 4: screencast_layout.py reroute — approved as generated (2 verifier passes: pass 1 caught 3 range-grounding flaws — docstring paragraph is :28-29 not :24-25, the replaced region is :173-212 not :170-208, and the CLAUDE.md quote must span the hard wrap and carry the full fragment; all fixed, pass 2 OK. The Gemini Files arm extracted unchanged into `_ask_gemini_files` so the precedence test needs no network; canary colocated in tests/test_screencast_layout.py. CLAUDE.md paragraph added to Files at the checkpoint; dormant-caller fact discovered and recorded: detect_content_ranges has no live caller at HEAD — main.py:1287 calls reframe_v2.render without content_ranges — manual criteria are module-level, no wiring change)
- Slice 5: editor.py frames path + endpoint resolution — approved as generated (1 verifier pass, VERDICT OK: the three extracted prompts byte-identical to the on-disk originals, every cited line range live-confirmed, gate-before-try placement confirmed for both endpoints, pydantic camelCase models fine. Dispatch took 3 attempts — two Fabric model-admission RPC timeouts on the Pi runner (router/auto, zai/glm-5.3), success on claude/sonnet; the developer requested a root-cause investigation of the Pi-runner admission failure before Slice 6)
- Slice 6: thumbnail image reroute + gate relax — approved as generated (1 verifier pass, VERDICT OK, 3 annotation nits fixed in place before the checkpoint: the IMAGE_MODEL insert anchor is :18 not :17, the plan-dispatch stay-region is :445 also staying + except/return :446-450, and the existing-test count is 8 not 9; the interactive ask tool timed out twice, so approval was given in chat)

## References

- `.rpiv/artifacts/solutions/2026-09-10_06-46-58_unified-ai-provider-config.md` — the solutions artifact this design implements (Option 1, 5 phases).
- `.rpiv/artifacts/research/2026-09-09_20-00-08_unified-ai-provider-config.md` — the research map.
- `.rpiv/artifacts/discover/2026-09-09_19-17-18_unified-ai-provider-config.md` — the FRD (D0-D9).
- `CLAUDE.md` — "Stage ownership: ai_provider.py vs llm_client.py" and the env namespace law.
