---
date: 2026-09-15T13:46:14+0700
author: Yogiswara Utama
commit: 73f670a
branch: main
repository: openshorts
topic: "AI Shorts local video_mode arm - per-stage local adapters"
tags: [plan, saasshorts, comfyui, tts, video-mode, local-models]
status: ready
parent: ".rpiv/artifacts/designs/2026-09-15_06-45-44_ai-shorts-local-models.md"
phase_count: 4
phases:
  - { n: 1, title: Local clients + adapters (foundation), files: [comfyui_client.py, tts_client.py, saasshorts_local.py, workflows/wan22_i2v_api.json, workflows/flux_dev_portrait_api.json, workflows/flux_schnell_broll_api.json, workflows/latentsync_lipsync_api.json, tests/test_saasshorts_local.py], depends_on: [] }
  - { n: 2, title: Dispatch + gates + validation, files: [saasshorts.py, app.py, tests/test_saasshorts_local.py], depends_on: [1] }
  - { n: 3, title: Wizard + gallery surface, files: [dashboard/src/components/SaaShortsTab.jsx, dashboard/src/components/UGCGallery.jsx], depends_on: [2] }
  - { n: 4, title: GPU-host calibration + docs, files: [workflows/latentsync_lipsync_api.json, workflows/wan22_i2v_api.json, README.md, .env.example], depends_on: [1, 2, 3] }
last_updated: 2026-09-15T14:40:00+0700
last_updated_by: Yogiswara Utama
---

# AI Shorts local video_mode arm - Implementation Plan

## Overview

Implements the AI Shorts local video_mode arm from the design artifact: five paid cloud stages (actor stills, voiceover, talking head, b-roll, voice listing) gain self-hosted ComfyUI and OpenAI-compatible TTS adapters behind a third mode value. Two HTTP clients and one adapter module hold the local logic; four dispatch sites, two mode-aware key gates, Literal validation, the wizard card, the gallery badge, and the docs complete the surface. Cloud paths stay byte-identical; generation makes zero cloud calls; sharing stays an explicit user opt-in.

Phase boundaries inherit 1:1 from the design's Slices section (slice-verifier-validated at design 6.2; per-slice code and criteria pass through unchanged). Phases run strictly sequentially: 1 gives 2 its adapters, 2 gives 3 its contracts (Slice 3 needs no app.py change), and 4 requires 1-3 plus the physical GPU host.

## Desired End State


```bash
# GPU host: ComfyUI portable running (port 8188), omnivoice-server running
export COMFYUI_URL=http://127.0.0.1:8188
export TTS_BASE_URL=http://127.0.0.1:8000
uvicorn app:app --port 8000
```

Wizard: pick **Local** mode card → no keys requested anywhere → Analyze → pick voice from the TTS server list → Generate. Job log shows ComfyUI submissions/polls per stage; `{slug}_head_wan_cache.mp4` → `{slug}_head_lipsync_cache.mp4` → muxed `{slug}_head.mp4` appear on disk; final composite carries narration from the muxed head. Cost panel reads $0 line items. No cloud egress occurs unless the user ticks Share to gallery.

```python
# Local adapter call shape (dispatch site, Slice 2)
if video_mode == "local":
    talking_head = generate_talking_head_local(actor_img, audio_path, talking_head, log=log)
```


## What We're NOT Doing


- In-process model hosting (FRD fixed external local servers; `QwenLocalTTS` precedent noted, not reused).
- Voice clone/design UI (read-only picker only; omnivoice-server clone CRUD is young).
- Local TTS for the VoiceOver page (`voiceover.py` stays ElevenLabs/qwen as-is).
- Subtitles/composite/text-stage changes (already local or `LLM_*`-routed, out of scope per developer).
- Header-based URL overrides (`X-ComfyUI-Url` etc.) — env-only per developer decision.
- Cloud fallback anywhere inside the local generation pipeline.
- HunyuanVideo A/B, Kokoro secondary voice (optional candidates documented in research, not built).


## Phase 1: Local clients + adapters (foundation)

### Overview
Creates the local foundation, CPU-testable end to end: the ComfyUI client (submit/poll/fetch/upload; the poll loop has no deadline per D11 and fails fast naming service + URL), the TTS client, the four stage adapters with the in-adapter narration mux (D8) and head-atomicity caches (D6), the four workflow templates (LatentSync ships as a documented skeleton, D12), and the offline test suite for all of it. Steps 1-2 serialization is a Phase 2 dispatch property; this phase only provides the adapters.

### Changes Required:

#### 1. ComfyUI client
**File**: `comfyui_client.py`
**Changes**: NEW - submit/poll/fetch/upload client; one entry point run_stage; no poll deadline (D11); every error names service + URL (FR6).


ComfyUI HTTP client mirroring the `_fal_run` submit/poll shape (saasshorts.py:591-662) minus deadline (FR8/D11) and auth. Adapters use one entry point, `run_stage(stage, patches, dest_path, log)`; uploads go through `/upload/image`, and `image_ref()` builds the `subfolder/name` string that Load* inputs take (never a 2-element list — API-format prompts parse that as a node link).

```python
import json
import os
import time
from typing import Callable, Dict, List, Tuple

import httpx

DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"
POLL_INTERVAL = 5.0
_UPLOAD_TIMEOUT = 120.0
_DOWNLOAD_TIMEOUT = 600.0  # per-file GET /view; the poll loop itself has no deadline

# stage key -> shipped API-format template (COMFYUI_WORKFLOW_<STAGE> overrides, design D3)
_STAGE_TEMPLATES = {
    "portrait": "flux_dev_portrait_api.json",
    "broll": "flux_schnell_broll_api.json",
    "i2v": "wan22_i2v_api.json",
    "lipsync": "latentsync_lipsync_api.json",
}


class ComfyUIError(Exception):
    """ComfyUI call failed - the message always names the service and URL."""


def comfyui_url() -> str:
    """Base URL of the ComfyUI service (env-only config, design D3)."""
    return (os.environ.get("COMFYUI_URL") or DEFAULT_COMFYUI_URL).strip().rstrip("/") or DEFAULT_COMFYUI_URL


def _client(base_url: str) -> httpx.Client:
    # Test seam: tests install a MockTransport here (tests/test_llm_client.py idiom).
    return httpx.Client(base_url=base_url, timeout=_UPLOAD_TIMEOUT)


def image_ref(name: str, subfolder: str) -> str:
    """Reference string for an uploaded input file. LoadImage/LoadAudio-style
    string inputs take 'subfolder/name' - never a [name, subfolder] list,
    which API-format prompts parse as a node link."""
    return f"{subfolder}/{name}" if subfolder else name


def template_path(stage: str) -> str:
    """Template file for a stage. COMFYUI_WORKFLOW_<STAGE> points at a custom
    file - that is the per-stage model-selection knob (design D3)."""
    override = os.environ.get(f"COMFYUI_WORKFLOW_{stage.upper()}")
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "workflows", _STAGE_TEMPLATES[stage])


def load_template(stage: str) -> Dict:
    path = template_path(stage)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ComfyUIError(
            f"ComfyUI workflow template not found for stage '{stage}': {path} "
            f"(set COMFYUI_WORKFLOW_{stage.upper()} or restore the file)"
        )
    except ValueError as e:
        raise ComfyUIError(
            f"ComfyUI workflow template for stage '{stage}' is not valid JSON "
            f"({path}): {e} - service: ComfyUI at {comfyui_url()}"
        )


def patch_workflow(template: Dict, patches: Dict[str, Dict]) -> Dict:
    """Apply {node_id: {input_field: value}} onto a deep copy of a template."""
    workflow = json.loads(json.dumps(template))
    for node_id, fields in (patches or {}).items():
        node = workflow.get(str(node_id))
        if node is None:
            raise ComfyUIError(
                f"ComfyUI workflow patch targets missing node '{node_id}' - "
                f"template and adapter drifted; service: ComfyUI at {comfyui_url()}"
            )
        node.setdefault("inputs", {}).update(fields)
    return workflow


def upload_input(file_path: str, log: Callable[[str], None] = print) -> Tuple[str, str]:
    """Upload a local file (image, audio or video) through /upload/image.

    ComfyUI's only upload endpoint takes any file type in the `image`
    multipart field and answers {name, subfolder}. No /upload/audio exists.
    """
    base = comfyui_url()
    try:
        with _client(base) as c, open(file_path, "rb") as f:
            resp = c.post("/upload/image", files={"image": (os.path.basename(file_path), f)})
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise ComfyUIError(
            f"ComfyUI upload failed for {os.path.basename(file_path)} - "
            f"service: ComfyUI at {base} ({e})"
        )
    name = data.get("name") or data.get("filename")
    subfolder = data.get("subfolder", "")
    log(f"[local] Uploaded {os.path.basename(file_path)} (ComfyUI name: {name})")
    return name, subfolder


def submit_workflow(workflow: Dict, log: Callable[[str], None] = print) -> str:
    """POST /prompt with an API-format workflow; returns the prompt_id."""
    base = comfyui_url()
    try:
        with _client(base) as c:
            resp = c.post("/prompt", json={"prompt": workflow})
    except Exception as e:
        raise ComfyUIError(f"ComfyUI submit failed - service: ComfyUI at {base} ({e})")
    if resp.status_code != 200:
        raise ComfyUIError(
            f"ComfyUI rejected the workflow (HTTP {resp.status_code}) - service: "
            f"ComfyUI at {base}: {resp.text[:400]}"
        )
    try:
        payload = resp.json()
    except ValueError as e:
        raise ComfyUIError(f"ComfyUI submit returned an unreadable body - service: ComfyUI at {base} ({e})")
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        raise ComfyUIError(f"ComfyUI submit returned no prompt_id - service: ComfyUI at {base}")
    return prompt_id


_TERMINAL_BAD = {"error", "cancelled", "failed"}


def wait_for_output(prompt_id: str, log: Callable[[str], None] = print) -> Dict:
    """Poll /history/{id} until the prompt finishes. No deadline (FR8);
    raises on error/cancelled status, a vanished prompt (no history entry and
    not in /queue - e.g. a ComfyUI restart), or transport failure (D11).
    Returns the prompt's outputs dict {node_id: {images|gifs|videos|video|audio: [...]}}.
    """
    base = comfyui_url()
    start = time.time()
    while True:
        try:
            with _client(base) as c:
                resp = c.get(f"/history/{prompt_id}")
            resp.raise_for_status()
            body = resp.json()
        except Exception as e:
            raise ComfyUIError(f"ComfyUI poll failed - service: ComfyUI at {base} ({e})")
        entry = body.get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            status_str = str(status.get("status_str", "")).lower()
            if status_str in _TERMINAL_BAD:
                msgs = json.dumps(status.get("messages") or [])[:400]
                raise ComfyUIError(
                    f"ComfyUI prompt {status_str} - service: ComfyUI at {base}: {msgs}"
                )
            log(f"[local] ComfyUI done in {time.time() - start:.0f}s")
            return entry.get("outputs") or {}
        # D11: a prompt absent from BOTH /history and /queue is gone (server
        # restart, rejected prompt) - fail fast instead of polling forever.
        try:
            with _client(base) as c:
                qresp = c.get("/queue")
            qresp.raise_for_status()
            qbody = qresp.json()
        except Exception as e:
            raise ComfyUIError(f"ComfyUI queue poll failed - service: ComfyUI at {base} ({e})")
        # /queue entries are [number, prompt_id, ...] tuples
        queued = {
            str(item[1])
            for item in (qbody.get("queue_running") or []) + (qbody.get("queue_pending") or [])
            if len(item) > 1
        }
        if prompt_id not in queued:
            raise ComfyUIError(
                f"ComfyUI prompt {str(prompt_id)[:8]} vanished (no history entry, not queued) "
                f"- service: ComfyUI at {base}"
            )
        log(f"[local] ComfyUI running... ({time.time() - start:.0f}s)")
        time.sleep(POLL_INTERVAL)


_OUTPUT_KEYS = ("images", "gifs", "videos", "video", "audio")


def output_files(outputs: Dict) -> List[Dict]:
    """Flatten every saved file reference from a history outputs dict
    (SaveImage reports images, SaveVideo reports video/videos, older nodes gifs)."""
    files = []
    for node_out in (outputs or {}).values():
        for key in _OUTPUT_KEYS:
            entries = node_out.get(key)
            if isinstance(entries, dict):
                entries = [entries]
            if isinstance(entries, list):
                files.extend(e for e in entries if isinstance(e, dict) and e.get("filename"))
    return files


def fetch_file(filename: str, subfolder: str, dest_path: str, log: Callable[[str], None] = print) -> str:
    """GET /view for one output file and write it to dest_path."""
    base = comfyui_url()
    try:
        with _client(base) as c:
            resp = c.get(
                "/view",
                params={"filename": filename, "subfolder": subfolder or "", "type": "output"},
                timeout=_DOWNLOAD_TIMEOUT,
            )
        resp.raise_for_status()
    except Exception as e:
        raise ComfyUIError(f"ComfyUI download failed for {filename} - service: ComfyUI at {base} ({e})")
    with open(dest_path, "wb") as f:
        f.write(resp.content)
    log(f"[local] Fetched {filename} -> {dest_path}")
    return dest_path


def run_stage(stage: str, patches: Dict[str, Dict], dest_path: str, log: Callable[[str], None] = print) -> str:
    """Load + patch + submit + wait + fetch the first output to dest_path."""
    workflow = patch_workflow(load_template(stage), patches)
    prompt_id = submit_workflow(workflow, log)
    log(f"[local] ComfyUI stage '{stage}' submitted (prompt {str(prompt_id)[:8]}).")
    outputs = wait_for_output(prompt_id, log)
    files = output_files(outputs)
    if not files:
        raise ComfyUIError(
            f"ComfyUI finished stage '{stage}' with no output files - service: "
            f"ComfyUI at {comfyui_url()}"
        )
    first = files[0]
    return fetch_file(first["filename"], first.get("subfolder", ""), dest_path, log)
```


