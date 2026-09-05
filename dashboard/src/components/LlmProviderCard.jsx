import React, { useState } from 'react';
import { Cpu, Eye, EyeOff, Check, Loader2, AlertTriangle } from 'lucide-react';
import { apiJson } from '../lib/api';
import { llmConfigComplete, llmHeaders } from '../lib/llm';

// The OpenAI-compatible AI provider card. Mounted only in the self-host
// Settings branch (App.jsx), so the surface is structurally absent on cloud.
//
// D2: llm_client goes silently inert on a half-configured provider, so Save
// stays disabled until all three fields have content. "Test connection" posts
// the typed-but-unsaved values to the connection-check endpoint, which
// resolves them exactly like a real job would; D10: its 400 means this
// config is wrong (malformed URL, no model) and 502 means the provider
// refused or failed — never 402.
// D9: the header builder omits the model header when empty, so a model-less
// test falls back to the server's env chain.

const PRESETS = [
  { id: 'ollama-cloud', label: 'ollama cloud', baseUrl: 'https://ollama.com/v1' },
  { id: 'ollama-local', label: 'local ollama', baseUrl: 'http://localhost:11434/v1' },
  { id: 'openrouter', label: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1' },
];

export default function LlmProviderCard({ savedConfig, onConfigSet, llmConfigured, llmModel, llmBaseUrl }) {
  const [form, setForm] = useState(() => ({
    baseUrl: savedConfig?.baseUrl || '',
    apiKey: savedConfig?.apiKey || '',
    model: savedConfig?.model || '',
  }));
  const [saved, setSaved] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  // null | {phase:'running'} | {phase:'ok', latencyMs, model} | {phase:'error', kind, detail}
  const [test, setTest] = useState(null);
  // The server-status block stands in for the form until the user asks for it.
  // Derived, not stored: the config read lands after mount, so a stored
  // flag would freeze on the pre-fetch value. A complete saved config IS an
  // override, so with one saved the form is the primary surface.
  const [overrideRequested, setOverrideRequested] = useState(false);
  const serverConfigured = !!llmConfigured && !llmConfigComplete(savedConfig);
  const showForm = overrideRequested || !serverConfigured;

  const setField = (name) => (e) => {
    setForm((f) => ({ ...f, [name]: e.target.value }));
    setSaved(false);
  };

  const handleSave = () => {
    if (!llmConfigComplete(form)) return;
    // Stored trimmed: the card is the only writer of the config, and the
    // header builder trims anyway — storing clean costs nothing.
    onConfigSet({ baseUrl: form.baseUrl.trim(), apiKey: form.apiKey.trim(), model: form.model.trim() });
    setSaved(true);
  };

  const handleTest = async () => {
    setTest({ phase: 'running' });
    try {
      const data = await apiJson('/api/llm/test', { method: 'POST', headers: llmHeaders(form) });
      setTest({ phase: 'ok', latencyMs: data.latencyMs, model: data.model });
    } catch (e) {
      setTest({
        phase: 'error',
        kind: e?.status === 400 ? 'config' : 'provider',
        detail: e?.detail || e?.message || 'Request failed',
      });
    }
  };

  const canTest = !!(form.baseUrl.trim() && form.apiKey.trim());
  const canSave = llmConfigComplete(form);

  return (
    <div className="card p-4 sm:p-6 mb-8 animate-fade">
      <div className="flex items-center gap-3 mb-4">
        <div className="p-2 bg-paper3 rounded-input text-brass">
          <Cpu size={18} />
        </div>
        <h2 className="font-display lowercase text-lg text-ink">AI Provider</h2>
      </div>

      {showForm ? (
        <>
          <p className="text-xs text-muted mb-4 leading-relaxed">
            Any OpenAI-compatible endpoint (Ollama, OpenRouter, vLLM, …) can power clip analysis, titles and
            descriptions — a Gemini key stays optional. Keys are only stored in your browser and sent per request,
            never stored server-side.
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
              <label className="block text-sm text-muted mb-1" htmlFor="llm-base-url">Endpoint URL</label>
              <input
                id="llm-base-url"
                type="text"
                value={form.baseUrl}
                onChange={setField('baseUrl')}
                placeholder="https://ollama.com/v1"
                className="input-field font-mono"
                autoComplete="off"
                spellCheck="false"
              />
            </div>
            <div>
              <label className="block text-sm text-muted mb-1" htmlFor="llm-api-key">API Key</label>
              <div className="relative">
                <input
                  id="llm-api-key"
                  type={isVisible ? 'text' : 'password'}
                  value={form.apiKey}
                  onChange={setField('apiKey')}
                  placeholder="sk-..."
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
              <label className="block text-sm text-muted mb-1" htmlFor="llm-model">Model</label>
              <input
                id="llm-model"
                type="text"
                value={form.model}
                onChange={setField('model')}
                placeholder="gpt-oss:120b"
                className="input-field font-mono"
                autoComplete="off"
                spellCheck="false"
              />
              <p className="text-xs text-muted mt-1">
                Left empty, the model falls back to the server's LLM_MODEL setting — when it has one.
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
          </div>

          {test?.phase === 'ok' && (
            <p className="badge-ok mt-3 inline-flex items-center gap-1.5" role="status">
              <Check size={12} /> responded in {test.latencyMs}ms{test.model ? ` · ${test.model}` : ''}
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
              <p className="text-muted text-xs mt-0.5 font-mono break-all">{llmBaseUrl}</p>
              {llmModel && <p className="text-muted text-xs">model: <span className="font-mono">{llmModel}</span></p>}
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
