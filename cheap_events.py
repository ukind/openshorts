"""
Cheap whole-VOD event extraction (Phase 4).

Combines inexpensive signals before any expensive LLM call, per ROADMAP.md §3, §11, §31.4
and the reinforced VOD architecture provided:

VOD → whisper/scene/audio/cheap-visual → MULTIMODAL EVENT/CANDIDATE DETECTION → ...

Critical rule: candidate generation MUST NOT depend primarily on transcript
significance. Example that must still become a candidate:
  10s dialogue → 15s silence → 17s jumpscare → 18s scream → 20s reaction
even when transcript is meaningless.

Each enhancement beyond default whisper is toggleable via ENABLE_* env flags.

Output is a structured event timeline, e.g.:
  24.1s entity appears
  24.3s player turns camera
  24.7s sudden loudness
  ...

These events are then merged with transcript windows to seed candidates.
"""
import os
import re
import json
import subprocess
import tempfile
from collections import defaultdict

# ---------------------------------------------------------------------------
# Transcript signals
# ---------------------------------------------------------------------------
def extract_transcript_events(transcript_result, silence_gap=1.2, speech_density_window=5.0):
    """
    From Whisper word timestamps.
    Returns events: silence gaps, speech density peaks, keyword hits.
    Always enabled (default whisper is the baseline).
    """
    events = []
    if not transcript_result or "segments" not in transcript_result:
        return events

    words = []
    for seg in transcript_result.get("segments", []):
        for w in seg.get("words", []):
            words.append({"w": w.get("word",""), "s": float(w.get("start",0)), "e": float(w.get("end",0))})

    if not words:
        return events

    # Silence gaps between words
    for i in range(1, len(words)):
        gap = words[i]["s"] - words[i-1]["e"]
        if gap >= silence_gap:
            mid = (words[i-1]["e"] + words[i]["s"]) / 2
            events.append({
                "timestamp": round(mid, 2),
                "type": "silence",
                "confidence": min(0.95, 0.5 + gap/3),
                "description": f"silence {gap:.1f}s",
                "source": "transcript"
            })

    # Speech density peaks (words per window)
    if words:
        duration = words[-1]["e"]
        step = speech_density_window / 2
        max_density = 1
        densities = []
        t = 0
        while t < duration:
            cnt = sum(1 for w in words if t <= w["s"] < t + speech_density_window)
            densities.append((t + speech_density_window/2, cnt))
            max_density = max(max_density, cnt)
            t += step
        # peaks above 90th percentile (was 75th — too noisy, 250 peaks on 36m video)
        if densities:
            vals = sorted(c for _,c in densities)
            thresh = vals[int(len(vals)*0.90)] if vals else 0
            for ts,cnt in densities:
                if cnt >= thresh and cnt > 5:
                    events.append({
                        "timestamp": round(ts,2),
                        "type": "speech_density_peak",
                        "confidence": round(min(0.9, cnt / max(1, max_density)),2),
                        "description": f"speech density {cnt} words/{speech_density_window:.0f}s",
                        "source": "transcript"
                    })

    # Keyword hits (cheap heuristic)
    keywords = ["oh", "wow", "whoa", "no way", "let's go", "scream", "help", "watch out", "what", "oh my"]
    for w in words:
        low = w["w"].lower().strip(".,!?")
        if low in keywords:
            events.append({
                "timestamp": round(w["s"],2),
                "type": "keyword",
                "confidence": 0.6,
                "description": f"keyword '{low}'",
                "source": "transcript"
            })

    return events


def extract_scene_events(video_path):
    """Scene boundaries via PySceneDetect ContentDetector. Toggle: ENABLE_SCENE_DETECTION"""
    events = []
    try:
        import scenedetect
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
        video = open_video(video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=27.0))
        scene_manager.detect_scenes(video)
        scenes = scene_manager.get_scene_list()
        for start, end in scenes:
            ts = start.get_seconds()
            # First scene at 0s is not an event
            if ts > 0.5:
                events.append({
                    "timestamp": round(ts,2),
                    "type": "scene_change",
                    "confidence": 0.85,
                    "description": f"scene change at {ts:.1f}s",
                    "source": "scene"
                })
    except Exception as e:
        # Fallback: no scene events but don't fail pipeline
        print(f"⚠️ scene detection skipped: {e}")
    return events


