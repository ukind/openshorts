import os
import random
import shutil
import subprocess
from typing import Callable, List, Optional

import comfyui_client
import tts_client
from ffmpeg_utils import DELIVERY, video_encode_args

# The route's voice_id fallback is the ElevenLabs "Rachel" id (app.py:8027,
# saasshorts.py:1350). In local mode that id is meaningless -> server default.
ELEVENLABS_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"

# Verbatim the cloud Kling motion brief (saasshorts.py:886-891): text-level
# duplication is deliberate - importing saasshorts here would close a cycle.
I2V_MOTION_PROMPT = (
    "Natural UGC creator talking to camera. Expressive and energetic. "
    "Subtle hand gestures to emphasize points. Slight head movements and nods. "
    "Occasional leaning forward for emphasis. Relaxed shoulders, casual vibe. "
    "Maintain eye contact with camera. Natural blinking and micro-expressions."
)

# Per-shot motion variations: each actor segment gets its own Wan2.2 shot so
# the head reads as multi-cam promo coverage, not one repeated take.
I2V_SHOT_VARIATIONS = [
    "",
    "The creator leans in closer to the lens, talking with more intensity, "
    "gesturing toward an unseen product held in one hand.",
    "The creator steps back slightly to show the upper body, gesturing openly "
    "with both hands as if presenting something beside them.",
    "The creator smiles warmly and relaxed, small nods, one hand raised in a "
    "confident point toward the camera.",
]

_WAN_CACHE = "_head_wan_cache.mp4"
_LOOP_CACHE = "_head_loop_cache.mp4"
_LIPSYNC_CACHE = "_head_lipsync_cache.mp4"


class LocalStageError(Exception):
    """A local stage failed - the message names the stage and cause."""


def _exists(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


def _ffprobe_has_audio(path: str) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def _ffprobe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60,
        )
        if out.stdout.strip():
            return float(out.stdout.strip())
    except Exception:
        pass
    return 0.0


def _actor_prompt(description: str, product_description: Optional[str]) -> str:
    # Mirror of the cloud prompt builder (saasshorts.generate_actor_images:
    #727-738, including the trailing "Reddit selfie." tag) - duplicated
    # deliberately to avoid the module cycle.
    clean = description
    for remove in ["hablando", "talking", "sentad", "sitting", "desde", "from",
                   "con una", "with a", "detrás", "behind"]:
        if remove in clean.lower():
            idx = clean.lower().find(remove)
            if idx > 10:
                clean = clean[:idx].rstrip(" ,.")
    img_num = random.randint(1000, 9999)
    if product_description:
        return (
            f"IMG_{img_num}.jpg Raw candid selfie of {clean}, casually holding "
            f"{product_description}, showing it to the camera with a natural "
            f"smile. Product clearly visible in hand. Casual and real, not an ad. "
            f"Low quality front camera, soft room lighting. Reddit selfie."
        )
    return (
        f"IMG_{img_num}.jpg Raw candid selfie of {clean}, sitting at their desk "
        f"at home, looking at camera with a relaxed natural smile. Headphones "
        f"around neck, monitor glow behind them. Not posed, casual and real. "
        f"Low quality front camera, soft room lighting. Reddit selfie."
    )


def generate_actor_images_local(
    description: str,
    output_dir: str,
    title_slug: str,
    num_options: int = 3,
    product_description: Optional[str] = None,
    log: Callable[[str], None] = print,
) -> List[str]:
    """Local twin of generate_actor_images (saasshorts.py:711): Flux dev GGUF
    portraits through ComfyUI at {title_slug}_actor_option_{i}.png.
    Sequential loop: ComfyUI's queue serializes GPU work - parallel HTTP
    submissions would only reorder the queue."""
    log(f"[local] Generating {num_options} actor image options (Flux dev)...")
    paths = []
    for i in range(num_options):
        img_path = os.path.join(output_dir, f"{title_slug}_actor_option_{i}.png")
        comfyui_client.run_stage(
            "portrait",
            {
                "3": {"text": _actor_prompt(description, product_description)},
                "7": {"seed": random.randint(0, 2**32 - 1)},
            },
            img_path,
            log,
        )
        log(f"[local] Actor option {i + 1}: {img_path}")
        paths.append(img_path)
    return sorted(paths)


