import os
import re
import subprocess
import sys

from ffmpeg_utils import video_encode_args, QUALITY, METADATA_SCRUB


_STDIO_CONFIGURED = False

# Shared faster-whisper config so both transcription paths (this module and
# main.transcribe_video) behave identically. "small" is meaningfully better at
# German than "base" without being much slower on CPU.
DEFAULT_WHISPER_MODEL = "small"


def get_whisper_config():
    """Return the faster-whisper model config, overridable via env vars."""
    return {
        "model_size": os.environ.get("WHISPER_MODEL", DEFAULT_WHISPER_MODEL),
        "device": os.environ.get("WHISPER_DEVICE", "cpu"),
        "compute_type": os.environ.get("WHISPER_COMPUTE", "int8"),
    }


# Decode params shared by both transcription paths. condition_on_previous_text
# is off to avoid repetition/hallucination loops; vad_filter drops silence.
WHISPER_TRANSCRIBE_PARAMS = {
    "beam_size": 6,
    "vad_filter": True,
    "condition_on_previous_text": True,
    "word_timestamps": True,
    "vad_parameters": {
        "min_silence_duration_ms": 500
    },
}


def merge_continuation_words(words):
    """Merge faster-whisper continuation fragments into their base word.

    faster-whisper marks a word boundary with a LEADING SPACE on each token.
    Compound-word fragments (e.g. "-Kanal.", ".200") arrive WITHOUT a leading
    space and belong to the preceding word. Without merging, "YouTube" and
    "-Kanal." get space-joined into "YouTube -Kanal." or split across subtitle
    blocks. We concatenate such fragments onto the previous word and extend its
    end time. Normal words keep their leading space, so real word boundaries
    (e.g. "ich habe") are never glued together.

    Returns a new list; the input dicts are not mutated.
    """
    merged = []
    for word in words:
        text = word.get("word", "")
        if merged and isinstance(text, str) and text and not text.startswith(" "):
            prev = merged[-1]
            prev["word"] = f"{prev.get('word', '')}{text}"
            if word.get("end") is not None:
                prev["end"] = word["end"]
        else:
            merged.append(dict(word))
    return merged


def _configure_stdio():
    global _STDIO_CONFIGURED
    if _STDIO_CONFIGURED:
        return
    _STDIO_CONFIGURED = True
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if not stream or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _log(message):
    _configure_stdio()
    stream = sys.stdout
    text = str(message)
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        stream.write(safe_text + "\n")
    stream.flush()


def _escape_ffmpeg_filter_value(value):
    """Escape a path/value for use inside a quoted FFmpeg filter argument.

    NOTE: an apostrophe in the path cannot be made safe here. ffmpeg's
    filtergraph parser is not a shell — the shell idiom ``'\\''`` was tried on
    29-jul-2026 and is worse than doing nothing: it drops the apostrophe AND
    swallows the following option, so ``ass='…Earth'\\''s.ass':fontsdir='…'``
    resolved to a filename of "…Earths.ass:fontsdir=…" and failed to open.

    The only reliable answer is to keep apostrophes OUT of any path that is
    interpolated into a filter. Callers generate their own subtitle filenames,
    so they control this: use a neutral name (``subs_<i>_<ts>.ass``), never one
    derived from a video title.
    """
    return value.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")


def _normalize_subtitle_word(value):
    return " ".join(str(value or "").split())


def transcribe_audio(video_path):
    """
    Transcribe audio from a video file via the configured ASR backend.
    Returns transcript in the same format as main.py for compatibility.
    """
    # Lazy import: transcribe_backends imports helpers from this module.
    from transcribe_backends import transcribe_media

    _log(f"🎙️  Transcribing audio from: {video_path}")
    transcript = transcribe_media(video_path)
    _log(f"✅ Transcription complete. Language: {transcript['language']}")
    return transcript


def generate_srt_from_video(video_path, output_path, max_chars=20, max_duration=2.0,
                            style="classic", **style_opts):
    """
    Transcribe a video and generate a subtitle file directly (SRT, or karaoke
    ASS when style="karaoke"). Used for dubbed videos without a transcript.
    """
    transcript = transcribe_audio(video_path)

    # Get video duration to use as clip_end
    import cv2
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps else 0
    cap.release()

    if style == "karaoke":
        return generate_ass(transcript, 0, duration, output_path, max_chars, max_duration, **style_opts)
    return generate_srt(transcript, 0, duration, output_path, max_chars, max_duration)