def _ffmpeg_audio_rms(video_path, sr=16000, win_ms=200):
    """
    Extract mono 16kHz PCM via ffmpeg to bytes, compute RMS per window.
    Returns list of (timestamp, rms) or None if ffmpeg unavailable.
    """
    try:
        cmd = [
            "ffmpeg", "-v", "error",
            "-i", video_path,
            "-ac", "1", "-ar", str(sr), "-f", "s16le", "-acodec", "pcm_s16le",
            "-"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if proc.returncode != 0 or not proc.stdout:
            return None
        import numpy as np
        data = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        win = int(sr * win_ms / 1000)
        if win <= 0 or len(data) < win:
            return None
        # RMS per window
        rms = []
        for i in range(0, len(data) - win, win):
            chunk = data[i:i+win]
            r = float(np.sqrt((chunk*chunk).mean())) if len(chunk) else 0.0
            ts = (i + win/2) / sr
            rms.append((ts, r))
        return rms
    except Exception as e:
        print(f"⚠️ ffmpeg audio RMS skipped: {e}")
        return None


def _spectral_flux(pcm, sr=16000, n_fft=2048, hop=512):
    """Cheap spectral flux (autoshorts-inspired) via numpy rfft, no torch.
    Returns (times, flux) aligned to hop. Flux = sqrt(sum(positive diff^2)).
    Lightweight: ~70k FFTs for 36min at 16kHz — okay on CPU (<2s).
    """
    import numpy as np
    if len(pcm) < n_fft:
        return np.array([]), np.array([])
    pad = n_fft // 2
    try:
        padded = np.pad(pcm, (pad, pad), mode='reflect')
    except Exception:
        padded = np.concatenate([pcm[pad-1::-1], pcm, pcm[-pad:][::-1]])
    window = np.hanning(n_fft).astype(np.float32)
    n_frames = (len(padded) - n_fft)//hop + 1
    if n_frames <= 0:
        return np.array([]), np.array([])
    mag_prev = np.zeros(n_fft//2 + 1, dtype=np.float32)
    fluxes = np.empty(n_frames, dtype=np.float32)
    for i in range(n_frames):
        frame = padded[i*hop:i*hop+n_fft].astype(np.float32) * window
        mag = np.abs(np.fft.rfft(frame, n=n_fft))
        diff = mag - mag_prev
        diff[diff < 0] = 0
        fluxes[i] = float(np.sqrt((diff.astype(np.float64)**2).sum()))
        mag_prev = mag
    times = (np.arange(n_frames) * hop).astype(float) / sr
    return times, fluxes

def _smooth(x, win=21):
    import numpy as np
    if len(x) == 0 or win <= 1:
        return x
    if win > len(x):
        win = len(x) // 2 * 2 + 1
        if win < 3:
            return x
    kernel = np.ones(win, dtype=float) / win
    pad = win // 2
    padded = np.pad(x, (pad, pad), mode='reflect')
    return np.convolve(padded, kernel, mode='valid')[:len(x)]

def _zscore(s):
    import numpy as np
    if len(s) == 0:
        return s
    m = float(np.mean(s))
    sd = float(np.std(s) + 1e-8)
    return (s - m) / sd

def extract_audio_events(video_path, silence_rms=0.02, loud_spike_ratio=3.0):
    """
    Cheap audio events: silence, sudden loudness, audio activity peaks.
    Toggle: ENABLE_AUDIO_EVENTS. No heavy model required.
    Heuristics for screams/laughter/gasps are derived from loudness + duration.
    """
    events = []
    rms_series = _ffmpeg_audio_rms(video_path)
    if not rms_series:
        return events

    import numpy as np
    r_vals = np.array([r for _,r in rms_series])
    median = float(np.median(r_vals)) if len(r_vals) else 0.02
    # Avoid division by zero
    median = max(median, 0.01)

    # Silence: sustained low RMS
    silence_run = 0
    silence_start = None
    for ts, r in rms_series:
        if r < silence_rms:
            if silence_start is None:
                silence_start = ts
            silence_run += 1
        else:
            if silence_start is not None and silence_run * 0.2 >= 0.8:  # ~0.8s
                mid = (silence_start + ts) / 2
                events.append({
                    "timestamp": round(mid,2),
                    "type": "silence",
                    "confidence": 0.8,
                    "description": f"audio silence ~{(ts-silence_start):.1f}s",
                    "source": "audio"
                })
            silence_start = None
            silence_run = 0

    # Sudden loudness / spike
    for i in range(1, len(rms_series)):
        prev_ts, prev_r = rms_series[i-1]
        ts, r = rms_series[i]
        if prev_r > 0 and r / max(prev_r, median*0.5) >= loud_spike_ratio and r > 0.08:
            # Classify spike duration as heuristic scream vs gasp
            # Look ahead for sustained loudness
            sustain = 0
            for j in range(i, min(i+5, len(rms_series))):
                if rms_series[j][1] > 0.07:
                    sustain += 1
            ev_type = "scream" if sustain >= 3 else "sudden_loudness"
            # Deduplicate nearby spikes
            if not events or abs(ts - events[-1]["timestamp"]) > 0.6 or events[-1]["type"] != ev_type:
                events.append({
                    "timestamp": round(ts,2),
                    "type": ev_type,
                    "confidence": round(min(0.95, r / (median*4)),2),
                    "description": f"{ev_type} {r:.2f} (prev {prev_r:.2f})",
                    "source": "audio"
                })

    # Audio activity peaks via combined RMS 0.6 + flux 0.4 (z-scored, smoothed) — autoshorts A fix
    # Also keep silence/sudden_loudness from RMS ratio (above)
    # Build combined score aligned to rms_series timestamps
    _combined_times = None
    _combined_score = None
    try:
        import numpy as np
        # Re-extract pcm for flux (reuse helper's internal _ffmpeg call via rms_series pcm approx)
        # Instead: approximate flux from rms_series envelope via derivative — lightweight fallback if pcm not kept
        # We have r_vals; compute pseudo-flux as positive diff of r_vals (cheap) + try real STFT flux if ffmpeg pcm available
        # Try real flux: re-run ffmpeg pcm fetch quickly
        _pcm = None
        try:
            cmd2 = ["ffmpeg", "-v", "error", "-i", video_path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-acodec", "pcm_s16le", "-"]
            import subprocess as _sp
            _proc2 = _sp.run(cmd2, stdout=_sp.PIPE, stderr=_sp.PIPE, timeout=30)
            if _proc2.returncode == 0 and _proc2.stdout:
                _pcm = np.frombuffer(_proc2.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            _pcm = None
        if _pcm is not None and len(_pcm) > 2048:
            _ft, _flux = _spectral_flux(_pcm, sr=16000, n_fft=2048, hop=512)
            if len(_ft) and len(_flux):
                # z-score + smooth both
                _r_times = np.array([ts for ts,_ in rms_series], dtype=float)
                _r_vals_n = _zscore(r_vals)
                _r_smooth = _smooth(_r_vals_n, 21)
                # Interpolate flux to rms times
                _flux_z = _zscore(_flux)
                _flux_smooth = _smooth(_flux_z, 21)
                import numpy as _np2
                _flux_interp = _np2.interp(_r_times, _ft, _flux_smooth, left=_flux_smooth[0], right=_flux_smooth[-1])
                _combined = 0.6 * _r_smooth + 0.4 * _flux_interp
                _combined_times = _r_times
                _combined_score = _combined
                # Use 92nd percentile on combined (was RMS only) — reduces spam, adds flux sensitivity
                thresh = float(np.percentile(_combined, 92))
                for idx, ts in enumerate(_r_times):
                    c = float(_combined[idx])
                    r = float(r_vals[idx])
                    if c >= thresh and r > 0.06:
                        if not any(abs(ts - e["timestamp"]) < 1.0 and e["source"]=="audio" for e in events):
                            events.append({
                                "timestamp": round(float(ts),2),
                                "type": "audio_activity",
                                "confidence": round(float(min(0.85, 0.5 + c*0.15)),2),
                                "description": f"audio activity {r:.2f} flux {c:.2f}",
                                "source": "audio"
                            })
            else:
                raise ValueError("flux empty")
        else:
            raise ValueError("no pcm")
    except Exception as _e:
        # Fallback to old RMS-only percentile 92 (keeps pipeline working if flux fails)
        import numpy as np
        thresh = float(np.percentile(r_vals, 92))
        for ts, r in rms_series:
            if r >= thresh and r > 0.07:
                if not any(abs(ts - e["timestamp"]) < 1.0 and e["source"]=="audio" for e in events):
                    events.append({
                        "timestamp": round(float(ts),2),
                        "type": "audio_activity",
                        "confidence": round(float(min(0.85, r)),2),
                        "description": f"audio activity {r:.2f}",
                        "source": "audio"
                    })

    # Heuristic laughter: repeated short spikes
    # If we see 3+ sudden_loudness within 2s, tag as laughter
    loud_times = [e["timestamp"] for e in events if e["type"] in ("sudden_loudness","scream")]
    for i in range(len(loud_times)-2):
        if loud_times[i+2] - loud_times[i] < 2.0:
            mid = (loud_times[i] + loud_times[i+2])/2
            if not any(abs(mid - e["timestamp"])<1.0 and e["type"]=="laughter" for e in events):
                events.append({
                    "timestamp": round(mid,2),
                    "type": "laughter",
                    "confidence": 0.6,
                    "description": "possible laughter (clustered spikes)",
                    "source": "audio"
                })

    # Cap spam: if audio still dominates (>300 events), keep only loudest 100
    if len(events) > 400:
        # keep all non-audio_activity plus top 100 audio_activity by confidence
        non_audio = [e for e in events if e["type"] != "audio_activity"]
        audio = sorted([e for e in events if e["type"] == "audio_activity"], key=lambda x: x["confidence"], reverse=True)[:100]
        events = sorted(non_audio + audio, key=lambda x: x["timestamp"])
        print(f"   📡 audio_activity capped: {len(events)} kept (was >400)")

    events.sort(key=lambda x: x["timestamp"])
    return events


def extract_visual_motion_profile(video_path, sample_fps=6):
    """Continuous video motion profile (autoshorts B) — grayscale diff mean at sample_fps.
    Returns (times, scores) np arrays, smoothed not yet. Lightweight cv2, 64x36 downscale.
    Used with audio profile 0.6/0.4 for scene-aware ranking (C).
    """
    import numpy as np
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return np.array([]), np.array([])
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30)
        if fps <= 0:
            fps = 30
        step = max(1, int(fps / sample_fps))
        prev_gray = None
        times, scores = [], []
        idx = 0
        while True:
            ret = cap.grab()
            if not ret:
                break
            if idx % step != 0:
                idx += 1
                continue
            ret, frame = cap.retrieve()
            if not ret or frame is None:
                idx += 1
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (64, 36))
            if prev_gray is not None:
                diff = gray.astype(float) - prev_gray.astype(float)
                score = float((diff*diff).mean()) / (255*255)
            else:
                score = 0.0
            times.append(idx / fps)
            scores.append(score)
            prev_gray = gray
            idx += 1
        cap.release()
        arr_t = np.array(times, dtype=float)
        arr_s = np.array(scores, dtype=float)
        # smooth 7 window (lighter than audio 21) + zscore later by caller
        if len(arr_s) > 7:
            arr_s = _smooth(_zscore(arr_s), 7) if len(arr_s) > 0 else arr_s
            # keep raw 0-1 range for event threshold: reconvert via percentile? downstream will percentile.
            # For scene sum we keep smoothed zscore.
        return arr_t, arr_s
    except Exception as e:
        print(f"⚠️ visual motion profile skipped: {e}")
        import numpy as np
        return np.array([]), np.array([])

def extract_visual_activity_events(video_path, sample_fps=2, diff_thresh=0.12):
    """
    Cheap visual/activity signals: frame differencing.
    Toggle: ENABLE_CHEAP_VISUAL. Samples at low fps, computes diff.
    """
    events = []
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return events
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, int(fps / sample_fps))
        prev_gray = None
        frame_idx = 0
        while True:
            ret = cap.grab()
            if not ret:
                break
            if frame_idx % step != 0:
                frame_idx += 1
                continue
            ret, frame = cap.retrieve()
            if not ret or frame is None:
                frame_idx += 1
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (64, 36))
            if prev_gray is not None:
                diff = (gray.astype(float) - prev_gray.astype(float))
                score = float((diff*diff).mean()) / (255*255)
                if score > diff_thresh:
                    ts = frame_idx / fps
                    events.append({
                        "timestamp": round(ts,2),
                        "type": "visual_activity",
                        "confidence": round(min(0.9, score*3),2),
                        "description": f"visual activity {score:.3f}",
                        "source": "visual"
                    })
            prev_gray = gray
            frame_idx += 1
        cap.release()
    except Exception as e:
        print(f"⚠️ visual activity skipped: {e}")
    return events