#### 2. TTS client
**File**: `tts_client.py`
**Changes**: NEW - OpenAI-compatible /v1/audio/speech + /v1/voices client; TTS_BASE_URL unset is a named configuration error (D3).


OpenAI-compatible TTS client: `synthesize(text, dest_path, voice_id, log)` and `list_voices(log)`. Every error names service + URL; `TTS_BASE_URL` unset is a named configuration error (D3).

```python
import os
from typing import Callable, List, Optional

import httpx

_TIMEOUT = 600.0  # per-request; a long narration is one POST


class TTSError(Exception):
    """TTS call failed - the message always names the service and URL."""


def tts_base_url() -> str:
    url = (os.environ.get("TTS_BASE_URL") or "").strip().rstrip("/")
    if not url:
        raise TTSError(
            "TTS_BASE_URL is not set - local voiceover needs the local TTS "
            "server URL (e.g. TTS_BASE_URL=http://127.0.0.1:8000). No default "
            "is guessed (design D3, FR6)."
        )
    return url


def _client(base_url: str) -> httpx.Client:
    # Test seam: tests install a MockTransport here (tests/test_llm_client.py idiom).
    return httpx.Client(base_url=base_url, timeout=_TIMEOUT)


def synthesize(
    text: str,
    dest_path: str,
    voice_id: Optional[str] = None,
    log: Callable[[str], None] = print,
) -> str:
    """Synthesize narration to MP3 at dest_path. voice_id None/empty = the
    server default voice."""
    base = tts_base_url()
    body = {"input": text, "response_format": "mp3"}
    if voice_id:
        body["voice"] = voice_id
    try:
        with _client(base) as c:
            resp = c.post("/v1/audio/speech", json=body)
    except Exception as e:
        raise TTSError(f"TTS request failed - service: TTS server at {base} ({e})")
    if resp.status_code != 200:
        raise TTSError(
            f"TTS synthesis error (HTTP {resp.status_code}) - service: TTS "
            f"server at {base}: {resp.text[:300]}"
        )
    with open(dest_path, "wb") as f:
        f.write(resp.content)
    log(f"[local] TTS synthesized {len(text)} chars -> {dest_path}")
    return dest_path


def list_voices(log: Callable[[str], None] = print) -> List[dict]:
    """Voice catalog as [{voice_id, name}]. Tolerant to {voices: [...]} or a
    bare list, and to id/voice_id keys (omnivoice-server API churn)."""
    base = tts_base_url()
    try:
        with _client(base) as c:
            resp = c.get("/v1/voices")
    except Exception as e:
        raise TTSError(f"TTS voice list failed - service: TTS server at {base} ({e})")
    if resp.status_code != 200:
        raise TTSError(
            f"TTS voice list error (HTTP {resp.status_code}) - service: TTS "
            f"server at {base}: {resp.text[:300]}"
        )
    data = resp.json()
    rows = data.get("voices") if isinstance(data, dict) else data
    voices = []
    for v in rows or []:
        if not isinstance(v, dict):
            continue
        vid = v.get("voice_id") or v.get("id")
        if vid:
            voices.append({"voice_id": vid, "name": v.get("name") or str(vid)})
    return voices
```


#### 3. Local stage adapters
**File**: `saasshorts_local.py`
**Changes**: NEW - four stage adapters mirroring the cloud signatures minus the key param; actor-options fanout; voices fetch; in-adapter narration mux (D8); retryable head caches + ffprobe-gated final write (D6).


The five local twins (four stages + actor fanout) mirroring cloud signatures minus the key param, voices fetch, and the mux with D6 atomicity. No saasshorts import at module level; encode args come from ffmpeg_utils directly.

```python
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
) -> str:
    """Local twin of generate_voiceover (saasshorts.py:795): TTS-server MP3
    at the fixed {slug}_voice.mp3 path. An ElevenLabs-default or empty
    voice_id resolves to the TTS server default (design D9)."""
    if not voice_id or voice_id == ELEVENLABS_DEFAULT_VOICE:
        voice_id = None
    log(f"[local] Generating voiceover ({len(text)} chars) via local TTS...")
    return tts_client.synthesize(text, output_path, voice_id=voice_id, log=log)


def get_local_tts_voices(log: Callable[[str], None] = print) -> List[dict]:
    """Voice catalog for the wizard picker (read-only, design D9). Raises
    TTSError - the route catches it and surfaces service+URL."""
    return tts_client.list_voices(log=log)


def _loop_clip_to_duration(clip_path: str, target_duration: float, dest_path: str) -> str:
    """Loop a short i2v clip to cover the narration (ffmpeg stream_loop,
    stream copy)."""
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
    narration from [0:a] of the head (saasshorts.py:1273-1291, design D8)."""
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
) -> str:
    """Local twin of the talking-head arms (saasshorts.py:862/:903):
    Wan2.2 i2v -> loop to narration length -> LatentSync lipsync -> mux.

    Atomicity per D6: three retryable caches; {slug}_head.mp4 is written
    last, after the mux and an audio-stream probe."""
    wan_cache = output_path.replace("_head.mp4", _WAN_CACHE)
    loop_cache = output_path.replace("_head.mp4", _LOOP_CACHE)
    lipsync_cache = output_path.replace("_head.mp4", _LIPSYNC_CACHE)

    # 1. Wan2.2 image-to-video (silent, ~7.5 s at 121 frames / 16 fps)
    if not _exists(wan_cache):
        log("[local] Talking head step 1/4: Wan2.2 i2v (the long stage)...")
        img_name, img_sub = comfyui_client.upload_input(image_path, log)
        comfyui_client.run_stage(
            "i2v",
            {
                "5": {"text": I2V_MOTION_PROMPT},
                "13": {"image": comfyui_client.image_ref(img_name, img_sub)},
                "9": {"seed": random.randint(0, 2**32 - 1)},
            },
            wan_cache,
            log,
        )
    else:
        log("[local] Wan clip cached, skipping i2v.")

    # 2. Loop the short clip to cover the narration
    if not _exists(loop_cache):
        log("[local] Talking head step 2/4: looping clip to narration length...")
        narration_dur = _ffprobe_duration(audio_path)
        if narration_dur <= 0:
            raise LocalStageError(
                f"Cannot read narration duration from {audio_path} - the local "
                "head needs it to loop the i2v clip."
            )
        _loop_clip_to_duration(wan_cache, narration_dur + 0.5, loop_cache)
    else:
        log("[local] Looped clip cached, skipping loop.")

    # 3. LatentSync lipsync over the full-length video
    if not _exists(lipsync_cache):
        log("[local] Talking head step 3/4: LatentSync lipsync (long)...")
        vid_name, vid_sub = comfyui_client.upload_input(loop_cache, log)
        aud_name, aud_sub = comfyui_client.upload_input(audio_path, log)
        comfyui_client.run_stage(
            "lipsync",
            {
                "load_video": {"video": comfyui_client.image_ref(vid_name, vid_sub)},
                "load_audio": {"audio": comfyui_client.image_ref(aud_name, aud_sub)},
            },
            lipsync_cache,
            log,
        )
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
    via ComfyUI + the same Ken Burns pan. Only the image source changes."""
    log("[local] Generating b-roll image + Ken Burns effect...")
    dur_secs = int(duration)
    img_path = output_path.replace(".mp4", "_img.png")

    comfyui_client.run_stage(
        "broll",
        {
            "3": {"text": f"{prompt}. Cinematic, shallow depth of field, professional photography."},
            "7": {"seed": random.randint(0, 2**32 - 1)},
        },
        img_path,
        log,
    )

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
```


#### 4. Wan2.2 i2v template
**File**: `workflows/wan22_i2v_api.json`
**Changes**: NEW - complete API-format template on installed nodes (UnetLoaderGGUF + Wan22ImageToVideoLatent + CreateVideo/SaveVideo; LoadImage for the input frame).


API-format template: native `Wan22ImageToVideoLatent` (comfy_extras/nodes_wan.py:1255 — no custom node needed) fed by LoadImage node 13, Wan2.2-TI2V-5B-Q8_0 GGUF loader chain, fastwan LoRA, ModelSamplingSD3 shift 8.0, KSampler 8 steps, VAEDecode, CreateVideo(16 fps) → SaveVideo. 704x1280, 121 frames = 7.5 s. Patch points: node 5 text, node 13 image ref, node 9 seed.

```json
{
  "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "Wan2.2-TI2V-5B-Q8_0.gguf"}},
  "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "wan2.2_ti2v_5B_fastwan.safetensors", "strength_model": 1.0, "model": ["1", 0]}},
  "3": {"class_type": "ModelSamplingSD3", "inputs": {"shift": 8.0, "model": ["2", 0]}},
  "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan"}},
  "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 0]}},
  "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 0]}},
  "7": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
  "8": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["7", 0], "width": 704, "height": 1280, "length": 121, "batch_size": 1, "start_image": ["13", 0]}},
  "9": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["3", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["8", 0]}},
  "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["7", 0]}},
  "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": 16.0}},
  "12": {"class_type": "SaveVideo", "inputs": {"video": ["11", 0], "filename_prefix": "openshorts/i2v", "format": "auto", "codec": "auto"}},
  "13": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}}
}
```

**Slice 4 calibration knobs** (defaults ship byte-identical to the JSON above; applied only if the live 10 GB calibration demands, in this order):

```python
# Two fallback knobs on node 8 (Wan22ImageToVideoLatent):
{"8": {"inputs": {"length": 81}}}                   # 121 -> 81 frames (7.6 s -> 5.1 s);
                                                    # _loop_clip_to_duration re-extends
                                                    # to narration length: the cost is
                                                    # temporal variety, never sync
{"8": {"inputs": {"width": 480, "height": 832}}}    # last-resort smaller latent
# Log per-stage seconds from the [local] lines for the PR description.
```


#### 5. Portrait template
**File**: `workflows/flux_dev_portrait_api.json`
**Changes**: NEW - complete Flux dev Q4 text2image template (DualCLIPLoaderGGUF clip_name1/2, FluxGuidance, SaveImage).


API-format template: Flux dev Q4 GGUF text2image portrait — UnetLoaderGGUF + DualCLIPLoaderGGUF(clip_name1 t5 Q4 / clip_name2 clip_l, host-verified) + FluxGuidance 3.5 + KSampler 20 steps + VAEDecode + SaveImage at 896x1152. Patch points: node 3 text, node 7 seed.

```json
{
  "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux1-dev-Q4_K_S.gguf"}},
  "2": {"class_type": "DualCLIPLoaderGGUF", "inputs": {"clip_name1": "t5-v1_1-xxl-encoder-Q4_K_S.gguf", "clip_name2": "clip_l.safetensors", "type": "flux"}},
  "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
  "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
  "5": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["3", 0], "guidance": 3.5}},
  "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 896, "height": 1152, "batch_size": 1}},
  "7": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["1", 0], "positive": ["5", 0], "negative": ["4", 0], "latent_image": ["6", 0]}},
  "8": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
  "9": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["8", 0]}},
  "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "openshorts/portrait"}}
}
```


#### 6. B-roll template
**File**: `workflows/flux_schnell_broll_api.json`
**Changes**: NEW - complete Flux schnell Q4 text2image template (4-step sampler for the b-roll stills).


API-format template: Flux schnell Q4 text2image for b-roll stills — same loader family, KSampler 4 steps, FluxGuidance 0.0. Patch points: node 3 text, node 7 seed.

```json
{
  "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux1-schnell-Q4_K_S.gguf"}},
  "2": {"class_type": "DualCLIPLoaderGGUF", "inputs": {"clip_name1": "t5-v1_1-xxl-encoder-Q4_K_S.gguf", "clip_name2": "clip_l.safetensors", "type": "flux"}},
  "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
  "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
  "5": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["3", 0], "guidance": 0.0}},
  "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 896, "height": 1152, "batch_size": 1}},
  "7": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "model": ["1", 0], "positive": ["5", 0], "negative": ["4", 0], "latent_image": ["6", 0]}},
  "8": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
  "9": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["8", 0]}},
  "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "openshorts/broll"}}
}
```


