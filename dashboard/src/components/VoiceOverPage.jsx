import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  Mic, Type, Upload, Loader2, AlertCircle, Check, Film, Pencil, Save, X,
  Gamepad2, Wand2, Play, RotateCcw, Download, Terminal, ChevronDown, Globe, Plus,
} from 'lucide-react';
import SubtitleModal from './SubtitleModal';
import RemotionPreview from './RemotionPreview';
import { apiFetch, apiJson } from '../lib/api';
import { aiProviderSet, llmHeaders, openaiHeaders } from '../lib/llm';
import { getApiUrl } from '../config';

// VoiceOver workflow page: source → caption options (4) → select/edit (with
// on-the-fly Remotion caption preview) → TTS → duck/mix → _voiceover.mp4 →
// caption burn → _voiceover_captioned.mp4, with an "Edit Subtitles" result
// card that reuses the existing SubtitleModal.

const POLL_MS = 1200;

// Session persistence (like the Clip Generator): the whole workflow context
// survives a page refresh. Server files live ~1h, matching that retention.
const VO_SESSION_KEY = 'openshorts_voiceover_session';
const VO_SESSION_MAX_AGE = 3600000;

const saveVoSession = (data) => {
  try {
    localStorage.setItem(VO_SESSION_KEY, JSON.stringify({ ...data, timestamp: Date.now() }));
  } catch { /* quota — non-critical */ }
};
const loadVoSession = () => {
  try {
    const raw = localStorage.getItem(VO_SESSION_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw);
    if (Date.now() - (session.timestamp || 0) > VO_SESSION_MAX_AGE) {
      localStorage.removeItem(VO_SESSION_KEY);
      return null;
    }
    return session;
  } catch { return null; }
};
const clearVoSession = () => { localStorage.removeItem(VO_SESSION_KEY); };

