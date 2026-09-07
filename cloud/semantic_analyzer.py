"""
Multimodal semantic analyzer for OpenShorts.

This module provides frame extraction and multimodal semantic analysis
capabilities that integrate with the existing AI provider abstraction.
"""

import base64
import cv2
import numpy as np
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from typing_extensions import Literal

# Import AI provider for multimodal support
from ai_provider import AIProvider, create_ai_provider


# Semantic signal models
class SemanticSignal(BaseModel):
    """Represents a detected semantic signal from transcript or vision analysis."""
    category: Literal[
        "emotional_reaction",
        "surprise",
        "tension",
        "social_interaction",
        "achievement",
        "humor",
        "outrage",
        "novelty"
    ]
    score: float = Field(..., ge=0.0, le=1.0)  # Normalized signal strength (0.0-1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)  # Confidence level of detection (0.0-1.0)


class SemanticSignalsResponse(BaseModel):
    """Response containing semantic signals detected from a window."""
    signals: List[SemanticSignal]


def extract_frames_from_window(
    video_path: str,
    window_start: float,
    window_end: float,
    num_frames: int = 3,
    target_width: int = 480,
    jpeg_quality: int = 80,
    peak_times: Optional[List[float]] = None
) -> List[Dict[str, Any]]:
    """
    Extract exactly num_frames from a video window.

    Default 6 peak-biased: 3 uniform + 3 around cheap peaks (scream/
    loudness/scene) to catch scared faces that last <1s. Falls back to
    uniform when no peaks. peak_times are absolute seconds, filtered to
    window.

    Args:
        video_path: Path to the source video file
        window_start: Start time of the window (seconds)
        window_end: End time of the window (seconds)
        num_frames: Number of frames to extract (default 3, Phase 6 uses 6)
        target_width: Target width for resized frames (default 480)
        jpeg_quality: JPEG compression quality (default 80)
        peak_times: Optional absolute timestamps of cheap events inside/near window

    Returns:
        List of frame dictionaries with frame_index and base64_image
    """
    try:
        # Open video capture
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        # Get video properties
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if total_frames <= 0:
            cap.release()
            return []

        # Calculate frame positions within the window
        window_duration = window_end - window_start

        # Handle very short windows safely
        if window_duration <= 0.1:
            # Use a single frame at the start of the window
            frame_positions = [window_start]
        elif peak_times:
            # Peak-biased: 3 uniform + 3 peak (or split for any num_frames)
            # Filter peaks to window with small margin, dedupe close peaks <0.7s
            window_peaks = [pt for pt in peak_times if window_start - 0.5 <= pt <= window_end + 0.5]
            # Dedupe: keep peaks at least 0.7s apart, priority to earlier
            deduped = []
            for pt in sorted(window_peaks):
                if not deduped or all(abs(pt - x) > 0.7 for x in deduped):
                    deduped.append(pt)
            peak_needed = num_frames // 2
            uniform_needed = num_frames - min(len(deduped), peak_needed)
            # Uniform positions
            frame_positions = []
            if uniform_needed == 1:
                frame_positions.append(window_start + window_duration / 2.0)
            else:
                for i in range(uniform_needed):
                    pos = window_start + (window_duration * (i + 0.5)) / uniform_needed if uniform_needed > 0 else window_start
                    # Alternative even spacing avoiding edges: use (i+0.5)/uniform_needed
                    frame_positions.append(pos)
            # Peak positions (clamped to window)
            for pt in deduped[:peak_needed]:
                clamped = max(window_start + 0.2, min(window_end - 0.2, pt))
                frame_positions.append(clamped)
            # If still short (few peaks), fill with extra uniform
            while len(frame_positions) < num_frames:
                # Add midpoints between existing
                frame_positions.append(window_start + window_duration * (len(frame_positions) + 0.5) / num_frames)
                frame_positions = sorted(frame_positions)[:num_frames]
            frame_positions = sorted(frame_positions)
            # Ensure exactly num_frames
            if len(frame_positions) > num_frames:
                frame_positions = frame_positions[:num_frames]
        else:
            # Distribute frames evenly across the window
            frame_positions = []
            for i in range(num_frames):
                # Calculate position within window (avoid exact boundaries)
                if num_frames == 1:
                    pos = window_start + window_duration / 2.0
                else:
                    pos = window_start + (window_duration * i) / (num_frames - 1) if num_frames > 1 else window_start
                frame_positions.append(pos)

        frames = []
        for i, pos in enumerate(frame_positions):
            # Convert position to frame number
            frame_num = int(pos * fps)

            # Ensure frame number is within valid range
            frame_num = max(0, min(frame_num, total_frames - 1))

            # Set capture position and read frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()

            if not ret or frame is None:
                continue

            # Resize frame while preserving aspect ratio
            h, w = frame.shape[:2]
            new_width = target_width
            new_height = int(h * (new_width / w))
            resized = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)

            # Encode as JPEG with specified quality
            success, buffer = cv2.imencode('.jpg', resized, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            if not success:
                continue

            # Convert to base64 data URL
            image_data = base64.b64encode(buffer).decode('utf-8')
            data_url = f"data:image/jpeg;base64,{image_data}"

            frames.append({
                "frame_index": i,
                "base64_image": data_url
            })

        cap.release()
        return frames

    except Exception:
        # Return empty list on any error to allow fallback behavior
        return []


def analyze_semantic_window(
    transcript: str,
    frames: List[Dict[str, Any]],
    game_profile_context: Dict[str, Any],
    provider: AIProvider
) -> SemanticSignalsResponse:
    """
    Analyze a video window using multimodal semantic analysis.

    Args:
        transcript: Transcript text for the window
        frames: List of frame dictionaries with base64_image data URLs
        game_profile_context: Game profile context (game_type, gameplay_characteristics, key_moments)
        provider: AIProvider instance to use for analysis

    Returns:
        SemanticSignalsResponse containing detected signals
    """
    # Build the multimodal prompt
    prompt = _build_multimodal_prompt(transcript, frames, game_profile_context)

    # Create content array for multimodal request (OpenAI-compatible format)
    content = []

    # Add text content
    content.append({
        "type": "text",
        "text": prompt
    })

    # Add image content
    for frame in frames:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": frame["base64_image"]
            }
        })

    # For the OpenAI-compatible provider, we need to pass the structured schema
    try:
        # Use the provider's generate_content method with structured output
        # Wrap in proper chat message with role for OpenAI compatibility
        result = provider.generate_content(
            prompt,
            schema=SemanticSignalsResponse,
            messages=[{"role": "user", "content": content}]
        )

        # Expose the call's cost so jobs can report the true total (the vision
        # pass is one multimodal call per window and used to be cost-invisible).
        cost = result.get("cost_analysis") if isinstance(result, dict) else None
        if cost:
            _record_semantic_cost(cost)

        response_data = result.get("response", {})
        if "signals" in response_data:
            return SemanticSignalsResponse(**response_data)
        else:
            # If we can't parse the response properly, return empty signals
            return SemanticSignalsResponse(signals=[])

    except Exception as exc:
        # Keep the pipeline resilient, but do not silently turn provider/protocol
        # failures into a legitimate "no signals" result. The old behavior made
        # Gemini/OpenAI multimodal contract bugs almost impossible to diagnose.
        print(f"⚠️ Semantic multimodal analysis failed ({type(exc).__name__}): {exc}")
        return SemanticSignalsResponse(signals=[])