def _collect_word_blocks(transcript, clip_start, clip_end, max_chars=20, max_duration=2.0):
    """
    Flatten transcript words for a clip range and group them into short blocks
    suitable for vertical video. Returns a list of blocks; each block is a list
    of {'word', 'start', 'end'} dicts with times relative to the clip.

    Continuation fragments are merged defensively here too, because transcripts
    from old jobs on disk store unmerged tokens (the leading space is still
    present, so the boundary signal survives).
    """
    # Keep Whisper word timings authoritative. Enhancement is allowed to add
    # emojis to segment text, but it must never rewrite the timing map just to
    # make the token count match. Add any newly introduced emoji as a synthetic
    # subtitle word at the end of its segment so the emoji is renderable while
    # all original spoken-word timings remain untouched.
    flat_words = []
    for segment in transcript.get('segments', []):
        seg_words = merge_continuation_words(segment.get('words', []))
        # Guard: whisper word timestamps for frequent function words (mi/no/e)
        # can land seconds outside their segment when attention drifts (e.g. mi at
        # 1.5 vs segment 59s). Without this a word sorts to the wrong clip.
        try:
            seg_s = float(segment.get('start', 0))
            seg_e = float(segment.get('end', seg_s + 0.2))
            # only correct if segment duration sane
            if seg_e > seg_s and seg_words:
                seg_dur = seg_e - seg_s
                for idx, w in enumerate(seg_words):
                    ws = float(w.get('start', seg_s))
                    # outside segment by >0.8s => interpolate linearly inside segment
                    if ws < seg_s - 0.8 or ws > seg_e + 0.8:
                        ratio = idx / max(1, len(seg_words) - 1) if len(seg_words) > 1 else 0.5
                        # leave 0.1s margin at end
                        new_s = seg_s + ratio * max(0.0, seg_dur - 0.2)
                        w['start'] = round(new_s, 3)
                        w['end'] = round(min(seg_e, new_s + 0.28), 3)
                        # also clamp original end if it was wild
                    we = float(w.get('end', ws + 0.2))
                    if we < seg_s - 0.8 or we > seg_e + 0.8 or we <= ws:
                        w['end'] = round(min(seg_e, float(w['start']) + 0.28), 3)
        except Exception:
            pass
        seg_text = str(segment.get('text') or '')
        existing_text = ''.join(w.get('word', '') for w in seg_words)
        try:
            emoji_matches = list(_EMOJI_RE.finditer(seg_text))
        except Exception:
            emoji_matches = []
        missing_emojis = []
        for match in emoji_matches:
            emoji = match.group(0)
            if emoji not in existing_text and emoji not in missing_emojis:
                missing_emojis.append(emoji)
        if missing_emojis:
            seg_start = float(segment.get('start', seg_words[0].get('start', 0) if seg_words else 0))
            seg_end = float(segment.get('end', seg_words[-1].get('end', seg_start + 0.2) if seg_words else seg_start + 0.2))
            cursor = max(seg_start, min(seg_end - 0.05, seg_words[-1].get('end', seg_start) if seg_words else seg_start))
            for emoji in missing_emojis:
                es = max(seg_start, min(cursor, seg_end - 0.05))
                avail = seg_end - es - 0.01
                if avail >= 1.0:
                    dur = 1.0
                elif avail >= 0.30:
                    dur = avail
                else:
                    # Need to steal 0.30-avail from previous word
                    if seg_words:
                        prev = seg_words[-1]
                        prev_s = float(prev.get('start', seg_start))
                        prev_e = float(prev.get('end', prev_s+0.2))
                        steal = min(0.30 - max(0, avail), max(0, (prev_e - prev_s) - 0.08))
                        prev['end'] = round(prev_e - steal, 3)
                        # shift cursor after shrinking prev
                        es = max(seg_start, min(float(prev['end']) + 0.02, seg_end - 0.05))
                        dur = 0.30
                    else:
                        dur = max(0.12, avail) if avail>0 else 0.30
                ee = min(seg_end, es + dur)
                if ee <= es:
                    ee = min(seg_end, es + 0.5)
                seg_words.append({'word': ' ' + emoji, 'start': es, 'end': ee})
                cursor = ee + 0.01
        flat_words.extend(seg_words)
    flat_words = merge_continuation_words(flat_words)
    # Ensure chronological order: whisper function words can be stamped
    # outside segment order and would otherwise group with wrong block
    try:
        flat_words.sort(key=lambda w: float(w.get('start', 0)))
    except Exception:
        pass

    words = []
    for word_info in flat_words:
        ws = word_info.get('start', 0)
        we = word_info.get('end', 0)
        # Strict start-based filter: avoid including a word that started
        # before the clip and would be clamped to 0s, overlapping the true
        # first word at the clip boundary (user-reported overlap).
        # Small 80ms tolerance covers Whisper float rounding.
        if ws + 0.08 < clip_start or ws >= clip_end:
            continue
        # Also skip if word ends before clip (no overlap at all)
        if we <= clip_start:
            continue
        cleaned_word = _normalize_subtitle_word(word_info.get('word', ''))
        if not cleaned_word:
            continue
        words.append({
            'word': cleaned_word,
            'start': max(0, ws - clip_start),
            'end': max(0, we - clip_start),
        })

    blocks = []
    current_block = []
    block_start = None

    for word in words:
        if not current_block:
            current_block = [word]
            block_start = word['start']
            continue

        # Split on long silence gap — prevents a word holding for seconds across silence
        gap = word['start'] - current_block[-1]['end']
        if gap > 0.6:
            blocks.append(current_block)
            current_block = [word]
            block_start = word['start']
            continue

        current_text_len = sum(len(w['word']) + 1 for w in current_block)
        duration = word['end'] - block_start

        if current_text_len + len(word['word']) > max_chars or duration > max_duration:
            blocks.append(current_block)
            current_block = [word]
            block_start = word['start']
        else:
            current_block.append(word)

    if current_block:
        blocks.append(current_block)
    return blocks