#### 7. Lipsync template (skeleton)
**File**: `workflows/latentsync_lipsync_api.json`
**Changes**: NEW - documented skeleton with load_video/load_audio string upload refs + exactly one save node; finalized against the live host in Phase 4 (D12).


SKELETON per D12 (developer-approved): Slice 4 installs the LatentSync 1.5 wrapper on the GPU host and replaces this file with the exported API-format workflow. Pinned patch contract: `load_video`.inputs.video and `load_audio`.inputs.audio receive `image_ref()` STRING refs; exactly one output node saves one video file fetchable via GET /view.

```json
{
  "_meta": {
    "title": "LatentSync 1.5 lipsync - SKELETON (Slice 4 finalizes)",
    "note": "No LatentSync wrapper node is installed on the GPU host yet. Slice 4 installs one (pin 1.5) and replaces this file with the exported API-format workflow. PATCH CONTRACT (fixed by the design): node 'load_video'.inputs.video and node 'load_audio'.inputs.audio each receive the upload reference STRING built by comfyui_client.image_ref(name, subfolder) - 'name' or 'subfolder/name'. Never a [name, subfolder] list: API-format prompts parse a 2-element list as a node link. The graph must contain exactly one output node saving one video file fetchable via GET /view. saasshorts_local.generate_talking_head_local patches only these two keys."
  },
  "load_video": {"class_type": "<VIDEO LOAD NODE - Slice 4>", "inputs": {"video": ""}},
  "load_audio": {"class_type": "<AUDIO LOAD NODE - Slice 4>", "inputs": {"audio": ""}},
  "lipsync": {"class_type": "<LatentSync wrapper node - Slice 4>", "inputs": {"video": ["load_video", 0], "audio": ["load_audio", 0], "checkpoint": "latentsync_v15"}},
  "save": {"class_type": "<SaveVideo - Slice 4>", "inputs": {"video": ["lipsync", 0], "filename_prefix": "openshorts/lipsync"}}
}
```

**Slice 4 finalize procedure** (the JSON above ships unchanged until the host export):

```text
1. Install the wrapper on the GPU host, pinned to 1.5 (main carries 1.6; the
   A/B in the manual steps decides whether the pin moves):
     cd F:/AI/ComfyUI_windows_portable/ComfyUI/custom_nodes
     git clone https://github.com/ShmuelRonen/ComfyUI-LatentSyncWrapper
     cd ComfyUI-LatentSyncWrapper
     git checkout 920c15ea
     ..\..\..\python_embeded\python.exe -m pip install -r requirements.txt
   Then drop the LatentSync 1.5 checkpoint and Whisper tiny.pt where the
   wrapper README (1.5 section) says.

2. Restart ComfyUI (port 8188). In the UI build the graph:
   Load Video (upload) -> LatentSync node (checkpoint 1.5) -> video save node;
   an independent Load Audio (upload) node also feeding the LatentSync node.

3. Export API format (Settings -> enable Dev mode -> "Save (API Format)") and
   save it as workflows/latentsync_lipsync_api.json in the repo.

4. Rename the two loader nodes to load_video / load_audio (node ids are
   free-form strings in API format) and run the contract check:
   python -c "import json; g=json.load(open('workflows/latentsync_lipsync_api.json',encoding='utf-8')); assert isinstance(g['load_video']['inputs']['video'],str); assert isinstance(g['load_audio']['inputs']['audio'],str); outs=[k for k,v in g.items() if isinstance(v,dict) and ('save' in str(v.get('class_type','')).lower() or 'videocombine' in str(v.get('class_type','')).lower())]; assert len(outs)==1, outs; print('lipsync template OK:', outs[0])"
   TestTemplates (Slice 1) asserts the node ids and the load_video string ref;
   this validator adds the load_audio string ref and the exactly-one-output
   check.

5. Run one end-to-end pass through generate_talking_head_local with a 2 s
   sample; the muxed head must carry narration (the Step 4/4 [local] line).
```


#### 8. Offline test suite (fixtures + client/adapter/mux/template classes)
**File**: `tests/test_saasshorts_local.py`
**Changes**: NEW - MockTransport client units (test_llm_client idiom), adapter units, mux contract, head atomicity, template contract. Phase 2 appends the dispatch/route/voices/SEO classes to this same file.


Offline suite: MockTransport client units (with `base_url`, per tests/test_llm_client.py:55-63), no-deadline poll proof, FR6 error-copy assertions, ffmpeg-gated mux/atomicity/ken-burns tests, template patch-contract tests. Slice 2 appends dispatch/gate/voices/SEO classes; Slice 1 content stays byte-identical (`import types` added to the stdlib import block).

```python
import json
import os
import shutil
import subprocess
import time
import types

import httpx
import pytest

import comfyui_client
import saasshorts_local
import tts_client

FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _ok_json(payload):
    return httpx.Response(200, json=payload)


@pytest.fixture
def mock_comfyui(monkeypatch):
    def install(handler, base="http://comfy.test"):
        monkeypatch.setenv("COMFYUI_URL", base)
        client = httpx.Client(base_url=base, transport=httpx.MockTransport(handler), timeout=10.0)
        monkeypatch.setattr(comfyui_client, "_client", lambda b: client)
    return install


@pytest.fixture
def mock_tts(monkeypatch):
    def install(handler, base="http://tts.test"):
        monkeypatch.setenv("TTS_BASE_URL", base)
        client = httpx.Client(base_url=base, transport=httpx.MockTransport(handler), timeout=10.0)
        monkeypatch.setattr(tts_client, "_client", lambda b: client)
    return install


class TestComfyUIClient:
    def test_run_stage_happy_path(self, mock_comfyui, tmp_path, monkeypatch):
        polls = {"n": 0}
        monkeypatch.setattr(time, "sleep", lambda s: None)

        def handler(request):
            if request.url.path == "/prompt":
                body = json.loads(request.read())
                assert body["prompt"]["3"]["inputs"]["text"] == "hello"
                return _ok_json({"prompt_id": "p1"})
            if request.url.path == "/history/p1":
                polls["n"] += 1
                if polls["n"] < 3:
                    return _ok_json({})
                return _ok_json({"p1": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"10": {"images": [
                        {"filename": "out_00001_.png", "subfolder": "openshorts", "type": "output"}
                    ]}},
                }})
            if request.url.path == "/queue":
                return _ok_json({"queue_running": [["1", "p1"]], "queue_pending": []})
            if request.url.path == "/view":
                assert request.url.params["filename"] == "out_00001_.png"
                assert request.url.params["type"] == "output"
                return httpx.Response(200, content=b"PNGDATA")
            raise AssertionError(f"unexpected path {request.url.path}")

        mock_comfyui(handler)
        dest = tmp_path / "portrait.png"
        out = comfyui_client.run_stage("portrait", {"3": {"text": "hello"}}, str(dest))
        assert out == str(dest)
        assert dest.read_bytes() == b"PNGDATA"
        assert polls["n"] == 3  # poll continued while the prompt was running

    def test_error_status_raises_naming_url(self, mock_comfyui, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)

        def handler(request):
            return _ok_json({"p1": {
                "status": {"status_str": "error", "messages": [["execution_error", {"node": "9"}]]},
                "outputs": {},
            }})

        mock_comfyui(handler)
        with pytest.raises(comfyui_client.ComfyUIError) as ei:
            comfyui_client.wait_for_output("p1")
        assert "http://comfy.test" in str(ei.value)

    def test_connection_error_names_url(self, mock_comfyui):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        mock_comfyui(handler)
        with pytest.raises(comfyui_client.ComfyUIError) as ei:
            comfyui_client.submit_workflow({"1": {"class_type": "X", "inputs": {}}})
        assert "comfy.test" in str(ei.value)

    def test_poll_has_no_deadline(self, mock_comfyui, monkeypatch):
        sleeps = []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 5:
                return _ok_json({})
            if request.url.path == "/queue":
                return _ok_json({"queue_running": [["1", "p1"]], "queue_pending": []})
            return _ok_json({"p1": {"status": {"status_str": "success"}, "outputs": {
                "n": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
            }}})

        mock_comfyui(handler)
        outs = comfyui_client.wait_for_output("p1")
        assert sleeps == [comfyui_client.POLL_INTERVAL] * 4  # keeps polling, never times out
        assert comfyui_client.output_files(outs)[0]["filename"] == "v.mp4"

    def test_missing_history_entry_raises(self, mock_comfyui, monkeypatch):
        # D11: vanished prompt (history entry gone, not queued) fails fast
        monkeypatch.setattr(time, "sleep", lambda s: None)

        def handler(request):
            if request.url.path == "/history/p1":
                return _ok_json({})
            if request.url.path == "/queue":
                return _ok_json({"queue_running": [], "queue_pending": []})
            raise AssertionError(f"unexpected path {request.url.path}")

        mock_comfyui(handler)
        with pytest.raises(comfyui_client.ComfyUIError) as ei:
            comfyui_client.wait_for_output("p1")
        assert "http://comfy.test" in str(ei.value)
        assert "p1" in str(ei.value)

    def test_upload_input_multipart(self, mock_comfyui, tmp_path):
        p = tmp_path / "a.png"
        p.write_bytes(b"x")
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["ct"] = request.headers.get("content-type", "")
            return _ok_json({"name": "a.png", "subfolder": ""})

        mock_comfyui(handler)
        assert comfyui_client.upload_input(str(p)) == ("a.png", "")
        assert seen["path"] == "/upload/image"
        assert "multipart/form-data" in seen["ct"]

    def test_image_ref(self):
        assert comfyui_client.image_ref("a.png", "") == "a.png"
        assert comfyui_client.image_ref("a.png", "sub") == "sub/a.png"

    def test_patch_workflow_missing_node_raises(self):
        with pytest.raises(comfyui_client.ComfyUIError):
            comfyui_client.patch_workflow({"1": {"inputs": {}}}, {"99": {"x": 1}})

    def test_template_env_override(self, monkeypatch):
        monkeypatch.setenv("COMFYUI_WORKFLOW_I2V", "D:/custom/i2v.json")
        assert comfyui_client.template_path("i2v") == "D:/custom/i2v.json"


class TestTTSClient:
    def test_missing_base_url_names_var(self, monkeypatch):
        monkeypatch.delenv("TTS_BASE_URL", raising=False)
        with pytest.raises(tts_client.TTSError) as ei:
            tts_client.tts_base_url()
        assert "TTS_BASE_URL" in str(ei.value)

    def test_synthesize_body_and_default_voice(self, mock_tts, tmp_path):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.read()))
            return httpx.Response(200, content=b"MP3DATA")

        mock_tts(handler)
        out = tts_client.synthesize("hola mundo", str(tmp_path / "v.mp3"))
        assert (tmp_path / "v.mp3").read_bytes() == b"MP3DATA"
        assert bodies[0] == {"input": "hola mundo", "response_format": "mp3"}

    def test_synthesize_error_names_url(self, mock_tts, tmp_path):
        def handler(request):
            return httpx.Response(500, text="boom")

        mock_tts(handler)
        with pytest.raises(tts_client.TTSError) as ei:
            tts_client.synthesize("x", str(tmp_path / "v.mp3"), voice_id="v9")
        assert "http://tts.test" in str(ei.value)

    def test_list_voices_tolerates_shapes(self, mock_tts):
        def handler(request):
            assert request.url.path == "/v1/voices"
            return _ok_json({"voices": [{"id": "v1", "name": "Ana"}, {"voice_id": "v2"}]})

        mock_tts(handler)
        assert tts_client.list_voices() == [
            {"voice_id": "v1", "name": "Ana"},
            {"voice_id": "v2", "name": "v2"},
        ]


class TestVoiceoverAdapter:
    def test_elevenlabs_default_resolves_to_server_default(self, monkeypatch, tmp_path):
        seen = {}

        def fake_synth(text, dest, voice_id=None, log=print):
            seen["voice"] = voice_id
            return dest

        monkeypatch.setattr(tts_client, "synthesize", fake_synth)
        saasshorts_local.generate_voiceover_local(
            "hi", str(tmp_path / "v.mp3"), voice_id="21m00Tcm4TlvDq8ikWAM"
        )
        assert seen["voice"] is None


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe required")
class TestMuxAndAtomicity:
    @staticmethod
    def _make_media(tmp_path):
        vid = tmp_path / "silent.mp4"
        aud = tmp_path / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x568:d=2:r=16",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:a", "libmp3lame", str(aud)],
            check=True, capture_output=True,
        )
        return vid, aud

    def test_muxed_head_has_audio_stream(self, tmp_path):
        vid, aud = self._make_media(tmp_path)
        out = tmp_path / "head.mp4"
        saasshorts_local._mux_audio(str(vid), str(aud), str(out))
        assert saasshorts_local._ffprobe_has_audio(str(out))

    def test_failed_lipsync_leaves_head_absent(self, tmp_path, monkeypatch):
        vid, aud = self._make_media(tmp_path)
        head = tmp_path / "x_head.mp4"

        def fake_run_stage(stage, patches, dest, log=print):
            if stage == "i2v":
                shutil.copy(vid, dest)  # the "wan" output is a silent clip
                return dest
            if stage == "lipsync":
                raise comfyui_client.ComfyUIError(
                    "ComfyUI prompt error - service: ComfyUI at http://comfy.test"
                )
            raise AssertionError(f"unexpected stage {stage}")

        monkeypatch.setattr(comfyui_client, "upload_input", lambda p, log=print: ("f", ""))
        monkeypatch.setattr(comfyui_client, "run_stage", fake_run_stage)
        with pytest.raises(comfyui_client.ComfyUIError):
            saasshorts_local.generate_talking_head_local(str(vid), str(aud), str(head))
        assert not head.exists()                              # final path never written (D6)
        assert (tmp_path / "x_head_wan_cache.mp4").exists()   # retryable intermediates kept
        assert (tmp_path / "x_head_loop_cache.mp4").exists()

    def test_head_pipeline_caches_each_stage(self, tmp_path, monkeypatch):
        vid, aud = self._make_media(tmp_path)
        head = tmp_path / "x_head.mp4"
        stages = []

        def fake_run_stage(stage, patches, dest, log=print):
            stages.append(stage)
            shutil.copy(vid, dest)
            return dest

        monkeypatch.setattr(comfyui_client, "upload_input", lambda p, log=print: ("f", ""))
        monkeypatch.setattr(comfyui_client, "run_stage", fake_run_stage)
        saasshorts_local.generate_talking_head_local(str(vid), str(aud), str(head))
        assert stages == ["i2v", "lipsync"]
        assert head.exists() and saasshorts_local._ffprobe_has_audio(str(head))

        stages.clear()  # second run: every ComfyUI stage cached, only the mux repeats
        saasshorts_local.generate_talking_head_local(str(vid), str(aud), str(head))
        assert stages == []

    def test_broll_local_ken_burns_has_audio(self, tmp_path, monkeypatch):
        def fake_run_stage(stage, patches, dest, log=print):
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x960:d=1",
                 "-frames:v", "1", str(dest)],
                check=True, capture_output=True,
            )
            return dest

        monkeypatch.setattr(comfyui_client, "run_stage", fake_run_stage)
        out = tmp_path / "b.mp4"
        saasshorts_local.generate_broll_local("test shot", str(out), "1")
        assert out.exists() and out.stat().st_size > 0
        assert saasshorts_local._ffprobe_has_audio(str(out))  # anullsrc track (D8 shape)


class TestTemplates:
    def test_all_templates_valid_json(self):
        here = os.path.dirname(os.path.abspath(saasshorts_local.__file__))
        wf_dir = os.path.join(here, "workflows")
        names = [n for n in os.listdir(wf_dir) if n.endswith(".json")]
        assert len(names) == 4
        for name in names:
            with open(os.path.join(wf_dir, name), encoding="utf-8") as f:
                graph = json.load(f)
            assert any(isinstance(v, dict) and "class_type" in v for v in graph.values()), name

    def test_patch_contract_anchors_exist(self):
        here = os.path.dirname(os.path.abspath(saasshorts_local.__file__))
        wf_dir = os.path.join(here, "workflows")
        with open(os.path.join(wf_dir, "flux_dev_portrait_api.json"), encoding="utf-8") as f:
            assert "3" in json.load(f)  # positive-text node the portrait patches
        with open(os.path.join(wf_dir, "wan22_i2v_api.json"), encoding="utf-8") as f:
            i2v = json.load(f)
            assert {"5", "9", "13"} <= set(i2v)          # text, seed, LoadImage nodes
            assert i2v["8"]["inputs"]["start_image"] == ["13", 0]  # wired via link
        with open(os.path.join(wf_dir, "latentsync_lipsync_api.json"), encoding="utf-8") as f:
            lipsync = json.load(f)
            assert {"load_video", "load_audio"} <= set(lipsync)  # documented skeleton contract
            assert isinstance(lipsync["load_video"]["inputs"]["video"], str)  # string ref, not a link list

# ═══════════════════════════════════════════════════════════════════════
# Slice 2: dispatch + gates + validation
# ═══════════════════════════════════════════════════════════════════════
```

