import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Plus, Edit3, Copy, Trash2, Loader2, Download, Upload, Mic, Type, ChevronDown, Check } from 'lucide-react';
import Modal from './ui/Modal';
import { apiJson } from '../lib/api';

// Voice/Style Presets page — CRUD + import/export for the VoiceOver feature's
// presets, mirroring the GameProfilesPage list+modal pattern.
//
// Voice presets: TTS persona prompt + TAGS. Tags must reference existing style
// preset ids, so voices can be matched to caption styles ("gaming" voice for
// the "Gaming" style). Style presets: caption-generation prompt + a free-form
// group (generic / genz / story / auto / anything the user invents).

function titleCase(s) {
  return String(s || '').charAt(0).toUpperCase() + String(s).slice(1);
}

// A real dropdown (button + menu) for picking/typing a group. Typing a new
// name creates the group on save; picking an existing one selects it.
function GroupDropdown({ value, options, onChange }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value || '');
  const boxRef = useRef(null);

  useEffect(() => { setQuery(value || ''); }, [value, open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  // Suggested groups: existing ones matching the typed query; the raw query
  // shows as a "create new" row on top when it's not an exact match.
  const suggestions = useMemo(() => {
    const q = (query || '').toLowerCase().trim();
    const matches = options.filter((o) => !q || o.toLowerCase().includes(q));
    if (q && !matches.some((m) => m === q)) {
      return [{ value: q, isNew: true }, ...matches.map((m) => ({ value: m, isNew: false }))];
    }
    return matches.map((m) => ({ value: m, isNew: false }));
  }, [options, query]);

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="input-field w-full flex items-center justify-between text-left"
      >
        <span className={value ? 'text-ink' : 'text-muted'}>{value || 'generic'}</span>
        <ChevronDown size={14} className={`text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-full bg-paper border border-rule2 rounded-input shadow-lg overflow-hidden">
          <input
            autoFocus
            className="w-full px-3 py-2 text-sm bg-paper2 border-b border-rule outline-none text-ink placeholder:text-muted"
            placeholder="Search or type a new group…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                onChange(query.trim());
                setOpen(false);
              } else if (e.key === 'Escape') {
                setOpen(false);
              }
            }}
          />
          <div className="max-h-48 overflow-y-auto custom-scrollbar">
            {(query && !options.some((o) => o === query.toLowerCase())) && (
              <button
                type="button"
                onClick={() => { onChange(query.toLowerCase()); setOpen(false); }}
                className="w-full px-3 py-2 text-left text-sm text-brass hover:bg-paper3 flex items-center gap-2"
              >
                <Plus size={13} /> Create group “{query.toLowerCase()}”
              </button>
            )}
            {options.map((o) => (
              <button
                key={o}
                type="button"
                onClick={() => { onChange(o); setOpen(false); }}
                className={`w-full px-3 py-2 text-left text-sm flex items-center justify-between hover:bg-paper3 ${
                  o === value ? 'text-brass' : 'text-ink2'
                }`}
              >
                {o}
                {o === value && <Check size={13} className="text-brass" />}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PresetEditModal({ isOpen, onClose, kind, preset, stylePresets = [], onSave }) {
  const [title, setTitle] = useState('');
  const [prompt, setPrompt] = useState('');
  const [kindField, setKindField] = useState('generic');
  const [tags, setTags] = useState([]);
  const isSeed = !!preset && !/^[0-9a-f]{8}-/i.test(preset.id || '');
  const isVoice = kind === 'voice';

  useEffect(() => {
    setTitle(preset?.title || '');
    setPrompt(preset?.prompt || '');
    setKindField(preset?.kind || 'generic');
    setTags(preset?.tags || []);
  }, [preset, isOpen]);

  const toggleTag = (id) => {
    setTags((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));
  };

  const valid = title.trim() && prompt.trim() && (!isVoice || tags.length > 0);

  const save = async () => {
    if (!valid) return;
    const body = { title: title.trim(), prompt: prompt.trim() };
    if (isVoice) body.tags = tags;
    else body.kind = kindField;
    if (preset) {
      await apiJson(`/api/${kind}-presets/${preset.id}`, { method: 'PUT', body });
    } else {
      await apiJson(`/api/${kind}-presets`, { method: 'POST', body });
    }
    onSave();
    onClose();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      eyebrow={isVoice ? 'VOICE PRESET' : 'STYLE PRESET'}
      title={preset ? `Edit ${preset.title}` : `New ${kind} preset`}
      footer={
        <div className="flex gap-3">
          <button onClick={onClose} className="btn-ghost flex-1 px-4 py-2 text-sm">Cancel</button>
          <button
            onClick={save}
            disabled={!valid}
            className="btn-primary flex-1 px-4 py-2 text-sm disabled:opacity-50"
          >
            {preset ? 'Save' : 'Create'}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="eyebrow block mb-1.5">Title</label>
          <input
            className="input-field w-full"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={isVoice ? 'e.g. Chill Narrator' : 'e.g. Hype Commentary'}
          />
        </div>

        {isVoice ? (
          <div>
            <label className="eyebrow block mb-1.5">Style tags (pick one or more)</label>
            <div className="flex flex-wrap gap-1.5">
              {stylePresets.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => toggleTag(s.id)}
                  className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${
                    tags.includes(s.id)
                      ? 'border-brass text-brass bg-brass/10'
                      : 'border-rule text-muted hover:text-ink2'
                  }`}
                >
                  {tags.includes(s.id) ? '★ ' : ''}{s.title}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted mt-1.5">
              Voices tagged with a style are highlighted first in the VoiceOver window when that
              style is selected. At least one tag is required.
            </p>
          </div>
        ) : (
          <div>
            <label className="eyebrow block mb-1.5">Group</label>
            <GroupDropdown
              value={kindField}
              options={[...new Set(['generic', 'genz', 'story', 'auto', kindField].filter(Boolean))]}
              onChange={(v) => setKindField(v)}
            />
            <p className="text-xs text-muted mt-1.5">
              Used for grouping/filtering (lowercased, spaces become dashes). Pick an existing
              group or type a new one.
            </p>
          </div>
        )}

        <div>
          <label className="eyebrow block mb-1.5">
            {isVoice ? 'Voice prompt (Qwen VoiceDesign persona)' : 'Style prompt (caption generation)'}
          </label>
          <textarea
            className="input-field w-full font-mono text-xs"
            rows={12}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>
        {preset && isSeed && (
          <p className="text-xs text-muted">
            This is a built-in preset: edits are saved, but it can't be deleted — duplicate it to
            experiment freely.
          </p>
        )}
      </div>
    </Modal>
  );
}

