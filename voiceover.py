"""
VoiceOver pipeline for OpenShorts, adapted from the AutoShorts reference
implementation (C:/AI_Studio/autoshorts — src/tts_generator.py,
src/ai_providers.py, src/subtitle_generator.py).

Contains:
- Qwen3-TTS VoiceDesign local inference (lazy import; optional dependency)
- ElevenLabs TTS via the existing OpenShorts saasshorts integration
- TTS text preprocessing (slang expansion, action-word/emoji stripping)
- 4-option caption generation against Gemini (video upload) / OpenAI (keyframes)
- Voiceover track building with per-caption TTS + measured timing
- Audio ducking (sidechain) + mixing via FFmpeg
- Word-level caption timing derived from actual TTS audio durations

OpenShorts is self-contained: nothing here imports from the AutoShorts tree.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("voiceover")

# ---------------------------------------------------------------------------
# Config (env, mirrors AutoShorts' TTS_* names where the semantics match)
# ---------------------------------------------------------------------------

QWEN_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"

LANGUAGE_MAP = {
    "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "de": "German", "fr": "French", "ru": "Russian", "pt": "Portuguese",
    "es": "Spanish", "it": "Italian",
}

def tts_language_name(code: str) -> str:
    return LANGUAGE_MAP.get((code or "en").lower(), "English")


# ---------------------------------------------------------------------------
# TTS text preprocessing — ported from AutoShorts tts_generator.py
# ---------------------------------------------------------------------------

SLANG_EXPANSIONS = {
    # GenZ slang
    "fr fr": "for real for real",
    "fr,fr": "for real for real",
    "fr, fr": "for real for real",
    "frfr": "for real for real",
    "fr": "for real",
    "rn": "right now",
    "ngl": "not gonna lie",
    "idk": "I don't know",
    "idc": "I don't care",
    "imo": "in my opinion",
    "imho": "in my humble opinion",
    "tbh": "to be honest",
    "tho": "though",
    "thru": "through",
    "u": "you",
    "ur": "your",
    "r": "are",
    "w/": "with",
    "w/o": "without",
    "bc": "because",
    "b4": "before",
    "2day": "today",
    "2nite": "tonight",
    "2morrow": "tomorrow",
    "smh": "shaking my head",
    "omg": "oh my god",
    "lol": "laughing out loud",
    "lmao": "laughing my ass off",
    "rofl": "rolling on the floor laughing",
    "brb": "be right back",
    "btw": "by the way",
    "fyi": "for your information",
    "gg": "good game",
    "ez": "easy",
    "pog": "play of the game",
    "poggers": "play of the game",
    "goat": "greatest of all time",
    "goated": "greatest of all time",
    "bussin": "bussin'",
    "finna": "fixing to",
    "gonna": "going to",
    "wanna": "want to",
    "gotta": "got to",
    "kinda": "kind of",
    "sorta": "sort of",
    "prolly": "probably",
    "aight": "alright",
    "ight": "alright",
    "yall": "y'all",
    "ya'll": "y'all",
    # Gaming terms
    "1v1": "one v one",
    "2v2": "two v two",
    "3v3": "three v three",
    "5v5": "five v five",
    "1hp": "one HP",
    "hp": "H P",
    "dps": "D P S",
    "aoe": "A O E",
    "fps": "F P S",
    "rpg": "R P G",
    "mmo": "M M O",
    "pvp": "P V P",
    "pve": "P V E",
    "npc": "N P C",
    "op": "O P",
}

_PUNCTUATION_REPLACEMENTS = {
    "...": ", ",
    "..": ", ",
    "…": ", ",
    "–": ", ",
    "--": ", ",
    "—": ", ",
    ",,": ",",
}

# Italian chat/gaming slang the caption AI legitimately writes, expanded to
# words an Italian voice actually pronounces. English expansions are skipped
# for non-English speech — "gg"→"good game" mid-Italian-sentence produces a
# jarring accent switch, so the voice hears the letters or an Italian phrase.
IT_EXPANSIONS = {
    "nn": "non",
    "cmq": "comunque",
    "qnd": "quando",
    "xk": "perché",
    "xkè": "perché",
    "peró": "però",
    "ke": "che",
    "kk": "che",
    "sn": "sono",
    "spt": "aspetta",
    "tds": "tutti e due",
    "tvb": "ti voglio bene",
    "6": "sei",
    "gg": "ge ge",
    "ez": "e zeta",
    "fr": "e erre",
    "1v1": "uno contro uno",
    "2v2": "due contro due",
    "3v3": "tre contro tre",
    "5v5": "cinque contro cinque",
    "1hp": "un punto vita",
    "hp": "punti vita",
    "dps": "danni al secondo",
    "fps": "frame al secondo",
    "rpg": "arre pe gi",
    "mmo": "emmemmo o",
    "pvp": "pi vi pi",
    "pve": "pi vi e",
    "npc": "enne pi ci",
    "op": "opp",
}

_IT_NUM_UNITS = ["", "uno", "due", "tre", "quattro", "cinque", "sei", "sette",
                 "otto", "nove", "dieci", "undici", "dodici", "tredici",
                 "quattordici", "quindici", "sedici", "diciassette",
                 "diciotto", "diciannove"]
_IT_NUM_TENS = {2: "venti", 3: "trenta", 4: "quaranta", 5: "cinquanta",
                6: "sessanta", 7: "settanta", 8: "ottanta", 9: "novanta"}


def _italian_number_to_words(n: int) -> str:
    """Italian number words for the small numbers captions actually contain
    (0-199). Above that the digits are left as-is for the model."""
    if n < 0:
        return str(n)
    if n == 0:
        return "zero"
    if n < 20:
        return _IT_NUM_UNITS[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        base = _IT_NUM_TENS[tens]
        # elision: venti+uno→ventuno, tre→ventitré (accent kept off for TTS)
        if unit == 1:
            return base[:-1] + "uno"
        if unit == 3:
            return base + "tré"
        return base + _IT_NUM_UNITS[unit]
    if n < 200:
        return "cento" + _italian_number_to_words(n - 100) if n > 100 else "cento"
    return str(n)


_IT_NUMBER_RE = re.compile(r"\d+")


def _expand_italian_numbers(text: str) -> str:
    """Standalone integers → Italian words (scores, countdowns, small counts)."""
    def _repl(m):
        val = int(m.group(0))
        return _italian_number_to_words(val) if val < 200 else m.group(0)
    return _IT_NUMBER_RE.sub(_repl, text)


def _split_italian_elisions(text: str) -> str:
    """Split elided articles so the voice pronounces both parts: "l'ho" →
    "l' ho", "dell'arma" → "dell' arma". Display captions keep the natural
    form; speak-text only. Covers the simple article (lo/la/gli/le → l') and
    the articulated/contracted ones (del/dello/della/dei/delle/degli,
    sul/sulla, nel/nella, colla, quest', gl'). "un'" stays joined — Italian
    pronounces un'amica as one flow."""
    return re.sub(
        r"\b((?:de[il]{0,2}|sul{1,2}|ne[il]{0,2}|col{1,2}|quest|gl|l)')",
        r"\1 ",
        text,
        flags=re.IGNORECASE,
    )


def clean_display_text(text: str) -> str:
    """Strip TTS-only markup (*action words*, markdown, URLs) but KEEP emojis.

    Used for the burned/display captions: the user's emojis are part of the
    caption styling, while *stage directions* and markdown noise are not.
    """
    import unicodedata
    # *action words* only — '**bold**' keeps its word
    text = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", "", text)
    text = re.sub(r"[*_#`~>|]{2}", " ", text)
    text = re.sub(r"[*_#`~>|]", "", text)
    text = re.sub(r"http\S+", " ", text)
    cleaned = []
    for ch in text:
        if ord(ch) > 127:
            is_emoji = (
                "\U0001F000" <= ch <= "\U0001FAFF"
                or "\u2600" <= ch <= "\u27BF"
                or ch in ("\u2B50", "\u2764", "\uFE0F", "\u200D")
                or ("\u2300" <= ch <= "\u23FF")
                or ("\U0001F1E6" <= ch <= "\U0001F1FF")
            )
            if unicodedata.category(ch) in ("So", "Sk", "Cn") and not is_emoji:
                continue
        cleaned.append(ch)
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()


def preprocess_text_for_tts(text: str, language: str = "en") -> str:
    """Expand slang/abbreviations for better TTS pronunciation.

    Language-aware: the English gaming-slang table (AutoShorts port) is only
    applied to English speech — expanding "gg" to "good game" mid-Italian-
    sentence forces an accent switch. Italian gets its own table plus
    number-to-words; other languages get only punctuation normalization.
    """
    result = text
    for punct, repl in _PUNCTUATION_REPLACEMENTS.items():
        result = result.replace(punct, repl)
    lang = (language or "en").lower()
    if lang.startswith("it"):
        word_slang = IT_EXPANSIONS
    elif lang.startswith("en"):
        word_slang = {k: v for k, v in SLANG_EXPANSIONS.items()
                      if k not in _PUNCTUATION_REPLACEMENTS}
    else:
        word_slang = {}
    for slang in sorted(word_slang, key=len, reverse=True):
        result = re.sub(r"\b" + re.escape(slang) + r"\b", word_slang[slang],
                        result, flags=re.IGNORECASE)
    if lang.startswith("it"):
        result = _expand_italian_numbers(result)
        result = _split_italian_elisions(result)
    return re.sub(r"\s+", " ", result).strip()


def sanitize_tts_input(text: str) -> str:
    """Strip *action words*, emoji and markdown noise the TTS would read aloud.

    Combines AutoShorts' re.sub(r'\\*[^*]+\\*') with the emoji/markdown
    sanitization from its subtitle_generator.
    """
    import unicodedata
    # *action words* only: require non-star content that isn't itself bracketed
    # by stars, so '**bold**' keeps its word and only *chuckles* is dropped.
    text = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", "", text)
    text = re.sub(r"[*_#`~>|]{2}", " ", text)        # ** __ `` etc. become space
    text = re.sub(r"[*_#`~>|]", "", text)            # stray markdown chars
    text = re.sub(r"http\S+", " ", text)            # URLs read terribly
    cleaned = []
    for ch in text:
        if ord(ch) > 127 and unicodedata.category(ch) in ("So", "Sk", "Cn"):
            continue  # symbol / emoji / unassigned
        cleaned.append(ch)
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()


# ---------------------------------------------------------------------------
# Qwen3-TTS local provider — ported from AutoShorts QwenTTS (lazy singleton)
# ---------------------------------------------------------------------------

class QwenLocalTTS:
    """Qwen3-TTS VoiceDesign wrapper with a process-wide singleton.

    The qwen-tts package is OPTIONAL: importing it is deferred to the first
    generate() call so OpenShorts runs fine without it (ElevenLabs path).
    """

    _instance: Optional["QwenLocalTTS"] = None

    def __init__(self):
        device = os.environ.get("TTS_DEVICE", "").strip().lower()
        if not device:
            # Default to cuda when the installed torch can actually use it —
            # a CUDA-defaulted device_map on a CPU-only torch crashes at load.
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        self.device = device
        self.language = os.environ.get("TTS_LANGUAGE", "en")
        self._model = None
        self._sample_rate = 24000

    @classmethod
    def get_instance(cls) -> "QwenLocalTTS":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def clear_instance(cls):
        if cls._instance is not None:
            cls._instance._model = None
            cls._instance = None

    @classmethod
    def available(cls) -> bool:
        try:
            import qwen_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_initialized(self):
        if self._model is not None:
            return
        import torch
        from qwen_tts import Qwen3TTSModel

        device_map = f"{self.device}:0" if self.device == "cuda" else "cpu"
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        # sdpa is the safe default; flash_attention_2 wheels are GPU-generation picky
        attn = os.environ.get("TTS_ATTN_IMPLEMENTATION", "sdpa").strip().lower()
        if attn not in ("sdpa", "eager", "flash_attention_2"):
            attn = "sdpa"

        log.info("Loading Qwen3-TTS VoiceDesign model (device=%s attn=%s)...", device_map, attn)
        try:
            self._model = Qwen3TTSModel.from_pretrained(
                QWEN_MODEL_ID, device_map=device_map, dtype=dtype,
                attn_implementation=attn, local_files_only=True)
        except Exception as exc:
            log.warning("Local Qwen model cache miss, downloading: %s", exc)
            self._model = Qwen3TTSModel.from_pretrained(
                QWEN_MODEL_ID, device_map=device_map, dtype=dtype,
                attn_implementation=attn)
        self._sample_rate = 24000
        log.info("Qwen3-TTS model loaded")

    def generate(self, text: str, out_path: str, voice_description: str) -> Tuple[str, float]:
        """Generate one utterance as WAV. Returns (path, duration_seconds)."""
        import numpy as np
        import scipy.io.wavfile as wavfile

        self._ensure_initialized()
        processed = preprocess_text_for_tts(text, self.language)
        wavs, sr = self._model.generate_voice_design(
            text=processed,
            instruct=voice_description or "A clear, engaging voice with natural intonation",
            language=tts_language_name(self.language),
        )
        if not wavs or len(wavs) == 0:
            raise RuntimeError("Qwen TTS returned empty audio")
        audio = (np.asarray(wavs[0]) * 32767).astype("int16")
        wavfile.write(str(out_path), sr, audio)
        duration = len(audio) / float(sr)
        self._sample_rate = sr
        return str(out_path), duration


def qwen_status() -> Dict:
    ok = QwenLocalTTS.available()
    return {
        "available": ok,
        "device": QwenLocalTTS.get_instance().device,
        "hint": ("" if ok else
                 "Local TTS needs the optional qwen-tts package (pip install qwen-tts) "
                 "and the Qwen3-TTS VoiceDesign model. ElevenLabs works without it."),
    }


# ---------------------------------------------------------------------------
# ElevenLabs provider — reuses the existing OpenShorts integration + key
# ---------------------------------------------------------------------------

def elevenlabs_generate(text: str, out_path: str, api_key: str, voice_id: str) -> Tuple[str, float]:
    """Generate one utterance via ElevenLabs (saasshorts.generate_voiceover)."""
    from saasshorts import generate_voiceover as _el_generate
    _el_generate(text=text, elevenlabs_key=api_key, output_path=str(out_path),
                 voice_id=voice_id or "21m00Tcm4TlvDq8ikWAM")
    return str(out_path), probe_duration(out_path)


# ---------------------------------------------------------------------------
# Media helpers (ffprobe/ffmpeg)
# ---------------------------------------------------------------------------

def probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def has_audio_stream(path: str) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10)
        return "audio" in (out.stdout or "").strip().lower()
    except Exception:
        return False