### Success Criteria:

#### Automated Verification:
- [x] Tests pass: `python -m pytest tests/test_saasshorts_local.py -q`
- [x] Templates parse: `python -c "import json,glob; [json.load(open(p)) for p in glob.glob('workflows/*.json')]"` exits 0
- [x] Cycle guard holds: `python -c "import sys; import saasshorts_local; assert 'saasshorts' not in sys.modules"` exits 0
- [x] FR6 error copy present: `grep -c "service:" comfyui_client.py tts_client.py` returns >= 8 combined
- [x] No poll deadline: `grep -n "timeout" comfyui_client.py` shows `_UPLOAD_TIMEOUT`/`_DOWNLOAD_TIMEOUT` only - none inside `wait_for_output`
- [x] Missing-history fail-fast (test): a prompt in neither `/history` nor `/queue` raises `ComfyUIError` naming the ComfyUI URL
- [x] Mux contract (ffmpeg-gated test): muxed head carries an audio stream
- [x] Head atomicity (ffmpeg-gated test): a lipsync failure leaves `{slug}_head.mp4` absent while wan/loop caches persist

#### Manual Verification:
- [ ] None required (CPU-only slice; all GPU-host checks live in Slice 4)

---
## Phase 2: Dispatch + gates + validation

### Overview
Wires the local arm into the pipeline at the locked hunk anchors: keys via .get (local passes None), steps 1-2 serialized in local mode (D7, the only pipeline-shape divergence), the three-way head dispatch, the b-roll mode pick, the third $0 cost branch, Literal validation on both request models (D4), mode-aware gates (D5), the voices local branch (D9), and the SEO LOCAL surface (D10). Cloud modes stay byte-identical; the dispatch matrix test asserts zero cloud helper calls in local mode (silent-spend guard).

### Changes Required:

#### 1. Pipeline dispatch
**File**: `saasshorts.py`
**Changes**: MODIFY - six hunks at locked anchors (25, 1348-1351, 1382-1404, 1406-1418, 1446-1449, 1484-1502): local import, keys via .get + video_mode read once, steps 1-2 serialized (D7), three-way head dispatch, b-roll mode pick, third $0 cost branch. Cloud paths byte-identical.


Keys via `config.get` (`:1348-1351`); local dispatch arms at the four call sites (`:1382-1404` steps 1-2 with local serialization per D7, `:1406-1418` talking head three-way, `:1446-1449` b-roll loop body); third $0 cost branch (`:1484-1502`, `cost["total"]` re-emitted after the branch). Cloud helpers and signatures untouched. Hunk ranges verified against HEAD 73f670a by slice-verifier runs 2-3 (2026-09-15); hunks list only changed code with exact on-disk anchors.

```python
# Slice 2 hunks carry exact on-disk anchor ranges (HEAD 73f670a, verified by
# slice-verifier runs 2-3). Cloud code paths stay byte-identical.

# ── Hunk 1 — import (inserts after saasshorts.py:25) ──
from concurrent.futures import ThreadPoolExecutor, as_completed
import saasshorts_local

# ── Hunk 2 — keys via .get + video_mode read once (replaces lines 1348-1351) ──
    fal_key = config.get("fal_key")                 # None in local mode: no paid stage runs
    elevenlabs_key = config.get("elevenlabs_key")   # None in local mode
    voice_id = config.get("voice_id", "21m00Tcm4TlvDq8ikWAM")
    actor_desc = config.get("actor_description") or script.get("actor_description", "a young professional in their late 20s, wearing a casual modern outfit, clean background")
    video_mode = config.get("video_mode", "premium")  # read once, used by every dispatch site below

# ── Hunk 3 — steps 1-2: local serialization (replaces lines 1382-1404) ──
    if need_img or need_voice:
        tasks = []
        if need_img:
            tasks.append("actor image")
        if need_voice:
            tasks.append("voiceover")
        how = "sequential" if video_mode == "local" else "parallel"
        log(f"[1/6] Generating {' + '.join(tasks)} ({how})...")

        if video_mode == "local":
            # Local mode serializes steps 1-2 (design D7): the TTS server peaks
            # 5-7 GB VRAM and Wan2.2 peaks ~8 GB - one 10 GB card OOMs on overlap.
            if need_img:
                actor_img = saasshorts_local.generate_actor_image_local(actor_desc, actor_img, log=log)
            if need_voice:
                audio_path = saasshorts_local.generate_voiceover_local(full_narration, audio_path, voice_id, log=log)
        else:
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_img = executor.submit(generate_actor_image, actor_desc, fal_key, actor_img) if need_img else None
                future_voice = executor.submit(
                    generate_voiceover, full_narration, elevenlabs_key, audio_path, voice_id
                ) if need_voice else None

                if future_img:
                    actor_img = future_img.result()
                if future_voice:
                    audio_path = future_voice.result()

        log("[2/6] Actor image and voiceover ready.")
    else:
        log("[1/6] Actor image and voiceover cached, skipping.")
        log("[2/6] ✅ Using cached assets.")

# ── Hunk 4 — step 3: three-way head dispatch (replaces lines 1406-1418; the
#    _exists(talking_head) guard at 1408 is re-emitted verbatim - D6 cache
#    contract; the old line-1407 video_mode read is dropped, moved to Hunk 2) ──
    # ── Step 3: Generate talking head ──
    if not _exists(talking_head):
        if video_mode == "local":
            log("[3/6] Generating talking head (Local: Wan2.2 i2v + LatentSync lipsync)... No time limit.")
            talking_head = saasshorts_local.generate_talking_head_local(
                actor_img, audio_path, talking_head, log=log
            )
        elif video_mode == "lowcost":
            log("[3/6] Generating talking head (Low Cost: Hailuo + VEED Lipsync)... This takes 2-5 minutes.")
            talking_head = generate_talking_head_lowcost(actor_img, audio_path, fal_key, talking_head)
        else:
            log("[3/6] Generating talking head video (Kling Avatar v2)... This takes 2-5 minutes.")
            talking_head = generate_talking_head(actor_img, audio_path, fal_key, talking_head)
        log("[3/6] Talking head ready.")
    else:
        log("[3/6] ✅ Talking head cached, skipping.")

# ── Hunk 5 — step 4: b-roll submit picks the callable by mode (replaces lines
#    1446-1449, the loop body under the for-header at 1445; the with-block and
#    futures dict at 1443-1444 stay untouched) ──
                    if video_mode == "local":
                        # ComfyUI's own queue serializes GPU work per instance -
                        # the pool only parallelizes HTTP submissions (D7).
                        future = executor.submit(
                            saasshorts_local.generate_broll_local,
                            seg["broll_prompt"], broll_path, log=log,
                        )
                    else:
                        future = executor.submit(
                            generate_broll, seg["broll_prompt"], fal_key, broll_path
                        )
                    futures[future] = {"seg": seg, "path": broll_path}

# ── Hunk 6 — cost: third $0 branch (replaces lines 1484-1502; the cost["total"]
#    line at 1502 is re-emitted after the branch) ──
    audio_duration = _get_media_duration(audio_path)
    if video_mode == "local":
        # FR7: everything ran on the user's own GPU - nothing to bill.
        cost = {
            "actor_image_comfyui": 0.00,
            "voiceover_local_tts": 0.00,
            "talking_head_wan_lipsync": 0.00,
            "broll_comfyui": 0.00,
            "ffmpeg_compositing": 0.00,
        }
    elif video_mode == "lowcost":
        cost = {
            "actor_image_flux": 0.05,
            "voiceover_elevenlabs": round(len(full_narration) * 0.00003, 3),
            "hailuo_img2video": 0.19,
            "veed_lipsync": 0.20,
            "broll_flux": round(len(broll_clips) * 0.05, 2),
            "ffmpeg_compositing": 0.00,
        }
    else:
        cost = {
            "actor_image_flux": 0.05,
            "voiceover_elevenlabs": round(len(full_narration) * 0.00003, 3),
            "talking_head_kling": round(audio_duration * 0.056, 2),
            "broll_kling": round(len(broll_clips) * 5 * 0.07, 2),
            "ffmpeg_compositing": 0.00,
        }
    cost["total"] = round(sum(cost.values()), 2)
```

#### 2. Routes and validation
**File**: `app.py`
**Changes**: MODIFY - hunks A1-A8b at locked anchors (22, 6538, 6616-6619, 6669-6700, 7783, 7862, 7934, 7951-7955, 8120-8144): Literal validation (D4), mode-aware gates (D5), voices local branch (D9), SEO LOCAL surface (D10).


`Literal` on `SaaSGenerateRequest.video_mode` + field on `SaaSActorRequest` (D4); mode-aware gates (both routes, D5); actor-options local branch returning `/videos/` URLs without S3; SEO builders gain a `local` label branch (D10); voices route local branch per D9. Hunk ranges verified against HEAD 73f670a by slice-verifier runs 2-3 (2026-09-15).