def _collect_captions_blocks(captions, max_chars=20, max_duration=2.0):
    """Group clip-relative captions {text, startMs, endMs} into blocks for SRT/ASS."""
    # captions already clip-relative ms; convert to seconds
    words = []
    for c in captions:
        txt = _normalize_subtitle_word(c.get('text', ''))
        if not txt:
            continue
        s = max(0, int(c.get('startMs', 0)) / 1000.0)
        e = max(s + 0.05, int(c.get('endMs', 0)) / 1000.0)
        if e <= s:
            continue
        words.append({'word': txt, 'start': s, 'end': e})
    # Same blocking as transcript path
    blocks = []
    current = []
    block_start = None
    for w in words:
        if not current:
            current = [w]
            block_start = w['start']
            continue
        cur_len = sum(len(x['word']) + 1 for x in current)
        dur = w['end'] - block_start
        if cur_len + len(w['word']) > max_chars or dur > max_duration:
            blocks.append(current)
            current = [w]
            block_start = w['start']
        else:
            current.append(w)
    if current:
        blocks.append(current)
    return blocks


def generate_srt_from_captions(captions, output_path, max_chars=20, max_duration=2.0):
    """Generate SRT directly from edited clip-relative captions."""
    blocks = _collect_captions_blocks(captions, max_chars, max_duration)
    if not blocks:
        return False
    srt = ""
    for i, block in enumerate(blocks, 1):
        text = " ".join(w['word'] for w in block).strip()
        srt += format_srt_block(i, block[0]['start'], block[-1]['end'], text)
    with open(output_path, 'w', encoding='utf-8-sig') as f:
        f.write(srt)
    return True