def _run_ffmpeg(cmd: List[str], timeout: int = 600) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg failed ({res.returncode}): {res.stderr[-800:]}")


def extend_video_with_tpad(video_path: str, target_duration: float) -> Optional[str]:
    """Freeze the last frame until target_duration (AutoShorts tpad port).

    Returns the extended temp file path, or None when no extension is needed.
    Re-encodes with OpenShorts' delivery encode args (NVENC when available).
    """
    from ffmpeg_utils import video_encode_args
    duration = probe_duration(video_path)
    extend_by = target_duration - duration
    if extend_by <= 0.05 or duration <= 0:
        return None
    tmp = str(Path(video_path).with_name(Path(video_path).stem + "_tpad_tmp.mp4"))
    _run_ffmpeg([
        "ffmpeg", "-y", "-i", video_path,
        "-filter_complex", f"[0:v]tpad=stop=-1:stop_duration={extend_by:.3f}[vout]",
        "-map", "[vout]", "-map", "0:a?",
        *video_encode_args("delivery"), "-c:a", "aac", "-b:a", "192k", tmp,
    ])
    return tmp


# ---------------------------------------------------------------------------
# Audio ducking + mixing (sidechain — duck only while the voice speaks)
# ---------------------------------------------------------------------------

def duck_and_mix(video_path: str, voiceover_path: str, output_path: str,
                 game_volume: Optional[float] = None,
                 voice_volume: Optional[float] = None,
                 duck_threshold: Optional[float] = None,
                 duck_ratio: Optional[float] = None) -> str:
    """Mix the voiceover over the video's original audio.

    The original audio runs at full volume between speech intervals and is
    ducked (sidechain-compressed by the voiceover track) while the narrator
    talks. If the voiceover is longer than the video, the video is extended
    with a frozen last frame first (AutoShorts behavior).

    Env overrides: VOICEOVER_GAME_VOLUME, VOICEOVER_VOICE_VOLUME,
    VOICEOVER_DUCK_THRESHOLD (default 0.02), VOICEOVER_DUCK_RATIO (default 10).
    """
    game_volume = float(os.environ.get("VOICEOVER_GAME_VOLUME", "1.0") if game_volume is None else game_volume)
    voice_volume = float(os.environ.get("VOICEOVER_VOICE_VOLUME", "1.0") if voice_volume is None else voice_volume)
    duck_threshold = float(os.environ.get("VOICEOVER_DUCK_THRESHOLD", "0.02") if duck_threshold is None else duck_threshold)
    duck_ratio = float(os.environ.get("VOICEOVER_DUCK_RATIO", "10") if duck_ratio is None else duck_ratio)

    video_duration = probe_duration(video_path)
    audio_duration = probe_duration(voiceover_path)

    working_video = video_path
    tmp_extended = None
    if audio_duration > 0 and video_duration > 0 and audio_duration > video_duration:
        log.info("Voiceover (%.1fs) longer than video (%.1fs) — freezing last frame",
                 audio_duration, video_duration)
        tmp_extended = extend_video_with_tpad(video_path, audio_duration + 0.5)
        if tmp_extended:
            working_video = tmp_extended

    try:
        if not has_audio_stream(working_video):
            # No original audio: the voiceover becomes the only track.
            _run_ffmpeg([
                "ffmpeg", "-y", "-i", working_video, "-i", voiceover_path,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", output_path,
            ])
            return output_path

        # Voice track is split: one copy keys the compressor that ducks the
        # game audio, the other is mixed in at full volume. 48k stereo aligns
        # the (often 24k mono) TTS wav with typical video audio.
        filter_complex = (
            "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[key][voice];"
            f"[key]volume={voice_volume}[keyv];"
            f"[0:a][keyv]sidechaincompress=threshold={duck_threshold}:ratio={duck_ratio}"
            f":attack=25:release=500[ducked];"
            f"[ducked]volume={game_volume}[game];"
            f"[voice]volume={voice_volume}[voicef];"
            "[game][voicef]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", working_video, "-i", voiceover_path,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", output_path,
        ])
        return output_path
    finally:
        if tmp_extended and os.path.exists(tmp_extended):
            try:
                os.unlink(tmp_extended)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Caption generation (4 options) — Gemini video upload / OpenAI keyframes