```python
# ── Hunk A1 — line 22: add Literal ──
from typing import Any, Dict, Optional, List, Literal

# ── Hunk A2 — insert after the from-saasshorts import block (app.py:6521-6530;
#    i.e. after the closing ')' at 6530, before '# State for SaaSShorts jobs' at 6532;
#    corrected from 6537 by the post-finalization code review - 6537 is inside
#    class SaaSAnalyzeRequest) ──
import saasshorts_local  # local video_mode arm (env-configured, zero cloud calls)


def _local_arm_startup_warnings() -> List[str]:
    # Lesson 5: a set-but-unreachable COMFYUI_URL / TTS_BASE_URL surfaces at
    # startup as one warning line naming the env var and URL (llm_client
    # half-config spirit). Unset vars stay silent. Needs os (imported at HEAD)
    # and a local httpx import - no new module dependency.
    import httpx

    out: List[str] = []
    for env, label in (("COMFYUI_URL", "ComfyUI"), ("TTS_BASE_URL", "local TTS")):
        url = (os.environ.get(env) or "").strip()
        if not url:
            continue
        try:
            httpx.get(url, timeout=2.0)
        except Exception as e:
            out.append(
                f"{env}={url} unreachable ({e}) - local video_mode {label} calls will fail until it is up"
            )
    return out


for _warning in _local_arm_startup_warnings():
    print(f"[local] {_warning}", flush=True)

# ── Hunk A3 — SaaSActorRequest (lines 6616-6619): add video_mode field ──
class SaaSActorRequest(BaseModel):
    actor_description: str
    num_options: int = 3
    product_description: Optional[str] = None
    video_mode: Literal["premium", "lowcost", "local"] = "lowcost"

# ── Hunk A4 — actor-options route: mode-aware gate (replaces lines 6669-6670)
#    + local arm inserted after lines 6677-6678 (loop = asyncio.get_running_loop()
#    / import functools), before the cloud run_in_executor call at 6679 ──
    fal_key = x_fal_key
    if req.video_mode != "local" and not fal_key:
        raise HTTPException(status_code=400, detail="Missing fal.ai API Key")

    try:
        job_id = str(uuid.uuid4())
        out_dir = os.path.join(OUTPUT_DIR, f"saas_actors_{job_id}")
        os.makedirs(out_dir, exist_ok=True)

        loop = asyncio.get_running_loop()
        import functools
        if req.video_mode == "local":
            # Local mode: ComfyUI portraits, no keys, no S3 upload - the images
            # are served from the instance's /videos/ mount.
            paths = await loop.run_in_executor(
                None,
                functools.partial(
                    saasshorts_local.generate_actor_images_local,
                    req.actor_description, out_dir, "actor", req.num_options,
                    product_description=req.product_description,
                ),
            )
            return {
                "images": [
                    f"/videos/saas_actors_{job_id}/{os.path.basename(p)}" for p in paths
                ]
            }

        paths = await loop.run_in_executor(
            None,
            functools.partial(
                generate_actor_images,
                req.actor_description, fal_key, out_dir, "actor", req.num_options,
                product_description=req.product_description,
            ),
        )
        # (unchanged: S3 upload loop and return)

# ── Hunk A5 — SaaSGenerateRequest.video_mode (line 7934) ──
    video_mode: Literal["premium", "lowcost", "local"] = "lowcost"  # "local" = self-hosted ComfyUI + TTS

# ── Hunk A6 — generate route: mode-aware gate (replaces lines 7951-7955) ──
    if req.video_mode != "local":
        # Cloud modes need paid keys; local mode runs against env-configured
        # self-hosted services (design D5). Cloud behavior is byte-identical.
        if not fal_key:
            raise HTTPException(status_code=400, detail="Missing fal.ai API Key (X-Fal-Key header)")
        if not elevenlabs_key:
            raise HTTPException(status_code=400, detail="Missing ElevenLabs API Key (X-ElevenLabs-Key header)")

# ── Hunk A7 — voices route: local branch (replaces lines 8120-8144) ──
@app.get("/api/saasshorts/voices")
async def saasshorts_voices(
    x_elevenlabs_key: Optional[str] = Header(None, alias="X-ElevenLabs-Key"),
    video_mode: str = "lowcost",
):
    """List voices: ElevenLabs by default, the local TTS server in local mode."""
    if video_mode == "local":
        # Read-only TTS-server catalog (design D9). Never falls back to cloud
        # defaults in local mode: an empty list plus the error lets the picker
        # say why, and generation still works via the server default voice.
        try:
            loop = asyncio.get_event_loop()
            voices = await loop.run_in_executor(None, saasshorts_local.get_local_tts_voices)
            return {
                "voices": [
                    {"voice_id": v.get("voice_id"), "name": v.get("name"), "category": "local"}
                    for v in voices
                ],
                "source": "local",
            }
        except Exception as e:
            return {"voices": [], "source": "local", "error": str(e)}

    if x_elevenlabs_key:
        try:
            loop = asyncio.get_event_loop()
            voices = await loop.run_in_executor(
                None, get_elevenlabs_voices, x_elevenlabs_key
            )
            if voices:
                return {"voices": voices, "source": "elevenlabs"}
        except Exception:
            pass

    # Fallback to default voices
    return {
        "voices": [
            {"voice_id": vid, "name": name, "category": "default"}
            for name, vid in DEFAULT_VOICES.items()
        ],
        "source": "defaults",
    }

# ── Hunk A8a — gallery SEO badge (line 7783): LOCAL branch prepended ──
        if mode == "local":
            mode_badge = '<span style="background:#0ea5e9;color:#fff;padding:2px 8px;border-radius:9999px;font-size:10px;font-weight:700">LOCAL</span>'
        else:
            mode_badge = '<span style="background:#22c55e;color:#000;padding:2px 8px;border-radius:9999px;font-size:10px;font-weight:700">LOW COST</span>' if mode == "lowcost" else '<span style="background:#8b5cf6;color:#fff;padding:2px 8px;border-radius:9999px;font-size:10px;font-weight:700">PREMIUM</span>'

# ── Hunk A8b — video detail SEO label (line 7862) ──
    mode_label = {"lowcost": "Low Cost", "local": "Local"}.get(mode, "Premium")
```

#### 3. Dispatch/route test classes (appended)
**File**: `tests/test_saasshorts_local.py`
**Changes**: MODIFY - appended to the Phase 1 file: TestDispatchMatrix (zero cloud calls, serialization), TestRouteModeMatrix (200/400/422), TestVoicesLocalBranch, TestSeoLocalSurface, and the app_client fixture.

```python
class TestDispatchMatrix:
    """generate_full_video mode arms: local -> only local adapters (zero cloud
    calls, silent-spend guard); premium/lowcost -> only their cloud helpers."""

    SCRIPT = {
        "title": "Dispatch Probe",
        "full_narration": "hello local mode",
        "segments": [{"start": 0, "end": 5, "visual": "broll", "broll_prompt": "desk shot"}],
    }

    @pytest.fixture
    def pipeline(self, monkeypatch, tmp_path):
        import saasshorts

        calls, intervals = [], {}

        def make(name, result):
            def fake(*args, **kwargs):
                t0 = time.monotonic()
                calls.append(name)
                time.sleep(0.01)
                intervals[name] = (t0, time.monotonic())
                return result
            return fake

        actor_png = str(tmp_path / "dispatch_probe_actor.png")
        voice_mp3 = str(tmp_path / "dispatch_probe_voice.mp3")
        head_mp4 = str(tmp_path / "dispatch_probe_head.mp4")

        # cloud helpers (patched on the saasshorts module namespace)
        monkeypatch.setattr(saasshorts, "generate_actor_image", make("actor:cloud", actor_png))
        monkeypatch.setattr(saasshorts, "generate_voiceover", make("voice:cloud", voice_mp3))
        monkeypatch.setattr(saasshorts, "generate_talking_head", make("head:premium", head_mp4))
        monkeypatch.setattr(saasshorts, "generate_talking_head_lowcost", make("head:lowcost", head_mp4))
        monkeypatch.setattr(saasshorts, "generate_broll", make("broll:cloud", None))
        # local adapters (patched on the shared saasshorts_local module object)
        monkeypatch.setattr(saasshorts_local, "generate_actor_image_local", make("actor:local", actor_png))
        monkeypatch.setattr(saasshorts_local, "generate_voiceover_local", make("voice:local", voice_mp3))
        monkeypatch.setattr(saasshorts_local, "generate_talking_head_local", make("head:local", head_mp4))
        monkeypatch.setattr(saasshorts_local, "generate_broll_local", make("broll:local", None))
        # tail stages: subtitles, composite, AI marking, duration
        monkeypatch.setattr(saasshorts, "generate_tiktok_subs", make("subs", None))
        monkeypatch.setattr(saasshorts, "composite_video", make("composite", str(tmp_path / "f.mp4")))
        monkeypatch.setattr(saasshorts, "mark_ai_generated", make("mark", None))
        monkeypatch.setattr(saasshorts, "_get_media_duration", make("dur", 5.0))
        return types.SimpleNamespace(calls=calls, intervals=intervals)

    def test_local_calls_only_local_adapters(self, pipeline, tmp_path):
        import saasshorts

        result = saasshorts.generate_full_video(
            self.SCRIPT,
            {"video_mode": "local"},  # no keys at all - the local contract
            str(tmp_path),
            log=lambda m: None,
        )
        for name in ("actor:local", "voice:local", "head:local", "broll:local"):
            assert name in pipeline.calls
        cloud = {"actor:cloud", "voice:cloud", "head:premium", "head:lowcost", "broll:cloud"}
        assert not set(pipeline.calls) & cloud  # silent-spend guard (advisor 4a)
        assert result["cost_estimate"]["total"] == 0.0
        assert all(v == 0 for k, v in result["cost_estimate"].items() if k != "total")

    def test_local_serializes_steps_1_and_2(self, pipeline, tmp_path):
        import saasshorts

        saasshorts.generate_full_video(
            self.SCRIPT, {"video_mode": "local"}, str(tmp_path), log=lambda m: None
        )
        a0, a1 = pipeline.intervals["actor:local"]
        v0, v1 = pipeline.intervals["voice:local"]
        assert a1 <= v0  # D7: actor image fully done before voiceover starts

    def test_premium_unchanged(self, pipeline, tmp_path):
        import saasshorts

        result = saasshorts.generate_full_video(
            self.SCRIPT,
            {"video_mode": "premium", "fal_key": "k", "elevenlabs_key": "k"},
            str(tmp_path),
            log=lambda m: None,
        )
        assert {"actor:cloud", "voice:cloud", "head:premium", "broll:cloud"} <= set(pipeline.calls)
        assert not any(c.endswith(":local") for c in pipeline.calls)
        assert result["cost_estimate"]["talking_head_kling"] == round(5.0 * 0.056, 2)

    def test_lowcost_unchanged(self, pipeline, tmp_path):
        import saasshorts

        result = saasshorts.generate_full_video(
            self.SCRIPT,
            {"video_mode": "lowcost", "fal_key": "k", "elevenlabs_key": "k"},
            str(tmp_path),
            log=lambda m: None,
        )
        assert "head:lowcost" in pipeline.calls and "head:premium" not in pipeline.calls
        assert not any(c.endswith(":local") for c in pipeline.calls)
        assert result["cost_estimate"]["veed_lipsync"] == 0.20


APP_RESULT = {
    "video_path": "o/x_final.mp4",
    "video_filename": "x_final.mp4",
    "srt_path": "o/x_subs.ass",
    "actor_image": "o/x_actor.png",
    "duration": 5.0,
    "cost_estimate": {"total": 0.0},
}


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    import app as app_module
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "generate_full_video", lambda *a, **k: dict(APP_RESULT))
    return TestClient(app_module.app, raise_server_exceptions=False)


class TestRouteModeMatrix:
    def test_generate_local_without_keys_is_accepted(self, app_client):
        r = app_client.post(
            "/api/saasshorts/generate",
            json={"script": TestDispatchMatrix.SCRIPT, "video_mode": "local"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "processing" and body["job_id"]

    def test_generate_premium_without_keys_is_400(self, app_client):
        r = app_client.post(
            "/api/saasshorts/generate",
            json={"script": TestDispatchMatrix.SCRIPT, "video_mode": "premium"},
        )
        assert r.status_code == 400
        assert "fal.ai" in r.json()["detail"]

    def test_generate_premium_missing_elevenlabs_is_400(self, app_client):
        r = app_client.post(
            "/api/saasshorts/generate",
            json={"script": TestDispatchMatrix.SCRIPT, "video_mode": "premium"},
            headers={"X-Fal-Key": "k"},
        )
        assert r.status_code == 400
        assert "ElevenLabs" in r.json()["detail"]

    def test_generate_invalid_mode_is_422(self, app_client):
        r = app_client.post(
            "/api/saasshorts/generate",
            json={"script": TestDispatchMatrix.SCRIPT, "video_mode": "premiun"},
        )
        assert r.status_code == 422  # never silently runs premium

    def test_actor_options_local_without_keys_serves_videos_urls(self, monkeypatch, app_client):
        monkeypatch.setattr(
            saasshorts_local,
            "generate_actor_images_local",
            lambda description, output_dir, title_slug, num_options=3,
                   product_description=None, log=print: [
                os.path.join(output_dir, f"{title_slug}_actor_option_{i}.png")
                for i in range(num_options)
            ],
        )
        r = app_client.post(
            "/api/saasshorts/actor-options",
            json={"actor_description": "young dev", "video_mode": "local"},
        )
        assert r.status_code == 200
        images = r.json()["images"]
        assert len(images) == 3
        assert all(u.startswith("/videos/saas_actors_") and u.endswith(".png") for u in images)

    def test_actor_options_cloud_without_keys_is_400(self, app_client):
        r = app_client.post(
            "/api/saasshorts/actor-options",
            json={"actor_description": "young dev", "video_mode": "lowcost"},
        )
        assert r.status_code == 400

    def test_actor_options_invalid_mode_is_422(self, app_client):
        r = app_client.post(
            "/api/saasshorts/actor-options",
            json={"actor_description": "young dev", "video_mode": "locall"},
        )
        assert r.status_code == 422


class TestVoicesLocalBranch:
    def test_local_lists_tts_voices(self, monkeypatch, app_client):
        monkeypatch.setattr(
            saasshorts_local,
            "get_local_tts_voices",
            lambda log=print: [{"voice_id": "v1", "name": "Ana"}],
        )
        r = app_client.get("/api/saasshorts/voices", params={"video_mode": "local"})
        assert r.status_code == 200
        assert r.json() == {
            "voices": [{"voice_id": "v1", "name": "Ana", "category": "local"}],
            "source": "local",
        }

    def test_local_tts_down_returns_empty_with_error(self, monkeypatch, app_client):
        def boom(log=print):
            raise tts_client.TTSError(
                "TTS voice list failed - service: TTS server at http://127.0.0.1:8000"
            )

        monkeypatch.setattr(saasshorts_local, "get_local_tts_voices", boom)
        r = app_client.get("/api/saasshorts/voices", params={"video_mode": "local"})
        assert r.status_code == 200  # picker renders the error; generation still works
        body = r.json()
        assert body["voices"] == [] and body["source"] == "local"
        assert "TTS server" in body["error"]

    def test_cloud_mode_unchanged_without_key(self, monkeypatch, app_client):
        monkeypatch.delenv("TTS_BASE_URL", raising=False)
        body = app_client.get("/api/saasshorts/voices").json()
        assert body["source"] == "defaults"
        assert body["voices"][0]["category"] == "default"


class TestSeoLocalSurface:
    META = {
        "title": "Mode Probe", "caption": "cap", "full_narration": "",
        "video_url": "/videos/x.mp4", "actor_url": "/videos/a.png",
        "video_id": "modeprobe", "duration": 5, "video_mode": "local",
        "product_name": "pn", "product_url": "", "language": "en",
        "hashtags": [], "cost_estimate": {"total": 0}, "created_at": "",
        "actor_description": "",
    }

    def test_gallery_and_detail_render_local(self, monkeypatch, app_client):
        import app as app_module

        monkeypatch.setattr(app_module, "list_video_gallery", lambda limit=100: [dict(self.META)])
        gallery = app_client.get("/gallery").text
        assert ">LOCAL<" in gallery
        # badge markup only: the static gallery subtitle legitimately names the cloud modes
        assert ">PREMIUM<" not in gallery and ">LOW COST<" not in gallery
        detail = app_client.get("/video/modeprobe").text
        assert "Local" in detail
        assert "Premium" not in detail and "Low Cost" not in detail


class TestLocalArmStartupProbe:
    def test_names_unreachable_url_and_stays_silent_unset(self, monkeypatch):
        import app as app_module
        import httpx as _httpx

        def refused(*args, **kwargs):
            raise _httpx.ConnectError("refused")

        monkeypatch.setattr(_httpx, "get", refused)
        monkeypatch.setenv("COMFYUI_URL", "http://comfy.test:59999")
        monkeypatch.delenv("TTS_BASE_URL", raising=False)
        warns = app_module._local_arm_startup_warnings()
        assert len(warns) == 1 and "COMFYUI_URL" in warns[0]
        monkeypatch.delenv("COMFYUI_URL", raising=False)
        assert app_module._local_arm_startup_warnings() == []
```

