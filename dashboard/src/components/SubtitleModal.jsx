import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Loader2 } from 'lucide-react';
import { apiFetch } from '../lib/api';
import RemotionPreview from './RemotionPreview';
import Modal from './ui/Modal';
import SegmentedControl from './ui/SegmentedControl';

// In-memory draft cache per clip — survives window close/reopen, cleared on page refresh
const subtitleDraftCache = new Map(); // key: `${jobId}:${clipIndex}` -> {captions, originalCaptions, editableText, durationSec, style}

const FONT_OPTIONS = [
    { value: 'Anton', label: 'Anton' },
    { value: 'Verdana', label: 'Verdana' },
    { value: 'Arial', label: 'Arial' },
    { value: 'Impact', label: 'Impact' },
    { value: 'Helvetica', label: 'Helvetica' },
    { value: 'Georgia', label: 'Georgia' },
    { value: 'Courier New', label: 'Courier New' },
];

const COLOR_PRESETS = [
    { color: '#FFFFFF', label: 'White' },
    { color: '#FFFF00', label: 'Yellow' },
    { color: '#00FFFF', label: 'Cyan' },
    { color: '#00FF00', label: 'Green' },
    { color: '#FF0000', label: 'Red' },
    { color: '#FF69B4', label: 'Pink' },
];

const HIGHLIGHT_PRESETS = [
    { color: '#FFDD00', label: 'Gold' },
    { color: '#FF4444', label: 'Red' },
    { color: '#00FF88', label: 'Green' },
    { color: '#00BBFF', label: 'Blue' },
    { color: '#FF69B4', label: 'Pink' },
];

const ANIMATION_OPTIONS = [
    { value: 'pop', label: 'Pop' },
    { value: 'word-highlight', label: 'Glow' },
    { value: 'karaoke', label: 'Karaoke' },
    { value: 'none', label: 'None' },
];

const POSITION_OPTIONS = [
    { value: 'top', label: 'top' },
    { value: 'middle', label: 'middle' },
    { value: 'bottom', label: 'bottom' },
];

// Ready-made caption looks burned server-side as karaoke ASS (word highlight):
// dimmed base text + strong active word, optional glow/pop/box effect.
const CAPTION_PRESETS = [
    { id: 'auto',    label: 'Auto',       style: 'karaoke', effect: 'pop',  highlightColor: '#FFE500', baseOpacity: 1.0,  uppercase: true,  fontName: 'Anton',   borderWidth: 4, fontSize: 20, maxChars: 16, maxDuration: 1400 },
    { id: 'tiktok',  label: 'TikTok',     style: 'karaoke', effect: 'none', highlightColor: '#FE2C55', baseOpacity: 0.75, uppercase: false, fontName: 'Verdana', borderWidth: 2, fontSize: 22, maxChars: 14, maxDuration: 1400 },
    { id: 'reels',   label: 'Reels',      style: 'karaoke', effect: 'none', highlightColor: '#E1306C', baseOpacity: 0.7,  uppercase: false, fontName: 'Verdana', borderWidth: 2, fontSize: 22, maxChars: 14, maxDuration: 1400 },
    { id: 'shorts',  label: 'Shorts Pop', style: 'karaoke', effect: 'pop',  highlightColor: '#FF0000', baseOpacity: 0.7,  uppercase: false, fontName: 'Verdana', borderWidth: 2, fontSize: 22, maxChars: 14, maxDuration: 1400 },
    { id: 'gold',    label: 'Gold Glow',  style: 'karaoke', effect: 'glow', highlightColor: '#FFD700', baseOpacity: 0.6,  uppercase: false, fontName: 'Verdana', borderWidth: 2, fontSize: 22, maxChars: 14, maxDuration: 1400 },
    { id: 'neon',    label: 'Neon',       style: 'karaoke', effect: 'glow', highlightColor: '#00FF88', baseOpacity: 0.55, uppercase: false, fontName: 'Verdana', borderWidth: 2, fontSize: 22, maxChars: 14, maxDuration: 1400 },
    { id: 'cyber',   label: 'Cyber',      style: 'karaoke', effect: 'glow', highlightColor: '#00FFFF', baseOpacity: 0.5,  uppercase: false, fontName: 'Verdana', borderWidth: 2, fontSize: 22, maxChars: 14, maxDuration: 1400 },
    { id: 'karaoke', label: 'Karaoke',    style: 'karaoke', effect: 'none', highlightColor: '#FF6B6B', baseOpacity: 0.6,  uppercase: false, fontName: 'Verdana', borderWidth: 2, fontSize: 22, maxChars: 14, maxDuration: 1400 },
    { id: 'minimal', label: 'Minimal',    style: 'karaoke', effect: 'none', highlightColor: '#FFFFFF', baseOpacity: 0.65, uppercase: false, fontName: 'Verdana', borderWidth: 1, fontSize: 22, maxChars: 14, maxDuration: 1400 },
    { id: 'beast',   label: 'Beast',      style: 'karaoke', effect: 'pop',  highlightColor: '#FFD700', baseOpacity: 1.0,  uppercase: true,  fontName: 'Impact',  borderWidth: 3, fontSize: 24, maxChars: 12, maxDuration: 1200 },
    { id: 'boxed',   label: 'Boxed',      style: 'karaoke', effect: 'box',  highlightColor: '#7C3AED', baseOpacity: 0.85, uppercase: false, fontName: 'Verdana', borderWidth: 2, fontSize: 22, maxChars: 14, maxDuration: 1400 },
    { id: 'classic', label: 'Classic',    style: 'classic', effect: 'none', highlightColor: '#FFD700', baseOpacity: 1.0,  uppercase: false, fontName: 'Verdana', borderWidth: 2, fontSize: 22, maxChars: 14, maxDuration: 1400 },
];

const swatchClass = (selected) =>
    `w-6 h-6 rounded-full transition-all ${selected
        ? 'ring-2 ring-[color:var(--color-accent)] ring-offset-2 ring-offset-[color:var(--color-paper-2)]'
        : 'ring-1 ring-[color:var(--color-rule-2)] hover:ring-[color:var(--color-accent)]'}`;