def generate_ass_from_captions(captions, output_path, max_chars=20, max_duration=2.0,
                               alignment='bottom', fontsize=16, font_name="Verdana",
                               font_color="#FFFFFF", border_color="#000000", border_width=2,
                               highlight_color="#FFD700", bg_color="#000000", bg_opacity=0.0,
                               effect="none", base_opacity=1.0, uppercase=False,
                               margin_v=43, word_gap=8, letter_spacing=0,
                               marginV=None, wordGap=None, letterSpacing=None):
    """Karaoke ASS directly from edited captions."""
    # Normalize aliases (camelCase from frontend styleOptions vs snake_case direct calls)
    if marginV is not None:
        margin_v = marginV
    if wordGap is not None:
        word_gap = wordGap
    if letterSpacing is not None:
        letter_spacing = letterSpacing
    margin_v = int(_clamp_number(margin_v, 0, 200, SAFE_MARGIN_V))
    spacing = int(_clamp_number(letter_spacing, -2, 20, 0))
    # word_gap is preview-only (Remotion flex gap); kept for API parity / future ASS hacks
    _ = word_gap
    blocks = _collect_captions_blocks(captions, max_chars, max_duration)
    if not blocks:
        return False
    # Reuse karaoke rendering by building a pseudo-transcript with same timing
    # so we don't duplicate ASS logic — call generate_ass via synthetic transcript
    # that maps 1:1 to blocks (avoid double grouping)
    final_fontsize = int(_clamp_number(fontsize, 10, 200, 16) * 0.85)
    if final_fontsize < 10:
        final_fontsize = 10
    align_map = {'top': 8, 'middle': 5, 'bottom': 2}
    ass_alignment = align_map.get(str(alignment).lower(), 2)
    safe_font = _sanitize_font_name(font_name)
    base_opacity = _clamp_number(base_opacity, 0.05, 1.0, 1.0)
    primary_colour = hex_to_ass_color(_dim_hex_color(font_color, base_opacity), 1.0)
    bg_opacity = _clamp_number(bg_opacity, 0.0, 1.0, 0.0)
    border_width = _clamp_number(border_width, 0, 10, 2)
    if bg_opacity > 0:
        border_style = 3
        outline_colour = hex_to_ass_color(bg_color, bg_opacity, fallback="000000")
        outline_width = 1
    else:
        border_style = 1
        outline_colour = hex_to_ass_color(border_color, 1.0, fallback="000000")
        outline_width = max(1, int(border_width))
    back_colour = hex_to_ass_color("#000000", 0.0)
    highlight_inline = _hex_to_ass_inline_color(highlight_color, fallback="FFD700")

    # Build ASS content from blocks with karaoke per-word highlight
    ass = f"""[Script Info]
Title: Karaoke Captions
ScriptType: v4.00+
PlayResX: 384
PlayResY: 288
WrapStyle: 1
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{safe_font},{final_fontsize},{primary_colour},&H00FFFFFF,{outline_colour},{back_colour},1,0,0,0,100,100,{spacing},0,{border_style},{outline_width},0,{ass_alignment},10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    for block in blocks:
        # One dialogue per word for karaoke highlight
        for idx, w in enumerate(block):
            word_text = _wrap_emoji_ass(w['word'].upper() if uppercase else w['word'], safe_font)
            # Build line with highlight on current word, dimmed others
            parts = []
            for j, ow in enumerate(block):
                ot = _wrap_emoji_ass(ow['word'].upper() if uppercase else ow['word'], safe_font)
                if j == idx:
                    # Active word: highlight color + optional effect
                    if effect == "glow":
                        parts.append(f"{{\\c{highlight_inline}\\blur2}}{ot}{{\\c&HFFFFFF&\\blur0}}")
                    elif effect == "pop":
                        parts.append(f"{{\\c{highlight_inline}\\fs{int(final_fontsize*1.2)}}}{ot}{{\\c&HFFFFFF&\\fs{final_fontsize}}}")
                    elif effect == "box":
                        parts.append(f"{{\\c{highlight_inline}\\bord3}}{ot}{{\\c&HFFFFFF&\\bord{outline_width}}}")
                    else:
                        parts.append(f"{{\\c{highlight_inline}}}{ot}{{\\c&HFFFFFF&}}")
                else:
                    parts.append(ot)
            line = " ".join(parts)
            ass += f"Dialogue: 0,{_ass_time(w['start'])},{_ass_time(w['end'])},Default,,0,0,0,,{line}\n"

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass)
    return True


def generate_srt(transcript, clip_start, clip_end, output_path, max_chars=20, max_duration=2.0):
    """
    Generates an SRT file from the transcript for a specific time range.
    Groups words into short lines suitable for vertical video.
    """
    blocks = _collect_word_blocks(transcript, clip_start, clip_end, max_chars, max_duration)
    if not blocks:
        return False

    srt_content = ""
    for index, block in enumerate(blocks, 1):
        text = " ".join(w['word'] for w in block).strip()
        srt_content += format_srt_block(index, block[0]['start'], block[-1]['end'], text)

    # Write UTF-8 with BOM so Windows/FFmpeg subtitle readers reliably detect Unicode text.
    with open(output_path, 'w', encoding='utf-8-sig') as f:
        f.write(srt_content)

    return True


# Vertical margin for burned captions, in PlayResY=288 units (so ~15% of the
# frame height). The old hardcoded 25 (8.7%) put captions underneath TikTok's
# and Reels' own bottom UI — the caption/username block and the music ticker —
# where they were partly covered on the platform even though the exported file
# looked fine.
SAFE_MARGIN_V = 43


# The caption look applied automatically to every generated clip. Chosen by
# rendering four candidates on a real clip and comparing them (25-jul-2026):
# white Anton uppercase with a yellow active word, heavy black outline, gentle
# pop. Yellow because it is the one colour that almost never occurs in footage,
# so the active word reads instantly on any background; the base text stays
# fully opaque (dimming it tested worse over bright scenes). This is a starting
# point, not a cage — the subtitle modal still overrides every field.
AUTO_CAPTION_STYLE = {
    "style": "karaoke",
    "alignment": "bottom",
    "font_name": "Anton",
    "font_size": 44,
    "font_color": "#FFFFFF",
    "highlight_color": "#FFE500",
    "border_color": "#000000",
    "border_width": 4,
    "effect": "pop",
    "base_opacity": 1.0,
    "uppercase": True,
    "max_chars": 16,
    "max_duration": 1.4,
    "margin_v": SAFE_MARGIN_V,
    "word_gap": 8,
    "letter_spacing": 0,
}


def _ass_time(seconds):
    """Format seconds as ASS timestamp H:MM:SS.cc (centiseconds)."""
    seconds = max(0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _hex_to_ass_inline_color(hex_color, fallback="FFFFFF"):
    """Convert #RRGGBB to the &HBBGGRR& form used by inline \\c override tags."""
    hex_digits = str(hex_color or "").lstrip('#')
    if not _HEX_COLOR_RE.match(hex_digits):
        hex_digits = fallback
    r = hex_digits[0:2]
    g = hex_digits[2:4]
    b = hex_digits[4:6]
    return f"&H{b}{g}{r}&".upper()