### Success Criteria:

#### Automated Verification:
- [x] Tests pass: `python -m pytest tests/test_saasshorts_local.py -q`
- [x] Dispatch (test): `video_mode="local"` calls only local adapters - zero cloud helpers, b-roll included (silent-spend guard)
- [x] Serialization (test): local steps 1-2 run non-overlapping - the actor interval ends before the voiceover interval starts
- [x] Cloud unchanged (test): premium -> Kling head, lowcost -> Hailuo/VEED head; neither touches a local adapter; cost tables keep their keys
- [x] $0 cost (test): local `cost_estimate.total == 0.0` and every line item is 0
- [x] Route matrix (test): local + no keys -> 200; premium + no keys -> 400 (fal, then ElevenLabs); invalid `video_mode` -> 422 on both `/api/saasshorts/generate` and `/api/saasshorts/actor-options`
- [x] Actor-options local (test): returns `/videos/saas_actors_*` URLs with no S3 upload
- [x] Voices (test): local lists TTS voices with `category: "local"`; TTS down -> `{voices: [], source: "local", error}` with 200; cloud default path still `source: "defaults"`
- [x] SEO (test): `/gallery` renders a LOCAL badge and `/video/{id}` renders the Local label for a `video_mode: "local"` video; badge markup carries no `>PREMIUM<`/`>LOW COST<` (subtitle prose naming cloud modes is allowed)
- [x] Gates present: `grep -c 'video_mode != "local"' app.py` returns 2; `grep -c 'Literal\["premium", "lowcost", "local"\]' app.py` returns 2
- [x] Half-config probe (test): with `COMFYUI_URL` set to an unreachable URL, `_local_arm_startup_warnings()` returns a warning naming the env var and URL; unset vars return `[]`

#### Manual Verification:
- [ ] None required (offline slice; live GPU-host end-to-end is Slice 4)

---
## Phase 3: Wizard + gallery surface

### Overview
Consumes the Phase 2 contracts with no app.py change (slice-verifier confirmed): the wizard gains the third mode card, the FREE cost card, mode-aware key pre-alerts/disables, the voices refetch on mode switch with the TTS-down edge, and the T2b guard that stops the gender/language voice-reset effect from writing ElevenLabs ids in local mode. The gallery card and modal gain the LOCAL badge branch. Sharing stays live (D10).

### Changes Required:

#### 1. Wizard surface
**File**: `dashboard/src/components/SaaShortsTab.jsx`
**Changes**: MODIFY - third mode card (sm:grid-cols-3), local $0 cost rows, mode-aware key pre-alerts/disables (8 videoMode !== 'local' guards incl. Hunk T2b voice-reset early return), voices refetch on mode switch, LOCAL empty-voice edge.


Third mode card (`sm:grid-cols-3`, hardware-need copy); cost panel local row; mode-aware pre-alerts, generate-button disable/label, actor-options disable + `video_mode` in its body; voices refetch on mode switch with empty-with-error rendering; the gender/language voice-reset effect is a no-op in local mode (D9).