def _build_multimodal_prompt(
    transcript: str,
    frames: List[Dict[str, Any]],
    game_profile_context: Dict[str, Any]
) -> str:
    """
    Build the multimodal analysis prompt for semantic signals.

    Args:
        transcript: Transcript text for the window
        frames: List of frame dictionaries with base64_image data URLs
        game_profile_context: Game profile context

    Returns:
        Formatted prompt string for AI analysis
    """
    # Extract game profile information
    game_type = game_profile_context.get("game_type", "unknown")
    gameplay_characteristics = game_profile_context.get("gameplay_characteristics", [])
    key_moments = game_profile_context.get("key_moments", [])

    # Format the context for the prompt
    context_str = f"""
Game Type: {game_type}
Gameplay Characteristics: {', '.join(gameplay_characteristics) if gameplay_characteristics else 'None'}
Key Moments: {', '.join(key_moments) if key_moments else 'None'}
"""

    # Build the main prompt
    prompt = f"""
You are analyzing a video window for semantic signals. Analyze the provided transcript and visual evidence together to detect specific emotional and gameplay-related signals.

INSTRUCTIONS:
1. Inspect both the transcript AND visual evidence together
2. Analyze ONLY the supplied window (no external context)
3. Use GameProfile context only as interpretation guidance, not as scoring weights
4. Identify ONLY these eight categories:
   - emotional_reaction: strong emotional response (shock, fear, joy, surprise)
   - surprise: unexpected events or revelations
   - tension: suspenseful or tense moments
   - social_interaction: communication between players or characters
   - achievement: successful actions or accomplishments
   - humor: comedy, funny fails, banter, absurdity
   - outrage: anger, controversy, rage moments
   - novelty: unique, bizarre, never-seen-before

5. For each detected signal:
   - Score: 0.0-1.0 (strength of the signal)
   - Confidence: 0.0-1.0 (confidence in detection)
   - Omit signals with score < 0.2

6. Do NOT apply GameProfile weights or calculate final clip scores
7. Do NOT select clips or invent events
8. Return ONLY valid JSON with the signals array

TRANSCRIPT:
{transcript}

GAME PROFILE CONTEXT:
{context_str}

RETURN STRUCTURED JSON ONLY:
{{"signals": [{{"category": "emotional_reaction", "score": 0.9, "confidence": 0.95}}, ...]}}
"""

    return prompt

# Accumulated cost of semantic (vision) calls for the current job. The caller
# resets it before the vision pass and drains it afterwards; the analyzer is
# called deep inside the pipeline, so a return-value change would ripple.
_semantic_cost_sink: list = []


def _record_semantic_cost(cost: dict) -> None:
    try:
        _semantic_cost_sink.append(cost)
    except Exception:
        pass


def drain_semantic_costs() -> list:
    """Return and clear the accumulated semantic-call costs."""
    costs = list(_semantic_cost_sink)
    _semantic_cost_sink.clear()
    return costs
