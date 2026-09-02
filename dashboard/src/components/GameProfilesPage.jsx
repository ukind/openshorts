import React, { useState, useEffect, useRef } from 'react';
import { Plus, Edit3, Copy, Trash2, Loader2, Download, Upload } from 'lucide-react';
import Modal from './ui/Modal';
import CreateEditProfileModal from './CreateEditProfileModal';
import { apiJson } from '../lib/api';

export default function GameProfilesPage() {
  const [profiles, setProfiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingProfile, setEditingProfile] = useState(null);
  const [editLoading, setEditLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState(null);
  const fileInputRef = useRef(null);

  // Load profiles on component mount
  useEffect(() => {
    loadProfiles();
  }, []);

  const loadProfiles = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await apiJson('/api/game-profiles');
      setProfiles(data);
    } catch (err) {
      setError(err.message || 'Failed to load game profiles');
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    try {
      setError(null); setImportMsg(null);
      const data = await apiJson('/api/game-profiles/export');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const stamp = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `openshorts-game-profiles-${stamp}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setImportMsg(`Exported ${data.count} profiles`);
      setTimeout(() => setImportMsg(null), 3000);
    } catch (err) {
      setError(err.message || 'Failed to export');
    }
  };

  const handleImportFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true); setError(null); setImportMsg(null);
    try {
      const text = await file.text();
      const json = JSON.parse(text);
      // Normalize to {profiles: [...]}
      const body = Array.isArray(json) ? { profiles: json } : json.profiles ? json : { profiles: [json] };
      const result = await apiJson('/api/game-profiles/import', { method: 'POST', body });
      const parts = [];
      if (result.imported) parts.push(`${result.imported} imported`);
      if (result.skipped?.length) parts.push(`${result.skipped.length} skipped`);
      if (result.errors?.length) parts.push(`${result.errors.length} errors`);
      setImportMsg(parts.join(' · ') || 'Import complete');
      await loadProfiles();
    } catch (err) {
      setError(err.message || 'Failed to import — check file format');
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
      setTimeout(() => setImportMsg(null), 4000);
    }
  };

  const handleDelete = async (profileId) => {
    if (!window.confirm('Are you sure you want to delete this game profile?')) {
      return;
    }

    try {
      await apiJson(`/api/game-profiles/${profileId}`, { method: 'DELETE' });
      // Refresh the list
      await loadProfiles();
    } catch (err) {
      setError(err.message || 'Failed to delete game profile');
    }
  };

  const handleDuplicate = async (profileId) => {
    try {
      await apiJson(`/api/game-profiles/${profileId}/duplicate`, { method: 'POST' });
      // Refresh the list
      await loadProfiles();
    } catch (err) {
      setError(err.message || 'Failed to duplicate game profile');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="animate-spin" size={32} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
        Error: {error}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-bold text-ink">Game Profiles</h1>
          {editLoading && <Loader2 size={18} className="animate-spin text-muted" />}
        </div>
        <div className="flex items-center gap-2">
          <input ref={fileInputRef} type="file" accept=".json,application/json" onChange={handleImportFile} className="hidden" />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="flex items-center gap-1.5 bg-paper2 border border-rule text-ink px-3 py-2 rounded-lg hover:bg-paper3 transition-colors text-sm disabled:opacity-50"
            title="Import profiles from JSON"
          >
            {importing ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            {importing ? 'Importing…' : 'Import'}
          </button>
          <button
            onClick={handleExport}
            className="flex items-center gap-1.5 bg-paper2 border border-rule text-ink px-3 py-2 rounded-lg hover:bg-paper3 transition-colors text-sm"
            title="Export all profiles to JSON"
          >
            <Upload size={14} />
            Export
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 bg-brass text-white px-4 py-2 rounded-lg hover:bg-brass-hover transition-colors"
          >
            <Plus size={16} />
            Create Profile
          </button>
        </div>
      </div>
      {importMsg && (
        <div className="px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-lg text-emerald-800 text-sm flex items-center gap-2">
          <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse" />
          {importMsg}
        </div>
      )}

      {profiles.length === 0 ? (
        <div className="text-center py-12 border border-dashed border-rule rounded-lg">
          <p className="text-ink2">No game profiles yet. Create your first profile to get started.</p>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {profiles.map(profile => (
            <div key={profile.id} className="card p-4 border border-rule rounded-lg">
              <div className="flex justify-between items-start mb-3">
                <h2 className="font-semibold text-lg">{profile.name}</h2>
                <div className="flex gap-1">
                  <button
                    onClick={async () => {
                      setEditLoading(true);
                      try {
                        const full = await apiJson(`/api/game-profiles/${profile.id}`);
                        setEditingProfile(full);
                      } catch (e) {
                        setError(e.message || 'Failed to load profile');
                      } finally { setEditLoading(false); }
                    }}
                    disabled={editLoading}
                    className="p-1.5 rounded hover:bg-paper3 transition-colors disabled:opacity-50"
                    title="Edit"
                  >
                    <Edit3 size={16} />
                  </button>
                  <button
                    onClick={() => handleDuplicate(profile.id)}
                    className="p-1.5 rounded hover:bg-paper3 transition-colors"
                    title="Duplicate"
                  >
                    <Copy size={16} />
                  </button>
                  <button
                    onClick={async () => {
                      try {
                        const full = await apiJson(`/api/game-profiles/${profile.id}`);
                        const blob = new Blob([JSON.stringify({ version: 1, count: 1, profiles: [full] }, null, 2)], { type: 'application/json' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        const safe = (full.name || full.game_title || 'profile').replace(/[^a-z0-9_-]/gi, '_').slice(0, 40);
                        a.href = url; a.download = `${safe}.json`;
                        document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
                      } catch (e) { setError(e.message || 'Failed to export profile'); }
                    }}
                    className="p-1.5 rounded hover:bg-paper3 transition-colors"
                    title="Export single profile"
                  >
                    <Upload size={16} />
                  </button>
                  <button
                    onClick={() => handleDelete(profile.id)}
                    className="p-1.5 rounded hover:bg-paper3 transition-colors text-red-500"
                    title="Delete"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              <div className="space-y-2 text-sm">
                <p><span className="text-muted">Game:</span> {profile.game_title}</p>
                {profile.custom_description && (
                  <p className="text-xs text-muted line-clamp-2" title={profile.custom_description}>
                    <span className="text-muted">Notes:</span> {profile.custom_description.slice(0, 120)}{profile.custom_description.length > 120 ? '…' : ''}
                  </p>
                )}
                {profile.steam_description && !profile.custom_description && (
                  <p className="text-xs text-muted line-clamp-2" title={profile.steam_description}>
                    <span className="text-muted">Steam:</span> {profile.steam_description.slice(0, 120)}…
                  </p>
                )}
                <p><span className="text-muted">ID:</span> {profile.id.substring(0, 8)}…{profile.id.slice(-4)}</p>
                {profile.active_weights && (
                  <div>
                    <p className="text-muted">Active Weights:</p>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {Object.entries(profile.active_weights)
                        .filter(([_, value]) => value > 0)
                        .map(([category, weight]) => (
                          <span key={category} className="bg-paper2 text-xs px-2 py-1 rounded">
                            {category.replace('_', ' ')}: {weight.toFixed(2)}
                          </span>
                        ))}
                    </div>
                  </div>
                )}
                <p><span className="text-muted">Created:</span> {new Date(profile.created_at).toLocaleDateString()}</p>
                <p><span className="text-muted">Updated:</span> {new Date(profile.updated_at).toLocaleDateString()}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create/Edit Modal */}
      <CreateEditProfileModal
        isOpen={showCreateModal || !!editingProfile}
        onClose={() => {
          setShowCreateModal(false);
          setEditingProfile(null);
        }}
        profile={editingProfile}
        onSave={loadProfiles}
      />
    </div>
  );
}