# ---------------------------------------------------------------------------

def compute_max_captions(duration: float, style_prompt: str) -> int:
    """AutoShorts-style dynamic caption count (regular vs story pacing)."""
    is_story = "2-3 sentences" in style_prompt or "narrative" in style_prompt.lower()
    if is_story:
        return max(3, min(8, int(duration / 6) or 3))
    return max(2, min(14, int(duration / 2.5) or 2))


def build_caption_prompt(style_prompt: str, duration: float, max_captions: int,
                         game_title: str = "", game_description: str = "",
                         language: str = "en") -> str:
    """Build the 4-option caption generation prompt.

    Style guide + rules are ported from AutoShorts _get_caption_prompt; the
    response schema asks for 4 distinct scripts instead of one list.
    """
    is_story = "2-3 sentences" in style_prompt or "narrative" in style_prompt.lower()
    caption_duration = "4-8 seconds" if is_story else "1-3 seconds"
    caption_type = "narrative segments" if is_story else "short captions"

    game_context = ""
    if game_title or game_description:
        parts = []
        if game_title:
            parts.append(f"GAME: {game_title}")
        if game_description:
            parts.append(f"GAME CONTEXT: {game_description}")
        game_context = "\n\n" + "\n".join(parts)

    language_instruction = ""
    if (language or "en").lower() != "en":
        lang_name = tts_language_name(language)
        language_instruction = f"""

LANGUAGE REQUIREMENT:
- Generate ALL caption text in {lang_name}.
- Adapt the style examples to be culturally appropriate for {lang_name} speakers.
- Use natural {lang_name} expressions and slang where appropriate."""

    return f"""Watch this gameplay video and generate 4 DISTINCT caption scripts for it. Each option must take a noticeably different creative angle while following the style guide.

STYLE GUIDE:
{style_prompt}{game_context}{language_instruction}

VIDEO DURATION: {duration:.1f} seconds

RULES:
1. Each option contains about {max_captions} {caption_type} covering the whole video
2. Space captions throughout the video (not all at the start)
3. Each caption should appear for {caption_duration}
4. Focus on action moments, close calls, achievements, or funny situations
5. Captions should enhance the viewing experience, not describe obvious actions
6. The 4 options must be genuinely different from each other (tone, wording, focus)
7. Add 1-2 fitting emojis to most captions where they feel natural (gaming and
   internet culture emojis like 🔥💀😱💯🎮🎯), matching the caption's energy

Respond with ONLY valid JSON object:
{{
  "options": [
    {{
      "label": "short creative label",
      "captions": [
        {{"start": 1.5, "end": 3.0, "text": "CAPTION TEXT"}},
        {{"start": 5.2, "end": 6.5, "text": "another caption"}}
      ]
    }},
    {{"label": "...", "captions": [ ... ]}},
    {{"label": "...", "captions": [ ... ]}},
    {{"label": "...", "captions": [ ... ]}}
  ]
}}
"""