def extract_cheap_events(video_path, transcript_result=None, enable_scene=True, enable_audio=True, enable_visual=True):
    """
    Master entry point. Returns sorted event timeline.
    Each enhancement beyond default whisper is toggleable.
    """
    all_events = []

    # Transcript is baseline (always)
    try:
        all_events.extend(extract_transcript_events(transcript_result))
    except Exception as e:
        print(f"⚠️ transcript events failed: {e}")

    if enable_scene:
        try:
            all_events.extend(extract_scene_events(video_path))
        except Exception as e:
            print(f"⚠️ scene events failed: {e}")

    if enable_audio:
        try:
            all_events.extend(extract_audio_events(video_path))
        except Exception as e:
            print(f"⚠️ audio events failed: {e}")

    if enable_visual:
        try:
            all_events.extend(extract_visual_activity_events(video_path))
        except Exception as e:
            print(f"⚠️ visual events failed: {e}")

    # Deduplicate near-duplicate events (within 0.3s same type)
    all_events.sort(key=lambda x: x["timestamp"])
    deduped = []
    for ev in all_events:
        if deduped and ev["type"] == deduped[-1]["type"] and abs(ev["timestamp"] - deduped[-1]["timestamp"]) < 0.35:
            # keep higher confidence
            if ev["confidence"] > deduped[-1]["confidence"]:
                deduped[-1] = ev
            continue
        deduped.append(ev)

    return deduped


