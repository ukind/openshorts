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
    "faceswap": "reactor_faceswap_api.json",
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
    # ComfyUI validates every top-level key of the prompt as a node: an
    # API-format export that carries a top-level _meta block (exporter
    # notes) makes the submission 400 with missing_node_type. Submit
    # nodes only, and say so when something was dropped.
    nodes = {key: node for key, node in workflow.items()
             if isinstance(node, dict) and isinstance(node.get("class_type"), str)}
    dropped = len(workflow) - len(nodes)
    if dropped:
        log(f"[local] Dropped {dropped} non-node key(s) from the workflow "
            "before submitting (exporter metadata).")
    try:
        with _client(base) as c:
            resp = c.post("/prompt", json={"prompt": nodes})
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
    transient = 0
    while True:
        try:
            with _client(base) as c:
                resp = c.get(f"/history/{prompt_id}")
            resp.raise_for_status()
            body = resp.json()
            transient = 0
        except Exception as e:
            # A long CUDA phase can leave the server briefly unreachable
            # (full listen backlog / socket churn). A refused or timed-out
            # poll is retried before it is allowed to kill the stage.
            transient += 1
            if transient <= 12:
                time.sleep(5)
                continue
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
            # Race guard: the prompt can finish between the history fetch
            # above and the queue fetch here (gone from the queue, the first
            # history read already stale). Re-read history once before
            # declaring it vanished.
            with _client(base) as c2:
                resp2 = c2.get(f"/history/{prompt_id}")
            resp2.raise_for_status()
            entry2 = resp2.json().get(prompt_id)
            if entry2:
                status = entry2.get("status") or {}
                status_str = str(status.get("status_str", "")).lower()
                if status_str in _TERMINAL_BAD:
                    msgs = json.dumps(status.get("messages") or [])[:400]
                    raise ComfyUIError(
                        f"ComfyUI prompt {status_str} - service: ComfyUI at {base}: {msgs}")
                log(f"[local] ComfyUI done in {time.time() - start:.0f}s")
                return entry2.get("outputs") or {}
            raise ComfyUIError(
                f"ComfyUI prompt {str(prompt_id)[:8]} vanished (no history entry, not queued) "
                f"- service: ComfyUI at {base}")
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