def _clamp_captions(captions: List[Dict], duration: float) -> List[Dict]:
    out = []
    for c in captions:
        text = str(c.get("text", "")).strip()
        if not text:
            continue
        try:
            start = max(0.0, min(float(c.get("start", 0)), max(0.0, duration - 0.3)))
            end = float(c.get("end", start + 2.0))
        except (TypeError, ValueError):
            continue
        end = max(start + 0.3, min(end, duration))
        out.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    out.sort(key=lambda c: c["start"])
    return out


def parse_caption_options(raw: Dict, duration: float) -> List[Dict]:
    """Tolerant parse (AutoShorts _parse_caption_response semantics) for the
    4-option schema, degrading gracefully to a single-option response."""
    options = []
    if isinstance(raw, dict) and isinstance(raw.get("options"), list):
        for opt in raw["options"]:
            caps = opt.get("captions") if isinstance(opt, dict) else None
            if isinstance(caps, list):
                options.append({
                    "label": str(opt.get("label") or f"Option {len(options) + 1}"),
                    "captions": _clamp_captions(caps, duration),
                })
    elif isinstance(raw, dict) and isinstance(raw.get("captions"), list):
        options.append({"label": "Option 1",
                        "captions": _clamp_captions(raw["captions"], duration)})
    elif isinstance(raw, list):
        options.append({"label": "Option 1", "captions": _clamp_captions(raw, duration)})

    options = [o for o in options if o["captions"]]
    if not options:
        raise ValueError("Caption model response contained no usable captions")
    return options[:4]


