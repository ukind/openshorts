"""Post-processing robustness for LLM clip responses.

Adapted from patterns proven in clippyme (per-clip validation so one malformed
clip never kills a batch; IoU dedup so near-identical clips don't both render)
— restructured for OpenShorts' pipeline. Wordless clips are deliberately NOT
dropped: silent scare moments are valid viral clips.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def parse_llm_json(raw: str) -> Optional[Dict[str, Any]]:
    """Tolerant JSON extraction from an LLM reply.

    Chain: direct parse -> strip markdown fences -> repair common defects
    (smart quotes, trailing commas, lone backslashes) -> regex-extract the
    outermost {...}. Returns None when nothing parses.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    txt = str(raw).strip()

    # 1. Direct
    try:
        return json.loads(txt)
    except Exception:
        pass

    # 2. Strip markdown fences
    if txt.startswith("```"):
        txt = txt.split("\n", 1)[-1] if "\n" in txt else txt[3:]
        if txt.rstrip().endswith("```"):
            txt = txt.rstrip()[:-3]
        txt = txt.strip()
        try:
            return json.loads(txt)
        except Exception:
            pass

    # 3. Deterministic repairs
    def _repair(t: str) -> str:
        t = t.replace("\u201c", '"').replace("\u201d", '"')
        t = t.replace("\u2018", "'").replace("\u2019", "'")
        t = re.sub(r",\s*([}\]])", r"\1", t)            # trailing commas
        t = re.sub(r"\\(?![\"\\/bfnrtu])", r"\\\\", t)  # lone backslashes
        return t

    try:
        return json.loads(_repair(txt))
    except Exception:
        pass

    # 4. Extract outermost {...} (model echoed prose around the JSON)
    start, end = txt.find("{"), txt.rfind("}")
    if start != -1 and end > start:
        candidate = _repair(txt[start:end + 1])
        try:
            return json.loads(candidate)
        except Exception:
            pass
    return None


def _coerce_clip(raw: Any, video_duration: float) -> Optional[Dict[str, Any]]:
    """Validate/repair one returned clip. Returns None only when unsalvageable."""
    if not isinstance(raw, dict):
        return None
    clip = dict(raw)
    try:
        start = float(clip.get("start", 0))
        end = float(clip.get("end", 0))
    except (TypeError, ValueError):
        return None
    if end < start:
        start, end = end, start  # swapped boundaries are repairable
    duration = end - start
    if duration <= 0.5 or duration > 600:
        return None
    if video_duration > 0:
        start = max(0.0, min(start, video_duration - 1.0))
        end = max(start + 0.5, min(end, video_duration))
    clip["start"], clip["end"] = start, end
    # A clip needs at least one human-facing string to be usable
    if not any(str(clip.get(k) or "").strip() for k in
               ("title", "video_title_for_youtube_short", "text", "reason")):
        return None
    return clip


def validate_clips(clips: List[Any], video_duration: float) -> List[Dict[str, Any]]:
    """Per-clip validation: salvage what's fixable, drop only the broken ones."""
    out = []
    for raw in clips or []:
        fixed = _coerce_clip(raw, video_duration)
        if fixed is not None:
            out.append(fixed)
    return out


def iou_dedup(clips: List[Dict[str, Any]], threshold: float = 0.7) -> List[Dict[str, Any]]:
    """Drop clips that heavily overlap a better-ranked clip (IoU >= threshold).

    Expects the list already sorted best-first; keeps the first occurrence of
    each temporal spot. Two clips from adjacent windows covering the same
    moment would otherwise both render.
    """
    def _span(c: Dict[str, Any]) -> tuple:
        return float(c.get("start", 0)), float(c.get("end", 0))

    def _iou(a: tuple, b: tuple) -> float:
        inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
        union = max(a[1], b[1]) - min(a[0], b[0])
        return inter / union if union > 0 else 0.0

    kept: List[Dict[str, Any]] = []
    kept_spans: List[tuple] = []
    for clip in clips:
        span = _span(clip)
        if any(_iou(span, s) >= threshold for s in kept_spans):
            continue
        kept.append(clip)
        kept_spans.append(span)
    return kept