def generate_actor_image_local(
    description: str, output_path: str, log: Callable[[str], None] = print
) -> str:
    """Local twin of generate_actor_image (saasshorts.py:782)."""
    output_dir = os.path.dirname(output_path)
    title_slug = os.path.basename(output_path).replace("_actor.png", "")
    paths = generate_actor_images_local(
        description, output_dir, title_slug, num_options=1, log=log
    )
    if paths:
        shutil.move(paths[0], output_path)
    return output_path


def generate_voiceover_local(
    text: str,
    output_path: str,
    voice_id: Optional[str] = None,
    log: Callable[[str], None] = print,
    segments: Optional[List[str]] = None,
) -> str:
    """Local twin of generate_voiceover (saasshorts.py:795): TTS-server MP3
    at the fixed {slug}_voice.mp3 path. An ElevenLabs-default or empty
    voice_id resolves to the TTS server default (design D9).

    The synthesis runs once per narration segment when the script carries
    segment text: flow-matching TTS degenerates into repetition loops
    ("forgetting-ing-ing") on long single requests, while short ones stay
    clean; the pieces are joined with a re-encode so no boundary artifact
    survives. Single-segment scripts take the original one-shot path."""
    if not voice_id or voice_id == ELEVENLABS_DEFAULT_VOICE:
        voice_id = None
    parts = [t.strip() for t in (segments or []) if t and t.strip()]
    if len(parts) <= 1:
        text = parts[0] if parts else text
        log(f"[local] Generating voiceover ({len(text)} chars) via local TTS...")
        return tts_client.synthesize(text, output_path, voice_id=voice_id, log=log)

    base = output_path[:-4] if output_path.lower().endswith(".mp3") else output_path
    piece_paths: List[str] = []
    try:
        for i, part in enumerate(parts):
            piece = f"{base}_vo_{i:02d}.mp3"
            tts_client.synthesize(part, piece, voice_id=voice_id, log=log)
            piece_paths.append(piece)
        inputs: List[str] = []
        for p in piece_paths:
            inputs += ["-i", p]
        n = len(piece_paths)
        fc = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[aout]"
        subprocess.run(
            ["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[aout]",
             "-c:a", "libmp3lame", "-q:a", "2", output_path],
            check=True, capture_output=True)
        log(f"[local] Voiceover joined from {n} segment syntheses.")
        return output_path
    finally:
        for p in piece_paths:
            try:
                os.remove(p)
            except OSError:
                pass


def get_local_tts_voices(log: Callable[[str], None] = print) -> List[dict]:
    """Voice catalog for the wizard picker (read-only, design D9). Raises
    TTSError - the route catches it and surfaces service+URL."""
    return tts_client.list_voices(log=log)


_MAX_LIPSYNC_FRAMES = 96  # bounds the wrapper's single-pass GPU tensor (~1.7 GiB at 704x1280)
_LIPSYNC_FPS = 25  # the wrapper writes its output at this rate no matter the input
_LOOP25_CACHE = "_head_loop25_cache.mp4"
_LOOP_FADE = 0.25  # seconds of tail-to-head dissolve at the loop wrap point


def _shot_plan(
    marks: Optional[List[dict]],
    narration_dur: float,
    planned_duration: Optional[float],
) -> List[dict]:
    """Scale the script's actor-segment boundaries onto the real narration
    duration, so each Wan shot covers its narration beat. No marks, or an
    unusable plan, falls back to one shot covering the whole narration."""
    if not marks:
        return [{"start": 0.0, "end": narration_dur}]
    planned = float(planned_duration or 0)
    if planned <= 0:
        planned = float(marks[-1].get("end") or 0)
    if planned <= 0:
        return [{"start": 0.0, "end": narration_dur}]
    factor = narration_dur / planned
    bounds = [0.0]
    for m in marks[:-1]:
        b = min(max(float(m.get("end", 0) or 0) * factor, bounds[-1] + 1.0), narration_dur)
        bounds.append(b)
    bounds.append(narration_dur)
    spans: List[dict] = []
    for a, b in zip(bounds, bounds[1:]):
        if spans and b - spans[-1]["start"] < 2.0:
            spans[-1]["end"] = b  # sliver: fold into the previous shot
        else:
            spans.append({"start": a, "end": b})
    return spans or [{"start": 0.0, "end": narration_dur}]