def events_to_candidate_windows(events, video_duration, window_seconds=90, context_seconds=5, min_gap=5.0):
    """
    Cluster events into candidate windows with surrounding context.
    Ensures a silent→jumpscare→scream region becomes a candidate even with no transcript.
    Returns list of (start, end, reason) windows.
    Caps cluster span to 30s to avoid one giant 0-2196s window when 725 events chain.
    Prioritizes high-value peaks (scream/sudden_loudness/scene_change) over frequent
    silence/speech_density which would otherwise dominate clustering.
    """
    if not events:
        return []

    # Filter clustering to high-value events only — silence/speech_density alone
    # would create 500+ events chaining across whole video (seen: 725 → 0-2196s window).
    # Keep them for timeline but don't let them drive giant clusters; they still
    # matter when they neighbor a peak (handled via context window).
    HIGH_VALUE = {"sudden_loudness", "scream", "laughter", "scene_change", "visual_activity", "keyword"}  # audio_activity excluded — too frequent (100) would tile video into 69 windows
    cluster_events = [e for e in events if e["type"] in HIGH_VALUE]
    # If no high-value events, fall back to all (e.g. transcript-only video)
    if not cluster_events:
        cluster_events = events

    # Cluster with max span cap (30s) — split if chain would exceed window
    MAX_CLUSTER_SPAN = 30.0
    clusters = []
    current = [cluster_events[0]]
    cluster_start = cluster_events[0]["timestamp"]
    for ev in cluster_events[1:]:
        gap = ev["timestamp"] - current[-1]["timestamp"]
        span = ev["timestamp"] - cluster_start
        if gap <= min_gap*2 and span <= MAX_CLUSTER_SPAN:
            current.append(ev)
        else:
            clusters.append(current)
            current = [ev]
            cluster_start = ev["timestamp"]
    clusters.append(current)

    windows = []
    for cluster in clusters:
        center = sum(e["timestamp"] for e in cluster) / len(cluster)
        # Window with context
        start = max(0, min(e["timestamp"] for e in cluster) - context_seconds)
        end = min(video_duration, max(e["timestamp"] for e in cluster) + context_seconds)
        # Expand to at least min window, but not exceeding window_seconds
        # For cheap events, keep windows tighter (30-45s) to avoid giant overlapping merges
        target_min = min(20.0, window_seconds * 0.22)  # ~20s window for cheap peaks
        if end - start < target_min:
            half = target_min / 2 + 2.0  # tight window
            start = max(0, center - half)
            end = min(video_duration, center + half)
        # Clip to window_seconds max
        if end - start > window_seconds:
            # keep centered
            mid = (start + end)/2
            start = max(0, mid - window_seconds/2)
            end = min(video_duration, start + window_seconds)

        reason = ", ".join(sorted(set(e["type"] for e in cluster)))[:80]
        windows.append({
            "start": round(start,2),
            "end": round(end,2),
            "center": round(center,2),
            "reason": reason,
            "events": cluster
        })

    # C: Scene-aware snap + sliding peak window inside long cheap windows
    # Snap each window to nearest scene_change boundaries (if ENABLE_SCENE_DETECTION)
    try:
        _scenes = [e["timestamp"] for e in events if e["type"] == "scene_change"]
        if _scenes:
            _scenes = sorted(set([0.0] + _scenes + [float(video_duration)]))
            for w in windows:
                # snap start to previous scene start, end to next scene end within 5s
                _s = min(_scenes, key=lambda x: abs(x - w["start"]))
                _e = min(_scenes, key=lambda x: abs(x - w["end"]))
                if abs(_s - w["start"]) <= 5.0:
                    w["start"] = round(max(0, _s), 2)
                if abs(_e - w["end"]) <= 5.0:
                    w["end"] = round(min(float(video_duration), _e), 2)
                # Sliding peak: if window >45s, slide 30s sub-window to max event density
                if w["end"] - w["start"] > 45:
                    _win = 30.0
                    _best_start = w["start"]
                    _best_cnt = -1
                    _ev_ts = [e["timestamp"] for e in w["events"]]
                    # slide step 2s
                    _t = w["start"]
                    while _t + _win <= w["end"]:
                        _cnt = sum(1 for ts in _ev_ts if _t <= ts <= _t + _win)
                        if _cnt > _best_cnt:
                            _best_cnt = _cnt
                            _best_start = _t
                        _t += 2.0
                    # keep centered on densest 30s with 5s context
                    w["start"] = round(max(w["start"], _best_start - 2), 2)
                    w["end"] = round(min(w["end"], _best_start + _win + 2), 2)
                    w["center"] = round((_best_start + _win/2), 2)
    except Exception as _e:
        print(f"⚠️ scene-aware windowing skipped: {_e}")

    # Merge overlapping windows — conservative: only merge if >50% overlap
    # Previous +1.0s gap merged 90s windows spaced 30s into giant 0-2196s
    windows.sort(key=lambda x: x["start"])
    merged = []
    for w in windows:
        if not merged:
            merged.append(w)
            continue
        prev = merged[-1]
        overlap = min(w["end"], prev["end"]) - max(w["start"], prev["start"])
        smaller = min(w["end"]-w["start"], prev["end"]-prev["start"])
        if overlap > smaller * 0.80:
            # Highly overlapping — merge into one
            prev["end"] = max(prev["end"], w["end"])
            prev["start"] = min(prev["start"], w["start"])
            prev["events"].extend(w["events"])
            prev["reason"] = ", ".join(sorted(set(e["type"] for e in prev["events"])))
            prev["center"] = sum(e["timestamp"] for e in prev["events"]) / len(prev["events"])
        else:
            merged.append(w)

    return merged