def _extract_json(text: str) -> Dict:
    txt = (text or "").strip()
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[-1] if "\n" in txt else txt[3:]
        if txt.endswith("```"):
            txt = txt[:-3]
        txt = txt.strip()
    start, end = txt.find("{"), txt.rfind("}")
    if start != -1 and end != -1:
        txt = txt[start:end + 1]
    return json.loads(txt)


def generate_caption_options_gemini(video_path: str, prompt: str, api_key: str,
                                    model: str = "gemini-2.5-flash") -> List[Dict]:
    """Gemini path: low-res proxy + File API upload (AutoShorts/openshorts pattern)."""
    from google import genai
    from google.genai import types as genai_types

    duration = probe_duration(video_path)
    client = genai.Client(api_key=api_key)

    proxy_path = None
    uploaded = None
    try:
        proxy_path = os.path.join(tempfile.gettempdir(), f"vo_proxy_{Path(video_path).name}")
        _run_ffmpeg(["ffmpeg", "-y", "-i", video_path, "-vf", "scale=640:-2",
                     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                     "-c:a", "aac", "-b:a", "32k", "-ac", "1", proxy_path], timeout=300)
        if not os.path.exists(proxy_path) or os.path.getsize(proxy_path) < 1024:
            proxy_path = video_path

        with open(proxy_path, "rb") as fh:
            uploaded = client.files.upload(file=fh, config={"mime_type": "video/mp4"})
        for _ in range(60):
            state = client.files.get(name=uploaded.name)
            if getattr(state.state, "name", "") == "ACTIVE":
                break
            if getattr(state.state, "name", "") == "FAILED":
                raise RuntimeError("Gemini file processing FAILED")
            time.sleep(3)

        config = genai_types.GenerateContentConfig(response_mime_type="application/json")
        response = client.models.generate_content(model=model, contents=[uploaded, prompt],
                                                  config=config)
        raw = _extract_json(getattr(response, "text", "") or "")
        return parse_caption_options(raw, duration)
    finally:
        for cleanup, fn in ((uploaded, lambda: client.files.delete(name=uploaded.name)),
                            (proxy_path, lambda: os.path.exists(proxy_path) and os.unlink(proxy_path))):
            try:
                if cleanup is not None:
                    fn()
            except Exception:
                pass