_ACTOR_ANGLE_PROMPTS = [
    "",  # canonical framing: the desk selfie the base prompt already paints
    "Medium shot from the waist up, standing behind the desk, holding the "
    "product at chest height, shoulders angled toward the camera.",
    "Wide shot, upper body visible behind the desk, the product standing on "
    "the desk in front of them, one hand resting beside it.",
]

# ponytail: inswapper keeps the target's hair/jaw, so angle portraits are
# generated from the same base seed family to hold the silhouette; if promo
# shots still read as different people, upgrade to IP-Adapter/PuLID identity.


def generate_actor_angles_local(
    actor_ref: str,
    description: str,
    output_dir: str,
    title_slug: str,
    product_description: Optional[str] = None,
    log: Callable[[str], None] = print,
) -> List[str]:
    """Multi-cam angles of the SAME person: one Flux portrait per framing
    (_ACTOR_ANGLE_PROMPTS), then ReActor swaps the canonical actor's face
    onto each. Angle 0 is the canonical ref itself. Cached per angle."""
    angles = [actor_ref]
    for i, extra in enumerate(_ACTOR_ANGLE_PROMPTS[1:], start=1):
        dest = os.path.join(output_dir, f"{title_slug}_angle_{i}.png")
        if _exists(dest):
            angles.append(dest)
            continue
        raw = dest.replace("_angle_", "_angle_raw_")
        comfyui_client.run_stage(
            "portrait",
            {
                "3": {"text": _actor_prompt(description, product_description) + " " + extra},
                "7": {"seed": random.randint(0, 2**32 - 1)},
            },
            raw,
            log,
        )
        log(f"[local] Face swap angle {i}: ReActor...")
        src_name, src_sub = comfyui_client.upload_input(actor_ref, log)
        tgt_name, tgt_sub = comfyui_client.upload_input(raw, log)
        comfyui_client.run_stage(
            "faceswap",
            {
                "10": {"image": comfyui_client.image_ref(src_name, src_sub)},
                "11": {"image": comfyui_client.image_ref(tgt_name, tgt_sub)},
            },
            dest,
            log,
        )
        if os.path.exists(raw):
            os.remove(raw)
        angles.append(dest)
    log(f"[local] Actor angles ready: {len(angles)}")
    return angles


def _split_span(dur: float, n: int) -> List[float]:
    """Split a span into n near-equal positive parts."""
    base = dur / n
    return [base] * n