function PresetList({ kind, icon: Icon, stylePresets = [] }) {
  const [presets, setPresets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState(null);
  const [importing, setImporting] = useState(false);
  const [msg, setMsg] = useState(null);
  const [filter, setFilter] = useState('all');
  const fileInputRef = useRef(null);
  const isVoice = kind === 'voice';

  const load = async () => {
    try {
      setLoading(true);
      setError(null);
      setPresets(await apiJson(`/api/${kind}-presets`));
    } catch (err) {
      setError(err.message || 'Failed to load presets');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [kind]);

  const flash = (text) => { setMsg(text); setTimeout(() => setMsg(null), 3000); };

  // Filter chips: for voices — each style tag + "untagged"; for styles — groups
  const chips = useMemo(() => {
    const counts = new Map();
    if (isVoice) {
      for (const p of presets) {
        const tags = p.tags?.length ? p.tags : ['(untagged)'];
        for (const t of tags) counts.set(t, (counts.get(t) || 0) + 1);
      }
      // Order chips by style list order for stability
      const styleOrder = stylePresets.map((s) => s.id);
      return [...counts.entries()].sort(
        (a, b) => (styleOrder.indexOf(a[0]) + 1 || 999) - (styleOrder.indexOf(b[0]) + 1 || 999)
      ).map(([id, n]) => ({
        id,
        label: stylePresets.find((s) => s.id === id)?.title || titleCase(id),
        count: n,
      }));
    }
    for (const p of presets) {
      const k = p.kind || 'generic';
      counts.set(k, (counts.get(k) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([k, n]) => ({ id: k, label: titleCase(k), count: n }));
  }, [presets, isVoice, stylePresets]);

  const filtered = useMemo(() => {
    if (filter === 'all') return presets;
    if (isVoice) {
      return presets.filter((p) =>
        filter === '(untagged)'
          ? !p.tags?.length
          : (p.tags || []).includes(filter));
    }
    return presets.filter((p) => (p.kind || 'generic') === filter);
  }, [presets, filter, isVoice]);

  const styleTitle = (id) => stylePresets.find((s) => s.id === id)?.title || id;

  const handleExport = async () => {
    try {
      const data = await apiJson(`/api/${kind}-presets/export`);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `openshorts-${kind}-presets-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      flash(`Exported ${data.count} presets`);
    } catch (err) { setError(err.message || 'Failed to export'); }
  };

  const handleImportFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true); setError(null);
    try {
      const json = JSON.parse(await file.text());
      const body = Array.isArray(json) ? { presets: json }
        : json[`${kind}_presets`] ? json
        : json.presets ? json
        : { presets: [json] };
      const result = await apiJson(`/api/${kind}-presets/import`, { method: 'POST', body });
      const parts = [];
      if (result.imported) parts.push(`${result.imported} imported`);
      if (result.skipped?.length) parts.push(`${result.skipped.length} skipped`);
      if (result.errors?.length) parts.push(`${result.errors.length} errors`);
      flash(parts.join(' · ') || 'Import complete');
      await load();
    } catch (err) {
      setError(err.detail || err.message || 'Failed to import — check file format');
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this preset?')) return;
    try {
      await apiJson(`/api/${kind}-presets/${id}`, { method: 'DELETE' });
      await load();
    } catch (err) { setError(err.detail || err.message || 'Failed to delete'); }
  };

  const handleDuplicate = async (id) => {
    try {
      await apiJson(`/api/${kind}-presets/${id}/duplicate`, { method: 'POST' });
      await load();
    } catch (err) { setError(err.detail || err.message || 'Failed to duplicate'); }
  };

  const openEdit = async (p) => {
    try {
      setEditing(await apiJson(`/api/${kind}-presets/${p.id}`));
    } catch (err) { setError(err.message || 'Failed to load preset'); }
  };

  if (loading) {
    return <div className="flex items-center justify-center h-40"><Loader2 className="animate-spin" size={28} /></div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center gap-3 flex-wrap">
        <h2 className="text-lg font-semibold text-ink flex items-center gap-2">
          <Icon size={18} className="text-brass" />
          {isVoice ? 'Voice Presets' : 'Style Presets'}
          <span className="readout">{presets.length}</span>
        </h2>
        <div className="flex items-center gap-2">
          <input ref={fileInputRef} type="file" accept=".json,application/json" onChange={handleImportFile} className="hidden" />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="flex items-center gap-1.5 bg-paper2 border border-rule text-ink px-3 py-2 rounded-lg hover:bg-paper3 transition-colors text-sm disabled:opacity-50"
            title={`Import ${kind} presets from JSON`}
          >
            {importing ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            {importing ? 'Importing…' : 'Import'}
          </button>
          <button
            onClick={handleExport}
            className="flex items-center gap-1.5 bg-paper2 border border-rule text-ink px-3 py-2 rounded-lg hover:bg-paper3 transition-colors text-sm"
            title="Export all presets to JSON"
          >
            <Upload size={14} /> Export
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 bg-brass text-white px-4 py-2 rounded-lg hover:bg-brass-hover transition-colors text-sm"
          >
            <Plus size={16} /> New
          </button>
        </div>
      </div>

      {/* Filter chips */}
      {chips.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => setFilter('all')}
            className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${
              filter === 'all' ? 'border-brass text-brass bg-brass/10' : 'border-rule text-muted hover:text-ink2'
            }`}
          >
            All ({presets.length})
          </button>
          {chips.map((c) => (
            <button
              key={c.id}
              onClick={() => setFilter(c.id)}
              className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${
                filter === c.id ? 'border-brass text-brass bg-brass/10' : 'border-rule text-muted hover:text-ink2'
              }`}
            >
              {c.label} ({c.count})
            </button>
          ))}
        </div>
      )}

      {msg && (
        <div className="px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-lg text-emerald-800 text-sm">
          {msg}
        </div>
      )}
      {error && (
        <div className="px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          {error}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {filtered.map((p) => (
          <div key={p.id} className="card p-4 border border-rule rounded-lg flex flex-col">
            <div className="flex justify-between items-start mb-2 gap-2">
              <h3 className="font-medium text-ink leading-tight break-words">{p.title}</h3>
              <div className="flex gap-1 shrink-0">
                <button onClick={() => openEdit(p)} className="p-1.5 rounded hover:bg-paper3 transition-colors" title="Edit">
                  <Edit3 size={15} />
                </button>
                <button onClick={() => handleDuplicate(p.id)} className="p-1.5 rounded hover:bg-paper3 transition-colors" title="Duplicate">
                  <Copy size={15} />
                </button>
                <button
                  onClick={() => handleDelete(p.id)}
                  className="p-1.5 rounded hover:bg-paper3 transition-colors text-red-500"
                  title="Delete"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
            <p className="text-xs text-muted font-mono whitespace-pre-wrap line-clamp-6 flex-1" title={p.prompt}>
              {p.prompt.slice(0, 400)}{p.prompt.length > 400 ? '…' : ''}
            </p>
            <div className="flex flex-wrap gap-1 mt-2">
              {isVoice
                ? (p.tags || []).map((t) => (
                    <span key={t} className="readout bg-brass/15 text-brass px-2 py-0.5 rounded-full">{styleTitle(t)}</span>
                  ))
                : (p.kind || 'generic') && (
                    <span className="readout bg-paper3 px-2 py-0.5 rounded-full">{p.kind}</span>
                  )}
            </div>
          </div>
        ))}
      </div>

      <PresetEditModal
        isOpen={showCreate || !!editing}
        onClose={() => { setShowCreate(false); setEditing(null); }}
        kind={kind}
        preset={editing}
        stylePresets={stylePresets}
        onSave={load}
      />
    </div>
  );
}

export default function VoiceStylePresetsPage() {
  const [tab, setTab] = useState('voice');
  const [stylePresets, setStylePresets] = useState([]);
  useEffect(() => {
    apiJson('/api/style-presets').then(setStylePresets).catch(() => {});
  }, [tab]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-ink">Voice/Style Presets</h1>
        <p className="text-sm text-muted mt-1">
          Voice presets shape the TTS narrator and are tagged with caption styles; style presets
          guide caption generation in the VoiceOver workflow.
        </p>
      </div>
      <div className="flex gap-2 border-b border-rule">
        {[
          { id: 'voice', label: 'Voice', icon: Mic },
          { id: 'style', label: 'Style', icon: Type },
        ].map(({ id, label, icon: TabIcon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-2 px-4 py-2 text-sm rounded-t-lg transition-colors ${
              tab === id ? 'text-brass border-b-2 border-brass bg-paper3/50' : 'text-muted hover:text-ink2'
            }`}
          >
            <TabIcon size={15} /> {label}
          </button>
        ))}
      </div>
      {tab === 'voice' ? (
        <PresetList key="voice" kind="voice" icon={Mic} stylePresets={stylePresets} />
      ) : (
        <PresetList key="style" kind="style" icon={Type} />
      )}
    </div>
  );
}