def _escape_ass_text(text):
    """Neutralize characters that would start ASS override blocks."""
    return str(text).replace('\\', '/').replace('{', '(').replace('}', ')')

# Emoji ranges (outline fallback via Symbola — Noto Color Emoji is CBDT bitmap and libass can't use it)
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\u2B50\u2764\U0001F900-\U0001F9FF]", flags=re.UNICODE)

def _wrap_emoji_ass(text, safe_font):
    """Wrap emoji codepoints with Symbola so libass renders them (otherwise squares).
    Uses \\fnSymbola\\b0 for the emoji run then restores the caption font with \\fn<safe_font>.
    Keeps the current colour/effect (no \\r) — only the font switches."""
    if not text or not _EMOJI_RE.search(text):
        return _escape_ass_text(text)
    # Escape first, then wrap emoji runs
    esc = _escape_ass_text(text)
    # Need to operate on original text codepoints, but escaped text is same length for emoji
    # Simplify: iterate escaped string and wrap emoji chars
    out = []
    i = 0
    while i < len(esc):
        ch = esc[i]
        if _EMOJI_RE.match(ch):
            # Collect consecutive emoji (including VS16, ZWJ, skin tone modifiers)
            j = i
            while j < len(esc) and (_EMOJI_RE.match(esc[j]) or esc[j] in "\uFE0F\u200D\U0001F3FB\U0001F3FC\U0001F3FD\U0001F3FE\U0001F3FF"):
                j += 1
            run = esc[i:j]
            out.append(f"{{\\fnSymbola\\b0}}{run}{{\\fn{safe_font}\\b1}}")
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _dim_hex_color(hex_color, opacity, fallback="FFFFFF"):
    """Fully-opaque 'dimmed' variant of a color (scaled toward black).

    Dimming via alpha looks muddy in ASS: libass draws the outline as a
    filled shape UNDER the fill, so a semi-transparent white fill blends
    with its own black outline into dark grey. Scaling the RGB instead
    keeps the text crisp on every player."""
    hex_digits = str(hex_color or "").lstrip('#')
    if not _HEX_COLOR_RE.match(hex_digits):
        hex_digits = fallback
    # Gentle curve: even strong dimming stays a readable light silver, matching
    # the airy look of browser-alpha dimming over bright video.
    factor = 0.5 + 0.5 * _clamp_number(opacity, 0.05, 1.0, 1.0)
    r = min(255, round(int(hex_digits[0:2], 16) * factor))
    g = min(255, round(int(hex_digits[2:4], 16) * factor))
    b = min(255, round(int(hex_digits[4:6], 16) * factor))
    return f"{r:02X}{g:02X}{b:02X}"