```jsx
// ── Hunk T0 — module scope: cost preview tables (insert after STYLE_OPTIONS,
//    which ends at line 16; the cost card and generate button read these) ──
// Per-mode wizard cost preview. The server's cost_estimate stays the
// authoritative breakdown; local rows mirror the $0 cost branch (FR7).
const COST_PREVIEWS = {
  lowcost: [
    ['Flux image', '$0.05'],
    ['ElevenLabs voice', '$0.10'],
    ['Hailuo 2.3 img2video', '$0.19'],
    ['VEED Lipsync', '$0.20'],
    ['Flux b-roll', '$0.10'],
  ],
  premium: [
    ['Flux image', '$0.05'],
    ['ElevenLabs voice', '$0.10'],
    ['Kling avatar', '$1.69'],
    ['Kling b-roll', '$0.70'],
  ],
  local: [
    ['ComfyUI actor image', '$0.00'],
    ['Local TTS voice', '$0.00'],
    ['Wan 2.2 head + lipsync', '$0.00'],
    ['ComfyUI b-roll', '$0.00'],
    ['ffmpeg compositing', '$0.00'],
  ],
};

const COST_TOTALS = { lowcost: '0.65', premium: '2.50', local: '0.00' };

// ── Hunk T1 — line 57 comment; new state beside :75 ──
  const [videoMode, setVideoMode] = useState('lowcost'); // "lowcost", "premium" or "local"

  const [voices, setVoices] = useState([]);
  const [voicesError, setVoicesError] = useState(''); // local mode: TTS list failure copy (D9)

// ── Hunk T2 — voices effect (replaces :124-131): refetch on mode switch ──
  // Fetch voices on mount and on every mode switch. Local lists TTS-server
  // voices without any key (D9); cloud keeps the ElevenLabs list when a key
  // exists, the hardcoded defaults otherwise.
  useEffect(() => {
    if (videoMode === 'local' || elevenLabsKey) {
      fetchVoices();
    } else {
      // Leaving local mode with no ElevenLabs key: drop server voices so no
      // local voice id leaks into a cloud job.
      setVoices([]);
      setVoicesError('');
      setSelectedVoice('21m00Tcm4TlvDq8ikWAM');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elevenLabsKey, videoMode]);

// ── Hunk T2b — gender/language voice reset (:133-149): no-op in local mode.
//    slice-verifier D9 fix: ungated, this effect writes ElevenLabs ids that
//    generate_voiceover_local passes verbatim to the TTS server (only the
//    default id is resolved server-side) → unknown-voice failure. ──
  // Reset selected voice when actor gender changes
  useEffect(() => {
    if (videoMode === 'local') return; // local selection: fetchVoices snap + picker clicks (D9)
    const genderDefaults = {
      'en-female': '21m00Tcm4TlvDq8ikWAM',  // Rachel
      'en-male': '29vD33N1CtxCmqQRPOHJ',    // Drew
      'es-female': 'EXAVITQu4vr4xnSDxMaL',  // Bella
      'es-male': 'ErXwobaYiN019PkySvjV',     // Antoni
    };
    // (rest of the effect unchanged)
  }, [actorGender, language]);

// ── Hunk T3 — fetchVoices (replaces :188-200; corrected from :188-202 by review - handleAnalyze at :202 stays): mode-aware request + selection ──
  const fetchVoices = async () => {
    const local = videoMode === 'local';
    try {
      const res = await fetch(getApiUrl(`/api/saasshorts/voices?video_mode=${videoMode}`), {
        headers: local ? {} : { 'X-ElevenLabs-Key': elevenLabsKey },
      });
      if (res.ok) {
        const data = await res.json();
        setVoices(data.voices || []);
        setVoicesError(local ? (data.error || '') : '');
        if (local) {
          // Keep the selection only when the server list actually has it;
          // otherwise take the first entry (empty id = server default, D9).
          const ids = (data.voices || []).map((v) => v.voice_id);
          setSelectedVoice((prev) => (ids.includes(prev) || !ids.length ? prev : ids[0]));
        }
      } else if (local) {
        setVoices([]);
        setVoicesError('Voice list unavailable. Generation can still run with the server default voice.');
      }
    } catch (e) {
      console.error('Voices fetch error:', e);
      if (local) {
        setVoices([]);
        setVoicesError('Voice list unavailable. Generation can still run with the server default voice.');
      }
    }
  };

// ── Hunk T4 — mode cards: grid at :430 (cards end :457), third card after premium ──
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">

                  <button
                    onClick={() => setVideoMode('local')}
                    className={`card card-hover p-4 text-left ${
                      videoMode === 'local' ? 'border-brass' : ''
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1.5 gap-2">
                      <span className={`text-sm font-medium lowercase ${videoMode === 'local' ? 'text-ink' : 'text-ink2'}`}>Local</span>
                      <span className="badge-ok">$0 · your GPU</span>
                    </div>
                    <p className="readout mb-1.5">FREE / YOUR HARDWARE</p>
                    <p className="text-xs text-muted leading-relaxed">ComfyUI + local TTS server on your machine. Needs a ~10 GB VRAM GPU. No per-video cost, no time limit.</p>
                  </button>

// ── Hunk T5 — voice picker: local branch at IIFE start (:815-816) + caption (:901-903) ──
                {(() => {
                  // Local mode: read-only TTS-server catalog. No gender/accent
                  // filtering, no preview, no ElevenLabs fallback (design D9).
                  if (videoMode === 'local') {
                    if (voicesError) {
                      return (
                        <div className="p-3 bg-warn/10 rounded-input flex items-start gap-2 text-sm text-warn">
                          <AlertCircle size={14} className="shrink-0 mt-0.5" />
                          <span>{voicesError}</span>
                        </div>
                      );
                    }
                    if (voices.length === 0) {
                      return (
                        <p className="text-xs lowercase text-muted">
                          No voices returned by the TTS server — its default voice will be used.
                        </p>
                      );
                    }
                    return (
                      <div className="space-y-1.5 max-h-48 overflow-y-auto custom-scrollbar">
                        {voices.map((v) => (
                          <button
                            key={v.voice_id}
                            onClick={() => setSelectedVoice(v.voice_id)}
                            className={`w-full flex items-center gap-3 p-2.5 rounded-input border text-left transition-colors duration-200 ${
                              selectedVoice === v.voice_id
                                ? 'border-brass bg-paper3'
                                : 'border-rule bg-paper hover:bg-paper3'
                            }`}
                          >
                            <div className="flex-1 min-w-0">
                              <div className={`text-sm truncate ${selectedVoice === v.voice_id ? 'text-ink' : 'text-ink2'}`}>{v.name}</div>
                              <div className="readout mt-0.5">local server voice</div>
                            </div>
                            {selectedVoice === v.voice_id && <Check size={14} className="text-brass shrink-0" />}
                          </button>
                        ))}
                      </div>
                    );
                  }
                  // Filter voices by language/accent
                  const filtered = voices.length > 0

                <p className="text-xs lowercase text-muted mt-1.5">
                  {videoMode === 'local'
                    ? 'voices from your local TTS server · read-only list'
                    : `${actorGender === 'female' ? 'female' : 'male'} voices · multilingual model speaks your selected language · click speaker to preview`}
                </p>

// ── Hunk T6 — actor options: guard (:1019), headers+body (:1026-1027),
//    disabled (:1042), label (:1045) ──
                    if ((videoMode !== 'local' && !falKey) || !actorDescription) return;

                        headers: {
                          'Content-Type': 'application/json',
                          ...(videoMode !== 'local' && { 'X-Fal-Key': falKey }),
                        },
                        body: JSON.stringify({ actor_description: actorDescription, num_options: 3, video_mode: videoMode }),

                  disabled={generatingActors || (videoMode !== 'local' && !falKey) || !actorDescription}

                  {generatingActors ? <><Loader2 size={14} className="animate-spin" /> Generating 3 actors...</> : <><User size={14} /> {actorOptions.length > 0 ? 'Regenerate actors' : 'Generate 3 new actors'} <span className="readout">{videoMode === 'local' ? 'free · ComfyUI' : '~$0.06'}</span></>}

// ── Hunk T7 — cost card (replaces :1096-1123) ──
              {/* Cost Estimate */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="eyebrow">Estimated Cost</span>
                  <span className="readout text-ink">
                    {videoMode === 'local' ? 'FREE' : `~$${COST_TOTALS[videoMode] || '0.65'}`}
                  </span>
                </div>
                <div className="space-y-1">
                  {(COST_PREVIEWS[videoMode] || COST_PREVIEWS.lowcost).map(([item, cost]) => (
                    <div key={item} className="flex items-center justify-between readout">
                      <span>{item}</span>
                      <span>{cost}</span>
                    </div>
                  ))}
                </div>
              </div>

// ── Hunk T8 — keys warning (:1125-1126): hidden in local mode ──
              {/* Missing keys warning (local mode runs without cloud keys) */}
              {videoMode !== 'local' && (!falKey || !elevenLabsKey) && (

// ── Hunk T9 — generate button: disabled (:1156) + label (:1159-1165) ──
                disabled={(videoMode !== 'local' && (!falKey || !elevenLabsKey)) || !selectedActor || generating}

                {generating ? (
                  <><Loader2 size={14} className="animate-spin" /> Generating...</>
                ) : !selectedActor ? (
                  <><User size={14} /> Select an actor first</>
                ) : videoMode === 'local' ? (
                  <><Film size={14} /> Generate video (free · your GPU)</>
                ) : (
                  <><Film size={14} /> Generate video (~${videoMode === 'lowcost' ? '0.65' : '2.00'})</>
                )}

// ── Hunk T10 — handleGenerate: key alerts (:269-276) + headers (:295-299) ──
  const handleGenerate = async () => {
    if (videoMode !== 'local') {
      if (!falKey) {
        alert('fal.ai API key required. Set it in Settings.');
        return;
      }
      if (!elevenLabsKey) {
        alert('ElevenLabs API key required. Set it in Settings.');
        return;
      }
    }

        headers: {
          'Content-Type': 'application/json',
          ...(videoMode !== 'local' && { 'X-Fal-Key': falKey, 'X-ElevenLabs-Key': elevenLabsKey }),
        },

// ── Hunk T11 — handleRetry headers (:342-346; body keeps retry_job_id at :351) ──
        headers: {
          'Content-Type': 'application/json',
          ...(videoMode !== 'local' && { 'X-Fal-Key': falKey, 'X-ElevenLabs-Key': elevenLabsKey }),
        },
```

#### 2. Gallery badge
**File**: `dashboard/src/components/UGCGallery.jsx`
**Changes**: MODIFY - LOCAL badge third branch at :112 and :204-205 (2 occurrences).


Badge ternary gains the `local` branch (`LOCAL`, sky `#0ea5e9` mirroring the SEO badge in app.py hunk A8a); the modal eyebrow handles the third value.

```jsx
// ── Hunk G1 — modal eyebrow (:112) ──
          eyebrow={selected.video_mode === 'local' ? 'LOCAL' : selected.video_mode === 'lowcost' ? 'LOW COST' : 'PREMIUM'}

// ── Hunk G2 — card badge (:204-205); sky #0ea5e9 mirrors the server-side SEO
//    badge (app.py hunk A8a), so the card and the /gallery HTML agree ──
          <span
            className={`${mode === 'lowcost' ? 'badge-ok' : 'badge-brass'} bg-black/70`}
            style={mode === 'local' ? { background: '#0ea5e9', color: '#fff' } : undefined}
          >
            {mode === 'local' ? 'LOCAL' : mode === 'lowcost' ? 'LOW COST' : 'PREMIUM'}
          </span>
```

### Success Criteria:

#### Automated Verification:
- [x] Lint passes: `cd dashboard && npm run lint`
- [x] Build passes: `cd dashboard && npm run build`
- [x] Third mode card: `grep -c "sm:grid-cols-3" dashboard/src/components/SaaShortsTab.jsx` returns 1
- [x] Local cost rows: `grep -c "Wan 2.2 head + lipsync" dashboard/src/components/SaaShortsTab.jsx` returns 1
- [x] Mode-aware key gates: `grep -c "videoMode !== 'local'" dashboard/src/components/SaaShortsTab.jsx` returns 8
- [x] Voices refetch on mode switch: `grep -c "elevenLabsKey, videoMode" dashboard/src/components/SaaShortsTab.jsx` returns 1
- [x] Gallery LOCAL surface: `grep -c "'LOCAL'" dashboard/src/components/UGCGallery.jsx` returns 2
- [x] Cloud key headers only behind the mode guard: `grep -n "X-Fal-Key" dashboard/src/components/SaaShortsTab.jsx` shows every occurrence inside a `videoMode !== 'local'` guard or spread

#### Manual Verification:
- [ ] With Slices 1-2 implemented and COMFYUI_URL/TTS_BASE_URL set: picking Local shows no key warnings anywhere, the cost card reads FREE with $0.00 rows, and Generate is enabled with no keys configured
- [ ] Configure lists the TTS server's voices; with the TTS server stopped, reload or toggle video modes → the warn line renders and generation still works (server default voice)
- [ ] A local job shared to the gallery shows LOCAL on the gallery card and in the modal; the /gallery SEO HTML carries the LOCAL badge (Slice 2 surface)
- [ ] premium/lowcost unchanged: key warnings, cost rows, badges, and buttons behave exactly as on main

---
## Phase 4: GPU-host calibration + docs

### Overview
Calibration and documentation on the physical GPU host (RTX 3080): the LatentSync wrapper is installed pinned to 1.5 and the lipsync template is finalized from the live export; the i2v fallback knobs (length 121 to 81, then 480x832) are exercised only if calibration demands; the README section and the .env.example block land. Offline end-to-end with no cloud keys is the gate.

### Changes Required:

#### 1. Lipsync template finalization
**File**: `workflows/latentsync_lipsync_api.json`
**Changes**: MODIFY (GPU host) - replace the Phase 1 skeleton's placeholder node ids with the live export (wrapper pinned 1.5 via git checkout 920c15ea); contract: load_video/load_audio inputs stay strings and exactly one save node. Validator command in the phase criteria; skeleton content in Phase 1 group 7.

```text
Finalized on the GPU host from ComfyUI 'Save (API Format)'. Ship-time contract is asserted by the validator in Automated Verification below; the Phase 1 skeleton documents the required node shape.
```

#### 2. i2v fallback knobs
**File**: `workflows/wan22_i2v_api.json`
**Changes**: MODIFY (only if calibration demands) - length 121 to 81, then 480x832, per the README calibration paragraph; template content in Phase 1 group 4.

```text
No code change unless the RTX 3080 calibration in Manual Verification demands it; the knobs and their order are documented in the README section below.
```

#### 3. README local-mode docs
**File**: `README.md`
**Changes**: MODIFY - H1 requirements line at :269, feature bullet at :53, new section 7 'AI Shorts fully local' inserted before '## Technical Pipeline' (:414); grep acceptance: 3 occurrences of the section name.


Local mode section: prerequisites (ComfyUI portable with Wan2.2 GGUF + Flux GGUF + LatentSync wrapper, omnivoice-server), env vars, GPU-host calibration notes.

**H1** — Requirements, fal.ai line (README.md:269):

```markdown
- **fal.ai API Key** ([Pay-per-use](https://fal.ai)) — required for the AI Shorts cloud modes; the fully local mode needs none (see [AI Shorts fully local](#7-ai-shorts-fully-local-video_mode-local-optional))
```

**H2** — AI Shorts feature bullet (README.md:53):

```markdown
- **Three cost modes**: Low Cost (~$0.65/video), Premium (~$2/video), or fully local on your own GPU at $0 — see [AI Shorts fully local](#7-ai-shorts-fully-local-video_mode-local-optional)
```

**H3** — new section, inserted between the end of section 6 (paragraph ending "Add a Gemini key alongside and you get both.", README.md:412) and "## Technical Pipeline" (README.md:414):

`````markdown
### 7. AI Shorts fully local (video_mode `local`, optional)

The third AI Shorts mode runs every paid media stage on your own GPU: actor portraits (Flux GGUF), the talking head (Wan2.2 image-to-video + LatentSync lip-sync), b-roll (Flux schnell), and the voiceover (any OpenAI-compatible TTS server). Generation makes zero cloud calls, costs $0 per video, and has no time limit. Sharing a finished video to the public gallery stays an explicit choice, same as the cloud modes. The analyze and script text stages are unchanged — they follow the normal text configuration: a Gemini key or the `LLM_*` endpoint (a local Ollama works, see section 6).

Hardware: an NVIDIA GPU with ~10 GB VRAM (calibrated on an RTX 3080) and 32 GB RAM. Start ComfyUI and the TTS server before you start a job.

**ComfyUI** (the Windows portable works — `F:/AI/ComfyUI_windows_portable`):

- Custom nodes: [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) and [ComfyUI-LatentSyncWrapper](https://github.com/ShmuelRonen/ComfyUI-LatentSyncWrapper) pinned to 1.5 (`git checkout 920c15ea`; the wrapper's `main` carries 1.6). With the portable's embedded Python: `..\..\..\python_embeded\python.exe -m pip install -r requirements.txt` inside the wrapper folder, then drop the LatentSync 1.5 checkpoint and Whisper `tiny.pt` where the wrapper README says.
- Models: `Wan2.2-TI2V-5B-Q8_0.gguf`, `wan2.2_vae.safetensors`, `umt5_xxl_fp8_e4m3fn_scaled.safetensors`, the `wan2.2_ti2v_5B_fastwan` LoRA; `flux1-dev-Q4_K_S.gguf` and `flux1-schnell-Q4_K_S.gguf` with `t5-v1_1-xxl-encoder-Q4_K_S.gguf`, `clip_l.safetensors`, and `ae.safetensors`.
- The repo ships API-format workflows under `workflows/`. To run a different model on one stage, export your own workflow from the ComfyUI UI ("Save (API Format)") and point the stage's `COMFYUI_WORKFLOW_*` variable at it. Keep the `load_video` / `load_audio` node ids and their string upload refs in the lipsync template; the validator below checks that.

**TTS server:** any OpenAI-compatible server exposing `POST /v1/audio/speech` and `GET /v1/voices` (for example omnivoice-server). `TTS_BASE_URL` has no default: a local job fails fast and names the missing variable.

**Environment (`.env`):**
```bash
COMFYUI_URL=http://127.0.0.1:8188
TTS_BASE_URL=http://127.0.0.1:8000
```

**How a local job runs:** the actor image and the voiceover run one after the other, never in parallel — together they exceed 10 GB VRAM. The talking head writes three retryable intermediates (`{slug}_head_wan_cache.mp4`, a looped copy, `{slug}_head_lipsync_cache.mp4`); only the final muxed `{slug}_head.mp4` counts as done, so an interrupted head never poisons the retry cache. ComfyUI polling has no deadline, but an error or cancelled status, or a dropped connection, fails the job immediately and names the service and URL.

**Calibration:** every local stage logs elapsed seconds (`[local] ...` lines in the job log). If Wan2.2 runs out of VRAM at 121 frames, set the template's `length` to 81 — the loop stage re-extends the clip to the narration length. Still tight, drop to 480x832. Otherwise the shipped template is calibrated as-is: 704x1280, 121 frames, 8 steps, fastwan LoRA, shift 8.0.

**Template validator** (run it after swapping any workflow template):

```bash
python -c "import json; g=json.load(open('workflows/latentsync_lipsync_api.json',encoding='utf-8')); assert isinstance(g['load_video']['inputs']['video'],str); assert isinstance(g['load_audio']['inputs']['audio'],str); outs=[k for k,v in g.items() if isinstance(v,dict) and ('save' in str(v.get('class_type','')).lower() or 'videocombine' in str(v.get('class_type','')).lower())]; assert len(outs)==1, outs; print('lipsync template OK:', outs[0])"
`````


#### 4. Environment example
**File**: `.env.example`
**Changes**: MODIFY - local env block inserted before '# --- Proxy / ops ---' (:119): COMFYUI_URL, TTS_BASE_URL, 4 COMFYUI_WORKFLOW_* overrides with comments.


New block: `COMFYUI_URL`, `TTS_BASE_URL`, `COMFYUI_WORKFLOW_PORTRAIT/BROLL/I2V/LIPSYNC` with comments.

Insert before the `# --- Proxy / ops ---` block (.env.example:119):

```text
# --- AI Shorts local video_mode (optional; self-hosted ComfyUI + TTS) --------
# video_mode "local" in the AI Shorts wizard runs the five paid media stages on
# your own GPU (ComfyUI + an OpenAI-compatible TTS server): $0 per video, zero
# cloud calls during generation, no time limit. Needs ~10 GB VRAM (RTX 3080
# calibrated). Text stages (analyze/script) follow LLM_* above or a Gemini key.
# See README "AI Shorts fully local".
# COMFYUI_URL=http://127.0.0.1:8188
# TTS_BASE_URL=http://127.0.0.1:8000  # no default; local mode fails fast naming it
# Per-stage workflow swaps (repo defaults live in workflows/*.json). Point a
# variable at your own API-format export ("Save (API Format)") to change the
# model for that stage.
# COMFYUI_WORKFLOW_PORTRAIT=
# COMFYUI_WORKFLOW_BROLL=
# COMFYUI_WORKFLOW_I2V=
# COMFYUI_WORKFLOW_LIPSYNC=
```

### Success Criteria:

#### Automated Verification:
- [x] Feature suite baseline: `python -m pytest tests/test_saasshorts_local.py -q` (includes TestTemplates against the real exported template)
- [x] Dashboard baseline: `cd dashboard && npm run lint`
- [x] Lipsync template contract (after the host export): `python -c "import json; g=json.load(open('workflows/latentsync_lipsync_api.json',encoding='utf-8')); assert isinstance(g['load_video']['inputs']['video'],str); assert isinstance(g['load_audio']['inputs']['audio'],str); outs=[k for k,v in g.items() if isinstance(v,dict) and ('save' in str(v.get('class_type','')).lower() or 'videocombine' in str(v.get('class_type','')).lower())]; assert len(outs)==1, outs"` exits 0
- [x] i2v template parses with patch anchors: `python -c "import json; g=json.load(open('workflows/wan22_i2v_api.json',encoding='utf-8')); assert {'5','9','13'} <= set(g) and g['8']['class_type']=='Wan22ImageToVideoLatent'"` exits 0
- [x] Env block present: `grep -c "COMFYUI_WORKFLOW_LIPSYNC" .env.example` returns 1 and `grep -c "TTS_BASE_URL" .env.example` returns 1
- [x] README section present: `grep -c "AI Shorts fully local" README.md` returns 3 (H1 + H2 links, H3 heading)

#### Manual Verification (GPU host, RTX 3080):
- [ ] Wrapper installed pinned 1.5; the LatentSync node is visible in ComfyUI and the checkpoint loads
- [ ] Offline end-to-end with NO cloud keys in env: one local job completes actor → voice → head (wan → loop → lipsync → mux) → b-roll → composite with narration audible
- [ ] Wan i2v per-clip seconds logged from the [local] lines and recorded in the PR description
- [ ] LatentSync 1.5 vs 1.6 A/B on the 10 GB card; the pin stays 1.5 unless 1.6 is faster AND artifact-clean
- [ ] Steps 1-2 observed serialized (no OOM across actor → voice → head); b-roll fanout rides the ComfyUI queue
- [ ] TTS voice list renders in the wizard; a non-default voice is used by the job
- [ ] Fallback knobs exercised only if calibration demands: length 121→81, then 480x832

---
## Testing Strategy

### Automated:
- Phase 1: python -m pytest tests/test_saasshorts_local.py -q; template JSON parse; import cycle guard; FR6 error-copy and no-poll-deadline greps; ffmpeg-gated mux and head-atomicity tests (see Phase 1 criteria)
- Phase 2: the same suite plus TestDispatchMatrix / TestRouteModeMatrix / TestVoicesLocalBranch / TestSeoLocalSurface and the gate-presence greps (see Phase 2 criteria)
- Phase 3: cd dashboard && npm run lint; cd dashboard && npm run build; the JSX grep counts (see Phase 3 criteria)
- Phase 4: suite baseline, dashboard lint, the two template contract validators, and the doc greps (see Phase 4 criteria)

### Manual Testing Steps:
(from the design's Verification Notes; reference only - each phase's criteria above carry the load-bearing checks)

- Client units: submit/poll/fetch/upload against `httpx.MockTransport` (idiom `tests/test_llm_client.py:55-63`), incl. FAILED/CANCELLED, missing-history error, connection error naming service+URL, and poll-continues-while-running (D11).
- Mux contract: synthetic silent mp4 + sine mp3 → ffprobe asserts an audio stream on the muxed output; test skips when ffmpeg is absent.
- Dispatch matrix: monkeypatched adapters; `video_mode="local"` → adapters called AND **zero** cloud helpers called (silent-spend guard, advisor finding 4a); steps 1-2 sequential in local mode (4b).
- Route mode matrix: local + no keys → 2xx; premium + no keys → 400; invalid mode → 422 (never silent premium); actor-options gate matrix same shape (advisor: assert 2xx, not 202 — the route returns FastAPI's 200).
- Voices branch: local mode returns TTS voices; TTS-down returns `{voices: [], source: "local", error}` without touching DEFAULT_VOICES.
- Head atomicity: failed lipsync leaves `{slug}_head.mp4` absent and `_exists` guard honest (D6).
- Cache-guard reuse: existing `{slug}_*` artifacts at fixed paths are consumed, not regenerated, on retry.
- Slice 4 (GPU host): offline end-to-end with no cloud keys in env; Wan i2v per-clip timing logged (no first-party 3080 timing exists); LatentSync 1.5 vs 1.6 A/B on 10 GB; VRAM sequencing between prompts; TTS voice list renders.
- Precedent lessons applied: gate sweep inside Slice 2 (not spread), mode-matrix tests before/with the third mode (`86117d3` follow-ups `d128fca`, `9c9342c`; `25222f5` gate regression).


---
## Performance Considerations


- Steps 1-2 sequential in local mode (D7) — the only pipeline-shape divergence; justified by 10 GB VRAM (advisor-reviewed as adequate).
- ComfyUI queue serializes GPU prompts; HTTP fanout pools unchanged.
- Local jobs hold a `concurrency_semaphore` slot for minutes (Wan ~4-10 min per 5 s clip with LoRA; RTF 0.15-0.3 TTS) — same slot model as cloud today; `MAX_CONCURRENT_JOBS` env remains the knob.
- Poll interval 5 s; log lines carry elapsed seconds so batch watchers see progress.
- No added wall-time limit anywhere in the local path (FR8).


## Migration Notes


- No persisted schema changes (saas_jobs in-memory; gallery metadata stores `video_mode` verbatim already).
- Backwards compatible: `premium`/`lowcost` behavior byte-identical; old wizard clients sending only two modes keep working (Literal includes them).
- Rollback = the Literal keeps accepting two modes; local adapters are dead code if unreferenced.


## Developer Context


## References

- Design: .rpiv/artifacts/designs/2026-09-15_06-45-44_ai-shorts-local-models.md
- Solution analysis: .rpiv/artifacts/solutions/2026-09-15_06-23-22_ai-shorts-local-models.md
- Research: .rpiv/artifacts/research/2026-09-14_21-41-44_ai-shorts-local-models.md
- FRD: .rpiv/artifacts/discover/2026-09-14_21-17-54_ai-shorts-local-models.md

## Plan Review (Step 4)

Post-finalization review at commit 73f670a. Sources: artifact-code-reviewer (c5a97f7e, 42 tool uses) and artifact-coverage-reviewer (6c86b2ad, 18 tool uses). Severity-sorted. Step 5 triage (developer, 2026-09-15): apply all.

| id | source | plan-loc | codebase-loc | severity | dimension | finding | recommendation | resolution |
|---|---|---|---|---|---|---|---|---|
| B1 | coverage-review | Phase 1 comfortable client fence + Phase 1 criteria | comfyui_client.wait_for_output | blocker | verification-coverage | Missing-history fail-fast absent: the fence treats an absent /history/{id} entry as running and re-polls forever, contradicting D11 (raise immediately on FAILED/CANCELLED, missing history entry, connection error); with the FR8 no-deadline poll a vanished history entry (ComfyUI restart) pins the job and its semaphore slot forever; no AV bullet covers it | Add a bounded-grace missing-history guard raising ComfyUIError naming service+URL, plus a Phase 1 AV bullet for a missing-history unit test | applied - queue-cross-check guard in wait_for_output + test_missing_history_entry_raises + Phase 1 AV bullet |
| B2 | code-review | Phase 2, Hunk A2 (app.py:6537) | app.py:6521-6530 vs 6536-6542 | blocker | actionability | A2 anchor wrong by ~7 lines: the from-saasshorts import block ends at 6530; 6537 is inside class SaaSAnalyzeRequest (url field). Inserting there puts import saasshorts_local in a class body - NameError at request time or failed anchor match. All other anchors verified exact | Re-anchor A2 to insert after app.py:6530 (closing paren of the import block, before the # State comment at 6532) | applied - Hunk A2 re-anchored to 6530/6532 |
| C1 | coverage-review | Precedents and Lessons lesson 5 | comfyui_url() / app startup | concern | verification-coverage | Startup probe for COMFYUI_URL/TTS_BASE_URL (llm_client half-config spirit) in neither criteria nor code; unset/typo COMFYUI_URL silently defaults to 127.0.0.1:8188 and surfaces only mid-job; call-time fail-fast covers only TTS-unset and connection-down | Add Phase 2 AV bullet + probe call at startup, or a Phase 1 unit asserting the probe fires on unreachable URLs | applied - _local_arm_startup_warnings in Hunk A2 + Phase 2 AV bullet + TestLocalArmStartupProbe |
| C2 | code-review | Plan lines 2121-2141 (Phase 4 tail) | n/a (artifact-internal) | concern | code-quality | Duplicated Phase 4 criteria: mangled fragment '#### Manual Verification:' + 'libration + docs', an orphan **Files** line, then an exact duplicate of the Phase 4 AV and MV blocks | Delete the duplicated block and fragment; keep one Phase 4 criteria section | applied pre-triage - duplicate block and 'libration + docs' fragment deleted (assembly defect from a masked prewalk continuation; design Slice 4 verified clean); structure re-verified |
| C3 | code-review | Phase 3, Hunk T3 (replaces SaaShortsTab.jsx:188-202) | SaaShortsTab.jsx:188-202 | concern | actionability | Replace range over-extends by 2 lines: fetchVoices ends at }; on line 200; an inclusive 188-202 replace deletes the handleAnalyze signature line at 202 and orphans its body (build break at the lint gate) | Correct the range to replaces :188-200 | applied - T3 corrected to :188-200 |
| S1 | code-review | Phase 2, TestSeoLocalSurface.test_gallery_and_detail_render_local | app.py:7829 | suggestion | code-quality | 'PREMIUM' not in / 'LOW COST' not in assertions pass vacuously: the static gallery subtitle carries 'Low Cost & Premium modes' in title case, so a local-mode gallery still advertises cloud modes | Make assertions case-insensitive and update the subtitle, or narrow the criterion to the badge markup (>LOCAL<) only | applied - gallery negatives narrowed to badge markup (>PREMIUM<, >LOW COST<); app.py:7829 untouched |

Tally: 2 blockers, 3 concerns, 1 suggestion (6 findings from 2 reviewers).