def generate_caption_options_openai(video_path: str, prompt: str, api_key: str,
                                    model: str = "gpt-4o-mini",
                                    base_url: str = "https://api.openai.com/v1") -> List[Dict]:
    """OpenAI path: keyframes interleaved with timestamps (AutoShorts port)."""
    import httpx

    duration = probe_duration(video_path)
    if not base_url:
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    # ~10 frames spread across the video, capped like AutoShorts (8-20)
    frame_count = max(8, min(16, int(duration / 2) or 8))
    interval = max(1.0, duration / frame_count) if duration > 0 else 1.0
    frames_dir = tempfile.mkdtemp(prefix="vo_frames_")
    _run_ffmpeg(["ffmpeg", "-y", "-i", video_path,
                 "-vf", f"fps=1/{interval:.2f}", "-frames:v", str(frame_count),
                 "-q:v", "2", os.path.join(frames_dir, "frame_%03d.jpg")], timeout=300)

    content: List[Dict] = [{"type": "text", "text": prompt}]
    frames = sorted(Path(frames_dir).glob("frame_*.jpg"))
    for i, frame in enumerate(frames):
        ts = i * interval
        with open(frame, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("utf-8")
        content.append({"type": "text", "text": f"[Frame at {ts:.1f}s]"})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}})

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_completion_tokens": 8000,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(f"{base_url.rstrip('/')}/chat/completions",
                           headers=headers, json=body)
        # Newer OpenAI-compatible endpoints reject json_object ("must be
        # 'json_schema' or 'text'") — retry once without response_format and
        # parse the JSON out of the free-text reply instead.
        if resp.status_code == 400 and "response_format" in resp.text:
            body.pop("response_format", None)
            resp = client.post(f"{base_url.rstrip('/')}/chat/completions",
                               headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI caption error ({resp.status_code}): {resp.text[:400]}")
        raw = _extract_json(resp.json()["choices"][0]["message"]["content"])
        options = parse_caption_options(raw, duration)
    # Best effort frame cleanup — frames can be large
    for frame in frames:
        try:
            frame.unlink()
        except OSError:
            pass
    try:
        os.rmdir(frames_dir)
    except OSError:
        pass
    return options


# ---------------------------------------------------------------------------
# Voiceover track building — per-caption TTS with measured timing
# (AutoShorts subtitle_generator.generate_subtitles timing model)
# ---------------------------------------------------------------------------

EMOJI_CHAR_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B50\u2764\uFE0F\u200D"
    "\U0001F1E6-\U0001F1FF\u2300-\u23FF]"
)