def generate_ass(transcript, clip_start, clip_end, output_path,
                 max_chars=20, max_duration=2.0, alignment='bottom',
                 fontsize=16, font_name="Verdana", font_color="#FFFFFF",
                 border_color="#000000", border_width=2,
                 highlight_color="#FFD700", bg_color="#000000", bg_opacity=0.0,
                 effect="none", base_opacity=1.0, uppercase=False,
                 margin_v=SAFE_MARGIN_V, word_gap=8, letter_spacing=0,
                 marginV=None, wordGap=None, letterSpacing=None):
    """
    Generates a karaoke-style ASS file: each block is shown like the SRT path,
    but the currently spoken word is rendered in highlight_color (modern
    TikTok/CapCut caption look). One dialogue event per word, back to back, so
    the highlight moves with the audio without flicker.

    effect: "none" | "glow" (neon shine around the active word) |
            "pop" (active word scales up) | "box" (thick colored outline).
    base_opacity: opacity of the non-active words — dimmed base text is the
    modern captioneer look (e.g. 0.4).
    """
    # Normalize aliases (camelCase from frontend styleOptions vs snake_case direct calls)
    if marginV is not None:
        margin_v = marginV
    if wordGap is not None:
        word_gap = wordGap
    if letterSpacing is not None:
        letter_spacing = letterSpacing
    margin_v = int(_clamp_number(margin_v, 0, 200, SAFE_MARGIN_V))
    spacing = int(_clamp_number(letter_spacing, -2, 20, 0))
    _ = word_gap
    blocks = _collect_word_blocks(transcript, clip_start, clip_end, max_chars, max_duration)
    if not blocks:
        return False

    # Match the SRT burn path: PlayResY 288 keeps font sizes consistent.
    final_fontsize = int(_clamp_number(fontsize, 10, 200, 16) * 0.85)
    if final_fontsize < 10:
        final_fontsize = 10

    align_map = {'top': 8, 'middle': 5, 'bottom': 2}
    ass_alignment = align_map.get(str(alignment).lower(), 2)

    safe_font = _sanitize_font_name(font_name)
    base_opacity = _clamp_number(base_opacity, 0.05, 1.0, 1.0)
    # Dim inactive words via a fully-opaque scaled color (NOT alpha — see
    # _dim_hex_color); the active word overrides the color inline.
    primary_colour = hex_to_ass_color(_dim_hex_color(font_color, base_opacity), 1.0)
    bg_opacity = _clamp_number(bg_opacity, 0.0, 1.0, 0.0)
    border_width = _clamp_number(border_width, 0, 10, 2)

    if bg_opacity > 0:
        border_style = 3
        outline_colour = hex_to_ass_color(bg_color, bg_opacity, fallback="000000")
        outline_width = 1
    else:
        border_style = 1
        outline_colour = hex_to_ass_color(border_color, 1.0, fallback="000000")
        outline_width = max(1, int(border_width))

    back_colour = hex_to_ass_color("#000000", 0.0)
    highlight_inline = _hex_to_ass_inline_color(highlight_color, fallback="FFD700")

    # Inline override tags for the active word; {\r} after it resets to the
    # (dimmed) style so the rest of the block stays untouched.
    if effect == "glow":
        glow_bord = max(3, int(outline_width) + 2)
        active_prefix = (f"{{\\c&HFFFFFF&\\3c{highlight_inline}"
                         f"\\bord{glow_bord}\\blur4}}")
    elif effect == "box":
        box_bord = max(4, int(outline_width) + 3)
        active_prefix = (f"{{\\c&HFFFFFF&\\3c{highlight_inline}"
                         f"\\bord{box_bord}\\blur0}}")
    elif effect == "pop":
        # Gentle pop. The old 75->112 range started the word so small that any
        # frame caught mid-animation read as a sizing bug rather than a beat.
        active_prefix = (f"{{\\c{highlight_inline}"
                         f"\\fscx90\\fscy90\\t(0,110,\\fscx108\\fscy108)}}")
    else:
        active_prefix = f"{{\\c{highlight_inline}}}"

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResY: 288\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{safe_font},{final_fontsize},{primary_colour},{primary_colour},"
        f"{outline_colour},{back_colour},1,0,0,0,100,100,{spacing},0,{border_style},"
        f"{outline_width},0,{ass_alignment},10,10,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    for block in blocks:
        for i, word in enumerate(block):
            # Event runs until the next word starts, capped to 300ms past word end to avoid 2s holds across gaps
            raw_next = block[i + 1]['start'] if i < len(block) - 1 else block[-1]['end']
            ev_start = block[0]['start'] if i == 0 else word['start']
            # Cap hold: don't keep highlight for >0.35s beyond word end unless next word is that close
            ev_end = min(raw_next, word['end'] + 0.35) if i < len(block) - 1 else block[-1]['end']
            if i < len(block) - 1 and ev_end < raw_next - 0.01:
                # Hold ends early — fill remaining gap with no highlight (next word's start will be next event)
                # Keep current event ending at capped point; next event covers gap
                pass
            if ev_end <= ev_start:
                continue

            parts = []
            for j, other in enumerate(block):
                raw = other['word'].upper() if uppercase else other['word']
                text = _wrap_emoji_ass(raw, safe_font)
                if j == i:
                    parts.append(f"{active_prefix}{text}{{\\r}}")
                else:
                    parts.append(text)

            events.append(
                f"Dialogue: 0,{_ass_time(ev_start)},{_ass_time(ev_end)},Default,,0,0,0,,{' '.join(parts)}"
            )

    if not events:
        return False

    with open(output_path, 'w', encoding='utf-8-sig') as f:
        f.write(header + "\n".join(events) + "\n")

    return True

