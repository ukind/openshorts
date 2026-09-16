import os
from typing import Callable, List, Optional

import httpx

_TIMEOUT = 1800.0  # per-request; a long narration is one POST (CPU TTS ~12x real-time)


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
