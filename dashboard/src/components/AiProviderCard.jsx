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