def format_srt_block(index, start, end, text):
    def format_time(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
        
    return f"{index}\n{format_time(start)} --> {format_time(end)}\n{text}\n\n"

_HEX_COLOR_RE = re.compile(r'^[0-9A-Fa-f]{6}$')
_FONT_NAME_RE = re.compile(r'[^A-Za-z0-9 _-]')


def hex_to_ass_color(hex_color, opacity=1.0, fallback="FFFFFF"):
    """Convert #RRGGBB to ASS &HAABBGGRR format. opacity: 0.0=transparent, 1.0=opaque.

    Invalid hex (e.g. "#GGGGGG", None, wrong length) falls back to `fallback`
    instead of raising, so a bad color from the client can't 500 the request.
    """
    hex_digits = str(hex_color or "").lstrip('#')
    if not _HEX_COLOR_RE.match(hex_digits):
        hex_digits = fallback
    opacity = _clamp_number(opacity, 0.0, 1.0, 1.0)
    r = int(hex_digits[0:2], 16)
    g = int(hex_digits[2:4], 16)
    b = int(hex_digits[4:6], 16)
    alpha = round((1.0 - opacity) * 255)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _clamp_number(value, lo, hi, default):
    """Coerce value to float and clamp to [lo, hi]; use default if not numeric."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = float(default)
    return max(lo, min(hi, num))


def _sanitize_font_name(name):
    """Strip anything but [A-Za-z0-9 _-] so the font name can't inject extra
    ASS override fields (commas/braces/backslashes) into force_style."""
    cleaned = _FONT_NAME_RE.sub('', str(name or '')).strip()
    return cleaned or "Verdana"


def burn_subtitles(video_path, srt_path, output_path, alignment=2, fontsize=16,
                   font_name="Verdana", font_color="#FFFFFF",
                   border_color="#000000", border_width=2,
                   bg_color="#000000", bg_opacity=0.0):
    """
    Burns subtitles into the video using FFmpeg.
    Supports two modes:
    - Outline mode (bg_opacity=0): Text with colored outline/border
    - Box mode (bg_opacity>0): Text with semi-transparent background box
    """
    # Position mapping
    ass_alignment = 2
    align_lower = str(alignment).lower()
    if align_lower == 'top':
        ass_alignment = 6
    elif align_lower == 'middle':
        ass_alignment = 10
    elif align_lower == 'bottom':
        ass_alignment = 2

    # Font size scaling for ASS virtual resolution (PlayResY=288 default)
    # For vertical 1080x1920 video, we need larger text for readability
    final_fontsize = int(_clamp_number(fontsize, 10, 200, 16) * 0.85)
    if final_fontsize < 10:
        final_fontsize = 10

    safe_font_name = _sanitize_font_name(font_name)
    bg_opacity = _clamp_number(bg_opacity, 0.0, 1.0, 0.0)
    border_width = _clamp_number(border_width, 0, 10, 2)

    # Path handling for FFmpeg filter syntax
    safe_srt_path = _escape_ffmpeg_filter_value(srt_path)

    # Convert colors to ASS format and build style
    primary_colour = hex_to_ass_color(font_color, 1.0)

    if bg_opacity > 0:
        # Box mode: opaque background box
        border_style = 3
        outline_colour = hex_to_ass_color(bg_color, bg_opacity, fallback="000000")
        outline_width = 1
    else:
        # Outline mode: text border/outline
        border_style = 1
        outline_colour = hex_to_ass_color(border_color, 1.0, fallback="000000")
        outline_width = max(1, int(border_width))

    back_colour = hex_to_ass_color("#000000", 0.0)

    style_string = (
        f"Alignment={ass_alignment},"
        f"Fontname={safe_font_name},"
        f"Fontsize={final_fontsize},"
        f"PrimaryColour={primary_colour},"
        f"OutlineColour={outline_colour},"
        f"BackColour={back_colour},"
        f"BorderStyle={border_style},"
        f"Outline={outline_width},"
        f"Shadow=0,"
        f"MarginV={SAFE_MARGIN_V},"
        f"Bold=1"
    )

    # Let libass see the fonts bundled with the app (e.g. Anton for Impact)
    # even when the system fontconfig has no cache for them.
    fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    safe_fonts_dir = _escape_ffmpeg_filter_value(fonts_dir)

    # The first option is named explicitly (filename=) rather than positional:
    # ffmpeg 8's filtergraph parser rejects a quoted positional value that is
    # followed by more :name=value options ("No option name near ..."), while
    # the named form parses on every version back to 4.x.
    if str(srt_path).lower().endswith('.ass'):
        # ASS files (karaoke style) carry their own styles; force_style would
        # override the per-word color tags.
        vf = f"ass=filename='{safe_srt_path}':fontsdir='{safe_fonts_dir}'"
    else:
        vf = (f"subtitles=filename='{safe_srt_path}':fontsdir='{safe_fonts_dir}'"
              f":charenc=UTF-8:force_style='{style_string}'")

    cmd = [
        'ffmpeg', '-y',
        '-i', video_path,
        '-vf', vf,
        '-c:a', 'copy',
        *video_encode_args(QUALITY),
        *METADATA_SCRUB,
        '-movflags', '+faststart',
        output_path
    ]

    _log(f"🎬 Burning subtitles: {' '.join(cmd)}")
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    if result.returncode != 0:
        stderr_text = result.stderr.decode(errors='replace')
        _log(f"❌ FFmpeg Subtitle Error: {stderr_text}")
        raise Exception(f"FFmpeg failed: {stderr_text}")

    return True