export default function SubtitleModal({ isOpen, onClose, onGenerate, onApplyAll, onRemove, isProcessing, videoUrl, jobId, clipIndex, existingHook, bulkCount = 0, bulkProgress }) {
    const [position, setPosition] = useState('bottom');
    const [fontSize, setFontSize] = useState(32);
    const [fontName, setFontName] = useState('Verdana');
    const [fontColor, setFontColor] = useState('#FFFFFF');
    const [highlightColor, setHighlightColor] = useState('#FFDD00');
    const [borderColor, setBorderColor] = useState('#000000');
    const [borderWidth, setBorderWidth] = useState(2);
    const [bgColor, setBgColor] = useState('#000000');
    const [bgOpacity, setBgOpacity] = useState(0.0);
    const [animation, setAnimation] = useState('pop');
    const [showTextEditor, setShowTextEditor] = useState(true);

    // Layout / spacing state (Tasks 5-6)
    const [marginV, setMarginV] = useState(43);
    const [wordGap, setWordGap] = useState(8);
    const [lineHeight, setLineHeight] = useState(1.0);
    const [letterSpacing, setLetterSpacing] = useState(0);
    const [maxChars, setMaxChars] = useState(16);
    const [maxDuration, setMaxDuration] = useState(1400);
    const [spacingOpen, setSpacingOpen] = useState(false);
    const [toast, setToast] = useState('');
    const [userPresets, setUserPresets] = useState(() => {
        try { const v = localStorage.getItem('openshorts_subtitle_presets'); return v ? JSON.parse(v) : []; } catch { return []; }
    });

    // Karaoke (server-side ASS burn) state
    const [style, setStyle] = useState('classic'); // classic | karaoke
    const [effect, setEffect] = useState('none'); // none | glow | pop | box
    const [baseOpacity, setBaseOpacity] = useState(1.0);
    const [uppercase, setUppercase] = useState(false);
    const [activePreset, setActivePreset] = useState(null);

    const showToast = useCallback((msg) => {
        setToast(msg);
        setTimeout(() => setToast(''), 2000);
    }, []);

    // Hydrate from localStorage defaults on open (Task 7)
    useEffect(() => {
        if (!isOpen) return;
        try {
            const raw = localStorage.getItem('openshorts_subtitle_defaults');
            if (raw) {
                const d = JSON.parse(raw);
                if (d.marginV != null) setMarginV(d.marginV);
                if (d.wordGap != null) setWordGap(d.wordGap);
                if (d.lineHeight != null) setLineHeight(d.lineHeight);
                if (d.letterSpacing != null) setLetterSpacing(d.letterSpacing);
                if (d.fontSize != null) setFontSize(d.fontSize);
                if (d.fontName != null) setFontName(d.fontName);
                if (d.fontColor != null) setFontColor(d.fontColor);
                if (d.highlightColor != null) setHighlightColor(d.highlightColor);
                if (d.borderColor != null) setBorderColor(d.borderColor);
                if (d.borderWidth != null) setBorderWidth(d.borderWidth);
                if (d.bgColor != null) setBgColor(d.bgColor);
                if (d.bgOpacity != null) setBgOpacity(d.bgOpacity);
                if (d.animation != null) setAnimation(d.animation);
                if (d.style != null) setStyle(d.style);
                if (d.effect != null) setEffect(d.effect);
                if (d.baseOpacity != null) setBaseOpacity(d.baseOpacity);
                if (d.uppercase != null) setUppercase(d.uppercase);
                if (d.position != null) setPosition(d.position);
                if (d.maxChars != null) setMaxChars(d.maxChars);
                if (d.maxDuration != null) setMaxDuration(d.maxDuration);
            }
            const presetsRaw = localStorage.getItem('openshorts_subtitle_presets');
            if (presetsRaw) setUserPresets(JSON.parse(presetsRaw));
        } catch { /* ignored */ }
    }, [isOpen]);

    const handleSaveDefault = useCallback(() => {
        try {
            const defaults = { fontSize, fontName, fontColor, highlightColor, borderColor, borderWidth, bgColor, bgOpacity, animation, style, effect, baseOpacity, uppercase, position, marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing };
            localStorage.setItem('openshorts_subtitle_defaults', JSON.stringify(defaults));
            showToast('Saved as default ✓');
        } catch { showToast('Failed to save'); }
    }, [fontSize, fontName, fontColor, highlightColor, borderColor, borderWidth, bgColor, bgOpacity, animation, style, effect, baseOpacity, uppercase, position, marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing, showToast]);

    const handleSavePreset = useCallback(() => {
        const name = window.prompt('Preset name:');
        if (!name || !name.trim()) return;
        const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || `preset-${Date.now()}`;
        const preset = { id: `user-${slug}-${Date.now()}`, label: name.trim(), name: name.trim(), style, effect, highlightColor, baseOpacity, uppercase, fontName, fontColor, borderColor, borderWidth, bgColor, bgOpacity, animation, fontSize, marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing, position };
        const next = [...userPresets, preset];
        setUserPresets(next);
        try { localStorage.setItem('openshorts_subtitle_presets', JSON.stringify(next)); } catch { /* ignored */ }
        showToast(`Preset "${name.trim()}" saved`);
    }, [style, effect, highlightColor, baseOpacity, uppercase, fontName, fontColor, borderColor, borderWidth, bgColor, bgOpacity, animation, fontSize, marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing, position, userPresets, showToast]);

    const removePreset = useCallback((id) => {
        const next = userPresets.filter(p => p.id !== id);
        setUserPresets(next);
        try { localStorage.setItem('openshorts_subtitle_presets', JSON.stringify(next)); } catch { /* ignored */ }
        if (activePreset === id) setActivePreset(null);
        showToast('Preset removed');
    }, [userPresets, activePreset, showToast]);

    const applyPreset = (p) => {
        setActivePreset(p.id);
        setStyle(p.style);
        setEffect(p.effect);
        setHighlightColor(p.highlightColor);
        setBaseOpacity(p.baseOpacity);
        setUppercase(p.uppercase);
        setFontName(p.fontName);
        setFontColor(p.fontColor || '#FFFFFF');
        setBorderColor(p.borderColor || '#000000');
        if (p.borderWidth != null) setBorderWidth(p.borderWidth);
        if (p.bgColor != null) setBgColor(p.bgColor);
        if (p.bgOpacity != null) setBgOpacity(p.bgOpacity);
        if (p.fontSize) setFontSize(p.fontSize);
        if (p.marginV != null) setMarginV(p.marginV);
        if (p.maxChars != null) setMaxChars(p.maxChars);
        if (p.maxDuration != null) setMaxDuration(p.maxDuration);
        if (p.wordGap != null) setWordGap(p.wordGap);
        if (p.lineHeight != null) setLineHeight(p.lineHeight);
        if (p.letterSpacing != null) setLetterSpacing(p.letterSpacing);
        if (p.position) setPosition(p.position);
        // Keep the Remotion preview roughly in sync with the burned look
        const anim = p.animation || (p.style === 'karaoke' ? (p.effect === 'pop' ? 'pop' : p.effect === 'glow' ? 'word-highlight' : 'karaoke') : 'none');
        setAnimation(anim);
    };

    const presetDirty = useMemo(() => {
        if (!activePreset) return false;
        const all = [...CAPTION_PRESETS, ...userPresets];
        const p = all.find(x => x.id === activePreset);
        if (!p) return false;
        // only user presets are updatable; built-ins dirty means "changed from preset" but we don't offer update for them
        if (!p.id.startsWith('user-')) return false;
        const keys = ['style','effect','highlightColor','baseOpacity','uppercase','fontName','fontColor','borderColor','borderWidth','bgColor','bgOpacity','animation','fontSize','marginV','maxChars','maxDuration','wordGap','lineHeight','letterSpacing','position'];
        for (const k of keys) {
            const pv = p[k];
            const cv = { style, effect, highlightColor, baseOpacity, uppercase, fontName, fontColor, borderColor, borderWidth, bgColor, bgOpacity, animation, fontSize, marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing, position }[k];
            if (pv !== cv && !(pv == null && cv == null)) return true;
        }
        return false;
    }, [activePreset, userPresets, style, effect, highlightColor, baseOpacity, uppercase, fontName, fontColor, borderColor, borderWidth, bgColor, bgOpacity, animation, fontSize, marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing, position]);

    const handleUpdatePreset = useCallback(() => {
        if (!activePreset) return;
        const idx = userPresets.findIndex(x => x.id === activePreset);
        if (idx === -1) { showToast('Only your presets can be updated'); return; }
        const prev = userPresets[idx];
        const updated = { ...prev, style, effect, highlightColor, baseOpacity, uppercase, fontName, fontColor, borderColor, borderWidth, bgColor, bgOpacity, animation, fontSize, marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing, position };
        const next = [...userPresets];
        next[idx] = updated;
        setUserPresets(next);
        try { localStorage.setItem('openshorts_subtitle_presets', JSON.stringify(next)); } catch { /* ignored */ }
        showToast(`Preset "${prev.label}" updated ✓`);
    }, [activePreset, userPresets, style, effect, highlightColor, baseOpacity, uppercase, fontName, fontColor, borderColor, borderWidth, bgColor, bgOpacity, animation, fontSize, marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing, position, showToast]);

    const handleResetToDefault = useCallback(() => {
        try {
            const raw = localStorage.getItem('openshorts_subtitle_defaults');
            if (raw) {
                const d = JSON.parse(raw);
                if (d.marginV != null) setMarginV(d.marginV); else setMarginV(43);
                if (d.wordGap != null) setWordGap(d.wordGap); else setWordGap(8);
                if (d.lineHeight != null) setLineHeight(d.lineHeight); else setLineHeight(1.0);
                if (d.letterSpacing != null) setLetterSpacing(d.letterSpacing); else setLetterSpacing(0);
                if (d.fontSize != null) setFontSize(d.fontSize); else setFontSize(32);
                if (d.fontName != null) setFontName(d.fontName); else setFontName('Verdana');
                if (d.fontColor != null) setFontColor(d.fontColor); else setFontColor('#FFFFFF');
                if (d.highlightColor != null) setHighlightColor(d.highlightColor); else setHighlightColor('#FFDD00');
                if (d.borderColor != null) setBorderColor(d.borderColor); else setBorderColor('#000000');
                if (d.borderWidth != null) setBorderWidth(d.borderWidth); else setBorderWidth(2);
                if (d.bgColor != null) setBgColor(d.bgColor); else setBgColor('#000000');
                if (d.bgOpacity != null) setBgOpacity(d.bgOpacity); else setBgOpacity(0.0);
                if (d.animation != null) setAnimation(d.animation); else setAnimation('pop');
                if (d.style != null) setStyle(d.style); else setStyle('classic');
                if (d.effect != null) setEffect(d.effect); else setEffect('none');
                if (d.baseOpacity != null) setBaseOpacity(d.baseOpacity); else setBaseOpacity(1.0);
                if (d.uppercase != null) setUppercase(d.uppercase); else setUppercase(false);
                if (d.position != null) setPosition(d.position); else setPosition('bottom');
                if (d.maxChars != null) setMaxChars(d.maxChars); else setMaxChars(16);
                if (d.maxDuration != null) setMaxDuration(d.maxDuration); else setMaxDuration(1400);
                showToast('Reset to default ✓');
                return;
            }
        } catch { /* ignored */ }
        // fallback: hard defaults
        setMarginV(43); setWordGap(8); setLineHeight(1.0); setLetterSpacing(0);
        setFontSize(32); setFontName('Verdana'); setFontColor('#FFFFFF'); setHighlightColor('#FFDD00');
        setBorderColor('#000000'); setBorderWidth(2); setBgColor('#000000'); setBgOpacity(0.0);
        setAnimation('pop'); setStyle('classic'); setEffect('none'); setBaseOpacity(1.0); setUppercase(false);
        setPosition('bottom'); setMaxChars(16); setMaxDuration(1400);
        showToast('Reset to default ✓');
    }, [showToast]);

    // Remotion preview state
    const [captions, setCaptions] = useState([]);
    const [originalCaptions, setOriginalCaptions] = useState([]);
    const [editableText, setEditableText] = useState('');
    const [durationSec, setDurationSec] = useState(30);
    const [dragging, setDragging] = useState(null); // {index, mode:'move'|'left'|'right', startX, origStart, origEnd, origMouseMs}
    const [timelineRef, setTimelineRef] = useState(null);
    // Zoomed timeline + playback sync
    const [playbackMs, setPlaybackMs] = useState(0);
    const [zoomSec, setZoomSec] = useState(5);
    const [followPlayhead, setFollowPlayhead] = useState(true);
    const [panMs, setPanMs] = useState(0);
    const playerTimeRef = useRef(0);
    const playerRef = useRef(null);
    const [draggingPlayhead, setDraggingPlayhead] = useState(false);
    const textAreaRef = useRef(null);
    const lastEditRef = useRef(0);
    const [showEmojiPicker, setShowEmojiPicker] = useState(false);
    const [recentEmojis, setRecentEmojis] = useState(() => {
        try { const v = localStorage.getItem('openshorts_recent_emojis'); return v ? JSON.parse(v) : []; } catch { return []; }
    });
    const [emojiSearch, setEmojiSearch] = useState('');
    const EMOJI_DATA = [
        { e: '😀', n: 'grinning', k: 'smile happy' }, { e: '😃', n: 'smiley', k: 'happy joy' }, { e: '😄', n: 'smile', k: 'happy' }, { e: '😁', n: 'grin', k: 'happy' }, { e: '😆', n: 'laugh', k: 'happy' }, { e: '😅', n: 'sweat smile', k: 'hot' }, { e: '🤣', n: 'rofl', k: 'laugh lol' }, { e: '😂', n: 'joy', k: 'laugh tear' }, { e: '🙂', n: 'slightly smiling', k: 'smile' }, { e: '🙃', n: 'upside down', k: 'silly' }, { e: '😉', n: 'wink', k: 'flirt' }, { e: '😊', n: 'blush', k: 'smile' }, { e: '😇', n: 'angel', k: 'halo' }, { e: '🥲', n: 'smiling tear', k: 'cry' }, { e: '🥰', n: 'smiling hearts', k: 'love' }, { e: '😍', n: 'heart eyes', k: 'love' }, { e: '🤩', n: 'star struck', k: 'wow' }, { e: '😘', n: 'kiss', k: 'love' }, { e: '😗', n: 'kissing', k: '' }, { e: '😚', n: 'kissing closed eyes', k: '' }, { e: '😙', n: 'kissing smiling eyes', k: '' }, { e: '🥺', n: 'pleading', k: 'puppy eyes' }, { e: '😢', n: 'cry', k: 'tear' }, { e: '😭', n: 'sob', k: 'cry loudly' }, { e: '😤', n: 'triumph', k: 'steam' }, { e: '😠', n: 'angry', k: 'mad' }, { e: '😡', n: 'rage', k: 'pouting red' }, { e: '🤬', n: 'cursing', k: 'swear' }, { e: '🤯', n: 'exploding head', k: 'mind blown' }, { e: '😳', n: 'flushed', k: 'embarrassed' }, { e: '🥵', n: 'hot face', k: 'heat' }, { e: '🥶', n: 'cold face', k: 'freeze' }, { e: '😱', n: 'scream', k: 'fear shocked' }, { e: '😨', n: 'fearful', k: 'scared' }, { e: '😰', n: 'anxious', k: 'sweat' }, { e: '😥', n: 'sad relieved', k: '' }, { e: '😓', n: 'downcast sweat', k: '' }, { e: '🤗', n: 'hugging', k: 'hug' }, { e: '🤔', n: 'thinking', k: 'hmm' }, { e: '🫡', n: 'salute', k: 'respect' }, { e: '🤭', n: 'hand over mouth', k: 'oops' }, { e: '🫢', n: 'open eyes hand mouth', k: 'shocked' }, { e: '🫣', n: 'peeking', k: 'shy' }, { e: '🤫', n: 'shushing', k: 'quiet' }, { e: '🤥', n: 'lying', k: 'pinocchio' }, { e: '😶', n: 'no mouth', k: 'silent' }, { e: '🫥', n: 'dotted line', k: 'invisible' }, { e: '😐', n: 'neutral', k: 'meh' }, { e: '😑', n: 'expressionless', k: '' }, { e: '😬', n: 'grimace', k: 'awkward' }, { e: '🙄', n: 'rolling eyes', k: 'annoyed' }, { e: '😯', n: 'hushed', k: 'surprised' }, { e: '😦', n: 'frowning', k: '' }, { e: '😧', n: 'anguished', k: '' }, { e: '😮', n: 'open mouth', k: 'wow' }, { e: '😲', n: 'astonished', k: 'shocked' }, { e: '🥱', n: 'yawning', k: 'tired bored' }, { e: '😴', n: 'sleeping', k: 'zzz' }, { e: '🤤', n: 'drooling', k: 'drool' }, { e: '😪', n: 'sleepy', k: '' }, { e: '😵', n: 'dizzy', k: 'cross eyes' }, { e: '🤐', n: 'zipper mouth', k: 'secret' }, { e: '🥴', n: 'woozy', k: 'drunk' }, { e: '🤠', n: 'cowboy', k: 'hat' }, { e: '🥳', n: 'partying', k: 'celebrate' }, { e: '🥸', n: 'disguised', k: 'glasses' }, { e: '😎', n: 'sunglasses', k: 'cool' }, { e: '🤓', n: 'nerd', k: 'glasses' }, { e: '🧐', n: 'monocle', k: 'fancy' },
        { e: '👍', n: 'thumbs up', k: 'like approve' }, { e: '👎', n: 'thumbs down', k: 'dislike' }, { e: '👌', n: 'ok hand', k: 'perfect' }, { e: '🤌', n: 'pinched', k: 'italian' }, { e: '🤏', n: 'pinching', k: 'small' }, { e: '✌️', n: 'victory', k: 'peace' }, { e: '🤞', n: 'crossed fingers', k: 'luck' }, { e: '🫰', n: 'heart hand', k: 'love' }, { e: '🤟', n: 'love you', k: 'ily' }, { e: '🤘', n: 'rock on', k: 'metal' }, { e: '🤙', n: 'call me', k: 'shaka' }, { e: '👈', n: 'backhand left', k: 'point' }, { e: '👉', n: 'backhand right', k: 'point' }, { e: '👆', n: 'backhand up', k: 'point' }, { e: '🖕', n: 'middle finger', k: 'rude' }, { e: '👇', n: 'backhand down', k: 'point' }, { e: '☝️', n: 'index up', k: 'one' }, { e: '👋', n: 'waving', k: 'hello bye' }, { e: '🤚', n: 'raised back', k: '' }, { e: '🖐️', n: 'hand splayed', k: 'five' }, { e: '✋', n: 'raised hand', k: 'stop' }, { e: '🖖', n: 'vulcan', k: 'spock' }, { e: '✊', n: 'fist', k: 'power' }, { e: '👊', n: 'punch', k: 'fist bump' }, { e: '🤛', n: 'left fist', k: '' }, { e: '🤜', n: 'right fist', k: '' }, { e: '👏', n: 'clapping', k: 'applause' }, { e: '🙌', n: 'raising hands', k: 'hooray' }, { e: '🫶', n: 'heart hands', k: 'love' }, { e: '👐', n: 'open hands', k: '' }, { e: '🤲', n: 'palms up', k: '' }, { e: '🤝', n: 'handshake', k: 'deal' }, { e: '🙏', n: 'pray', k: 'please thanks' }, { e: '💪', n: 'flexed biceps', k: 'strong muscle' }, { e: '🦾', n: 'mechanical arm', k: 'bionic' }, { e: '🦿', n: 'mechanical leg', k: '' }, { e: '🦵', n: 'leg', k: 'kick' }, { e: '🦶', n: 'foot', k: '' },
        { e: '❤️', n: 'red heart', k: 'love' }, { e: '🧡', n: 'orange heart', k: '' }, { e: '💛', n: 'yellow heart', k: '' }, { e: '💚', n: 'green heart', k: '' }, { e: '💙', n: 'blue heart', k: '' }, { e: '💜', n: 'purple heart', k: '' }, { e: '🖤', n: 'black heart', k: '' }, { e: '🤍', n: 'white heart', k: '' }, { e: '🤎', n: 'brown heart', k: '' }, { e: '💔', n: 'broken heart', k: 'breakup' }, { e: '❣️', n: 'heart exclamation', k: '' }, { e: '💕', n: 'two hearts', k: '' }, { e: '💞', n: 'revolving hearts', k: '' }, { e: '💓', n: 'beating heart', k: '' }, { e: '💗', n: 'growing heart', k: '' }, { e: '💖', n: 'sparkling heart', k: '' }, { e: '💘', n: 'heart arrow', k: 'cupid' }, { e: '💝', n: 'heart box', k: 'gift' }, { e: '💟', n: 'heart decoration', k: '' },
        { e: '🔥', n: 'fire', k: 'hot lit' }, { e: '✨', n: 'sparkles', k: 'stars glitter' }, { e: '💥', n: 'collision', k: 'boom bomb explosion' }, { e: '💫', n: 'dizzy', k: 'star' }, { e: '⭐', n: 'star', k: '' }, { e: '🌟', n: 'glowing star', k: '' }, { e: '💯', n: 'hundred', k: '100 perfect' }, { e: '💢', n: 'anger', k: 'mad' }, { e: '💦', n: 'sweat droplets', k: 'water' }, { e: '💨', n: 'dash', k: 'wind fast' }, { e: '💣', n: 'bomb', k: 'boom explosion' }, { e: '💀', n: 'skull', k: 'death dead' }, { e: '☠️', n: 'skull crossbones', k: 'poison death' }, { e: '👻', n: 'ghost', k: 'halloween' }, { e: '👽', n: 'alien', k: 'ufo' }, { e: '🤖', n: 'robot', k: '' }, { e: '💬', n: 'speech balloon', k: 'chat' }, { e: '👁️', n: 'eye', k: 'look see' }, { e: '🎉', n: 'tada', k: 'party celebrate' }, { e: '🎊', n: 'confetti', k: 'party' }, { e: '🎈', n: 'balloon', k: '' }, { e: '🎁', n: 'gift', k: 'present' }, { e: '🏆', n: 'trophy', k: 'win' }, { e: '🥇', n: 'gold medal', k: 'first' }, { e: '🥈', n: 'silver medal', k: '' }, { e: '🥉', n: 'bronze medal', k: '' }, { e: '⚽', n: 'soccer', k: 'football' }, { e: '🏀', n: 'basketball', k: '' }, { e: '🎮', n: 'video game', k: 'controller' }, { e: '🎧', n: 'headphone', k: 'music' }, { e: '🎤', n: 'microphone', k: 'karaoke' }, { e: '🎬', n: 'clapper', k: 'movie film' }, { e: '📸', n: 'camera', k: 'photo' }, { e: '💡', n: 'bulb', k: 'idea light' }, { e: '🔔', n: 'bell', k: 'notification' }, { e: '🔊', n: 'speaker loud', k: 'volume' }, { e: '🔇', n: 'muted speaker', k: 'quiet' }, { e: '🎵', n: 'musical note', k: 'music' }, { e: '🎶', n: 'notes', k: 'music' },
        { e: '✅', n: 'check', k: 'done' }, { e: '❌', n: 'cross', k: 'no' }, { e: '❓', n: 'question', k: 'what' }, { e: '❗', n: 'exclamation', k: '!' }, { e: '‼️', n: 'double exclamation', k: '' }, { e: '⁉️', n: 'exclamation question', k: '' }, { e: '⚡', n: 'zap', k: 'lightning fast' }, { e: '☀️', n: 'sun', k: 'sunny' }, { e: '🌙', n: 'moon', k: 'night' }, { e: '⚠️', n: 'warning', k: 'alert' }, { e: '🚫', n: 'prohibited', k: 'no' }, { e: '⛔', n: 'no entry', k: '' }, { e: '🔶', n: 'orange diamond', k: '' }, { e: '🔷', n: 'blue diamond', k: '' },
        { e: '🍏', n: 'green apple', k: '' }, { e: '🍎', n: 'red apple', k: '' }, { e: '🍐', n: 'pear', k: '' }, { e: '🍊', n: 'tangerine', k: 'orange' }, { e: '🍋', n: 'lemon', k: '' }, { e: '🍌', n: 'banana', k: '' }, { e: '🍉', n: 'watermelon', k: '' }, { e: '🍇', n: 'grapes', k: '' }, { e: '🍓', n: 'strawberry', k: '' }, { e: '🫐', n: 'blueberries', k: '' }, { e: '🍈', n: 'melon', k: '' }, { e: '🍒', n: 'cherries', k: '' }, { e: '🍑', n: 'peach', k: '' }, { e: '🥭', n: 'mango', k: '' }, { e: '🍍', n: 'pineapple', k: '' }, { e: '🥥', n: 'coconut', k: '' }, { e: '🥝', n: 'kiwi', k: '' }, { e: '🍅', n: 'tomato', k: '' },
    ];
    const EMOJI_CATEGORIES = [
        { name: 'Recent', emojis: recentEmojis },
        { name: 'Smileys', emojis: EMOJI_DATA.filter(d => ['grinning','smiley','smile','grin','laugh','sweat','rofl','joy','wink','blush','angel','pleading','cry','sob','triumph','angry','rage','cursing','exploding','flushed','hot','cold','scream','fearful','anxious','hugging','thinking','salute'].some(k => d.n.includes(k) || d.k.includes(k))).map(d => d.e) },
        { name: 'Gestures', emojis: EMOJI_DATA.filter(d => ['thumbs','hand','fist','clapping','raising','flexed','pray'].some(k => d.n.includes(k))).map(d => d.e) },
        { name: 'Hearts', emojis: EMOJI_DATA.filter(d => d.n.includes('heart')).map(d => d.e) },
        { name: 'Objects & Symbols', emojis: EMOJI_DATA.filter(d => ['fire','sparkles','collision','star','hundred','bomb','skull','ghost','alien','robot','speech','eye','tada','trophy','medal','soccer','basketball','game','headphone','microphone','clapper','camera','bulb','bell','speaker','check','cross','question','exclamation','zap','sun','moon','warning'].some(k => d.n.includes(k) || d.k.includes(k))).map(d => d.e) },
        { name: 'All', emojis: EMOJI_DATA.map(d => d.e) },
    ];
    const seekToMs = useCallback((ms) => {
        const clamped = Math.max(0, Math.min(durationSec * 1000, ms));
        const frame = Math.round((clamped / 1000) * 30);
        try { playerRef.current?.seekTo?.(frame); } catch { /* ignored */ }
        playerTimeRef.current = clamped;
        setPlaybackMs(clamped);
    }, [durationSec]);
    const [captionsLoading, setCaptionsLoading] = useState(false);
    const [useRemotionPreview, setUseRemotionPreview] = useState(false);

    // Fetch word-level captions when modal opens — restore draft from cache if present
    useEffect(() => {
        if (!isOpen || !jobId || clipIndex === undefined) return;

        const cacheKey = `${jobId}:${clipIndex}`;
        if (subtitleDraftCache.has(cacheKey)) {
            const d = subtitleDraftCache.get(cacheKey);
            setCaptions(d.captions);
            setOriginalCaptions(d.originalCaptions);
            setEditableText(d.editableText);
            setDurationSec(d.durationSec);
            setUseRemotionPreview(true);
            setShowTextEditor(true);
            // restore style draft if present
            if (d.style) {
                const s = d.style;
                if (s.position) setPosition(s.position);
                if (s.fontSize) setFontSize(s.fontSize);
                if (s.fontName) setFontName(s.fontName);
                if (s.fontColor) setFontColor(s.fontColor);
                if (s.highlightColor) setHighlightColor(s.highlightColor);
                if (s.borderColor) setBorderColor(s.borderColor);
                if (s.borderWidth != null) setBorderWidth(s.borderWidth);
                if (s.bgColor) setBgColor(s.bgColor);
                if (s.bgOpacity != null) setBgOpacity(s.bgOpacity);
                if (s.animation) setAnimation(s.animation);
                if (s.style) setStyle(s.style);
                if (s.effect) setEffect(s.effect);
                if (s.baseOpacity != null) setBaseOpacity(s.baseOpacity);
                if (s.uppercase != null) setUppercase(s.uppercase);
                if (s.marginV != null) setMarginV(s.marginV);
                if (s.wordGap != null) setWordGap(s.wordGap);
                if (s.lineHeight != null) setLineHeight(s.lineHeight);
                if (s.letterSpacing != null) setLetterSpacing(s.letterSpacing);
                if (s.maxChars != null) setMaxChars(s.maxChars);
                if (s.maxDuration != null) setMaxDuration(s.maxDuration);
            }
            setCaptionsLoading(false);
            // restore playback position if cached
            if (d.playbackMs != null) {
                setPlaybackMs(d.playbackMs);
                playerTimeRef.current = d.playbackMs;
                // seek player after mount
                setTimeout(() => {
                    try { playerRef.current?.seekTo?.(Math.round((d.playbackMs / 1000) * 30)); } catch { /* ignored */ }
                }, 100);
            }
            return;
        }

        setCaptionsLoading(true);
        apiFetch(`/api/clip/${jobId}/${clipIndex}/transcript`)
            .then((res) => res.ok ? res.json() : null)
            .then((data) => {
                if (data && data.captions && data.captions.length > 0) {
                    setCaptions(data.captions);
                    setOriginalCaptions(data.captions);
                    setEditableText(data.captions.map(c => c.text).join(' '));
                    setDurationSec(data.durationSec || 30);
                    setUseRemotionPreview(true);
                    setShowTextEditor(true);
                } else {
                    setUseRemotionPreview(false);
                }
            })
            .catch(() => setUseRemotionPreview(false))
            .finally(() => setCaptionsLoading(false));
    }, [isOpen, jobId, clipIndex]);

    // Keep draft in cache while window is open — survives close/reopen until page refresh
    useEffect(() => {
        if (!isOpen || !jobId || clipIndex === undefined) return;
        if (captions.length === 0 && !editableText) return;
        const key = `${jobId}:${clipIndex}`;
        subtitleDraftCache.set(key, {
            captions,
            originalCaptions,
            editableText,
            durationSec,
            playbackMs,
            style: { position, fontSize, fontName, fontColor, highlightColor, borderColor, borderWidth, bgColor, bgOpacity, animation, style, effect, baseOpacity, uppercase, marginV, wordGap, lineHeight, letterSpacing, maxChars, maxDuration },
        });
    }, [isOpen, jobId, clipIndex, captions, originalCaptions, editableText, durationSec, playbackMs, position, fontSize, fontName, fontColor, highlightColor, borderColor, borderWidth, bgColor, bgOpacity, animation, style, effect, baseOpacity, uppercase, marginV, wordGap, lineHeight, letterSpacing, maxChars, maxDuration]);

    // When user edits text: keep edited words in place with same duration,
    // do NOT stretch across gaps. Inserted words get 100ms and attach to
    // nearest neighbor (prefer playback position). Replacements inherit
    // the old word's timing at same index in the gap.
    const handleTextEdit = useCallback((newText, cursorPos = null) => {
        setEditableText(newText);
        lastEditRef.current = Date.now();
        // Use current captions as base for incremental edits so a selected placeholder keeps its 100ms timing
        const baseCaptions = captions.length > 0 ? captions : originalCaptions;
        // cursor position at edit time — used to anchor new words to closest word from cursor
        const caretWordIdx = (() => {
            if (cursorPos == null || !newText) return null;
            let wordIdx = -1; let curWord = 0; let inWord = false; let wordStart = 0;
            for (let i = 0; i <= newText.length; i++) {
                const ch = newText[i] || ' ';
                const isSpace = /\s/.test(ch);
                if (!inWord && !isSpace) { inWord = true; wordStart = i; }
                if (inWord && (isSpace || i === newText.length)) {
                    if (cursorPos >= wordStart && cursorPos <= i) { wordIdx = curWord; break; }
                    curWord++; inWord = false;
                }
            }
            if (wordIdx === -1) {
                // cursor on whitespace — find nearest word
                let best = -1; let bestDist = Infinity; let w = 0; let ws = -1; let iw = false;
                for (let i = 0; i <= newText.length; i++) {
                    const ch = newText[i] || ' ';
                    const isSpace = /\s/.test(ch);
                    if (!iw && !isSpace) { iw = true; ws = i; }
                    if (iw && (isSpace || i === newText.length)) {
                        const mid = (ws + i) / 2;
                        const dist = Math.abs(cursorPos - mid);
                        if (dist < bestDist) { bestDist = dist; best = w; }
                        w++; iw = false;
                    }
                }
                wordIdx = best;
            }
            return wordIdx;
        })();
        // Split attached emoji without spacing like "hello😁" -> ["hello", "😁"] so emoji renders color/animated
        const _emojiSplitRe = /([\u{1F300}-\u{1FAFF}\u2600-\u27BF\u2300-\u23FF\u2B50\u2764])/u;
        const rawWords = newText.split(/\s+/).filter(w => w.length > 0);
        const newWords = [];
        for (const w of rawWords) {
            // if word contains emoji mixed with text, split on emoji boundaries
            if (/[\u{1F300}-\u{1FAFF}\u2600-\u27BF\u2300-\u23FF\u2B50\u2764]/u.test(w) && /[a-zA-Z0-9]/.test(w)) {
                // use matchAll to split keep emoji
                const re = /(?:\uFE0F|[\u{1F300}-\u{1FAFF}\u2600-\u27BF\u2300-\u23FF\u2B50\u2764\u200D])+/gu;
                let m;
                let idx = 0;
                while ((m = re.exec(w)) !== null) {
                    if (m.index > idx) newWords.push(w.slice(idx, m.index));
                    newWords.push(m[0]);
                    idx = m.index + m[0].length;
                }
                if (idx < w.length) newWords.push(w.slice(idx));
                if (newWords.length === 0) newWords.push(w);
            } else {
                newWords.push(w);
            }
        }
        if (newWords.length === 0 || originalCaptions.length === 0) {
            setCaptions([]);
            return;
        }
        const oldWords = baseCaptions.map(c => c.text);
        const m = oldWords.length, n = newWords.length;
        const dp = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0));
        for (let i = m - 1; i >= 0; i--) {
            for (let j = n - 1; j >= 0; j--) {
                if (oldWords[i].toLowerCase() === newWords[j].toLowerCase()) dp[i][j] = dp[i+1][j+1] + 1;
                else dp[i][j] = Math.max(dp[i+1][j], dp[i][j+1]);
            }
        }
        const oldToNew = Array(m).fill(-1);
        const newToOld = Array(n).fill(-1);
        let i = 0, j = 0;
        while (i < m && j < n) {
            if (oldWords[i].toLowerCase() === newWords[j].toLowerCase()) {
                oldToNew[i] = j; newToOld[j] = i; i++; j++;
            } else if (dp[i+1][j] >= dp[i][j+1]) i++;
            else j++;
        }

        // Collect matched anchors sorted by new index
        const anchors = [];
        for (let nj = 0; nj < n; nj++) if (newToOld[nj] !== -1) anchors.push({ nj, oi: newToOld[nj] });
        // sentinel boundaries
        const FIXED_MS = 100;

        const newCaptions = [];
        // iterate gaps: before first anchor, between anchors, after last anchor
        const gaps = [];
        if (anchors.length === 0) {
            // No matches at all — treat as all new, sequence from original start with fixed slots near playback
            gaps.push({ newStart: 0, newEnd: n - 1, oldStart: 0, oldEnd: m - 1, prevEnd: baseCaptions[0].startMs - 6, nextStart: baseCaptions[m-1].endMs + 2000 });
        } else {
            // before first
            gaps.push({ newStart: 0, newEnd: anchors[0].nj - 1, oldStart: 0, oldEnd: anchors[0].oi - 1, prevEnd: baseCaptions[0].startMs - 200, nextStart: baseCaptions[anchors[0].oi].startMs });
            for (let a = 0; a < anchors.length - 1; a++) {
                gaps.push({
                    newStart: anchors[a].nj + 1,
                    newEnd: anchors[a+1].nj - 1,
                    oldStart: anchors[a].oi + 1,
                    oldEnd: anchors[a+1].oi - 1,
                    prevEnd: baseCaptions[anchors[a].oi].endMs,
                    nextStart: baseCaptions[anchors[a+1].oi].startMs,
                });
            }
            gaps.push({ newStart: anchors[anchors.length-1].nj + 1, newEnd: n - 1, oldStart: anchors[anchors.length-1].oi + 1, oldEnd: m - 1, prevEnd: baseCaptions[anchors[anchors.length-1].oi].endMs, nextStart: baseCaptions[m-1].endMs + 2000 });
        }

        // Build in order, interleaving anchors
        // We walk new index 0..n-1 merging gaps+anchors
        // Simpler: build by scanning new positions, picking from gaps or anchors
        const gapMap = new Map(); // nj -> gap that owns it
        for (const g of gaps) {
            for (let nj = g.newStart; nj <= g.newEnd; nj++) if (nj >= 0) gapMap.set(nj, g);
        }
        // For gaps we need sequential prevEnd that updates as we emit extras — use a cursor per gap
        const gapCursors = new Map(); // key gap object id -> current prevEnd
        for (const g of gaps) gapCursors.set(g, g.prevEnd);
        // Also need to track how many old slots already consumed per gap for replacement logic
        const gapOldConsumed = new Map();
        for (const g of gaps) gapOldConsumed.set(g, 0);

        for (let nj = 0; nj < n; nj++) {
            if (newToOld[nj] !== -1) {
                const oc = baseCaptions[newToOld[nj]];
                newCaptions.push({ text: newWords[nj], startMs: oc.startMs, endMs: oc.endMs });
            } else {
                const g = gapMap.get(nj);
                if (!g) { // should not happen
                    const last = newCaptions.length ? newCaptions[newCaptions.length-1].endMs + 6 : baseCaptions[0].startMs;
                    newCaptions.push({ text: newWords[nj], startMs: last, endMs: last + FIXED_MS });
                    continue;
                }
                const consumed = gapOldConsumed.get(g);
                const oldSliceLen = Math.max(0, g.oldEnd - g.oldStart + 1);
                if (consumed < oldSliceLen) {
                    const oc = baseCaptions[g.oldStart + consumed];
                    newCaptions.push({ text: newWords[nj], startMs: oc.startMs, endMs: oc.endMs });
                    gapOldConsumed.set(g, consumed + 1);
                    gapCursors.set(g, oc.endMs);
                } else {
                    // Extra word beyond old slots — anchor dur (100ms text, 1s emoji) to closest word from cursor, else sequentially after prev
                    const emojiRe2 = /[\u{1F300}-\u{1FAFF}\u2600-\u27BF\u2300-\u23FF\u2B50\u2764]/u;
                    const curWordForDur = newWords[nj];
                    const curDur = emojiRe2.test(curWordForDur) ? 1000 : FIXED_MS;
                    const prevEnd = gapCursors.get(g);
                    let s = prevEnd + 6;
                    if (caretWordIdx != null && caretWordIdx >= g.newStart && caretWordIdx <= g.newEnd) {
                        const caretNj = caretWordIdx;
                        const gapSpan = Math.max(120, g.nextStart - g.prevEnd);
                        const posInGap = (caretNj - g.newStart) / Math.max(1, g.newEnd - g.newStart + 1);
                        const cursorMsInGap = g.prevEnd + Math.round(gapSpan * posInGap);
                        s = Math.max(prevEnd + 6, Math.min(cursorMsInGap, g.nextStart - curDur - 6));
                        s = Math.max(s, prevEnd + 6);
                    }
                    if (s + curDur > g.nextStart - 6) s = Math.max(g.prevEnd + 6, g.nextStart - curDur - 6);
                    const e = s + curDur;
                    newCaptions.push({ text: newWords[nj], startMs: Math.round(s), endMs: Math.round(e) });
                    gapCursors.set(g, e);
                    gapOldConsumed.set(g, consumed + 1);
                }
            }
        }
        // Ensure strictly increasing and no overlap (small adjust, keep lengths)
        newCaptions.sort((a,b) => a.startMs - b.startMs);
        for (let k = 1; k < newCaptions.length; k++) {
            if (newCaptions[k].startMs < newCaptions[k-1].endMs + 6) {
                const shift = newCaptions[k-1].endMs + 6 - newCaptions[k].startMs;
                newCaptions[k].startMs += shift;
                newCaptions[k].endMs += shift;
            }
        }
        setCaptions(newCaptions);
    }, [captions, originalCaptions]);

    // When user selects a word in the textbox, jump timeline/playhead to that word (like Edit Clip)
    const handleTextAreaSelect = useCallback(() => {
        const el = textAreaRef.current;
        if (!el || !captions.length) return;
        // Don't seek immediately after typing — would fight the caret while editing
        if (Date.now() - lastEditRef.current < 600) return;
        const pos = el.selectionStart;
        // Map character position to word index by scanning whitespace
        const text = editableText;
        let wordIdx = -1;
        let curWord = 0;
        let inWord = false;
        let wordStart = 0;
        for (let i = 0; i <= text.length; i++) {
            const ch = text[i] || ' ';
            const isSpace = /\s/.test(ch);
            if (!inWord && !isSpace) { inWord = true; wordStart = i; }
            if (inWord && (isSpace || i === text.length)) {
                // word curWord spans [wordStart, i)
                if (pos >= wordStart && pos <= i) { wordIdx = curWord; break; }
                // also handle cursor at space between words -> pick next word
                if (pos > i && isSpace) {
                    // look ahead to next word start, but keep current for now
                }
                curWord++;
                inWord = false;
            }
        }
        // If cursor is on whitespace, bias to nearest word (next if exists, else previous)
        if (wordIdx === -1) {
            // Find closest word boundary
            let best = -1; let bestDist = Infinity;
            let w = 0; let ws = -1;
            let iw = false;
            for (let i = 0; i <= text.length; i++) {
                const ch = text[i] || ' ';
                const isSpace = /\s/.test(ch);
                if (!iw && !isSpace) { iw = true; ws = i; }
                if (iw && (isSpace || i === text.length)) {
                    const we = i;
                    const mid = (ws + we) / 2;
                    const dist = Math.abs(pos - mid);
                    if (dist < bestDist) { bestDist = dist; best = w; }
                    w++; iw = false;
                }
            }
            wordIdx = best;
        }
        if (wordIdx < 0 || wordIdx >= captions.length) return;
        const w = captions[wordIdx];
        if (!w) return;
        // Jump viewport + playhead to that word
        setFollowPlayhead(true);
        seekToMs(w.startMs);
    }, [captions, editableText, seekToMs]);

    const selectWordInTextarea = useCallback((wordIndex) => {
        const el = textAreaRef.current;
        if (!el) return;
        const text = editableText;
        let curWord = 0; let inWord = false; let ws = 0;
        for (let i = 0; i <= text.length; i++) {
            const ch = text[i] || ' ';
            const isSpace = /\s/.test(ch);
            if (!inWord && !isSpace) { inWord = true; ws = i; }
            if (inWord && (isSpace || i === text.length)) {
                if (curWord === wordIndex) {
                    const we = i;
                    // Select only the word chars, not the following space
                    const wordLen = text.slice(ws, we).length;
                    el.focus({ preventScroll: true });
                    requestAnimationFrame(() => {
                        try { el.setSelectionRange(ws, ws + wordLen); } catch { /* ignored */ }
                        el.scrollTop = el.scrollHeight;
                    });
                    lastEditRef.current = Date.now() + 800;
                    return;
                }
                curWord++; inWord = false;
            }
        }
    }, [editableText]);

    const insertEmojiAtCaret = useCallback((emoji) => {
        const el = textAreaRef.current;
        // If a word is highlighted (selection covers word chars), replace that word at playhead? spec: highlighted wins, else at playhead
        const hasSelection = el && el.selectionStart !== el.selectionEnd && editableText.slice(el.selectionStart, el.selectionEnd).trim().length > 0;
        if (hasSelection) {
            const pos = el.selectionStart;
            const end = el.selectionEnd;
            const before = editableText.slice(0, pos);
            const after = editableText.slice(end);
            const newText = before + emoji + after;
            const newPos = before.length + emoji.length;
            handleTextEdit(newText, newPos);
            requestAnimationFrame(() => {
                if (el) { el.focus({ preventScroll: true }); try { el.setSelectionRange(newPos, newPos); } catch { /* ignored */ } }
            });
        } else {
            // No selection -> insert at playhead point (timeline position), not textbox caret
            const ms = playerTimeRef.current ?? playbackMs;
            // Find insertion index at playhead
            let insertIdx = captions.findIndex(c => c.startMs > ms);
            if (insertIdx === -1) insertIdx = captions.length;
            // If playhead inside a word, treat as after that word (don't split word)
            const insideIdx = captions.findIndex(c => ms >= c.startMs && ms < c.endMs);
            if (insideIdx !== -1) insertIdx = insideIdx + 1;
            const words = editableText.split(/\s+/).filter(Boolean);
            const newWords = [...words];
            newWords.splice(insertIdx, 0, emoji);
            const newText = newWords.join(' ');
            // Compute caption timing at playhead (gap-aware, 1s or available)
            const prevEnd = insertIdx > 0 ? captions[insertIdx-1]?.endMs + 6 : 0;
            const nextStart = insertIdx < captions.length ? captions[insertIdx]?.startMs - 6 : durationSec*1000;
            let cs = Math.max(prevEnd, Math.round(ms));
            let avail = nextStart - cs;
            let dur = 1000;
            if (avail < 1000 && avail >= 300) dur = avail;
            else if (avail < 300) {
                // steal from previous if possible to get 300ms, else use avail
                if (insertIdx > 0) {
                    const prev = captions[insertIdx-1];
                    const prevDur = prev ? (prev.endMs - prev.startMs) : 0;
                    if (prevDur > 80) dur = 300;
                    else dur = Math.max(80, avail);
                } else dur = Math.max(80, avail);
            }
            if (cs + dur > nextStart) cs = Math.max(prevEnd, nextStart - dur);
            let ce = cs + dur;
            // Build new captions via handleTextEdit would lose timing; directly splice with correct timing
            const newCaps = [...captions];
            newCaps.splice(insertIdx, 0, { text: emoji, startMs: Math.round(cs), endMs: Math.round(ce) });
            // If we stole, shorten previous word
            if (avail < 300 && insertIdx > 0 && newCaps[insertIdx-1]) {
                const prev = newCaps[insertIdx-1];
                const need = 300 - Math.max(0, avail);
                if (prev.endMs - prev.startMs - need >= 80) {
                    prev.endMs = prev.endMs - need;
                    // re-adjust cs after shrink
                    newCaps[insertIdx].startMs = prev.endMs + 6;
                    newCaps[insertIdx].endMs = newCaps[insertIdx].startMs + dur;
                }
            }
            setEditableText(newText);
            setCaptions(newCaps);
            const newPos = newText.indexOf(emoji, 0) + emoji.length;
            requestAnimationFrame(() => {
                if (el) { el.focus({ preventScroll: true }); try { el.setSelectionRange(newPos, newPos); } catch { /* ignored */ } }
            });
            setFollowPlayhead(true);
            seekToMs(cs);
        }
        // Update recent and auto-close picker
        setRecentEmojis(prev => {
            const next = [emoji, ...prev.filter(e => e !== emoji)].slice(0, 24);
            try { localStorage.setItem('openshorts_recent_emojis', JSON.stringify(next)); } catch { /* ignored */ }
            return next;
        });
        setShowEmojiPicker(false);
        setEmojiSearch('');
    }, [editableText, captions, playbackMs, durationSec, seekToMs, handleTextEdit]);

    const insertWordAtPlayhead = useCallback(() => {
        if (!captions.length) return;
        const ms = playerTimeRef.current ?? playbackMs;
        // Is playhead in whitespace?
        const insideWord = captions.some(c => ms >= c.startMs && ms < c.endMs);
        if (insideWord) return; // only when in whitespace
        // Find insertion index: number of words ending before ms
        let insertIdx = captions.findIndex(c => c.startMs > ms);
        if (insertIdx === -1) insertIdx = captions.length;
        // Also verify gap: previous word ends before ms and next starts after
        const placeholder = 'word';
        const words = editableText.split(/\s+/).filter(Boolean);
        // Insert placeholder into text at insertIdx
        const newWords = [...words];
        newWords.splice(insertIdx, 0, placeholder);
        const newText = newWords.join(' ');
        // Compute new word timing at playhead
        const s = Math.round(ms);
        const e = Math.round(ms + 100);
        const newCaptions = [...captions];
        // Ensure no overlap: clamp to neighbors
        const prevEnd = insertIdx > 0 ? newCaptions[insertIdx - 1].endMs + 6 : 0;
        const nextStart = insertIdx < newCaptions.length ? newCaptions[insertIdx].startMs - 6 : durationSec * 1000;
        let cs = Math.max(s, prevEnd);
        let ce = Math.min(e, nextStart);
        if (ce - cs < 80) { ce = cs + 100; if (ce > nextStart) { cs = Math.max(prevEnd, nextStart - 100); ce = cs + 100; } }
        newCaptions.splice(insertIdx, 0, { text: placeholder, startMs: cs, endMs: ce });
        setEditableText(newText);
        setCaptions(newCaptions);
        // Select the new placeholder for immediate typing
        requestAnimationFrame(() => {
            const el = textAreaRef.current;
            if (!el) return;
            el.focus({ preventScroll: true });
            // compute char offsets for new word
            let curWord = 0; let inWord = false; let ws = 0;
            for (let i = 0; i <= newText.length; i++) {
                const ch = newText[i] || ' ';
                const isSpace = /\s/.test(ch);
                if (!inWord && !isSpace) { inWord = true; ws = i; }
                if (inWord && (isSpace || i === newText.length)) {
                    if (curWord === insertIdx) {
                        const we = i;
                        try { el.setSelectionRange(ws, we); } catch { /* ignored */ }
                        break;
                    }
                    curWord++; inWord = false;
                }
            }
        });
        setFollowPlayhead(true);
        seekToMs(s);
    }, [captions, editableText, playbackMs, durationSec, seekToMs]);

    const MIN_WORD_MS = 80;
    const GAP_MS = 6;
    // Global mouse listeners so drag continues outside the timeline box — uses zoomed viewport scale when zoomed
    useEffect(() => {
        if (!dragging || !timelineRef) return;
        const durMs = durationSec * 1000;
        const vpMs = zoomSec === 0 ? durMs : Math.min(durMs, zoomSec * 1000);
        const updateWordTiming = (index, nextStart, nextEnd) => {
            setCaptions(prev => {
                const cap = prev[index];
                if (!cap) return prev;
                const prevEnd = index > 0 ? prev[index - 1].endMs + GAP_MS : 0;
                const nextStartLimit = index < prev.length - 1 ? prev[index + 1].startMs - GAP_MS : durationSec * 1000;
                let s = Math.max(prevEnd, Math.min(nextStart, nextEnd - MIN_WORD_MS));
                let e = Math.min(nextStartLimit, Math.max(nextEnd, s + MIN_WORD_MS));
                // Clamp to valid after s adjustment
                s = Math.min(s, e - MIN_WORD_MS);
                const copy = prev.map((c, i) => i === index ? { ...c, startMs: Math.round(s), endMs: Math.round(e) } : c);
                return copy;
            });
        };
        const handleMove = (e) => {
            const rect = timelineRef.getBoundingClientRect();
            const pxToMs = vpMs / Math.max(1, rect.width);
            const deltaMs = (e.clientX - dragging.startX) * pxToMs;
            const idx = dragging.index;
            if (dragging.mode === 'move') {
                const w = dragging.origEnd - dragging.origStart;
                let ns = dragging.origStart + deltaMs;
                let ne = ns + w;
                const prevEnd = idx > 0 ? captions[idx - 1].endMs + GAP_MS : 0;
                const nextStart = idx < captions.length - 1 ? captions[idx + 1].startMs - GAP_MS : durMs;
                if (ns < prevEnd) { ns = prevEnd; ne = ns + w; }
                if (ne > nextStart) { ne = nextStart; ns = ne - w; }
                if (ns >= 0 && ne <= durMs) updateWordTiming(idx, ns, ne);
            } else if (dragging.mode === 'left') {
                updateWordTiming(idx, dragging.origStart + deltaMs, dragging.origEnd);
            } else if (dragging.mode === 'right') {
                updateWordTiming(idx, dragging.origStart, dragging.origEnd + deltaMs);
            }
        };
        const handleUp = () => setDragging(null);
        window.addEventListener('mousemove', handleMove);
        window.addEventListener('mouseup', handleUp);
        return () => { window.removeEventListener('mousemove', handleMove); window.removeEventListener('mouseup', handleUp); };
    }, [dragging, timelineRef, durationSec, captions, zoomSec]);

    const durMs = Math.max(1, durationSec * 1000);
    const viewportMs = zoomSec === 0 ? durMs : Math.min(durMs, zoomSec * 1000);
    // Effective pan: when following, derive from playback without extra state churn (fluid)
    const effectivePanMs = followPlayhead && !draggingPlayhead ? Math.max(0, Math.min(durMs - viewportMs, playbackMs - viewportMs * 0.33)) : panMs;

    // Clamp manual pan when zoom changes
    useEffect(() => {
        if (!followPlayhead) setPanMs((prev) => Math.max(0, Math.min(durMs - viewportMs, prev)));
    }, [viewportMs, durMs, followPlayhead]);

    const ZOOM_OPTIONS = [
        { value: 3, label: '3s' },
        { value: 5, label: '5s' },
        { value: 10, label: '10s' },
        { value: 20, label: '20s' },
        { value: 0, label: 'Full' },
    ];

    // Memoize so rAF playback updates don't thrash Remotion Player and cause audio jumps — must be before early return (Rules of Hooks)
    const subtitleConfig = React.useMemo(() => ({
        captions,
        position,
        marginV,
        maxChars,
        maxDuration,
        wordGap,
        lineHeight,
        letterSpacing,
        style: {
            fontFamily: fontName,
            fontSize: fontSize,
            fontColor,
            highlightColor,
            borderColor,
            borderWidth: borderWidth * 1.5,
            bgColor,
            bgOpacity,
            animation,
            baseOpacity: style === 'karaoke' ? baseOpacity : 1,
            uppercase: style === 'karaoke' ? uppercase : false,
            marginV,
            wordGap,
            lineHeight,
            letterSpacing,
        },
    }), [captions, position, marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing, fontName, fontSize, fontColor, highlightColor, borderColor, borderWidth, bgColor, bgOpacity, animation, style, baseOpacity, uppercase]);

    if (!isOpen) return null;

    // Fallback: static CSS preview (same as original)
    const bw = Math.max(borderWidth, 0);
    const bc = borderColor;
    const outlineShadow = bw > 0 ? [
        `-${bw}px -${bw}px 0 ${bc}`, `${bw}px -${bw}px 0 ${bc}`,
        `-${bw}px ${bw}px 0 ${bc}`, `${bw}px ${bw}px 0 ${bc}`,
        `0 -${bw}px 0 ${bc}`, `0 ${bw}px 0 ${bc}`,
        `-${bw}px 0 0 ${bc}`, `${bw}px 0 0 ${bc}`,
    ].join(', ') : 'none';

    const fallbackPreviewStyle = {
        fontFamily: fontName,
        color: fontColor,
        fontSize: '20px',
        fontWeight: 'bold',
        maxWidth: '85%',
        padding: '6px 12px',
        borderRadius: '4px',
        textAlign: 'center',
        lineHeight: '1.3',
        ...(bgOpacity > 0
            ? {
                backgroundColor: `${bgColor}${Math.round(bgOpacity * 255).toString(16).padStart(2, '0')}`,
                textShadow: 'none',
            }
            : { textShadow: outlineShadow }
        ),
    };

    return (
        <Modal isOpen={isOpen} onClose={onClose} size="2xl" eyebrow="EDITOR \u00b7 SUBTITLES" title="subtitles">
            <div className="flex flex-col lg:flex-row gap-6">
                {/* Left: Preview — fixed, right takes more space */}
                <div className="w-full lg:w-[360px] xl:w-[380px] shrink-0 flex flex-col items-center justify-center bg-black rounded-card border border-rule overflow-hidden relative aspect-[9/16] max-h-[580px] lg:sticky lg:top-0">
                    {captionsLoading ? (
                        <div className="flex items-center gap-2 text-muted">
                            <Loader2 size={16} className="animate-spin" />
                            <span className="text-sm lowercase">Loading preview...</span>
                        </div>
                    ) : useRemotionPreview ? (
                        <RemotionPreview
                            playerRef={playerRef}
                            videoUrl={videoUrl}
                            durationInSeconds={durationSec}
                            subtitles={subtitleConfig}
                            hook={existingHook || null}
                            onTimeUpdate={(ms) => {
                                if (draggingPlayhead) return;
                                playerTimeRef.current = ms;
                                // throttle setState to avoid 30fps double-render storm — update only if >40ms change or 2 frames
                                setPlaybackMs((prev) => Math.abs(prev - ms) > 35 ? ms : prev);
                            }}
                        />
                    ) : (
                        <>
                            <video src={videoUrl} className="w-full h-full object-contain opacity-50" muted playsInline />
                            <div className={`absolute w-full px-8 text-center transition-all duration-300 pointer-events-none flex flex-col items-center justify-center
                                ${position === 'top' ? 'top-20' : ''}
                                ${position === 'middle' ? 'top-0 bottom-0' : ''}
                                ${position === 'bottom' ? 'bottom-20' : ''}
                            `}>
                                <span style={fallbackPreviewStyle}>
                                    This is how your subtitles<br/>will appear on the video
                                </span>
                            </div>
                        </>
                    )}
                </div>

                {/* Right: Controls — takes more space */}
                <div className="flex-1 min-w-0 flex flex-col">
                    <div className="space-y-5 flex-1 overflow-y-auto custom-scrollbar pr-1">
                        {/* Caption presets (server-side karaoke burn) */}
                        <div>
                            <p className="eyebrow mb-2">Preset</p>
                            <div className="grid grid-cols-3 gap-1.5">
                                {[...CAPTION_PRESETS, ...userPresets].map((p) => {
                                    const isUser = p.id.startsWith('user-');
                                    return (
                                    <button
                                        key={p.id}
                                        onClick={() => applyPreset(p)}
                                        className={`relative px-2 py-1.5 rounded-input border text-xs transition-colors flex items-center gap-1.5 justify-center
                                            ${activePreset === p.id
                                                ? 'border-[color:var(--color-accent)] text-ink'
                                                : 'border-rule2 text-muted hover:border-[color:var(--color-accent)]'}`}
                                        title={p.label}
                                    >
                                        <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: p.highlightColor }} />
                                        {p.label}
                                        {isUser && (
                                            <span
                                                role="button"
                                                aria-label={`Delete preset ${p.label}`}
                                                onClick={(e) => { e.stopPropagation(); removePreset(p.id); }}
                                                className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-black text-white text-[10px] leading-none flex items-center justify-center hover:bg-red-600 border border-white/20"
                                            >×</span>
                                        )}
                                    </button>
                                    );
                                })}
                            </div>
                            {userPresets.length === 0 && (
                                <p className="text-[11px] text-muted mt-1.5">Save your first preset below</p>
                            )}
                            <div className="flex gap-2 mt-2">
                                <button onClick={handleSaveDefault} className="flex-1 px-2 py-1.5 rounded-input border border-rule2 text-xs text-muted hover:text-ink hover:border-[color:var(--color-accent)]">Save as default</button>
                                <button onClick={handleSavePreset} className="flex-1 px-2 py-1.5 rounded-input border border-rule2 text-xs text-muted hover:text-ink hover:border-[color:var(--color-accent)]">Save as preset</button>
                            </div>
                            <div className="flex gap-2 mt-2">
                                <button onClick={handleResetToDefault} className="flex-1 px-2 py-1.5 rounded-input border border-rule2 text-xs text-muted hover:text-ink hover:border-[color:var(--color-accent)]">Reset to default</button>
                                {presetDirty && <button onClick={handleUpdatePreset} className="flex-1 px-2 py-1.5 rounded-input border border-brass bg-brass/10 text-xs text-brass hover:bg-brass hover:text-brassink">Update preset</button>}
                            </div>
                            {toast && <div className="mt-2 text-xs text-center text-brass bg-paper2 rounded px-2 py-1 animate-fade">{toast}</div>}
                            {style === 'karaoke' && (
                                <div className="mt-3 space-y-3 animate-fade">
                                    <div className="flex items-center justify-between">
                                        <span className="readout">UPPERCASE</span>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input type="checkbox" checked={uppercase} onChange={(e) => setUppercase(e.target.checked)} className="sr-only peer" />
                                            <div className="w-8 h-4 rounded-full bg-paper3 peer-checked:bg-brass transition-colors after:content-[''] after:absolute after:top-0 after:left-0 after:h-4 after:w-4 after:rounded-full after:bg-ink after:transition-all peer-checked:after:translate-x-full"></div>
                                        </label>
                                    </div>
                                    <div>
                                        <div className="flex justify-between mb-1">
                                            <span className="readout">Dim inactive words</span>
                                            <span className="readout">{Math.round(baseOpacity * 100)}%</span>
                                        </div>
                                        <input
                                            type="range"
                                            min="30"
                                            max="100"
                                            value={Math.round(baseOpacity * 100)}
                                            onChange={(e) => setBaseOpacity(parseInt(e.target.value) / 100)}
                                            className="w-full accent-[var(--color-accent)]"
                                        />
                                    </div>
                                </div>
                            )}
                        </div>

                        {/* Position Selector */}
                        <div>
                            <p className="eyebrow mb-2">Position</p>
                            <SegmentedControl
                                options={POSITION_OPTIONS}
                                value={position}
                                onChange={(v) => {
                                    setPosition(v);
                                    if (v === 'top') setMarginV(15);
                                    else if (v === 'middle') setMarginV(50);
                                    else if (v === 'bottom') setMarginV(85);
                                }}
                                size="sm"
                            />
                        </div>

                        {/* Layout — vertical + size + density (Task 5) */}
                        <div>
                            <p className="eyebrow mb-2">Layout</p>
                            <div className="space-y-4">
                                <div>
                                    <div className="flex justify-between mb-1">
                                        <span className="readout">Vertical position</span>
                                        <span className="readout tabular-nums">{marginV}</span>
                                    </div>
                                    <input
                                        type="range"
                                        aria-label="Vertical position"
                                        min="0"
                                        max="100"
                                        value={marginV}
                                        onChange={(e) => {
                                            const v = parseInt(e.target.value);
                                            setMarginV(v);
                                            if (v <= 25) setPosition('top');
                                            else if (v >= 75) setPosition('bottom');
                                            else setPosition('middle');
                                        }}
                                        className="w-full accent-[var(--color-accent)]"
                                    />
                                    <div className="flex gap-1.5 mt-1.5">
                                        <button onClick={() => { setMarginV(15); setPosition('top'); }} className={`flex-1 px-2 py-1 text-xs rounded-input border ${marginV===15 ? 'bg-brass text-brassink border-brass' : 'border-rule2 text-muted hover:border-[color:var(--color-accent)]'}`}>Top</button>
                                        <button onClick={() => { setMarginV(50); setPosition('middle'); }} className={`flex-1 px-2 py-1 text-xs rounded-input border ${marginV===50 ? 'bg-brass text-brassink border-brass' : 'border-rule2 text-muted hover:border-[color:var(--color-accent)]'}`}>Middle</button>
                                        <button onClick={() => { setMarginV(85); setPosition('bottom'); }} className={`flex-1 px-2 py-1 text-xs rounded-input border ${marginV===85 ? 'bg-brass text-brassink border-brass' : 'border-rule2 text-muted hover:border-[color:var(--color-accent)]'}`}>Bottom</button>
                                    </div>
                                </div>
                                <div>
                                    <div className="flex justify-between mb-1">
                                        <span className="readout">Size</span>
                                        <span className="readout tabular-nums">{fontSize}px</span>
                                    </div>
                                    <input
                                        type="range"
                                        aria-label="Font size"
                                        min="18"
                                        max="72"
                                        value={fontSize}
                                        onChange={(e) => setFontSize(parseInt(e.target.value))}
                                        className="w-full accent-[var(--color-accent)]"
                                    />
                                </div>
                                <div className="grid grid-cols-2 gap-3">
                                    <div>
                                        <div className="flex justify-between mb-1">
                                            <span className="readout">Words per block</span>
                                            <span className="readout tabular-nums">{maxChars}</span>
                                        </div>
                                        <input
                                            type="range"
                                            aria-label="Words per block"
                                            min="8"
                                            max="28"
                                            value={maxChars}
                                            onChange={(e) => setMaxChars(parseInt(e.target.value))}
                                            className="w-full accent-[var(--color-accent)]"
                                        />
                                    </div>
                                    <div>
                                        <div className="flex justify-between mb-1">
                                            <span className="readout">Block duration</span>
                                            <span className="readout tabular-nums">{(maxDuration/1000).toFixed(1)}s</span>
                                        </div>
                                        <input
                                            type="range"
                                            aria-label="Block duration"
                                            min="800"
                                            max="3000"
                                            step="100"
                                            value={maxDuration}
                                            onChange={(e) => setMaxDuration(parseInt(e.target.value))}
                                            className="w-full accent-[var(--color-accent)]"
                                        />
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Spacing collapsible (Task 6) */}
                        <div>
                            <button
                                type="button"
                                onClick={() => setSpacingOpen(!spacingOpen)}
                                className="w-full flex items-center justify-between"
                                aria-expanded={spacingOpen}
                            >
                                <span className="eyebrow">Spacing</span>
                                <span className={`text-muted transition-transform ${spacingOpen ? 'rotate-180' : ''}`}>▾</span>
                            </button>
                            {spacingOpen && (
                                <div className="mt-3 space-y-4 animate-fade">
                                    <div>
                                        <div className="flex justify-between mb-1">
                                            <span className="readout">Word gap</span>
                                            <span className="readout tabular-nums">{wordGap}px</span>
                                        </div>
                                        <input
                                                type="range"
                                                aria-label="Word gap"
                                                min="0"
                                                max="100"
                                                value={wordGap}
                                                onChange={(e) => setWordGap(parseInt(e.target.value))}
                                                className="w-full accent-[var(--color-accent)]"
                                            />
                                            <div className="flex justify-between text-[10px] text-muted">
                                                <span>Tight</span><span>Wide</span>
                                            </div>
                                        </div>
                                        <div>
                                            <div className="flex justify-between mb-1">
                                                <span className="readout">Letter spacing</span>
                                                <span className="readout tabular-nums">{letterSpacing}px</span>
                                            </div>
                                            <input
                                                type="range"
                                                aria-label="Letter spacing"
                                                min="-2"
                                                max="20"
                                                step="0.5"
                                                value={letterSpacing}
                                                onChange={(e) => setLetterSpacing(parseFloat(e.target.value))}
                                                className="w-full accent-[var(--color-accent)]"
                                            />
                                            <div className="flex justify-between text-[10px] text-muted">
                                                <span>Condensed</span><span>Expanded</span>
                                            </div>
                                        </div>
                                </div>
                            )}
                        </div>

                        {/* Animation Style (new) */}
                        <div>
                            <p className="eyebrow mb-2">Animation</p>
                            <SegmentedControl
                                options={ANIMATION_OPTIONS}
                                value={animation}
                                onChange={setAnimation}
                                columns={2}
                                size="sm"
                            />
                        </div>

                        {/* Editable Transcript (collapsible) */}
                        {useRemotionPreview && (
                            <div>
                                <button
                                    type="button"
                                    onClick={() => setShowTextEditor(!showTextEditor)}
                                    className="w-full flex items-center justify-between mb-2"
                                >
                                    <span className="eyebrow">Edit text ({captions.length} words)</span>
                                    <span className={`text-muted transition-transform ${showTextEditor ? 'rotate-180' : ''}`}>▾</span>
                                </button>
                                {showTextEditor && (
                                    <>
                                        <div className="flex gap-2 items-start">
                                            <div className="flex-1 relative">
                                                <textarea
                                                    ref={textAreaRef}
                                                    value={editableText}
                                                    onChange={(e) => handleTextEdit(e.target.value, e.target.selectionStart)}
                                                    onSelect={handleTextAreaSelect}
                                                    onClick={handleTextAreaSelect}
                                                    onKeyUp={(e) => {
                                                        if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(e.key)) handleTextAreaSelect();
                                                    }}
                                                    rows={3}
                                                    className="input-field resize-none leading-relaxed animate-fade pr-3"
                                                    placeholder="Edit subtitle text — click timeline word to select, + on gap to add, emojis stay inline"
                                                />
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => setShowEmojiPicker(v => !v)}
                                                className="w-11 h-11 shrink-0 rounded-input bg-paper2 border border-rule2 flex items-center justify-center text-xl hover:border-brass hover:bg-paper3 transition-colors"
                                                title="Add emoji"
                                            >😊</button>
                                        </div>
                                        {showEmojiPicker && (
                                            <div className="mt-2 p-3 bg-paper2 border border-rule2 rounded-card shadow-lg animate-fade max-h-[320px] overflow-y-auto custom-scrollbar">
                                                <div className="flex items-center gap-2 mb-2">
                                                    <input
                                                        autoFocus
                                                        type="text"
                                                        value={emojiSearch}
                                                        onChange={(e) => setEmojiSearch(e.target.value)}
                                                        placeholder="Search emojis — try bomb, skull, fire, heart..."
                                                        className="flex-1 input-field text-sm py-1.5"
                                                    />
                                                    <button onClick={() => setShowEmojiPicker(false)} className="text-xs text-muted hover:text-ink px-2">✕</button>
                                                </div>
                                                {emojiSearch.trim() ? (
                                                    <div>
                                                        <p className="text-[10px] text-muted uppercase tracking-wide mb-1">Results for “{emojiSearch}” — {EMOJI_DATA.filter(d => d.n.includes(emojiSearch.toLowerCase()) || d.k.includes(emojiSearch.toLowerCase()) || d.e.includes(emojiSearch)).length} found</p>
                                                        <div className="grid grid-cols-10 gap-1">
                                                            {EMOJI_DATA.filter(d => d.n.includes(emojiSearch.toLowerCase()) || d.k.includes(emojiSearch.toLowerCase()) || d.e.includes(emojiSearch)).slice(0, 80).map(d => (
                                                                <button key={'s-'+d.e} onClick={() => insertEmojiAtCaret(d.e)} className="w-8 h-8 flex items-center justify-center hover:bg-paper3 rounded text-lg" title={d.n}>{d.e}</button>
                                                            ))}
                                                        </div>
                                                        {EMOJI_DATA.filter(d => d.n.includes(emojiSearch.toLowerCase()) || d.k.includes(emojiSearch.toLowerCase())).length === 0 && (
                                                            <p className="text-xs text-muted mt-2">No matches — try “bomb”, “skull”, “fire”, “heart”, “laugh”</p>
                                                        )}
                                                    </div>
                                                ) : (
                                                    <>
                                                        {recentEmojis.length > 0 && (
                                                            <div className="mb-3">
                                                                <p className="text-[10px] text-muted uppercase tracking-wide mb-1">Recent</p>
                                                                <div className="grid grid-cols-10 gap-1">
                                                                    {recentEmojis.map(em => (
                                                                        <button key={'r-'+em} onClick={() => insertEmojiAtCaret(em)} className="w-8 h-8 flex items-center justify-center hover:bg-paper3 rounded text-lg">{em}</button>
                                                                    ))}
                                                                </div>
                                                            </div>
                                                        )}
                                                        {EMOJI_CATEGORIES.slice(1).map(cat => (
                                                            <div key={cat.name} className="mb-3">
                                                                <p className="text-[10px] text-muted uppercase tracking-wide mb-1">{cat.name} — {cat.emojis.length}</p>
                                                                <div className="grid grid-cols-10 gap-1">
                                                                    {cat.emojis.slice(0, 60).map(em => (
                                                                        <button key={cat.name+'-'+em} onClick={() => insertEmojiAtCaret(em)} className="w-8 h-8 flex items-center justify-center hover:bg-paper3 rounded text-lg">{em}</button>
                                                                    ))}
                                                                </div>
                                                            </div>
                                                        ))}
                                                    </>
                                                )}
                                                <p className="text-[10px] text-muted mt-2">Tip: select a word in the textbox first — emoji will replace it inline with no extra space</p>
                                            </div>
                                        )}
                                        {/* Visual word timing — zoomed viewport that follows playhead */}
                                        {captions.length > 0 && (
                                            <div className="mt-3 space-y-2">
                                                <div className="flex items-center justify-between gap-2">
                                                    <span className="readout">Timing — drag words/handles · drag empty track to pan</span>
                                                    <div className="flex items-center gap-1.5">
                                                        <span className="readout text-muted hidden sm:inline">{durationSec.toFixed(1)}s · {captions.length} words</span>
                                                        <div className="flex rounded-input border border-rule2 overflow-hidden">
                                                            {ZOOM_OPTIONS.map((z) => (
                                                                <button
                                                                    key={z.value}
                                                                    onClick={() => { setZoomSec(z.value); setFollowPlayhead(true); }}
                                                                    className={`px-2 py-1 text-[11px] leading-none ${zoomSec === z.value ? 'bg-brass text-brassink' : 'bg-paper2 text-muted hover:text-ink'}`}
                                                                >{z.label}</button>
                                                            ))}
                                                        </div>
                                                    </div>
                                                </div>
                                                {/* Follow toggle + viewport readout */}
                                                <div className="flex items-center justify-between">
                                                    <label className="flex items-center gap-1.5 cursor-pointer">
                                                        <input type="checkbox" checked={followPlayhead} onChange={(e) => setFollowPlayhead(e.target.checked)} className="accent-[var(--color-accent)]" />
                                                        <span className="text-[11px] text-muted">Follow playhead</span>
                                                    </label>
                                                    <span className="text-[11px] text-muted tabular-nums">
                                                        {zoomSec === 0 ? 'Full' : `${(effectivePanMs/1000).toFixed(1)}s → ${((effectivePanMs+viewportMs)/1000).toFixed(1)}s`}
                                                        <span className="ml-2 inline-block w-2 h-2 rounded-full align-middle" style={{ background: followPlayhead ? '#22c55e' : '#a1a1aa' }} title={followPlayhead ? 'auto-following' : 'manual pan'} />
                                                    </span>
                                                </div>
                                                <div
                                                    ref={(el) => setTimelineRef(el)}
                                                    className="relative h-[92px] bg-paper2 rounded-input border border-rule2 overflow-x-hidden overflow-y-visible select-none"
                                                    onMouseDown={(e) => {
                                                        // Word lane pan only — no seek. Ruler above handles seek.
                                                        if (!timelineRef) return;
                                                        // Ignore ruler area (top 24px) and word pills
                                                        if (e.target !== e.currentTarget) return;
                                                        const rect = timelineRef.getBoundingClientRect();
                                                        if (e.clientY - rect.top < 24) return;
                                                        if (viewportMs >= durMs) return;
                                                        const pxToMs = viewportMs / Math.max(1, rect.width);
                                                        const startX = e.clientX;
                                                        const startPan = effectivePanMs;
                                                        let didDrag = false;
                                                        let hasStartedPan = false;
                                                        const onMove = (ev) => {
                                                            const dx = ev.clientX - startX;
                                                            if (Math.abs(dx) > 3) didDrag = true;
                                                            if (didDrag && !hasStartedPan) {
                                                                hasStartedPan = true;
                                                                setPanMs(startPan);
                                                                setFollowPlayhead(false);
                                                            }
                                                            if (didDrag) {
                                                                const next = Math.max(0, Math.min(durMs - viewportMs, startPan - dx * pxToMs));
                                                                setPanMs(next);
                                                            }
                                                        };
                                                        const onUp = () => {
                                                            window.removeEventListener('mousemove', onMove);
                                                            window.removeEventListener('mouseup', onUp);
                                                        };
                                                        window.addEventListener('mousemove', onMove);
                                                        window.addEventListener('mouseup', onUp);
                                                    }}
                                                >
                                                    {/* Ruler — ticks based on viewport — seek strip (only top moves playhead) */}
                                                    <div
                                                        className="absolute inset-x-0 top-0 h-6 bg-paper2/80 cursor-pointer"
                                                        title="move here — click to seek"
                                                        onMouseDown={(e) => {
                                                            if (!timelineRef) return;
                                                            const rect = timelineRef.getBoundingClientRect();
                                                            const pxToMs = viewportMs / Math.max(1, rect.width);
                                                            const clickMs = effectivePanMs + (e.clientX - rect.left) * pxToMs;
                                                            seekToMs(clickMs);
                                                            if (viewportMs >= durMs) return;
                                                            // drag on ruler to scrub
                                                            const onMove = (ev) => {
                                                                const ms = effectivePanMs + (ev.clientX - rect.left) * pxToMs;
                                                                seekToMs(ms);
                                                            };
                                                            const onUp = () => {
                                                                window.removeEventListener('mousemove', onMove);
                                                                window.removeEventListener('mouseup', onUp);
                                                            };
                                                            window.addEventListener('mousemove', onMove);
                                                            window.addEventListener('mouseup', onUp);
                                                        }}
                                                    >
                                                        {(() => {
                                                            const ticks = [];
                                                            const stepMs = viewportMs <= 4000 ? 500 : viewportMs <= 10000 ? 1000 : 2000;
                                                            const startTick = Math.ceil(effectivePanMs / stepMs) * stepMs;
                                                            for (let tMs = startTick; tMs <= effectivePanMs + viewportMs; tMs += stepMs) {
                                                                const leftPct = ((tMs - effectivePanMs) / viewportMs) * 100;
                                                                if (leftPct < 0 || leftPct > 100) continue;
                                                                ticks.push(
                                                                    <span key={tMs} className="absolute text-[9px] text-muted leading-none tabular-nums pointer-events-none" style={{ left: `calc(${leftPct}% - 10px)`, top: 5 }}>{(tMs/1000).toFixed(viewportMs <= 4000 ? 1 : 0)}s</span>
                                                                );
                                                            }
                                                            return ticks;
                                                        })()}
                                                    </div>
                                                    {/* Playhead — draggable to scrub */}
                                                    {(() => {
                                                        const leftPct = ((playbackMs - effectivePanMs) / viewportMs) * 100;
                                                        if (leftPct < -2 || leftPct > 102) return null;
                                                        return (
                                                            <div className="absolute top-0 bottom-0 z-20 pointer-events-none" style={{ left: `${leftPct}%` }}>
                                                                <div className="absolute inset-y-0 -left-3 w-6 cursor-ew-resize pointer-events-auto"
                                                                    onMouseDown={(e) => {
                                                                        e.preventDefault(); e.stopPropagation();
                                                                        if (!timelineRef) return;
                                                                        setDraggingPlayhead(true);
                                                                        setFollowPlayhead(false);
                                                                        const rect = timelineRef.getBoundingClientRect();
                                                                        const pxToMs = viewportMs / Math.max(1, rect.width);
                                                                        const onMove = (ev) => {
                                                                            const ms = effectivePanMs + (ev.clientX - rect.left) * pxToMs;
                                                                            seekToMs(ms);
                                                                        };
                                                                        const onUp = () => {
                                                                            setDraggingPlayhead(false);
                                                                            window.removeEventListener('mousemove', onMove);
                                                                            window.removeEventListener('mouseup', onUp);
                                                                        };
                                                                        window.addEventListener('mousemove', onMove);
                                                                        window.addEventListener('mouseup', onUp);
                                                                    }}
                                                                />
                                                                <div className="absolute inset-y-0 w-px bg-red-500/90" />
                                                                <div className="absolute -top-0 -left-1 w-2 h-2 bg-red-500 rotate-45" />
                                                                {(() => {
                                                                    const inWhitespace = !captions.some(c => playbackMs >= c.startMs && playbackMs < c.endMs);
                                                                    if (!inWhitespace || draggingPlayhead) return null;
                                                                    return (
                                                                        <button
                                                                            onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); insertWordAtPlayhead(); }}
                                                                            className="absolute -top-3 left-1/2 -translate-x-1/2 w-7 h-7 rounded-full bg-brass text-brassink flex items-center justify-center text-sm font-bold shadow-lg border-2 border-paper2 pointer-events-auto hover:scale-110 transition-transform z-40"
                                                                            title="Add word here (playhead in gap) — inserts and selects"
                                                                        >+</button>
                                                                    );
                                                                })()}
                                                            </div>
                                                        );
                                                    })()}
                                                    {/* Words in viewport */}
                                                    {captions.map((c, i) => {
                                                        const leftPct = ((c.startMs - effectivePanMs) / viewportMs) * 100;
                                                        const widthPct = ((c.endMs - c.startMs) / viewportMs) * 100;
                                                        // cull exactly at viewport edge — no slop, overflow-hidden clips the rest
                                                        if (c.endMs < effectivePanMs || c.startMs > effectivePanMs + viewportMs) return null;
                                                        const dur = c.endMs - c.startMs;
                                                        const isActive = playbackMs >= c.startMs && playbackMs < c.endMs;
                                                        return (
                                                            <div
                                                                key={i}
                                                                className={`absolute top-5 bottom-2 rounded-md border flex items-center justify-center text-xs font-medium cursor-grab active:cursor-grabbing overflow-hidden ${dragging?.index === i ? 'ring-2 ring-brass z-10' : ''} ${isActive ? 'z-[5]' : ''}`}
                                                                style={{
                                                                    left: `${leftPct}%`,
                                                                    width: `${Math.max(1.2, widthPct)}%`,
                                                                    backgroundColor: isActive ? 'oklch(76% 0.17 50 / 0.34)' : dragging?.index === i ? 'oklch(76% 0.17 50 / 0.28)' : 'oklch(76% 0.17 50 / 0.18)',
                                                                    borderColor: isActive ? 'oklch(76% 0.17 50 / 0.9)' : 'oklch(76% 0.17 50 / 0.45)',
                                                                    minWidth: 32,
                                                                }}
                                                                onMouseDown={(e) => {
                                                                    if (!timelineRef) return;
                                                                    e.preventDefault();
                                                                    setDragging({ index: i, mode: 'move', startX: e.clientX, origStart: c.startMs, origEnd: c.endMs });
                                                                }}
                                                                onClick={(e) => {
                                                                    if (dragging) return;
                                                                    e.stopPropagation();
                                                                    selectWordInTextarea(i);
                                                                }}
                                                                title={`${c.text}  ${(c.startMs/1000).toFixed(2)}s → ${(c.endMs/1000).toFixed(2)}s  (${dur}ms)`}
                                                            >
                                                                <div
                                                                    className="absolute left-0 top-0 bottom-0 w-3 flex items-center justify-center cursor-ew-resize bg-brass/25 hover:bg-brass/40"
                                                                    onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); setDragging({ index: i, mode: 'left', startX: e.clientX, origStart: c.startMs, origEnd: c.endMs }); }}
                                                                >
                                                                    <span className="w-0.5 h-5 rounded-full bg-ink/60" />
                                                                </div>
                                                                <span className="truncate px-4 text-ink text-[11px] leading-none pointer-events-none">{c.text}</span>
                                                                <div
                                                                    className="absolute right-0 top-0 bottom-0 w-3 flex items-center justify-center cursor-ew-resize bg-brass/25 hover:bg-brass/40"
                                                                    onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); setDragging({ index: i, mode: 'right', startX: e.clientX, origStart: c.startMs, origEnd: c.endMs }); }}
                                                                >
                                                                    <span className="w-0.5 h-5 rounded-full bg-ink/60" />
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                                <p className="text-[11px] text-muted leading-tight">Tip: words follow the red playhead when “Follow” is on · drag empty timeline to pan · handles trim · text edits keep sync via LCS</p>
                                            </div>
                                        )}
                                    </>
                                )}
                            </div>
                        )}

                        {/* Font Family */}
                        <div>
                            <p className="eyebrow mb-2">Font</p>
                            <select
                                value={fontName}
                                onChange={(e) => setFontName(e.target.value)}
                                className="input-field"
                            >
                                {FONT_OPTIONS.map((f) => (
                                    <option key={f.value} value={f.value} style={{ fontFamily: f.value }}>{f.label}</option>
                                ))}
                            </select>
                        </div>

                        {/* Text Color */}
                        <div>
                            <p className="eyebrow mb-2">Text color</p>
                            <div className="flex flex-wrap items-center gap-2.5">
                                {COLOR_PRESETS.map((c) => (
                                    <button
                                        key={c.color}
                                        onClick={() => setFontColor(c.color)}
                                        className={swatchClass(fontColor === c.color)}
                                        style={{ backgroundColor: c.color }}
                                        title={c.label}
                                    />
                                ))}
                                <label className="w-6 h-6 rounded-full border border-dashed border-rule2 cursor-pointer flex items-center justify-center hover:border-brass transition-colors overflow-hidden relative" title="Custom color">
                                    <span className="text-xs text-muted leading-none">+</span>
                                    <input type="color" value={fontColor} onChange={(e) => setFontColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
                                </label>
                            </div>
                        </div>

                        {/* Highlight Color (new) */}
                        <div>
                            <p className="eyebrow mb-2">Highlight</p>
                            <div className="flex flex-wrap items-center gap-2.5">
                                {HIGHLIGHT_PRESETS.map((c) => (
                                    <button
                                        key={c.color}
                                        onClick={() => setHighlightColor(c.color)}
                                        className={swatchClass(highlightColor === c.color)}
                                        style={{ backgroundColor: c.color }}
                                        title={c.label}
                                    />
                                ))}
                            </div>
                        </div>

                        {/* Border / Outline */}
                        <div>
                            <p className="eyebrow mb-2">Border</p>
                            <div className="flex items-center gap-3">
                                <label className="relative w-8 h-8 rounded-input border border-rule2 cursor-pointer overflow-hidden shrink-0" title="Border color">
                                    <div className="w-full h-full" style={{ backgroundColor: borderColor }} />
                                    <input type="color" value={borderColor} onChange={(e) => setBorderColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
                                </label>
                                <div className="flex-1">
                                    <input
                                        type="range"
                                        min="0"
                                        max="5"
                                        value={borderWidth}
                                        onChange={(e) => setBorderWidth(parseInt(e.target.value))}
                                        className="w-full accent-[var(--color-accent)]"
                                    />
                                    <div className="flex justify-between">
                                        <span className="readout">None</span>
                                        <span className="readout">Thick</span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Background Box */}
                        <div>
                            <div className="flex items-center justify-between mb-2">
                                <p className="eyebrow">Background</p>
                                <label className="relative inline-flex items-center cursor-pointer">
                                    <input type="checkbox" checked={bgOpacity > 0} onChange={(e) => setBgOpacity(e.target.checked ? 0.5 : 0)} className="sr-only peer" />
                                    <div className="w-8 h-4 rounded-full bg-paper3 peer-checked:bg-brass transition-colors after:content-[''] after:absolute after:top-0 after:left-0 after:h-4 after:w-4 after:rounded-full after:bg-ink after:transition-all peer-checked:after:translate-x-full"></div>
                                </label>
                            </div>
                            {bgOpacity > 0 && (
                                <div className="space-y-3 animate-fade">
                                    <div className="flex items-center gap-3">
                                        <label className="relative w-8 h-8 rounded-input border border-rule2 cursor-pointer overflow-hidden shrink-0" title="Background color">
                                            <div className="w-full h-full" style={{ backgroundColor: bgColor }} />
                                            <input type="color" value={bgColor} onChange={(e) => setBgColor(e.target.value)} className="absolute inset-0 opacity-0 cursor-pointer" />
                                        </label>
                                        <div className="flex-1">
                                            <input
                                                type="range"
                                                min="10"
                                                max="100"
                                                value={Math.round(bgOpacity * 100)}
                                                onChange={(e) => setBgOpacity(parseInt(e.target.value) / 100)}
                                                className="w-full accent-[var(--color-accent)]"
                                            />
                                            <div className="flex justify-between">
                                                <span className="readout">Transparent</span>
                                                <span className="readout">{Math.round(bgOpacity * 100)}%</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="mt-5 shrink-0 space-y-2">
                        {(() => {
                            // Text edits must survive the server render path too
                            // (issue #69): send the edited words whenever the text
                            // differs from what the transcript produced.
                            const textEdited = originalCaptions.length > 0
                                && editableText.trim() !== originalCaptions.map((c) => c.text).join(' ').trim();
                            const styleOptions = {
                                position, fontSize: fontSize, fontName, fontColor, borderColor, borderWidth, bgColor, bgOpacity,
                                marginV, maxChars, maxDuration, wordGap, lineHeight, letterSpacing,
                                // Karaoke burn (server-side ASS render)
                                style, effect, baseOpacity, uppercase, highlightColor,
                                // Remotion data
                                remotion: useRemotionPreview ? subtitleConfig : null,
                                captions: textEdited ? captions : null,
                            };
                            const bulkRunning = bulkProgress?.running;
                            return (
                                <>
                                    <div className="flex gap-2">
                                        <button onClick={onClose} className="btn-ghost">
                                            cancel
                                        </button>
                                        <button
                                            onClick={() => onGenerate(styleOptions)}
                                            disabled={isProcessing}
                                            className="btn-primary flex-1"
                                        >
                                            {(isProcessing && !bulkRunning) && <Loader2 size={16} className="animate-spin text-brassink" />}
                                            {(isProcessing && !bulkRunning) ? 'generating...' : 'apply to this clip'}
                                        </button>
                                    </div>
                                    {onApplyAll && bulkCount > 1 && (
                                        <button
                                            onClick={() => onApplyAll({ ...styleOptions, captions: null })}
                                            disabled={isProcessing}
                                            className="btn-ghost w-full flex items-center justify-center gap-2"
                                        >
                                            {bulkRunning
                                                ? <><Loader2 size={16} className="animate-spin" />applying to all… {bulkProgress.current}/{bulkProgress.total}</>
                                                : `apply this style to all ${bulkCount} clips`}
                                        </button>
                                    )}
                                    {/* Clips ship captioned by default, so the way
                                        out has to be here — otherwise a user who
                                        doesn't want captions is stuck with them. */}
                                    {onRemove && (
                                        <button
                                            onClick={onRemove}
                                            disabled={isProcessing}
                                            className="text-xs text-muted underline underline-offset-2 lowercase hover:text-ink2 disabled:opacity-50"
                                        >
                                            remove captions from this clip
                                        </button>
                                    )}
                                </>
                            );
                        })()}
                    </div>
                </div>
            </div>
        </Modal>
    );
}
