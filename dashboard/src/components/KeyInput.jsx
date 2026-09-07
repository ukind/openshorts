import React, { useState, useEffect } from 'react';
import { Key, Eye, EyeOff, Check } from 'lucide-react';

export default function KeyInput({ onKeySet, savedKey, geminiModel, setGeminiModel, dropdownOpen, setDropdownOpen }) {
    const [key, setKey] = useState(savedKey || '');
    const [isVisible, setIsVisible] = useState(false);
    const [isSaved, setIsSaved] = useState(!!savedKey);

    useEffect(() => {
        if (savedKey) setKey(savedKey);
    }, [savedKey]);

    const handleSave = () => {
        if (key.trim().length > 0) {
            onKeySet(key);
            setIsSaved(true);
        }
    };

    const geminiOptions = [
        "gemini-3.7-flash",
        "gemini-3.7-flash-preview",
        "gemini-3.6-flash",
        "gemini-3.6-flash-preview",
        "gemini-3.1-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ];

    return (
        <div className="card p-4 sm:p-6 mb-8 animate-fade">
            <div className="flex items-center gap-3 mb-4">
                <div className="p-2 bg-paper3 rounded-input text-brass">
                    <Key size={18} />
                </div>
                <h2 className="font-display lowercase text-lg text-ink">Gemini API Key</h2>
            </div>

            <div className="flex flex-col sm:flex-row gap-3">
                <div className="relative sm:flex-1">
                    <input
                        type={isVisible ? "text" : "password"}
                        value={key}
                        onChange={(e) => {
                            setKey(e.target.value);
                            setIsSaved(false);
                        }}
                        placeholder="AIzaSy..."
                        className="input-field pr-12 font-mono"
                    />
                    <button
                        onClick={() => setIsVisible(!isVisible)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-ink transition-colors"
                    >
                        {isVisible ? <EyeOff size={18} /> : <Eye size={18} />}
                    </button>
                </div>
                <button
                    onClick={handleSave}
                    disabled={!key || isSaved}
                    className={isSaved ? 'badge-ok px-4 cursor-default' : 'btn-primary'}
                >
                    {isSaved ? <><Check size={14} /> Ready</> : 'Set Key'}
                </button>
            </div>
            <p className="mt-3 text-xs text-muted">
                Your key is stored locally in your browser for convenience.
                <br />
                <a
                    href="https://aistudio.google.com/app/apikey"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-brass hover:underline mt-1 inline-block"
                >
                    Get your free Gemini API Key here →
                </a>
            </p>
            {setGeminiModel && (
                <div className="mt-5 pt-4 border-t border-rule">
                    <label className="block text-sm font-medium text-ink mb-1">Gemini model <span className="text-muted font-normal">— used for deep + scoring</span></label>
                    <div className="relative">
                        <input
                            value={geminiModel || ''}
                            onChange={(e) => setGeminiModel(e.target.value)}
                            onFocus={() => setDropdownOpen && setDropdownOpen(true)}
                            onBlur={() => setTimeout(() => setDropdownOpen && setDropdownOpen(false), 150)}
                            placeholder="gemini-3.1-flash-lite"
                            className="input-field font-mono pr-10"
                        />
                        <button type="button" onMouseDown={e=>e.preventDefault()} onClick={()=> setDropdownOpen && setDropdownOpen(v=>!v)} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-ink text-xs px-2 py-1">▼</button>
                        {dropdownOpen && (
                            <div className="absolute z-10 mt-1 w-full max-h-48 overflow-y-auto bg-paper border border-rule rounded-input shadow-lg custom-scrollbar">
                                {geminiOptions.map(m => (
                                    <button key={m} type="button" onMouseDown={e=>e.preventDefault()} onClick={()=>{ setGeminiModel(m); setDropdownOpen(false); }}
                                        className={`w-full text-left px-3 py-2 text-sm font-mono hover:bg-paper3 transition-colors ${geminiModel===m ? "bg-paper3 text-brass" : "text-ink"}`}>{m}</button>
                                ))}
                            </div>
                        )}
                    </div>
                    <p className="text-micro text-muted mt-1">Pick or type custom. Latest: 3.7 flash / 3.6 flash. Sent as <code className="readout">X-Gemini-Model</code>.</p>
                </div>
            )}
        </div>
    );
}
