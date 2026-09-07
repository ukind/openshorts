import React, { useState, useEffect } from 'react';
import { X, Search, Loader2, Check, AlertCircle } from 'lucide-react';
import Modal from './ui/Modal';
import { apiJson } from '../lib/api';

export default function CreateEditProfileModal({ isOpen, onClose, profile, onSave,
                                                aiProvider, geminiApiKey,
                                                openaiApiKey, openaiModel,
                                                openaiBaseUrl }) {
  const [formData, setFormData] = useState({
    name: '',
    game_title: '',
    steam_app_id: '',
    steam_description: '',
    steam_genres: [],
    steam_tags: [],
    custom_description: '',
    ai_analysis: {
      game_type: '',
      gameplay_characteristics: [],
      key_moments: []
    },
    recommended_weights: {
      emotional_reaction: 0.0,
      surprise: 0.0,
      tension: 0.0,
      social_interaction: 0.0,
      achievement: 0.0,
      humor: 0.0,
      outrage: 0.0,
      novelty: 0.0
    },
    active_weights: {
      emotional_reaction: 0.0,
      surprise: 0.0,
      tension: 0.0,
      social_interaction: 0.0,
      achievement: 0.0,
      humor: 0.0,
      outrage: 0.0,
      novelty: 0.0
    }
  });

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [steamQuery, setSteamQuery] = useState('');
  const [steamResults, setSteamResults] = useState([]);
  const [steamLoading, setSteamLoading] = useState(false);
  const [steamError, setSteamError] = useState(null);
  const [steamSelecting, setSteamSelecting] = useState(null);
  const [aiAnalyzing, setAiAnalyzing] = useState(false);
  const [aiError, setAiError] = useState(null);
  // Per-analysis provider choice, seeded from the app's dial each mount. The
  // app owns ai_provider persistence (D5) — this modal only reads keys/provider
  // via props (VoiceOverPage mount pattern); no localStorage access survives.
  const [provider, setProvider] = useState(aiProvider || 'gemini');

  useEffect(() => {
    if (profile) {
      setSteamQuery(profile.game_title || '');
      setSteamResults([]);
      setSteamError(null);
      // For edit mode, populate form with existing data
      setFormData({
        name: profile.name || '',
        game_title: profile.game_title || '',
        steam_app_id: profile.steam_app_id || '',
        steam_description: profile.steam_description || '',
        steam_genres: profile.steam_genres || [],
        steam_tags: profile.steam_tags || [],
        custom_description: profile.custom_description || '',
        ai_analysis: {
          game_type: profile.ai_analysis?.game_type || '',
          gameplay_characteristics: profile.ai_analysis?.gameplay_characteristics || [],
          key_moments: profile.ai_analysis?.key_moments || []
        },
        recommended_weights: {
          emotional_reaction: 0.0,
          surprise: 0.0,
          tension: 0.0,
          social_interaction: 0.0,
          achievement: 0.0,
          humor: 0.0,
          outrage: 0.0,
          novelty: 0.0,
          ...(profile.recommended_weights || {})
        },
        active_weights: {
          emotional_reaction: 0.0,
          surprise: 0.0,
          tension: 0.0,
          social_interaction: 0.0,
          achievement: 0.0,
          humor: 0.0,
          outrage: 0.0,
          novelty: 0.0,
          ...(profile.active_weights || {})
        }
      });
    } else {
      setSteamQuery('');
      setSteamResults([]);
      setSteamError(null);
      // Reset to default for create mode
      setFormData({
        name: '',
        game_title: '',
        steam_app_id: '',
        steam_description: '',
        steam_genres: [],
        steam_tags: [],
        custom_description: '',
        ai_analysis: {
          game_type: '',
          gameplay_characteristics: [],
          key_moments: []
        },
        recommended_weights: {
          emotional_reaction: 0.0,
          surprise: 0.0,
          tension: 0.0,
          social_interaction: 0.0,
          achievement: 0.0,
          humor: 0.0,
          outrage: 0.0,
          novelty: 0.0
        },
        active_weights: {
          emotional_reaction: 0.0,
          surprise: 0.0,
          tension: 0.0,
          social_interaction: 0.0,
          achievement: 0.0,
          humor: 0.0,
          outrage: 0.0,
          novelty: 0.0
        }
      });
    }
  }, [profile]);

  const handleChange = (field, value) => {
    setFormData(prev => ({
      ...prev,
      [field]: value
    }));
  };

  const handleNestedChange = (parentField, childField, value) => {
    setFormData(prev => ({
      ...prev,
      [parentField]: {
        ...prev[parentField],
        [childField]: value
      }
    }));
  };

  // Fixed: Proper handling of nested array paths
  const handleArrayChange = (field, index, value) => {
    // Handle nested paths like 'ai_analysis.gameplay_characteristics'
    if (field.includes('.')) {
      const [parent, child] = field.split('.');
      setFormData(prev => {
        const newData = { ...prev };
        newData[parent] = { ...newData[parent] };
        newData[parent][child] = newData[parent][child].map((item, i) => i === index ? value : item);
        return newData;
      });
    } else {
      setFormData(prev => ({
        ...prev,
        [field]: prev[field].map((item, i) => i === index ? value : item)
      }));
    }
  };

  const addArrayItem = (field) => {
    setFormData(prev => ({
      ...prev,
      [field]: [...prev[field], '']
    }));
  };

  const removeArrayItem = (field, index) => {
    const newArray = [...formData[field]];
    newArray.splice(index, 1);
    setFormData(prev => ({
      ...prev,
      [field]: newArray
    }));
  };

  const handleSteamSearch = async () => {
    const q = (steamQuery || formData.game_title || '').trim();
    if (!q) { setSteamError('Enter a game name to search'); return; }
    setSteamLoading(true); setSteamError(null); setSteamResults([]);
    try {
      const data = await apiJson(`/api/steam/search?q=${encodeURIComponent(q)}`);
      const results = data.results || [];
      setSteamResults(results);
      if (results.length === 0) setSteamError('No results found on Steam — try a different name or Steam App ID');
    } catch (e) {
      setSteamError(e.message || 'Steam search failed');
    } finally { setSteamLoading(false); }
  };

  const handleSteamSelect = async (result) => {
    setSteamSelecting(result.app_id); setSteamError(null);
    try {
      const meta = await apiJson(`/api/steam/games/${result.app_id}`);
      setFormData(prev => ({
        ...prev,
        game_title: meta.name || prev.game_title,
        steam_app_id: String(meta.app_id || result.app_id),
        steam_description: meta.description || prev.steam_description,
        steam_genres: meta.genres?.length ? meta.genres : prev.steam_genres,
        steam_tags: meta.tags?.length ? meta.tags : (meta.categories?.length ? meta.categories : prev.steam_tags),
      }));
      setSteamQuery(meta.name || result.name);
    } catch (e) {
      setSteamError(e.message || 'Failed to load Steam details');
    } finally { setSteamSelecting(null); }
  };

  const handleSteamAppIdLookup = async () => {
    const id = String(formData.steam_app_id || '').trim();
    if (!id || !/^\d+$/.test(id)) { setSteamError('Enter a valid numeric Steam App ID'); return; }
    setSteamSelecting(Number(id)); setSteamError(null);
    try {
      const meta = await apiJson(`/api/steam/games/${id}`);
      setFormData(prev => ({
        ...prev,
        game_title: meta.name || prev.game_title,
        steam_app_id: String(meta.app_id || id),
        steam_description: meta.description || prev.steam_description,
        steam_genres: meta.genres?.length ? meta.genres : prev.steam_genres,
        steam_tags: meta.tags?.length ? meta.tags : (meta.categories?.length ? meta.categories : prev.steam_tags),
      }));
      setSteamQuery(meta.name || '');
      setSteamResults([{ app_id: meta.app_id, name: meta.name, url: `https://store.steampowered.com/app/${meta.app_id}/` }]);
    } catch (e) {
      setSteamError(e.message || 'Failed to load Steam details for that App ID');
    } finally { setSteamSelecting(null); }
  };

  const handleAiAnalyze = async () => {
    const title = (formData.game_title || steamQuery || '').trim();
    if (!title) { setAiError('Enter a Game Title first (or run Steam Lookup)'); return; }
    setAiAnalyzing(true); setAiError(null);
    try {
      const headers = { 'X-AI-Provider': provider };
      if (provider === 'openai') {
        if (openaiApiKey) headers['X-OpenAI-Key'] = openaiApiKey;
        if (openaiModel) headers['X-OpenAI-Model'] = openaiModel;
        if (openaiBaseUrl) headers['X-OpenAI-Base-Url'] = openaiBaseUrl;
        if (geminiApiKey) headers['X-Gemini-Key'] = geminiApiKey;
      } else {
        if (geminiApiKey) headers['X-Gemini-Key'] = geminiApiKey;
        if (openaiApiKey) headers['X-OpenAI-Key'] = openaiApiKey;
        if (openaiModel) headers['X-OpenAI-Model'] = openaiModel;
        if (openaiBaseUrl) headers['X-OpenAI-Base-Url'] = openaiBaseUrl;
      }
      const data = await apiJson('/api/game-profiles/analyze', {
        method: 'POST',
        headers,
        body: {
          game_title: title,
          steam_description: formData.steam_description || '',
          steam_genres: formData.steam_genres || [],
          steam_tags: formData.steam_tags || [],
          provider,
          openai_model: openaiModel || undefined,
          openai_base_url: openaiBaseUrl || undefined,
          openai_key: openaiApiKey || undefined,
        },
      });
      setFormData(prev => ({
        ...prev,
        ai_analysis: data.ai_analysis || prev.ai_analysis,
        recommended_weights: {
          emotional_reaction: 0.0, surprise: 0.0, tension: 0.0, social_interaction: 0.0,
          achievement: 0.0, humor: 0.0, outrage: 0.0, novelty: 0.0,
          ...(prev.recommended_weights || {}),
          ...(data.recommended_weights || {})
        },
        active_weights: {
          emotional_reaction: 0.0, surprise: 0.0, tension: 0.0, social_interaction: 0.0,
          achievement: 0.0, humor: 0.0, outrage: 0.0, novelty: 0.0,
          ...(prev.active_weights || {}),
          ...(data.recommended_weights || prev.recommended_weights || {})
        },
      }));
      if (data.source === 'heuristic') {
        setAiError(null);
        // transient info: heuristic used — inform but not as error
        console.debug('AI analysis: heuristic fallback (no Gemini key or Gemini failed)');
      }
    } catch (e) {
      setAiError(e.message || 'AI analysis failed');
    } finally { setAiAnalyzing(false); }
  };

  const handleWeightChange = (category, value) => {
    // Ensure value is between 0 and 1
    const clampedValue = Math.max(0.0, Math.min(1.0, parseFloat(value) || 0.0));

    setFormData(prev => ({
      ...prev,
      active_weights: {
        ...prev.active_weights,
        [category]: clampedValue
      }
    }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      if (profile) {
        // Update existing profile - FIXED: pass object instead of JSON string
        await apiJson(`/api/game-profiles/${profile.id}`, {
          method: 'PUT',
          body: formData  // This was previously JSON.stringify(formData)
        });
      } else {
        // Create new profile - FIXED: pass object instead of JSON string
        await apiJson('/api/game-profiles', {
          method: 'POST',
          body: formData  // This was previously JSON.stringify(formData)
        });
      }

      // Close modal and refresh list
      onClose();
      if (onSave) onSave();
    } catch (err) {
      setError(err.message || 'Failed to save game profile');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="xl">
      <div className="w-full">
        <div className="mb-6 pr-8">
          <h2 className="text-xl font-bold text-ink">
            {profile ? 'Edit Game Profile' : 'Create Game Profile'}
          </h2>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Left Column */}
            <div>
              {/* Basic Info */}
              <div className="card p-4 border border-rule rounded-lg mb-6">
                <h3 className="font-semibold mb-3">Basic Information</h3>

                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-ink mb-1">Profile Name *</label>
                    <input
                      type="text"
                      value={formData.name}
                      onChange={(e) => handleChange('name', e.target.value)}
                      className="w-full p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass"
                      required
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-ink mb-1">Game Title *</label>
                    <input
                      type="text"
                      value={formData.game_title}
                      onChange={(e) => handleChange('game_title', e.target.value)}
                      className="w-full p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass"
                      required
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-ink mb-1">Steam App ID</label>
                    <input
                      type="text"
                      value={formData.steam_app_id}
                      onChange={(e) => handleChange('steam_app_id', e.target.value)}
                      className="w-full p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass"
                    />
                  </div>
                </div>
              </div>

              {/* Steam Lookup */}
              <div className="card p-4 border border-rule rounded-lg mb-6">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold flex items-center gap-2">
                    <Search size={14} className="text-brass" />
                    Steam Lookup
                  </h3>
                  <span className="readout">auto-fill</span>
                </div>
                <p className="text-xs text-muted mb-3 leading-relaxed">
                  Search Steam by name, pick a result, and the profile fills in title, description, genres and tags.
                  Or paste a Steam App ID and hit Lookup.
                </p>

                <div className="flex gap-2 mb-2">
                  <input
                    type="text"
                    value={steamQuery}
                    onChange={(e) => setSteamQuery(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleSteamSearch(); } }}
                    placeholder="e.g. Elden Ring, Stardew Valley"
                    className="flex-1 p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass text-sm"
                  />
                  <button
                    type="button"
                    onClick={handleSteamSearch}
                    disabled={steamLoading}
                    className="btn-primary px-3 py-2 text-xs whitespace-nowrap disabled:opacity-50"
                  >
                    {steamLoading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
                    <span className="hidden sm:inline ml-1.5">{steamLoading ? 'Searching…' : 'Search'}</span>
                  </button>
                </div>

                <div className="flex gap-2 mb-3">
                  <button
                    type="button"
                    onClick={handleSteamAppIdLookup}
                    disabled={!!steamSelecting}
                    className="btn-quiet px-3 py-1.5 text-xs w-full justify-center"
                    title="Lookup the numeric App ID currently in the field below"
                  >
                    {steamSelecting ? <Loader2 size={12} className="animate-spin" /> : null}
                    Lookup by Steam App ID
                  </button>
                </div>

                {steamError && (
                  <div className="mb-3 p-2 bg-amber-50 border border-amber-200 rounded-lg text-amber-800 text-xs flex gap-2">
                    <AlertCircle size={14} className="shrink-0 mt-0.5" />
                    <span>{steamError}</span>
                  </div>
                )}

                {steamResults.length > 0 && (
                  <div className="border border-rule rounded-lg overflow-hidden max-h-56 overflow-y-auto custom-scrollbar">
                    {steamResults.map((r) => (
                      <button
                        key={r.app_id}
                        type="button"
                        onClick={() => handleSteamSelect(r)}
                        disabled={steamSelecting === r.app_id}
                        className="w-full flex items-center justify-between px-3 py-2.5 hover:bg-paper3 transition-colors text-left border-b border-rule last:border-0 disabled:opacity-50"
                      >
                        <div className="min-w-0">
                          <div className="text-sm font-medium text-ink truncate">{r.name}</div>
                          <div className="text-xs text-muted font-mono">App ID {r.app_id}</div>
                        </div>
                        <div className="ml-2 shrink-0">
                          {steamSelecting === r.app_id ? (
                            <Loader2 size={14} className="animate-spin text-brass" />
                          ) : String(formData.steam_app_id) === String(r.app_id) ? (
                            <Check size={14} className="text-ok" />
                          ) : (
                            <span className="text-xs text-brass">Use</span>
                          )}
                        </div>
                      </button>
                    ))}
                  </div>
                )}

                {steamResults.length > 0 && (
                  <p className="text-micro text-muted mt-2">Picking a result overwrites Title, Description, Genres and Tags — you can still edit them after.</p>
                )}
              </div>

              {/* Steam Info */}
              <div className="card p-4 border border-rule rounded-lg mb-6">
                <h3 className="font-semibold mb-3">Steam Information</h3>

                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-ink mb-1">Description</label>
                    <textarea
                      value={formData.steam_description}
                      onChange={(e) => handleChange('steam_description', e.target.value)}
                      rows={3}
                      className="w-full p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-ink mb-1">Genres</label>
                    {formData.steam_genres.map((genre, index) => (
                      <div key={index} className="flex gap-2 mb-2">
                        <input
                          type="text"
                          value={genre}
                          onChange={(e) => handleArrayChange('steam_genres', index, e.target.value)}
                          className="flex-1 p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass"
                        />
                        <button
                          type="button"
                          onClick={() => removeArrayItem('steam_genres', index)}
                          className="p-2 text-red-500 hover:bg-red-50 rounded-lg"
                        >
                          ×
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={() => addArrayItem('steam_genres')}
                      className="text-sm text-brass hover:text-brass-hover"
                    >
                      + Add Genre
                    </button>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-ink mb-1">Tags</label>
                    {formData.steam_tags.map((tag, index) => (
                      <div key={index} className="flex gap-2 mb-2">
                        <input
                          type="text"
                          value={tag}
                          onChange={(e) => handleArrayChange('steam_tags', index, e.target.value)}
                          className="flex-1 p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass"
                        />
                        <button
                          type="button"
                          onClick={() => removeArrayItem('steam_tags', index)}
                          className="p-2 text-red-500 hover:bg-red-50 rounded-lg"
                        >
                          ×
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={() => addArrayItem('steam_tags')}
                      className="text-sm text-brass hover:text-brass-hover"
                    >
                      + Add Tag
                    </button>
                  </div>
                </div>
              </div>

              {/* Custom Description — never overwritten by Steam lookup */}
              <div className="card p-4 border border-rule rounded-lg mb-6">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold">Your Notes</h3>
                  <span className="readout">custom</span>
                </div>
                <p className="text-xs text-muted mb-3 leading-relaxed">
                  Steam description above is auto-filled and never overwrites this. Use it for your own context that the scorer should consider.
                </p>
                <textarea
                  value={formData.custom_description}
                  onChange={(e) => handleChange('custom_description', e.target.value)}
                  rows={4}
                  placeholder="e.g. Focus on funny fails, streamer is very expressive, prioritize chat interaction…"
                  className="w-full p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass text-sm"
                />
                <p className="text-micro text-muted mt-1.5">Shown to AI analysis and used for future scoring tweaks (not sent to Steam).</p>
              </div>
            </div>

            {/* Right Column */}
            <div>
              {/* AI Analysis */}
              <div className="card p-4 border border-rule rounded-lg mb-6">
                <div className="flex items-center justify-between mb-3 gap-2">
                  <h3 className="font-semibold flex items-center gap-2">
                    AI Analysis
                    <span className="readout hidden sm:inline">{provider}</span>
                  </h3>
                  <div className="flex items-center gap-1">
                    <button type="button" onClick={() => setProvider('gemini')} className={provider === 'gemini' ? 'btn-primary px-2 py-1 text-micro' : 'btn-quiet px-2 py-1 text-micro'}>Gemini</button>
                    <button type="button" onClick={() => setProvider('openai')} className={provider === 'openai' ? 'btn-primary px-2 py-1 text-micro' : 'btn-quiet px-2 py-1 text-micro'}>OpenAI</button>
                  </div>
                </div>
                <p className="text-micro text-muted mb-2">Provider <code className="readout">{provider}</code> — uses Settings override if set.</p>
                <div className="flex items-center justify-between mb-3 gap-2">
                  <span className="text-sm text-muted">Classify & weight</span>
                  <button
                    type="button"
                    onClick={handleAiAnalyze}
                    disabled={aiAnalyzing || !formData.game_title?.trim()}
                    className="btn-primary px-3 py-1.5 text-xs whitespace-nowrap disabled:opacity-40"
                    title={!formData.game_title?.trim() ? 'Enter a Game Title first' : 'Generate game type, traits, key moments and recommended weights'}
                  >
                    {aiAnalyzing ? <Loader2 size={14} className="animate-spin" /> : <span className="text-[11px]">✦</span>}
                    <span className="ml-1.5">{aiAnalyzing ? 'Analyzing…' : 'Analyze with AI'}</span>
                  </button>
                </div>
                <p className="text-xs text-muted mb-3 leading-relaxed">
                  Uses Steam description/genres/tags to classify the game and suggest scoring weights. Requires a Gemini key (Settings).
                </p>
                {aiError && (
                  <div className="mb-3 p-2 bg-amber-50 border border-amber-200 rounded-lg text-amber-800 text-xs flex gap-2">
                    <AlertCircle size={14} className="shrink-0 mt-0.5" />
                    <span>{aiError}</span>
                  </div>
                )}

                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-ink mb-1">Game Type</label>
                    <input
                      type="text"
                      value={formData.ai_analysis.game_type}
                      onChange={(e) => handleNestedChange('ai_analysis', 'game_type', e.target.value)}
                      className="w-full p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-ink mb-1">Gameplay Characteristics</label>
                    {formData.ai_analysis.gameplay_characteristics.map((char, index) => (
                      <div key={index} className="flex gap-2 mb-2">
                        <input
                          type="text"
                          value={char}
                          onChange={(e) => handleArrayChange('ai_analysis.gameplay_characteristics', index, e.target.value)}
                          className="flex-1 p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass"
                        />
                        <button
                          type="button"
                          onClick={() => removeArrayItem('ai_analysis.gameplay_characteristics', index)}
                          className="p-2 text-red-500 hover:bg-red-50 rounded-lg"
                        >
                          ×
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={() => addArrayItem('ai_analysis.gameplay_characteristics')}
                      className="text-sm text-brass hover:text-brass-hover"
                    >
                      + Add Characteristic
                    </button>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-ink mb-1">Key Moments</label>
                    {formData.ai_analysis.key_moments.map((moment, index) => (
                      <div key={index} className="flex gap-2 mb-2">
                        <input
                          type="text"
                          value={moment}
                          onChange={(e) => handleArrayChange('ai_analysis.key_moments', index, e.target.value)}
                          className="flex-1 p-2 border border-rule rounded-lg bg-paper3 focus:ring-brass focus:border-brass"
                        />
                        <button
                          type="button"
                          onClick={() => removeArrayItem('ai_analysis.key_moments', index)}
                          className="p-2 text-red-500 hover:bg-red-50 rounded-lg"
                        >
                          ×
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      onClick={() => addArrayItem('ai_analysis.key_moments')}
                      className="text-sm text-brass hover:text-brass-hover"
                    >
                      + Add Key Moment
                    </button>
                  </div>
                </div>
              </div>

              {/* Weights */}
              <div className="card p-4 border border-rule rounded-lg">
                <h3 className="font-semibold mb-3">Weight Settings</h3>

                <div className="space-y-6">
                  <div>
                    <h4 className="font-medium mb-2">Recommended Weights (AI Suggestion)</h4>
                    <p className="text-sm text-muted mb-3">AI suggestion — click Analyze with AI to regenerate. Copy to Active with one click below.</p>
                    <div className="mb-3 flex gap-2">
                      <button
                        type="button"
                        onClick={() => setFormData(prev => ({ ...prev, active_weights: { ...prev.recommended_weights } }))}
                        className="btn-quiet px-3 py-1.5 text-xs"
                      >
                        Copy recommended → active
                      </button>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {Object.entries(formData.recommended_weights).map(([category, value]) => (
                        <div key={category} className="flex items-center justify-between p-2 bg-paper2 rounded-lg">
                          <span className="capitalize">{category.replace('_', ' ')}</span>
                          <span className="text-sm font-mono">{value.toFixed(2)}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div>
                    <h4 className="font-medium mb-2">Active Weights (Used in Scoring)</h4>
                    <p className="text-sm text-muted mb-3">These weights are used during semantic scoring.</p>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {Object.entries(formData.active_weights).map(([category, value]) => (
                        <div key={category} className="space-y-2">
                          <label className="block text-sm capitalize">{category.replace('_', ' ')}</label>
                          <input
                            type="range"
                            min="0"
                            max="1"
                            step="0.01"
                            value={value}
                            onChange={(e) => handleWeightChange(category, e.target.value)}
                            className="w-full h-2 rounded-lg appearance-none cursor-pointer accent-[var(--color-accent)]"
                            style={{ background: `linear-gradient(to right, var(--color-accent) 0%, var(--color-accent) ${value * 100}%, var(--color-paper-3) ${value * 100}%, var(--color-paper-3) 100%)` }}
                          />
                          <div className="flex justify-between text-xs">
                            <span>0.0</span>
                            <span className="font-mono">{value.toFixed(2)}</span>
                            <span>1.0</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Submit Button */}
          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 border border-rule rounded-lg hover:bg-paper3 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 bg-brass text-white rounded-lg hover:bg-brass-hover transition-colors disabled:opacity-50"
            >
              {loading ? 'Saving...' : (profile ? 'Update Profile' : 'Create Profile')}
            </button>
          </div>
        </form>
      </div>
    </Modal>
  );
}