def _is_emoji_token(word: str) -> bool:
    """True when the token is (mostly) emoji — these get a longer on-screen
    window so their pop/highlight animation is actually visible."""
    stripped = EMOJI_CHAR_RE.sub("", word).strip()
    return bool(word) and not stripped


def _distribute_words(text: str, start: float, duration: float) -> List[Dict]:
    """Spread words across [start, start+duration] proportionally to length.

    Emoji tokens get a guaranteed 1.0–2.0s window (clamped into the caption's
    span, borrowing time from neighbouring words) so the Remotion pop/highlight
    animation has time to play instead of flashing for ~150ms.
    """
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not words or duration <= 0:
        return []
    weights = [len(w) + 1 for w in words]
    total = float(sum(weights))

    # Proportional base allocation
    raw = [duration * (wt / total) for wt in weights]
    # Enforce a minimum on-screen time for emoji tokens
    MIN_EMOJI_S, MAX_EMOJI_S = 1.0, 2.0
    MIN_WORD_S = 0.12
    for i, w in enumerate(words):
        if _is_emoji_token(w):
            raw[i] = max(raw[i], MIN_EMOJI_S)
    # Shrink non-emoji words to fund the emoji minimums; clamp emoji max
    overflow = 0.0
    for i, w in enumerate(words):
        if _is_emoji_token(w):
            capped = min(raw[i], MAX_EMOJI_S)
            overflow += raw[i] - capped
            raw[i] = capped
        else:
            overflow += max(0.0, raw[i] - MIN_WORD_S * 0 - raw[i])  # no-op keep
    needed = sum(raw) - duration
    if needed > 0:
        # Give back from the longest non-emoji words
        flexible = [i for i, w in enumerate(words) if not _is_emoji_token(w)]
        flex_total = sum(raw[i] for i in flexible) or 1.0
        for i in flexible:
            raw[i] = max(MIN_WORD_S, raw[i] - needed * (raw[i] / flex_total))
    elif needed < 0:
        # Distribute leftover time to non-emoji words
        flexible = [i for i, w in enumerate(words) if not _is_emoji_token(w)]
        if flexible:
            for i in flexible:
                raw[i] += (-needed) / len(flexible)

    caps = []
    cursor = start
    for w, share in zip(words, raw):
        caps.append({"text": w,
                     "startMs": int(round(cursor * 1000)),
                     "endMs": int(round((cursor + share) * 1000))})
        cursor += share
    # Last word absorbs rounding drift
    if caps:
        caps[-1]["endMs"] = int(round((start + duration) * 1000))
    return caps