def _seamless_wrap(clip_path: str, dest_path: str) -> str:
    """Crossfade a clip's tail into its head, so stream_loop repeats it
    without a visible seam: the wrap point becomes a short dissolve."""
    dur = _ffprobe_duration(clip_path)
    fade = min(_LOOP_FADE, dur / 4)
    if dur <= fade * 2:
        shutil.copyfile(clip_path, dest_path)
        return dest_path
    off = dur - fade
    cmd = [
        "ffmpeg", "-y", "-i", clip_path, "-filter_complex",
        f"[0:v]split=3[a][b][c];"
        f"[a]trim=end={off:.3f},setpts=PTS-STARTPTS[main];"
        f"[b]trim=start={off:.3f},setpts=PTS-STARTPTS[tail];"
        f"[c]trim=end={fade:.3f},setpts=PTS-STARTPTS[head];"
        f"[tail][head]xfade=transition=fade:duration={fade:.3f}:offset=0[blend];"
        f"[main][blend]concat=n=2:v=1[v]",
        "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-pix_fmt", "yuv420p",
        dest_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest_path


def _ffprobe_fps(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of",
             "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60)
        num, _, den = out.stdout.strip().partition("/")
        fps = float(num) / float(den or 1)
        if fps > 0:
            return fps
    except Exception:
        pass
    return 0.0


def _lipsync_chunk_plan(cover_frames: int, max_frames: int) -> List[int]:
    """Frame spans per lipsync chunk: [n] when one pass fits, else k near-
    equal spans. Pure - unit-tested without ffmpeg."""
    if cover_frames <= 0:
        return []
    if cover_frames <= max_frames:
        return [cover_frames]
    chunks = -(-cover_frames // max_frames)
    base, extra = divmod(cover_frames, chunks)
    return [base + (1 if i < extra else 0) for i in range(chunks)]


def _cut_media(src: str, start: float, dur: float, dest: str,
               video_only: bool) -> str:
    """Frame/sample-accurate slice (input seek + re-encode)."""
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src]
    if video_only:
        cmd += ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-vn", "-c:a", "aac", "-b:a", "128k"]
    cmd.append(dest)
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def _concat_media(paths: List[str], dest: str, with_audio: bool = False) -> str:
    """Concatenate clips through the concat FILTER with a re-encode:
    stream copy would demand identical codec parameters across inputs,
    which does not hold when some parts were re-wrapped and others are
    straight ComfyUI output. with_audio joins each input's audio track in
    the same order - the lipsync chunks self-contain their narration slice,
    so the joined audio stays locked to the joined mouths."""
    inputs: List[str] = []
    for p in paths:
        inputs += ["-i", p]
    n = len(paths)
    chains = ";".join(
        f"[{i}:v]scale=iw:ih,setsar=1,settb=AVTB[v{i}]" for i in range(n))
    if with_audio:
        chains += ";" + ";".join(
            f"[{i}:a]aformat=sample_rates=24000:channel_layouts=mono[a{i}]" for i in range(n))
        # concat pairs its inputs per segment ([v0][a0][v1][a1]...), so the
        # pads must be interleaved, not videos-first-audios-after.
        fc = (chains + ";" + "".join(f"[v{i}][a{i}]" for i in range(n))
              + f"concat=n={n}:v=1:a=1[vout][aout]")
        maps = ["-map", "[vout]", "-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    else:
        fc = chains + ";" + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vout]"
        maps = ["-map", "[vout]"]
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", fc, *maps,
        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-pix_fmt", "yuv420p",
        dest,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def _loop_clip_to_duration(clip_path: str, target_duration: float, dest_path: str) -> str:
    """Loop a short i2v clip to cover the narration. When the clip is
    shorter than the target, it is first seam-wrapped (its tail dissolving
    into its head), so each repeat reads as a continuous take."""
    dur = _ffprobe_duration(clip_path)
    if dur > 0 and target_duration > dur + 0.4:
        work = dest_path.replace(".mp4", "_wrap.mp4")
        _seamless_wrap(clip_path, work)
        clip_path = work
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", clip_path,
        "-t", f"{max(target_duration, 0.1):.3f}",
        "-c", "copy",
        dest_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest_path


def _mux_audio(video_path: str, audio_path: str, dest_path: str) -> str:
    """Mux narration onto the lipsynced video - the composite pulls ALL
    narration from [0:a] of the head (saasshorts.py:1273-1291, design D8).
    When the lipsynced video already carries the chunk-joined narration
    (with_audio concat), that track is authoritative: it is locked to the
    chunk timeline, so re-muxing the raw mp3 could only re-introduce drift
    (and -shortest would truncate whatever falls past the video)."""
    if _ffprobe_has_audio(video_path):
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-map", "0:v", "-map", "0:a",
            *video_encode_args(DELIVERY),
            "-c:a", "aac", "-b:a", "128k",
            dest_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return dest_path
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path, "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        *video_encode_args(DELIVERY),
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        dest_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest_path


def generate_talking_head_local(
    image_path: str,
    audio_path: str,
    output_path: str,
    log: Callable[[str], None] = print,
    shot_marks: Optional[List[dict]] = None,
    planned_duration: Optional[float] = None,
    image_paths: Optional[List[str]] = None,
) -> str:
    """Local twin of the talking-head arms (saasshorts.py:862/:903):
    Wan2.2 i2v -> loop to narration length -> LatentSync lipsync -> mux.

    shot_marks (actor segments with start/end seconds) build the head as
    multi-cam promo coverage: one distinct Wan shot per segment, boundaries
    scaled to the real narration duration. None = legacy single shot.

    Atomicity per D6: retryable caches; {slug}_head.mp4 is written
    last, after the mux and an audio-stream probe."""
    lipsync_cache = output_path.replace("_head.mp4", _LIPSYNC_CACHE)

    narration_dur = _ffprobe_duration(audio_path)
    if narration_dur <= 0:
        raise LocalStageError(
            f"Cannot read narration duration from {audio_path} - the local "
            "head needs it to build the shot plan."
        )
    spans = _shot_plan(shot_marks, narration_dur, planned_duration)
    if len(spans) > 1:
        log("[local] Multi-shot head: " + ", ".join(
            f"{s['end'] - s['start']:.1f}s" for s in spans) + f" ({len(spans)} shots)")

    # Multi-cam: shots longer than one Wan generation (~5 s) are split into
    # several DISTINCT shots that cycle through the actor angles, so a 12 s
    # actor span is 3 cuts between framings instead of one clip looped - the
    # cut-every-4-seconds grammar of a real product promo.
    angles = list(image_paths) if image_paths else [image_path]
    if image_path not in angles:
        angles.insert(0, image_path)
    shots_total = sum(
        1 if len(spans) == 1 else max(1, round((s["end"] - s["start"]) / 5.0))
        for s in spans)
    log(f"[local] Promo head: {len(spans)} actor spans -> {shots_total} "
        f"distinct shots across {len(angles)} angles")

    loop_parts: List[str] = []
    shot_cursor = 0
    for idx, span in enumerate(spans):
        span_dur = span["end"] - span["start"]
        n_shots = 1 if len(spans) == 1 else max(1, int(span_dur / 5.0) + (1 if span_dur % 5.0 else 0))
        for j, part_dur in enumerate(_split_span(span_dur, n_shots)):
            cursor = shot_cursor
            shot_cursor += 1
            angle = angles[cursor % len(angles)]
            suffix = (f"_{idx}" if len(spans) > 1 else "") + (f"_{j}" if n_shots > 1 else "")
            wan_cache = output_path.replace("_head.mp4", _WAN_CACHE.replace(".mp4", f"{suffix}.mp4"))
            if not _exists(wan_cache):
                log(f"[local] Talking head shot {cursor + 1}/{shots_total} "
                    f"(angle {cursor % len(angles) + 1}/{len(angles)}): Wan2.2 i2v...")
                img_name, img_sub = comfyui_client.upload_input(angle, log)
                comfyui_client.run_stage(
                    "i2v",
                    {
                        "5": {"text": I2V_MOTION_PROMPT + I2V_SHOT_VARIATIONS[cursor % len(I2V_SHOT_VARIATIONS)]},
                        "13": {"image": comfyui_client.image_ref(img_name, img_sub)},
                        "9": {"seed": random.randint(0, 2**32 - 1)},
                    },
                    wan_cache,
                    log,
                )
            else:
                log(f"[local] Shot {cursor + 1} Wan clip cached, skipping i2v.")
            loop_i = output_path.replace("_head.mp4", _LOOP_CACHE.replace(".mp4", f"{suffix}.mp4"))
            if not _exists(loop_i):
                _loop_clip_to_duration(wan_cache, part_dur + 0.5, loop_i)
            loop_parts.append(loop_i)

    # 2. One continuous head track for the chunked lipsync stage.
    loop_cache = output_path.replace("_head.mp4", _LOOP_CACHE)
    if not _exists(loop_cache):
        if len(loop_parts) == 1:
            shutil.copyfile(loop_parts[0], loop_cache)
        else:
            _concat_media(loop_parts, loop_cache)
        log("[local] Talking head step 2/4: shot loops assembled.")
    else:
        log("[local] Looped clip cached, skipping loop.")

    # 3. LatentSync lipsync. The wrapper materializes the WHOLE input video
    # as one fp32 GPU tensor and converts it to uint8 in a single pass
    # (frames*255).byte(), so memory scales linearly with the frame count:
    # 475 frames at 704x1280 peak past the 8 GiB ComfyUI torch budget and
    # OOM. Chunk the looped video into <= _MAX_LIPSYNC_FRAMES windows, lipsync
    # each chunk with its matching narration slice (whisper drives the mouth
    # from that window's audio), and concat - seamless at identical codec
    # settings, and bounded memory for any narration length.
    if not _exists(lipsync_cache):
        log("[local] Talking head step 3/4: LatentSync lipsync (long)...")
        # The wrapper writes its output at a hard-coded 25 fps regardless of
        # the input's rate, so a 32 fps chunk comes back time-compressed and
        # every seam drifts. Normalize the loop to 25 fps ONCE, then chunk;
        # input and output frame counts then agree 1:1 per chunk.
        if not _ffprobe_fps(loop_cache) == _LIPSYNC_FPS:
            loop25 = output_path.replace("_head.mp4", _LOOP25_CACHE)
            if not _exists(loop25):
                subprocess.run(
                    ["ffmpeg", "-y", "-i", loop_cache, "-vf", f"fps={_LIPSYNC_FPS}",
                     "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
                     "-pix_fmt", "yuv420p", loop25],
                    check=True, capture_output=True)
            loop_cache = loop25
        fps = _ffprobe_fps(loop_cache)
        audio_dur = _ffprobe_duration(audio_path)
        loop_dur = _ffprobe_duration(loop_cache)
        if fps <= 0 or audio_dur <= 0 or loop_dur <= 0:
            raise LocalStageError(
                f"Cannot read fps/duration for lipsync chunking from "
                f"{loop_cache} (fps={fps}) or {audio_path} (duration={audio_dur}).")
        cover = int(fps * min(audio_dur, loop_dur))
        spans = _lipsync_chunk_plan(cover, _MAX_LIPSYNC_FRAMES)
        log(f"[local] Lipsync plan: {cover} frames in {len(spans)} chunk(s) "
            f"at {fps:.0f} fps.")
        workdir = output_path + ".lipsync"
        synced = []
        t0 = 0.0
        for idx, span in enumerate(spans):
            seg = span / fps
            if len(spans) == 1:
                src, aud = loop_cache, audio_path
            else:
                work_src = os.path.join(workdir, f"in_{idx:02d}.mp4")
                work_aud = os.path.join(workdir, f"in_{idx:02d}.m4a")
                os.makedirs(workdir, exist_ok=True)
                _cut_media(loop_cache, t0, seg, work_src, video_only=True)
                _cut_media(audio_path, t0, seg, work_aud, video_only=False)
                src, aud = work_src, work_aud
            vid_name, vid_sub = comfyui_client.upload_input(src, log)
            aud_name, aud_sub = comfyui_client.upload_input(aud, log)
            dst = lipsync_cache if len(spans) == 1 else os.path.join(
                workdir, f"out_{idx:02d}.mp4")
            comfyui_client.run_stage(
                "lipsync",
                {
                    "load_video": {"video": comfyui_client.image_ref(vid_name, vid_sub)},
                    "load_audio": {"audio": comfyui_client.image_ref(aud_name, aud_sub)},
                },
                dst,
                log,
            )
            synced.append(dst)
            t0 += seg
        if len(spans) > 1:
            _concat_media(synced, lipsync_cache,
                          with_audio=all(
                              _ffprobe_has_audio(p) for p in synced))
            shutil.rmtree(workdir, ignore_errors=True)
    else:
        log("[local] Lipsynced clip cached, skipping lipsync.")

    # 4. Mux narration, verify the audio stream, then write the final path (D6)
    log("[local] Talking head step 4/4: muxing narration...")
    tmp_mux = output_path + ".muxing.mp4"
    try:
        _mux_audio(lipsync_cache, audio_path, tmp_mux)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"")[:300].decode(errors="replace")
        raise LocalStageError(f"Local head mux failed: {stderr or e}")
    if not _ffprobe_has_audio(tmp_mux):
        raise LocalStageError(
            "Local head mux produced no audio stream - refusing to write "
            f"{output_path} (the composite reads narration from [0:a], "
            "saasshorts.py:1273-1291)."
        )
    os.replace(tmp_mux, output_path)
    log(f"[local] Talking head ready: {output_path}")
    return output_path


def generate_broll_local(
    prompt: str,
    output_path: str,
    duration: str = "5",
    log: Callable[[str], None] = print,
) -> str:
    """Local twin of generate_broll (saasshorts.py:986): Flux schnell still
    via ComfyUI, then Wan2.2 i2v turns the still into MOVING product footage
    - ceil(duration/5) distinct shots with different seeds, cut together, so
    a 12 s b-roll beat is 3 cuts of motion instead of one Ken Burns pan over
    a photo. Ken Burns stays as the fallback when the GPU stage fails."""
    log("[local] Generating b-roll image, then Wan2.2 motion...")
    dur_secs = int(duration)
    img_path = output_path.replace(".mp4", "_img.png")
    if not _exists(img_path):
        comfyui_client.run_stage(
            "broll",
            {
                "3": {"text": f"{prompt}. Cinematic, shallow depth of field, professional photography."},
                "7": {"seed": random.randint(0, 2**32 - 1)},
            },
            img_path,
            log,
        )

    # The i2v workflow renders 704x1280; cover-crop the 896x1152 still onto
    # that frame so Wan never letterboxes or squashes the product.
    padded = output_path.replace(".mp4", "_pad.png")
    if not _exists(padded):
        subprocess.run(
            ["ffmpeg", "-y", "-i", img_path,
             "-vf", "scale=704:1280:force_original_aspect_ratio=increase,crop=704:1280",
             padded],
            check=True, capture_output=True)

    motion = (
        f"{prompt}. Slow cinematic camera push-in, subtle parallax, natural "
        "hand movement interacting with the product. Professional commercial "
        "b-roll, photorealistic."
    )
    n_shots = max(1, int(dur_secs / 5.0) + (1 if dur_secs % 5.0 else 0))
    parts: List[str] = []
    try:
        for k in range(n_shots):
            part = output_path.replace(".mp4", f"_i2v_{k}.mp4")
            if not _exists(part):
                img_name, img_sub = comfyui_client.upload_input(padded, log)
                comfyui_client.run_stage(
                    "i2v",
                    {
                        "5": {"text": motion},
                        "13": {"image": comfyui_client.image_ref(img_name, img_sub)},
                        "9": {"seed": random.randint(0, 2**32 - 1)},
                    },
                    part,
                    log,
                )
            parts.append(part)
        joined = output_path.replace(".mp4", "_join.mp4")
        if len(parts) == 1:
            shutil.copyfile(parts[0], joined)
        else:
            _concat_media(parts, joined)
        # Trim to the beat length and carry a silent track, matching the
        # shape the composite has always received.
        subprocess.run(
            ["ffmpeg", "-y", "-i", joined,
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-t", str(dur_secs),
             "-map", "0:v", "-map", "1:a",
             *video_encode_args(DELIVERY),
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest",
             output_path],
            check=True, capture_output=True)
        for p in (img_path, padded, joined):
            if os.path.exists(p):
                os.remove(p)
        log(f"[local] B-roll (Wan2.2 motion, {n_shots} shots): {output_path}")
        return output_path
    except Exception as e:
        log(f"[local] B-roll i2v failed ({e}); falling back to Ken Burns pan.")

    fps = 30
    total_frames = dur_secs * fps
    zoompan_filter = (
        f"scale=2160:3840,"
        f"zoompan=z='1+0.15*on/{total_frames}':"
        f"x='iw/2-(iw/zoom/2)+10*on/{total_frames}':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s=1080x1920:fps={fps},"
        f"setsar=1"
    )
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", zoompan_filter,
        "-t", str(dur_secs),
        "-map", "0:v", "-map", "1:a",
        *video_encode_args(DELIVERY),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        if os.path.exists(img_path):
            os.remove(img_path)
    log(f"[local] B-roll (Ken Burns): {output_path}")
    return output_path