// Same full-name map Qwen TTS uses — the dropdown sends codes; backend resolves.
const LANGUAGES = [
  { value: 'auto', label: 'Auto-detect (from clip)' },
  { value: 'en', label: 'English' }, { value: 'it', label: 'Italian' },
  { value: 'es', label: 'Spanish' }, { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' }, { value: 'pt', label: 'Portuguese' },
  { value: 'ru', label: 'Russian' }, { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' }, { value: 'zh', label: 'Chinese' },
];

// Burn-in defaults must match the backend's caption style so the preview
// approximates what will be rendered into _voiceover_captioned.mp4.
const BURN_STYLE = {
  fontFamily: 'Anton', fontSize: 20, fontColor: '#FFFFFF',
  highlightColor: '#FFE500', borderColor: '#000000', borderWidth: 6,
  bgColor: '#000000', bgOpacity: 0, animation: 'pop',
  baseOpacity: 1, uppercase: true, marginV: 43, wordGap: 8,
  lineHeight: 1, letterSpacing: 0,
};

function stageLabel(stage) {
  if (!stage) return 'Getting things ready…';
  if (stage === 'preparing') return 'Preparing your video…';
  if (stage === 'tts') return 'Recording the narration…';
  if (stage === 'resuming') return 'Checking progress…';
  if (stage === 'mixing') return 'Mixing narration with the game audio…';
  if (stage === 'captions') return 'Adding animated captions…';
  if (stage === 'saving') return 'Saving your videos…';
  if (stage === 'done') return 'Done!';
  if (stage.startsWith('voice')) {
    const seg = stage.split(' ')[1] || '';
    return seg ? `Recording line ${seg} of your script…` : 'Recording the narration…';
  }
  return stage;
}

export default function VoiceOverPage({
  presetSource,
  onClearPresetSource,
  aiProvider,
  geminiApiKey,
  geminiModel,
  aiProviderConfig,
  elevenLabsKey,
  results,
  jobId,
  gameProfiles,
}) {
  // --- source selection -----------------------------------------------------
  const [sourceMode, setSourceMode] = useState('upload'); // upload | clip
  const [uploadId, setUploadId] = useState(null);
  const [uploadName, setUploadName] = useState('');
  const [uploadDuration, setUploadDuration] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [selectedClipIndex, setSelectedClipIndex] = useState(null);
  const fileInputRef = useRef(null);

  // Apply hand-offs: a ResultCard click (preselect the clip's raw version) or
  // a History reopen of a voiceover project (restore its result for editing).
  const handledPresetRef = useRef(null);
  useEffect(() => {
    if (!presetSource || handledPresetRef.current === presetSource) return;
    handledPresetRef.current = presetSource;
    if (presetSource.restored) {
      const r = presetSource.restored.result || {};
      setResult(r);
      setVoId(presetSource.jobId || null);
      setVoJob({ status: 'completed', stage: 'done', progress: 100, logs: [] });
      // Restore the caption suggestions the user had selected/edited, so the
      // workflow can be re-run or tweaked without regenerating from scratch.
      if (r.captions?.length) {
        setOptions([{ label: 'Reopened script', captions: r.captions.map((c) => ({ ...c })) }]);
        setSelectedOption(0);
        setEditedCaptions(r.captions.map((c) => ({ ...c })));
      }
      pushLog(`Voiceover project reopened from your library`
        + (r.captions?.length ? ` — your ${r.captions.length} captions are ready to re-run.` : '.'));
      onClearPresetSource?.();
      return;
    }
    if (results?.clips?.length && presetSource.clipIndex < results.clips.length) {
      setSourceMode('clip');
      setSelectedClipIndex(presetSource.clipIndex);
      onClearPresetSource?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetSource, results]);

  const source = sourceMode === 'upload' && uploadId
    ? { upload_id: uploadId }
    : (sourceMode === 'clip' && jobId && selectedClipIndex != null
        ? { job_id: jobId, clip_index: selectedClipIndex }
        : null);

  const sourceReady = !!source;

  // --- providers / presets ----------------------------------------------------
  const [captionProvider, setCaptionProvider] = useState(aiProvider === 'openai' ? 'openai' : 'gemini');
  const [voiceProvider, setVoiceProvider] = useState('elevenlabs');
  const [ttsStatus, setTtsStatus] = useState(null);

  const [voicePresets, setVoicePresets] = useState([]);
  const [stylePresets, setStylePresets] = useState([]);
  const [voicePresetId, setVoicePresetId] = useState('');
  const [stylePresetId, setStylePresetId] = useState('');
  const [voicePrompt, setVoicePrompt] = useState('');
  const [voicePromptDirty, setVoicePromptDirty] = useState(false);
  const [stylePrompt, setStylePrompt] = useState('');
  const [stylePromptDirty, setStylePromptDirty] = useState(false);
  const [editingVoice, setEditingVoice] = useState(false);
  const [editingStyle, setEditingStyle] = useState(false);
  const [savingPreset, setSavingPreset] = useState(false);
  const [elVoices, setElVoices] = useState([]);
  const [elVoiceId, setElVoiceId] = useState('');

  // --- language ------------------------------------------------------------------
  const [language, setLanguage] = useState('auto');
  const [resolvedLanguage, setResolvedLanguage] = useState(null);

  // --- game profile -----------------------------------------------------------
  const [gameProfileId, setGameProfileId] = useState('');

  // --- caption options ---------------------------------------------------------
  const [options, setOptions] = useState(null);
  const [selectedOption, setSelectedOption] = useState(null);
  const [editedCaptions, setEditedCaptions] = useState(null);
  const [generatingCaptions, setGeneratingCaptions] = useState(false);
  const [showCaptionPreview, setShowCaptionPreview] = useState(true);
  const [error, setError] = useState(null);

  // --- source video URL (raw, for caption preview) --------------------------------
  const sourceVideoUrl = useMemo(() => {
    if (sourceMode === 'upload' && uploadId) return getApiUrl(`/api/voiceover/upload/${uploadId}`);
    if (sourceMode === 'clip' && jobId && selectedClipIndex != null && results?.clips?.[selectedClipIndex]) {
      // Preview the RAW clip — strip subtitled_/hook_ prefixes like ResultCard does
      const url = results.clips[selectedClipIndex].video_url || '';
      let f = url.split('/').pop() || '';
      let prev;
      do { prev = f; f = f.replace(/^subtitled_\d+_/, '').replace(/^hook_/, ''); } while (f !== prev);
      return getApiUrl(`/videos/${jobId}/${f}`);
    }
    return null;
  }, [sourceMode, uploadId, jobId, selectedClipIndex, results]);

  const durationSec = sourceMode === 'upload' ? uploadDuration
    : (results?.clips?.[selectedClipIndex]
        ? (results.clips[selectedClipIndex].end - results.clips[selectedClipIndex].start) || 30
        : 30);

  // --- voiceover job -----------------------------------------------------------
  const [voId, setVoId] = useState(null);
  const [voJob, setVoJob] = useState(null);
  const [result, setResult] = useState(null);
  const [showSubtitleModal, setShowSubtitleModal] = useState(false);
  const [isRestyling, setIsRestyling] = useState(false);
  const [logsVisible, setLogsVisible] = useState(true);
  // Unified log feed: client-side lines for steps 1-3 (source, caption
  // generation) + server-side lines from the voiceover job. Timestamp format
  // matches the Clip Generator terminal.
  const [clientLogs, setClientLogs] = useState([]);
  const clientLogsRef = useRef([]);
  const pushLog = useCallback((msg) => {
    const line = `${new Date().toTimeString().slice(0, 8)} ${msg}`;
    clientLogsRef.current = [...clientLogsRef.current, line];
    setClientLogs(clientLogsRef.current);
  }, []);

  // --- data loading --------------------------------------------------------------
  useEffect(() => {
    apiJson('/api/voiceover/tts/status').then(setTtsStatus).catch(() => setTtsStatus({ available: false }));
    apiJson('/api/voice-presets').then((v) => {
      setVoicePresets(v);
      // Remember the last voice selection across visits; validate it still exists.
      const savedVoice = localStorage.getItem('vo_last_voice_preset');
      const picked = v.find((p) => p.id === savedVoice) || v[0];
      if (picked) {
        setVoicePresetId(picked.id);
        setVoicePrompt(picked.prompt);
      }
    }).catch(() => {});
    apiJson('/api/style-presets').then((s) => {
      setStylePresets(s);
      const savedStyle = localStorage.getItem('vo_last_style_preset');
      const picked = s.find((p) => p.id === savedStyle) || s[0];
      if (picked) {
        setStylePresetId(picked.id);
        setStylePrompt(picked.prompt);
      }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (voiceProvider === 'elevenlabs' && elevenLabsKey && elVoices.length === 0) {
      apiFetch('/api/saasshorts/voices', { headers: { 'X-ElevenLabs-Key': elevenLabsKey } })
        .then((r) => (r.ok ? r.json() : []))
        .then((voices) => {
          setElVoices(voices);
          if (voices.length && !elVoiceId) setElVoiceId(voices[0].voice_id);
        })
        .catch(() => {});
    }
  }, [voiceProvider, elevenLabsKey, elVoices.length, elVoiceId]);

  useEffect(() => {
    if (ttsStatus?.available) setVoiceProvider('qwen');
  }, [ttsStatus]);

  const selectVoicePreset = (id, { remember = true } = {}) => {
    const p = voicePresets.find((v) => v.id === id);
    setVoicePresetId(id);
    setVoicePrompt(p ? p.prompt : '');
    setVoicePromptDirty(false);
    setEditingVoice(false);
    if (remember && id) localStorage.setItem('vo_last_voice_preset', id);
  };

  const selectStylePreset = (id, { remember = true, autoselectVoice = false } = {}) => {
    const p = stylePresets.find((s) => s.id === id);
    setStylePresetId(id);
    setStylePrompt(p ? p.prompt : '');
    setStylePromptDirty(false);
    setEditingStyle(false);
    if (remember && id) localStorage.setItem('vo_last_style_preset', id);
    // Selecting a style auto-picks the first voice tagged with it (unless the
    // current voice already matches).
    if (autoselectVoice && id) {
      const current = voicePresets.find((v) => v.id === voicePresetId);
      if (!current || !(current.tags || []).includes(id)) {
        const match = voicePresets.find((v) => (v.tags || []).includes(id));
        if (match) selectVoicePreset(match.id);
      }
    }
  };

  const savePresetBack = async (kind) => {
    setSavingPreset(true);
    try {
      const id = kind === 'voice' ? voicePresetId : stylePresetId;
      const prompt = kind === 'voice' ? voicePrompt : stylePrompt;
      const title = (kind === 'voice' ? voicePresets : stylePresets).find((p) => p.id === id)?.title || 'Preset';
      await apiJson(`/api/${kind}-presets/${id}`, { method: 'PUT', body: { title, prompt } });
      if (kind === 'voice') {
        setVoicePresets((prev) => prev.map((p) => (p.id === id ? { ...p, prompt } : p)));
        setVoicePromptDirty(false);
      } else {
        setStylePresets((prev) => prev.map((p) => (p.id === id ? { ...p, prompt } : p)));
        setStylePromptDirty(false);
      }
    } catch (e) {
      setError(e.detail || e.message || 'Failed to save preset');
    } finally {
      setSavingPreset(false);
    }
  };

  // --- upload ----------------------------------------------------------------------
  const handleUploadFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await doUpload(file);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const doUpload = async (file) => {
    setUploading(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const data = await apiJson('/api/voiceover/upload', { method: 'POST', body: fd });
      setUploadId(data.upload_id);
      setUploadName(data.filename);
      setUploadDuration(data.duration);
      pushLog(`Video loaded — ${data.filename} (${(data.duration || 0).toFixed(1)}s). Pick a style preset, then generate captions.`);
      resetPipeline();
    } catch (err) {
      setError(err.detail || err.message || 'Upload failed');
      pushLog(`The upload didn't go through — ${err.detail || err.message || 'please try again'}`);
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    const file = e.dataTransfer?.files?.[0];
    if (file) doUpload(file);
  };

  const resetPipeline = () => {
    setOptions(null);
    setSelectedOption(null);
    setEditedCaptions(null);
    setVoId(null);
    setVoJob(null);
    setResult(null);
    setShowSubtitleModal(false);
    setResolvedLanguage(null);
  };

  // --- caption generation -------------------------------------------------------------
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

  const generateCaptions = async () => {
    if (!sourceReady) return;
    if (captionProvider === 'gemini' && !geminiApiKey) {
      setError('Add your Gemini API key in Settings first.');
      return;
    }
    if (captionProvider === 'openai' && !aiProviderSet(aiProviderConfig)) {
      // D10: a base URL alone is a runnable keyless local endpoint; the old
      // key-only gate blocked exactly those.
      setError('Set your AI provider in Settings first.');
      return;
    }
    setGeneratingCaptions(true);
    setError(null);
    setOptions(null);
    setSelectedOption(null);
    setEditedCaptions(null);
    pushLog(`Watching the video and writing 4 caption scripts with ${captionProvider === 'gemini' ? 'Gemini' : 'OpenAI'} — this usually takes under a minute…`);
    try {
      const profile = (gameProfiles || []).find((p) => p.id === gameProfileId);
      let game_title = '';
      let game_description = '';
      if (profile) {
        try {
          const full = await apiJson(`/api/game-profiles/${profile.id}`);
          game_title = full.game_title || full.name || '';
          game_description = full.custom_description || full.steam_description || '';
        } catch { /* profile context is optional */ }
      }
      const data = await apiJson('/api/voiceover/captions', {
        method: 'POST',
        headers: byokHeaders(),
        body: {
          source,
          provider: captionProvider,
          style_prompt: stylePrompt,
          game_title,
          game_description,
          language,
        },
      });
      setOptions(data.options);
      setResolvedLanguage(data.language || null);
      pushLog(`${data.options?.length || 0} caption scripts ready in ${data.language || 'your language'} — pick the one you like and tweak the text.`);
      if (data.options?.length) {
        setSelectedOption(0);
        setEditedCaptions(data.options[0].captions.map((c) => ({ ...c })));
        pushLog(`Option 1 pre-selected (${data.options[0].captions.length} captions) — edit it on the right or pick another.`);
      }
    } catch (err) {
      setError(err.detail || err.message || 'Caption generation failed');
      pushLog(`Caption generation failed — ${err.detail || err.message || 'check your API key in Settings and try again'}`);
    } finally {
      setGeneratingCaptions(false);
    }
  };

  const selectOption = (idx) => {
    setSelectedOption(idx);
    setEditedCaptions((options[idx].captions || []).map((c) => ({ ...c })));
  };

  const editCaption = (i, field, value) => {
    setEditedCaptions((prev) => prev.map((c, j) => (j === i ? { ...c, [field]: value } : c)));
  };

  // Live Remotion preview config from the currently selected/edited captions.
  // The AI may return overlapping time ranges, which would stack blocks in the
  // preview; the actual burn re-times everything sequentially from the TTS
  // audio, so the preview mirrors that: captions are flattened to a
  // non-overlapping word timeline (max ~2.5s per block, like the burn style).
  const previewConfig = useMemo(() => {
    if (!editedCaptions?.length) return null;
    const words = [];
    let cursor = 0;
    for (const c of editedCaptions) {
      const text = (c.text || '').trim();
      if (!text) continue;
      const start = Math.max(Number(c.start) || 0, cursor);
      const end = Math.max(Number(c.end) || 0, start + 1.2);
      const toks = text.split(/\s+/).filter(Boolean);
      const span = end - start;
      const weights = toks.map((w) => w.length + 1);
      const totalW = weights.reduce((a, b) => a + b, 0) || 1;
      let wc = start;
      for (const w of toks) {
        const share = span * (w.length + 1) / totalW;
        words.push({ text: w, startMs: Math.round(wc * 1000), endMs: Math.round((wc + share) * 1000) });
        wc += share;
      }
      cursor = end + 0.25;
    }
    return {
      captions: words,
      position: 'bottom',
      maxChars: 16,
      maxDuration: 2500,
      style: {
        ...BURN_STYLE,
        borderWidth: BURN_STYLE.borderWidth * 1.5,
        animation: 'pop',
        baseOpacity: 1,
        uppercase: BURN_STYLE.uppercase,
        marginV: BURN_STYLE.marginV,
        wordGap: BURN_STYLE.wordGap,
        lineHeight: BURN_STYLE.lineHeight,
        letterSpacing: BURN_STYLE.letterSpacing,
      },
    };
  }, [editedCaptions]);

  // --- full pipeline ---------------------------------------------------------------------
  const startVoiceover = async () => {
    if (!sourceReady || !editedCaptions?.length) return;
    if (voiceProvider === 'elevenlabs' && !elevenLabsKey) {
      setError('Add your ElevenLabs API key in Settings first.');
      return;
    }
    setError(null);
    setResult(null);
    try {
      const data = await apiJson('/api/voiceover/generate', {
        method: 'POST',
        headers: {
          ...(voiceProvider === 'elevenlabs' && elevenLabsKey
            ? { 'X-ElevenLabs-Key': elevenLabsKey } : {}),
        },
        body: {
          source,
          captions: editedCaptions,
          voice_provider: voiceProvider,
          voice_prompt: voicePrompt,
          voice_preset_id: voicePresetId,
          elevenlabs_voice_id: elVoiceId,
          language,
          style: {},
        },
      });
      setVoId(data.vo_id);
      setVoJob({ status: 'processing', stage: 'preparing', progress: 0, logs: [] });
      pushLog(`Recording the voiceover in ${language}… keep this page open; you can switch tabs and come back.`);
    } catch (err) {
      setError(err.detail || err.message || 'VoiceOver generation failed to start');
      pushLog(`Couldn't start the voiceover — ${err.detail || err.message || 'please try again'}`);
    }
  };

  // Poll the background job (progress, stage, logs)
  useEffect(() => {
    if (!voId || voJob?.status === 'completed' || voJob?.status === 'error') return;
    let cancelled = false;
    const tick = async () => {
      try {
        const job = await apiJson(`/api/voiceover/jobs/${voId}`);
        if (cancelled) return;
        setVoJob(job);
        if (job.status === 'completed') setResult(job.result);
        else if (job.status === 'error') setError(job.error || 'VoiceOver generation failed');
      } catch { /* transient poll failure — keep trying */ }
    };
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [voId, voJob?.status]);

  const logs = useMemo(() => {
    const server = voJob?.logs || [];
    // Merge client + server lines chronologically by their HH:MM:SS prefix
    const all = [...clientLogs, ...server];
    return all.sort((a, b) => {
      const ta = a.match(/^(\d{2}:\d{2}:\d{2})/)?.[1] || '';
      const tb = b.match(/^(\d{2}:\d{2}:\d{2})/)?.[1] || '';
      return ta.localeCompare(tb);
    });
  }, [clientLogs, voJob?.logs]);

  // --- session persistence (refresh recovery) -------------------------------
  // Persist the workflow context; on reload the job poll resumes automatically
  // because restoring voId re-arms the polling effect below.
  useEffect(() => {
    saveVoSession({
      sourceMode, uploadId, uploadName, uploadDuration, selectedClipIndex,
      options, selectedOption, editedCaptions, language, captionProvider,
      voiceProvider, voId, clientLogs: clientLogsRef.current,
      result: result ? { ...result } : null,
    });
  }, [sourceMode, uploadId, uploadName, uploadDuration, selectedClipIndex,
    options, selectedOption, editedCaptions, language, captionProvider,
    voiceProvider, voId, result, clientLogs]);

  // Restore once on mount, before the first poll tick can double-fire.
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    const s = loadVoSession();
    if (!s) return;
    try {
      if (s.uploadId) { setUploadId(s.uploadId); setUploadName(s.uploadName || ''); setUploadDuration(s.uploadDuration || 0); }
      if (s.sourceMode) setSourceMode(s.sourceMode);
      if (s.selectedClipIndex != null) setSelectedClipIndex(s.selectedClipIndex);
      if (s.options?.length) setOptions(s.options);
      if (s.selectedOption != null) setSelectedOption(s.selectedOption);
      if (s.editedCaptions?.length) setEditedCaptions(s.editedCaptions);
      if (s.language) setLanguage(s.language);
      if (s.captionProvider) setCaptionProvider(s.captionProvider);
      if (s.voiceProvider) setVoiceProvider(s.voiceProvider);
      if (s.clientLogs?.length) { clientLogsRef.current = s.clientLogs; setClientLogs(s.clientLogs); }
      if (s.result) setResult(s.result);
      if (s.voId) {
        setVoId(s.voId);
        setVoJob({ status: 'processing', stage: 'resuming', progress: 0 });
        pushLog('Welcome back — your voiceover is still being generated.');
      } else if (s.options?.length || s.uploadId) {
        pushLog('Welcome back — your work in progress was restored.');
      }
    } catch { /* malformed session — start fresh */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // "New Project": clear the session and reset the whole workflow.
  const handleNewProject = () => {
    clearVoSession();
    clientLogsRef.current = [];
    setClientLogs([]);
    setSourceMode('upload');
    setUploadId(null);
    setUploadName('');
    setUploadDuration(0);
    setSelectedClipIndex(null);
    resetPipeline();
    pushLog('Started a fresh voiceover project.');
  };
  // Keep the logs terminal pinned to the newest line as it grows
  const logsContainerRef = useCallback((el) => {
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // --- subtitle re-style (existing SubtitleModal on the voiceover job) ----------------------
  const restyleSubtitles = async (options) => {
    if (!result?.job_id) return;
    setIsRestyling(true);
    setError(null);
    try {
      const payload = {
        job_id: result.job_id,
        clip_index: 0,
        position: options.position,
        font_size: options.fontSize,
        font_name: options.fontName,
        font_color: options.fontColor,
        border_color: options.borderColor,
        border_width: options.borderWidth,
        bg_color: options.bgColor,
        bg_opacity: options.bgOpacity,
        style: options.style || 'classic',
        highlight_color: options.highlightColor || '#FFD700',
        effect: options.effect || 'none',
        base_opacity: options.baseOpacity ?? 1.0,
        uppercase: options.uppercase || false,
        margin_v: options.marginV,
        word_gap: options.wordGap,
        max_chars: options.maxChars,
        max_duration: options.maxDuration,
        line_height: options.lineHeight,
        letter_spacing: options.letterSpacing,
        input_filename: null,
      };
      if (options.remotion?.captions?.length) {
        payload.captions = options.remotion.captions;
      }
      const res = await apiFetch('/api/subtitle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      if (data.new_video_url) {
        setResult((prev) => ({ ...prev, captioned_url: data.new_video_url }));
        setShowSubtitleModal(false);
      }
    } catch (e) {
      setError(e.message || 'Subtitle re-render failed');
    } finally {
      setIsRestyling(false);
    }
  };

  const busy = !!voId && voJob?.status === 'processing';
  const progress = voJob?.progress ?? 0;

  // Style-aware voice ordering: voices tagged with the selected style come
  // first under a highlighted group, everything else follows.
  const selectedStyle = stylePresets.find((s) => s.id === stylePresetId);
  const matchingVoices = selectedStyle
    ? voicePresets.filter((v) => (v.tags || []).includes(selectedStyle.id))
    : [];
  const otherVoices = selectedStyle
    ? voicePresets.filter((v) => !(v.tags || []).includes(selectedStyle.id))
    : voicePresets;

  // Style dropdown groups presets by their group tag (kind).
  const styleGroups = useMemo(() => {
    const groups = new Map();
    for (const s of stylePresets) {
      const k = s.kind || 'generic';
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(s);
    }
    return [...groups.entries()];
  }, [stylePresets]);

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ink">VoiceOver</h1>
          <p className="text-sm text-muted mt-1">
            Turn a clip into a narrated short: captions → TTS voiceover → ducked mix → burned captions.
          </p>
        </div>
        {(voId || uploadId || options) && (
          <button
            onClick={handleNewProject}
            disabled={busy}
            title={busy ? 'Wait for the current job to finish' : 'Cancel the current work and start over'}
            className="btn-quiet px-3 py-1.5 text-xs shrink-0 disabled:opacity-50"
          >
            <Plus size={14} />
            <span className="hidden sm:inline">New Project</span>
          </button>
        )}
      </div>

      {error && (
        <div className="px-4 py-3 rounded-input text-sm text-danger bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] flex items-start gap-2">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <span className="break-words flex-1">{error}</span>
          <button onClick={() => setError(null)} className="shrink-0 hover:text-ink"><X size={14} /></button>
        </div>
      )}

      {/* 1. Source */}
      <section className="card border border-rule rounded-lg p-4 space-y-3">
        <h2 className="eyebrow">1 · source video</h2>
        <div className="flex gap-2">
          <button
            onClick={() => { setSourceMode('upload'); resetPipeline(); }}
            className={`flex items-center gap-2 px-3 py-2 rounded-input text-sm border transition-colors ${sourceMode === 'upload' ? 'border-brass text-brass bg-paper3/50' : 'border-rule text-muted hover:text-ink2'}`}
          >
            <Upload size={15} /> upload mp4
          </button>
          {results?.clips?.length > 0 && (
            <button
              onClick={() => { setSourceMode('clip'); resetPipeline(); }}
              className={`flex items-center gap-2 px-3 py-2 rounded-input text-sm border transition-colors ${sourceMode === 'clip' ? 'border-brass text-brass bg-paper3/50' : 'border-rule text-muted hover:text-ink2'}`}
            >
              <Film size={15} /> generated clip
            </button>
          )}
        </div>

        {sourceMode === 'upload' && (
          <div
            className={`border-2 border-dashed rounded-card p-6 sm:p-8 text-center transition-colors ${uploadId ? 'border-brass' : 'border-rule2 hover:border-brass'}`}
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
          >
            {uploadId ? (
              <div className="flex items-center justify-center gap-3 text-ok min-w-0">
                <Film size={18} className="shrink-0" />
                <span className="font-medium truncate">{uploadName}</span>
                <span className="readout shrink-0">{uploadDuration?.toFixed?.(1)}s</span>
                <button
                  type="button"
                  onClick={() => { setUploadId(null); setUploadName(''); setUploadDuration(0); resetPipeline(); }}
                  className="p-1 text-muted hover:text-ink hover:bg-paper3 rounded-full transition-colors"
                >
                  <X size={16} />
                </button>
              </div>
            ) : (
              <label className="cursor-pointer block">
                <input ref={fileInputRef} type="file" accept="video/mp4,video/quicktime" onChange={handleUploadFile} className="hidden" />
                {uploading
                  ? <Loader2 className="mx-auto mb-3 text-brass animate-spin" size={18} />
                  : <Upload className="mx-auto mb-3 text-muted" size={18} />}
                <p className="text-ink2 lowercase">{uploading ? 'uploading…' : 'Click to upload or drag and drop'}</p>
                <p className="readout mt-2">MP4, MOV</p>
              </label>
            )}
          </div>
        )}

        {sourceMode === 'clip' && (
          <div className="space-y-2">
            {results?.clips?.length ? (
              <>
                <p className="text-xs text-muted">The clip's raw (uncaptioned) version is used as the TTS source.</p>
                <div className="grid gap-2 md:grid-cols-2">
                  {results.clips.map((clip, i) => (
                    <button
                      key={i}
                      onClick={() => { setSelectedClipIndex(i); resetPipeline(); }}
                      className={`text-left px-3 py-2 rounded-input border text-sm transition-colors ${selectedClipIndex === i ? 'border-brass bg-paper3/50' : 'border-rule hover:border-rule2'}`}
                    >
                      <span className="text-ink">Clip {i + 1}</span>
                      <span className="block text-xs text-muted truncate">
                        {clip.video_title_for_youtube_short || clip.video_url?.split('/').pop()}
                      </span>
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-sm text-muted">Generate clips first (Clip Generator), or upload a file above.</p>
            )}
          </div>
        )}
      </section>

      {/* 2. Providers & presets */}
      <section className="card border border-rule rounded-lg p-4 space-y-4">
        <h2 className="eyebrow">2 · providers &amp; presets</h2>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-ink lowercase">caption provider</span>
              <span className="readout">{captionProvider}</span>
            </div>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => setCaptionProvider('gemini')}
                className={captionProvider === 'gemini' ? 'btn-primary px-3 py-1 text-xs' : 'btn-quiet px-3 py-1 text-xs'}
              >
                Gemini
              </button>
              <button
                type="button"
                onClick={() => setCaptionProvider('openai')}
                className={captionProvider === 'openai' ? 'btn-primary px-3 py-1 text-xs' : 'btn-quiet px-3 py-1 text-xs'}
              >
                OpenAI
              </button>
            </div>
            <select
              className="input-field"
              value={stylePresetId}
              onChange={(e) => selectStylePreset(e.target.value, { autoselectVoice: true })}
            >
              {styleGroups.map(([group, styles]) => (
                <optgroup key={group} label={group.charAt(0).toUpperCase() + group.slice(1)}>
                  {styles.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
                </optgroup>
              ))}
            </select>
            {!editingStyle ? (
              <div className="bg-paper rounded-input border border-rule p-3">
                <pre className="text-xs text-muted whitespace-pre-wrap font-mono max-h-32 overflow-y-auto custom-scrollbar">{stylePrompt}</pre>
                <button
                  onClick={() => setEditingStyle(true)}
                  disabled={!stylePresetId}
                  className="mt-2 flex items-center gap-1 text-xs text-brass hover:underline disabled:opacity-40"
                >
                  <Pencil size={12} /> edit for this run
                </button>
              </div>
            ) : (
              <div className="bg-paper rounded-input border border-brass p-3 space-y-2">
                <textarea
                  className="input-field font-mono text-xs w-full"
                  rows={8}
                  value={stylePrompt}
                  onChange={(e) => { setStylePrompt(e.target.value); setStylePromptDirty(true); }}
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => savePresetBack('style')}
                    disabled={!stylePromptDirty || savingPreset}
                    className="flex items-center gap-1 text-xs text-ok hover:underline disabled:opacity-40"
                  >
                    {savingPreset ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} save to preset
                  </button>
                  <button onClick={() => selectStylePreset(stylePresetId)} className="flex items-center gap-1 text-xs text-muted hover:text-ink2">
                    <RotateCcw size={12} /> revert
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-ink lowercase">voice provider</span>
              <span className="readout">{voiceProvider === 'qwen' ? 'qwen tts' : 'elevenlabs'}</span>
            </div>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => setVoiceProvider('qwen')}
                disabled={!ttsStatus?.available}
                title={ttsStatus?.hint || 'Local Qwen3-TTS'}
                className={voiceProvider === 'qwen' ? 'btn-primary px-3 py-1 text-xs' : 'btn-quiet px-3 py-1 text-xs'}
              >
                Local Qwen TTS
              </button>
              <button
                type="button"
                onClick={() => setVoiceProvider('elevenlabs')}
                className={voiceProvider === 'elevenlabs' ? 'btn-primary px-3 py-1 text-xs' : 'btn-quiet px-3 py-1 text-xs'}
              >
                ElevenLabs
              </button>
            </div>
            {!ttsStatus?.available && (
              <p className="text-xs text-muted">{ttsStatus?.hint || 'Local TTS unavailable on this server.'}</p>
            )}
            {voiceProvider === 'elevenlabs' && (
              <select className="input-field" value={elVoiceId} onChange={(e) => setElVoiceId(e.target.value)}>
                {elVoices.length === 0 && <option value="">default voice</option>}
                {elVoices.map((v) => <option key={v.voice_id} value={v.voice_id}>{v.name}</option>)}
              </select>
            )}
            <select
              className="input-field"
              value={voicePresetId}
              onChange={(e) => selectVoicePreset(e.target.value)}
            >
              {selectedStyle && matchingVoices.length > 0 && (
                <optgroup label={`★ Matches ${selectedStyle.title}`}>
                  {matchingVoices.map((v) => (
                    <option key={v.id} value={v.id}>{v.title}</option>
                  ))}
                </optgroup>
              )}
              <optgroup label={selectedStyle && matchingVoices.length > 0 ? 'All voices' : 'Voices'}>
                {otherVoices.map((v) => (
                  <option key={v.id} value={v.id}>{v.title}</option>
                ))}
              </optgroup>
            </select>
            {selectedStyle && matchingVoices.length > 0 && (
              <p className="text-xs text-brass">
                {matchingVoices.length} voice{matchingVoices.length > 1 ? 's' : ''} tagged for “{selectedStyle.title}” listed first.
              </p>
            )}

            <label className="eyebrow flex items-center gap-1.5 mt-1"><Globe size={13} /> voiceover language</label>
            <select className="input-field" value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANGUAGES.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
            </select>
            <p className="text-xs text-muted">
              {language === 'auto'
                ? (resolvedLanguage
                    ? `Auto-detected: ${resolvedLanguage}`
                    : 'Auto-detects the clip\'s transcript language (uploads default to English).')
                : `Captions and voice will be generated in this language.`}
            </p>

            {!editingVoice ? (
              <div className="bg-paper rounded-input border border-rule p-3">
                <pre className="text-xs text-muted whitespace-pre-wrap font-mono max-h-32 overflow-y-auto custom-scrollbar">{voicePrompt}</pre>
                <button
                  onClick={() => setEditingVoice(true)}
                  disabled={!voicePresetId}
                  className="mt-2 flex items-center gap-1 text-xs text-brass hover:underline disabled:opacity-40"
                >
                  <Pencil size={12} /> edit for this run
                </button>
              </div>
            ) : (
              <div className="bg-paper rounded-input border border-brass p-3 space-y-2">
                <textarea
                  className="input-field font-mono text-xs w-full"
                  rows={8}
                  value={voicePrompt}
                  onChange={(e) => { setVoicePrompt(e.target.value); setVoicePromptDirty(true); }}
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => savePresetBack('voice')}
                    disabled={!voicePromptDirty || savingPreset}
                    className="flex items-center gap-1 text-xs text-ok hover:underline disabled:opacity-40"
                  >
                    {savingPreset ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} save to preset
                  </button>
                  <button onClick={() => selectVoicePreset(voicePresetId)} className="flex items-center gap-1 text-xs text-muted hover:text-ink2">
                    <RotateCcw size={12} /> revert
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Game profile */}
        {gameProfiles?.length > 0 && (
          <div className="space-y-1.5">
            <label className="eyebrow flex items-center gap-1.5"><Gamepad2 size={13} /> game profile context (optional)</label>
            <select className="input-field" value={gameProfileId} onChange={(e) => setGameProfileId(e.target.value)}>
              <option value="">none</option>
              {gameProfiles.map((p) => <option key={p.id} value={p.id}>{p.name || p.game_title}</option>)}
            </select>
            <p className="text-xs text-muted">Only the game title and description are sent as context.</p>
          </div>
        )}
      </section>

      {/* 3. Caption suggestions */}
      <section className="card border border-rule rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="eyebrow">3 · caption suggestions</h2>
          <button
            onClick={generateCaptions}
            disabled={!sourceReady || generatingCaptions || busy || !stylePrompt}
            className="btn-primary px-4 py-2 text-sm flex items-center gap-2 disabled:opacity-50"
          >
            {generatingCaptions ? <Loader2 size={15} className="animate-spin" /> : <Wand2 size={15} />}
            {generatingCaptions ? 'generating…' : options ? 'regenerate' : 'generate 4 options'}
          </button>
        </div>

        {options?.length ? (
          <div className="flex flex-col lg:flex-row gap-4">
            {/* Left: live Remotion caption preview over the RAW source (no burned subs) */}
            {sourceVideoUrl && previewConfig && (
              <div className="relative rounded-input overflow-hidden border border-rule bg-black shrink-0 self-start mx-auto lg:mx-0"
                style={{ width: 'min(100%, 20rem)', height: '35.5rem' }}
              >
                <RemotionPreview
                  videoUrl={sourceVideoUrl}
                  durationInSeconds={Math.max(
                    durationSec,
                    (previewConfig.captions[previewConfig.captions.length - 1]?.endMs || 0) / 1000,
                  )}
                  subtitles={showCaptionPreview ? previewConfig : null}
                  className="w-full h-full"
                />
                <button
                  onClick={() => setShowCaptionPreview(!showCaptionPreview)}
                  className="absolute top-2 right-2 z-10 bg-black/60 text-white text-xs px-2 py-1 rounded flex items-center gap-1"
                >
                  <ChevronDown size={12} className={showCaptionPreview ? '' : 'rotate-180'} />
                  {showCaptionPreview ? 'hide captions' : 'show captions'}
                </button>
              </div>
            )}

            {/* Right: option picker + editable captions */}
            <div className="flex-1 min-w-0 space-y-3">
              <div className="flex flex-wrap gap-2">
                {options.map((opt, i) => (
                  <button
                    key={i}
                    onClick={() => selectOption(i)}
                    className={`px-3 py-1.5 rounded-input text-sm border transition-colors ${selectedOption === i ? 'border-brass text-brass bg-paper3/50' : 'border-rule text-muted hover:text-ink2'}`}
                  >
                    {opt.label || `Option ${i + 1}`}
                  </button>
                ))}
              </div>

              {editedCaptions && (
                <div className="space-y-2">
                  <p className="text-xs text-muted">Edit any caption text or timing — the preview updates live.</p>
                  <div className="max-h-[26rem] overflow-y-auto custom-scrollbar space-y-1.5 pr-1">
                    {editedCaptions.map((c, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <span className="readout bg-paper3 px-1.5 py-0.5 rounded shrink-0">
                          {c.start?.toFixed?.(1)}–{c.end?.toFixed?.(1)}s
                        </span>
                        <input
                          className="input-field flex-1 min-w-0"
                          value={c.text}
                          onChange={(e) => editCaption(i, 'text', e.target.value)}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted">
            {sourceReady ? 'Generate 4 caption options with your caption provider, then pick and edit one.' : 'Pick a source video first.'}
          </p>
        )}
      </section>

      {/* 4. Generate */}
      <section className="card border border-rule rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="eyebrow">4 · voiceover</h2>
          <button
            onClick={startVoiceover}
            disabled={!sourceReady || !editedCaptions?.length || busy || !voicePrompt}
            className="btn-primary px-4 py-2 text-sm flex items-center gap-2 disabled:opacity-50"
          >
            {busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
            {busy ? stageLabel(voJob?.stage) : 'continue → generate voiceover'}
          </button>
        </div>
        {busy && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-ink2 flex items-center gap-1.5">
                {voJob?.stage?.startsWith('voice') ? <Mic size={12} className="text-brass animate-pulse" /> : <Loader2 size={12} className="animate-spin text-brass" />}
                {stageLabel(voJob?.stage)}
              </span>
              <span className="readout">{progress}%</span>
            </div>
            <div className="h-1.5 bg-paper3 rounded-full overflow-hidden">
              <div className="h-full bg-brass transition-all" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}
      </section>

      {/* System logs — page-level terminal (like the Clip Generator), visible
          through the whole workflow, not tied to any single step. */}
      {(busy || logs.length > 0 || generatingCaptions || uploading) && (
        <div className="bg-paper rounded-card border border-rule overflow-hidden flex flex-col h-48 min-h-0">
          <div className="px-4 py-2 border-b border-rule flex items-center justify-between bg-paper2 shrink-0">
            <span className="readout flex items-center gap-2">
              <Terminal size={12} /> Activity
            </span>
            <button onClick={() => setLogsVisible(!logsVisible)} className="text-muted hover:text-ink transition-colors">
              <ChevronDown size={14} className={logsVisible ? '' : 'rotate-180'} />
            </button>
          </div>
          {logsVisible && (
            <div ref={logsContainerRef} className="flex-1 p-4 overflow-y-auto font-sans text-sm space-y-2 custom-scrollbar text-ink2">
              {logs.map((log, i) => {
                const m = log.match(/^(\d{2}:\d{2}:\d{2})\s+(.*)$/);
                const ts = m ? m[1] : '--:--:--';
                const msg = m ? m[2] : log;
                const isError = /didn't go through|failed|something went wrong/i.test(msg);
                const isSuccess = /ready|All done|loaded|reopened/i.test(msg);
                return (
                  <div key={i} className={`flex gap-2.5 ${isError ? 'text-danger' : isSuccess ? 'text-ok' : 'text-ink2'}`}>
                    <span className="text-muted opacity-40 shrink-0 font-mono text-[11px] leading-5">{ts}</span>
                    <span className="leading-5">{msg}</span>
                  </div>
                );
              })}
              {busy && (
                <div className="flex items-center gap-2.5 text-brass">
                  <span className="text-muted opacity-40 shrink-0 font-mono text-[11px] leading-5">{new Date().toTimeString().slice(0, 8)}</span>
                  <span className="animate-pulse">{stageLabel(voJob?.stage)}</span>
                </div>
              )}
              {generatingCaptions && (
                <div className="flex items-center gap-2.5 text-brass">
                  <span className="text-muted opacity-40 shrink-0 font-mono text-[11px] leading-5">{new Date().toTimeString().slice(0, 8)}</span>
                  <span className="animate-pulse">The AI is watching your video — this can take up to a minute…</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* 5. Result — restored projects may miss one of the outputs (e.g. the
          captioned render was purged), so guard every URL before use. */}
      {result && (
        <section className="card border border-brass rounded-lg p-4 space-y-3">
          <h2 className="eyebrow text-ok flex items-center gap-1.5"><Check size={14} /> done</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {result.voiceover_url && (
              <div className="space-y-2">
                <p className="text-sm text-ink2 flex items-center gap-2">
                  <Mic size={14} className="text-muted shrink-0" /> voiceover mix
                </p>
                <video src={getApiUrl(result.voiceover_url)} controls className="w-full rounded-input bg-black" />
                <a
                  href={getApiUrl(result.voiceover_url)}
                  download
                  className="btn-ghost w-full text-xs flex items-center justify-center gap-1.5"
                >
                  <Download size={13} /> download _voiceover.mp4
                </a>
              </div>
            )}
            {result.captioned_url ? (
              <div className="space-y-2">
                <p className="text-sm text-ink2 flex items-center gap-2">
                  <Type size={14} className="text-muted shrink-0" /> captioned
                </p>
                <video src={getApiUrl(result.captioned_url)} controls className="w-full rounded-input bg-black" />
                <button
                  onClick={() => setShowSubtitleModal(true)}
                  className="btn-primary w-full text-xs flex items-center justify-center gap-1.5"
                >
                  <Pencil size={13} /> edit subtitles
                </button>
              </div>
            ) : (
              <div className="space-y-2 flex flex-col">
                <p className="text-sm text-ink2 flex items-center gap-2">
                  <Type size={14} className="text-muted shrink-0" /> captioned
                </p>
                <div className="flex-1 rounded-input bg-paper2 border border-dashed border-rule flex items-center justify-center p-6 text-center">
                  <p className="text-xs text-muted">
                    The captioned render isn't on the server anymore. Re-run "continue → generate
                    voiceover" from the steps above, or edit subtitles to recreate it.
                  </p>
                </div>
                <button
                  onClick={() => setShowSubtitleModal(true)}
                  disabled={!result.voiceover_url}
                  className="btn-primary w-full text-xs flex items-center justify-center gap-1.5 disabled:opacity-50"
                >
                  <Pencil size={13} /> edit subtitles
                </button>
              </div>
            )}
          </div>
          <p className="text-xs text-muted">
            Subtitle re-renders always burn from the clean <code>_voiceover.mp4</code> — captions never stack.
          </p>
        </section>
      )}

      {/* Subtitle editor: preview plays the CLEAN voiceover mix (no burned-in
          captions); only the download card shows the captioned file. */}
      {result?.voiceover_url && (
        <SubtitleModal
          isOpen={showSubtitleModal}
          onClose={() => setShowSubtitleModal(false)}
          onGenerate={restyleSubtitles}
          onApplyAll={null}
          onRemove={null}
          isProcessing={isRestyling}
          videoUrl={getApiUrl(result.voiceover_url)}
          jobId={result.job_id}
          clipIndex={0}
          bulkCount={0}
        />
      )}
    </div>
  );
}