def build_voiceover_track(captions: List[Dict], provider: str, voice_prompt: str,
                          out_dir: str, elevenlabs_key: str = "",
                          elevenlabs_voice_id: str = "",
                          language: str = "en",
                          progress_cb=None) -> Dict:
    """Generate TTS per caption and assemble one voiceover audio track.

    Timing model (AutoShorts): caption i starts at max(its AI start, end of
    the previous chunk + gap). Each chunk's real audio duration is measured,
    and word-level captions are distributed over the measured speech window —
    so burned captions stay in sync with the voice even when TTS runs long.

    Returns {audio_path, segments, word_captions, total_duration}.
    """
    segments_dir = os.path.join(out_dir, "tts_segments")
    os.makedirs(segments_dir, exist_ok=True)
    tts = QwenLocalTTS.get_instance() if provider == "qwen" else None
    if provider == "qwen" and tts is not None:
        tts.language = language

    segment_files: List[str] = []
    segments: List[Dict] = []      # placement info per caption chunk
    word_caps: List[Dict] = []     # Remotion word-level captions
    cursor = 0.0                   # audio timeline position
    gap = 0.25                     # silence between chunks (AutoShorts-ish)

    usable = []
    for c in captions:
        display = clean_display_text(str(c.get("text", "")))
        speak = sanitize_tts_input(str(c.get("text", "")))
        # Provider-agnostic Italian speak-side normalization: the Qwen path
        # re-runs the full language-aware expander on its own; ElevenLabs
        # reads raw text, so give it the same Italian slang/number/elision
        # expansions here (its multilingual model pronounces the words well).
        if (language or "en").lower().startswith("it") and provider != "qwen":
            speak = preprocess_text_for_tts(speak, language)
        if speak:
            usable.append((display, speak, c))
    total = len(usable)
    for idx, (display, text, cap) in enumerate(usable):
        try:
            want_start = float(cap.get("start", cursor))
        except (TypeError, ValueError):
            want_start = cursor
        start = max(want_start, cursor if cursor > 0 else 0.0)
        seg_path = os.path.join(segments_dir, f"seg_{idx:03d}.{'wav' if provider == 'qwen' else 'mp3'}")

        if provider == "qwen":
            _, dur = tts.generate(text, seg_path, voice_prompt)
        elif provider == "elevenlabs":
            if not elevenlabs_key:
                raise ValueError("ElevenLabs API key is required (Settings → ElevenLabs)")
            _, dur = elevenlabs_generate(text, seg_path, elevenlabs_key, elevenlabs_voice_id)
        else:
            raise ValueError(f"Unknown voice provider: {provider}")

        if dur <= 0.05:
            dur = 0.5  # degenerate chunk — keep the timeline moving

        end = start + dur
        segments.append({"index": idx, "start": round(start, 3), "end": round(end, 3),
                         "duration": round(dur, 3), "text": text, "file": seg_path})
        # Word captions keep the ORIGINAL display text (emojis included) so
        # burned captions match what the user wrote; only the spoken audio is
        # sanitized. Words are matched 1:1 with the spoken tokens by position,
        # falling back to the sanitized word when counts differ.
        display_words = [w for w in re.split(r"\s+", display.strip()) if w]
        speak_words = [w for w in re.split(r"\s+", text.strip()) if w]
        timed = _distribute_words(text, start, dur)
        if display_words and len(display_words) == len(timed):
            for w_cap, dw in zip(timed, display_words):
                w_cap["text"] = dw
        elif display_words and len(display_words) != len(speak_words):
            # Sanitization merged/split words — spread display words over the
            # same timeline proportionally so nothing is dropped.
            timed = _distribute_words(display, start, dur)
        word_caps.extend(timed)
        segment_files.append(seg_path)
        cursor = end + gap
        if progress_cb:
            progress_cb(idx + 1, total)

    if not segment_files:
        raise ValueError("No captions to speak")

    # Assemble the single voiceover track: each segment delayed to its start,
    # mixed (not summed) so overlaps can't clip even if starts collide.
    audio_path = os.path.join(out_dir, "voiceover_track.wav")
    inputs: List[str] = []
    filters: List[str] = []
    mix_labels: List[str] = []
    for i, seg in enumerate(segments):
        inputs.extend(["-i", seg["file"]])
        delay_ms = int(round(seg["start"] * 1000))
        # adelay needs one value per channel; aformat normalizes to stereo first
        filters.append(
            f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms}[d{i}]"
        )
        mix_labels.append(f"[d{i}]")
    filters.append(f"{''.join(mix_labels)}amix=inputs={len(segments)}:normalize=0:duration=longest[aout]")
    _run_ffmpeg(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
                 "-map", "[aout]", "-c:a", "pcm_s16le", audio_path])

    total_duration = max(probe_duration(audio_path), segments[-1]["end"])
    return {"audio_path": audio_path, "segments": segments,
            "word_captions": word_caps, "total_duration": total_duration}
