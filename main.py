import time
import cv2
import subprocess
import argparse
import re
import sys
import threading
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector
from ultralytics import YOLO
import torch
import os
import numpy as np
from tqdm import tqdm
import yt_dlp
import mediapipe as mp
# import whisper (replaced by faster_whisper inside function)
from google import genai
from google.genai import types as genai_types

import gemini_worker
import ai_provider
import hook_grounding
import layout_picker
import clip_quality
from clip_selection import (build_transcript_windows, clip_count_targets,
                            clip_duration_bounds, snap_clip_to_words,
                            trim_to_best)
from ffmpeg_utils import (video_encode_args, audio_encode_args, QUALITY,
                          QUALITY_FAST, METADATA_SCRUB)
from dotenv import load_dotenv
import json
from pydantic import BaseModel

# Import semantic analyzer for multimodal analysis
try:
    from cloud.semantic_analyzer import (
        analyze_semantic_window,
        extract_frames_from_window,
        SemanticSignalsResponse,
        drain_semantic_costs
    )
    HAS_SEMANTIC_ANALYZER = True
except ImportError:
    # Fallback for environments without semantic analyzer support
    HAS_SEMANTIC_ANALYZER = False
    def analyze_semantic_window(*args, **kwargs):
        return SemanticSignalsResponse(signals=[])
    def drain_semantic_costs():
        return []
    def extract_frames_from_window(*args, **kwargs):
        return []
    class SemanticSignalsResponse:
        def __init__(self, signals=None):
            self.signals = signals or []

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module='google.protobuf')

# Load environment variables
load_dotenv()

# --- Constants ---
ASPECT_RATIO = 9 / 16

# Deep-only clip duration ceiling. Normal clip-selection paths keep their existing limits.
DEEP_MAX_CLIP_SECONDS = 80

# Import GameProfile and semantic scoring modules
try:
    from cloud.semantic_scoring import apply_game_profile_scoring
    from cloud.game_profiles import get_game_profile
    HAS_GAME_PROFILE_SUPPORT = True
except ImportError:
    # Fallback for environments without cloud support
    HAS_GAME_PROFILE_SUPPORT = False
    def apply_game_profile_scoring(existing_score, active_weights, detected_signals):
        return existing_score
    def get_game_profile(profile_id, user_id):
        return None

# AI Provider Configuration — lazy (settings are resolved by app.py per job)
def _get_ai_provider():
    provider = (os.environ.get("AI_PROVIDER") or "gemini").lower().strip()
    return provider if provider in ("gemini", "openai") else "gemini"

def _get_gemini_model():
    return os.environ.get("GEMINI_MODEL") or "gemini-3.1-flash-lite"

def _get_openai_model():
    return os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"

def _get_gemini_api_key():
    return os.environ.get("GEMINI_API_KEY") or ""

def _get_openai_api_key():
    return os.environ.get("OPENAI_API_KEY") or ""

def _get_ai_api_key():
    # Backwards-compatible helper: always return the key for the selected provider.
    return _get_openai_api_key() if _get_ai_provider() == "openai" else _get_gemini_api_key()

def _get_openai_base_url():
    return os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"

# Eager aliases kept for external imports (reflect env at import, stale if env changes per job — prefer _get_*())
AI_PROVIDER = os.environ.get("AI_PROVIDER", "gemini").lower()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
AI_TEMPERATURE = float(os.environ.get("AI_TEMPERATURE", "0.7"))
AI_MAX_TOKENS = int(os.environ.get("AI_MAX_TOKENS", "4096"))
AI_TIMEOUT = int(os.environ.get("AI_TIMEOUT", "30"))

# --- Cheap multimodal toggles (Phase 4) ---
# Everything beyond default Whisper is toggleable per ROADMAP §3/§11.
# Default ON so cheap signals contribute unless explicitly disabled.
ENABLE_SCENE_DETECTION = os.environ.get("ENABLE_SCENE_DETECTION", "1").lower() not in ("0", "false", "no", "off")
ENABLE_AUDIO_EVENTS = os.environ.get("ENABLE_AUDIO_EVENTS", "1").lower() not in ("0", "false", "no", "off")
ENABLE_CHEAP_VISUAL = os.environ.get("ENABLE_CHEAP_VISUAL", "1").lower() not in ("0", "false", "no", "off")
ENABLE_VISION_ANALYSIS = os.environ.get("ENABLE_VISION_ANALYSIS", "1").lower() not in ("0", "false", "no", "off")
ENABLE_DEEP_ANALYSIS = os.environ.get("ENABLE_DEEP_ANALYSIS", "0").lower() in ("1", "true", "yes", "on")
DEBUG_LOGS = os.environ.get("DEBUG_LOGS", "").lower() in ("1", "true", "yes")


def _dbg(msg):
    """Verbose pipeline detail — only printed when DEBUG_LOGS is enabled.

    User-facing lines use plain print(); everything here is diagnostics
    (per-window dumps, budget math, payloads) that would otherwise flood
    the System Logs panel.
    """
    if DEBUG_LOGS:
        print(msg)

GEMINI_PROMPT_TEMPLATE = """
You are a senior short-form video editor. Read the ENTIRE transcript and word-level timestamps to choose the 3–15 MOST VIRAL moments for TikTok/IG Reels/YouTube Shorts. Each clip must be between 15 and 60 seconds long.

⚠️ FFMPEG TIME CONTRACT — STRICT REQUIREMENTS:
- Return timestamps in ABSOLUTE SECONDS from the start of the video (usable in: ffmpeg -ss <start> -to <end> -i <input> ...).
- Only NUMBERS with decimal point, up to 3 decimals (examples: 0, 1.250, 17.350).
- Ensure 0 ≤ start < end ≤ VIDEO_DURATION_SECONDS.
- Each clip between 15 and 60 s (inclusive).
- Prefer starting 0.2–0.4 s BEFORE the hook and ending 0.2–0.4 s AFTER the payoff.
- Use silence moments for natural cuts; never cut in the middle of a word or phrase.
- STRICTLY FORBIDDEN to use time formats other than absolute seconds.

VIDEO_DURATION_SECONDS: {video_duration}

TRANSCRIPT_TEXT (raw):
{transcript_text}

WORDS_JSON (array of {{w, s, e}} where s/e are seconds):
{words_json}

STRICT EXCLUSIONS:
- No generic intros/outros or purely sponsorship segments unless they contain the hook.
- No clips < 15 s or > 60 s.

OUTPUT — RETURN ONLY VALID JSON (no markdown, no comments). Return 2–6 clips when enough strong moments exist; do not add weak clips just to reach the count. Order clips by predicted viral performance (best to worst). In the descriptions, ALWAYS include a CTA like "Follow me and comment X and I'll send you the workflow" (especially if discussing an n8n workflow):
{{
  "shorts": [
    {{
      "start": <number in seconds, e.g., 12.340>,
      "end": <number in seconds, e.g., 37.900>,
      "video_description_for_tiktok": "<description for TikTok oriented to get views>",
      "video_description_for_instagram": "<description for Instagram oriented to get views>",
      "video_title_for_youtube_short": "<title for YouTube Short oriented to get views 100 chars max>",
      "viral_hook_text": "<SHORT punchy text overlay (max 10 words) with 1-2 fitting emojis. MUST BE IN THE SAME LANGUAGE AS THE VIDEO TRANSCRIPT. Examples: 'POV: You realized... 😳', 'Did you know? 🤯', 'Stop doing this! 🚫'>"
    }}
  ]
}}
"""

# Load the YOLO model once (Keep for backup or scene analysis if needed)
# YOLO_MODEL_PATH lets deployments point at a pre-downloaded weights file so a
# volume mounted over the workdir doesn't trigger a re-download at startup.
model = YOLO(os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt"))

# --- MediaPipe Setup ---
# Use standard Face Detection (BlazeFace) for speed
mp_face_detection = mp.solutions.face_detection
face_detection = mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)

# Consecutive detections a large target move must survive before the camera
# follows it (see SmoothedCameraman.update_target). Env-overridable so the
# damping can be dialled back without a deploy; 1 restores the old behaviour.
JUMP_CONFIRM_FRAMES = max(int(os.environ.get("JUMP_CONFIRM_FRAMES", "3")), 1)

# Reset the tracker and the cameraman's damping at every scene cut, so the
# first face found in the new shot is framed instantly instead of being treated
# as a suspicious "jump" from the previous shot's subject (see
# SmoothedCameraman.begin_scene). 0 restores the old behaviour.
SCENE_CUT_RESET = os.environ.get("SCENE_CUT_RESET", "1") != "0"


class SmoothedCameraman:
    """
    Handles smooth camera movement.
    Simplified Logic: "Heavy Tripod"
    Only moves if the subject leaves the center safe zone.
    Moves slowly and linearly.
    """
    def __init__(self, output_width, output_height, video_width, video_height, aspect_ratio=ASPECT_RATIO):
        self.output_width = output_width
        self.output_height = output_height
        self.video_width = video_width
        self.video_height = video_height
        self.aspect_ratio = aspect_ratio

        # Initial State
        self.current_center_x = video_width / 2
        self.target_center_x = video_width / 2

        # Calculate crop dimensions once
        self.crop_height = video_height
        self.crop_width = int(self.crop_height * aspect_ratio)
        if self.crop_width > video_width:
             self.crop_width = video_width
             self.crop_height = int(self.crop_width / aspect_ratio)

        # Safe Zone: 20% of the video width
        # As long as the target is within this zone relative to current center, DO NOT MOVE.
        self.safe_zone_radius = self.crop_width * 0.25

        # A target that teleports further than the safe zone in one detection is
        # far more often a detector error — a second face, a false positive, a
        # box snapping to a different body part — than a person who actually
        # moved that far. Committing to it immediately is what made the camera
        # swing: measured on real user footage, 22% of target updates jumped
        # more than the entire safe zone. So a big move has to REPEAT this many
        # times before the camera follows it; a wrong reading disappears on the
        # next detection and never moves the frame.
        #
        # The cost is latency on a genuinely fast move: at DETECT_STRIDE=4 and
        # 30fps, three confirmations is ~0.4s. That reads as an operator being
        # unhurried, which is the look we want, and it is far cheaper than the
        # whip-panning it replaces.
        #
        # Measured over 262s of TRACK footage from two real user videos
        # (26-jul-2026), confirm=1 -> 3: in-scene reversals 0.41/s -> 0.13/s
        # (-69%), camera travel 91px/s -> 60px/s (-34%). Per scene, 54 of 84 get
        # calmer and 23 are unchanged — but 7 get BUSIER, up to 59 -> 108px/s,
        # because committing later can leave the camera further to travel. Net
        # strongly positive, not universally so.
        self.jump_confirm_frames = JUMP_CONFIRM_FRAMES
        self._pending_target = None
        self._pending_count = 0
        self._snap_pending = False

    def begin_scene(self):
        """Forget the previous shot's subject at a scene cut.

        The jump damping above exists to reject detector noise INSIDE a shot.
        Across a cut it does the opposite of what is wanted: the new shot's face
        is (by construction) far from the old target, so it was held back for
        JUMP_CONFIRM_FRAMES detections and the camera then panned towards it at
        pan speed. On a real two-camera podcast (24-aug-2026) that showed up as
        a headless torso for 1.5s after every cut while the frame slid over to
        the speaker. The snap at the scene's first frame did not help: the
        target it snapped to was still the previous shot's.

        So: drop any pending jump, and cut (rather than pan) to the first target
        accepted in the new shot.
        """
        self._pending_target = None
        self._pending_count = 0
        self._snap_pending = True

    def update_target(self, face_box):
        """Update the target centre from a detection, ignoring lone big jumps."""
        if not face_box:
            return
        x, y, w, h = face_box
        new_center = x + w / 2

        if self._snap_pending:
            self._snap_pending = False
            self._pending_target = None
            self._pending_count = 0
            self.target_center_x = new_center
            self.current_center_x = new_center
            return

        if abs(new_center - self.target_center_x) > self.safe_zone_radius:
            # Same big move as last time? Count it. Otherwise start counting
            # afresh — two contradictory outliers must not confirm each other.
            if (self._pending_target is not None
                    and abs(new_center - self._pending_target) <= self.safe_zone_radius):
                self._pending_count += 1
            else:
                self._pending_target = new_center
                self._pending_count = 1
            if self._pending_count < self.jump_confirm_frames:
                return  # not convinced yet — hold the frame

        self._pending_target = None
        self._pending_count = 0
        self.target_center_x = new_center

    def get_crop_box(self, force_snap=False):
        """
        Returns the (x1, y1, x2, y2) for the current frame.
        """
        if force_snap:
            self.current_center_x = self.target_center_x
        else:
            diff = self.target_center_x - self.current_center_x

            # SIMPLIFIED LOGIC:
            # 1. Is the target outside the safe zone?
            if abs(diff) > self.safe_zone_radius:
                # 2. If yes, move towards it slowly (Linear Speed)
                # Determine direction
                direction = 1 if diff > 0 else -1

                # Speed: 2 pixels per frame (Slow pan)
                # If the distance is HUGE (scene change or fast movement), speed up slightly
                if abs(diff) > self.crop_width * 0.5:
                    speed = 15.0 # Fast re-frame
                else:
                    speed = 3.0  # Slow, steady pan

                self.current_center_x += direction * speed

                # Check if we overshot (prevent oscillation)
                new_diff = self.target_center_x - self.current_center_x
                if (direction == 1 and new_diff < 0) or (direction == -1 and new_diff > 0):
                    self.current_center_x = self.target_center_x

            # If inside safe zone, DO NOTHING (Stationary Camera)

        # Clamp center
        half_crop = self.crop_width / 2

        if self.current_center_x - half_crop < 0:
            self.current_center_x = half_crop
        if self.current_center_x + half_crop > self.video_width:
            self.current_center_x = self.video_width - half_crop

        x1 = int(self.current_center_x - half_crop)
        x2 = int(self.current_center_x + half_crop)

        x1 = max(0, x1)
        x2 = min(self.video_width, x2)

        y1 = 0
        y2 = self.video_height

        return x1, y1, x2, y2

class SpeakerTracker:
    """
    Tracks speakers over time to prevent rapid switching and handle temporary obstructions.
    """
    def __init__(self, stabilization_frames=15, cooldown_frames=30):
        self.active_speaker_id = None
        self.speaker_scores = {}  # {id: score}
        self.last_seen = {}       # {id: frame_number}
        self.locked_counter = 0   # How long we've been locked on current speaker

        # Hyperparameters
        self.stabilization_threshold = stabilization_frames # Frames needed to confirm a new speaker
        self.switch_cooldown = cooldown_frames              # Minimum frames before switching again
        self.last_switch_frame = -1000

        # ID tracking
        self.next_id = 0
        self.known_faces = [] # [{'id': 0, 'center': x, 'last_frame': 123}]

    def reset(self):
        """Forget every speaker at a scene cut.

        Identity, hysteresis and the switch cooldown are all about continuity
        within a shot. After a cut none of it applies: the sticky x3 bonus and
        the cooldown were holding the previous shot's speaker (returning None)
        for up to 30 frames while a new face sat unframed.
        """
        self.active_speaker_id = None
        self.speaker_scores = {}
        self.last_seen = {}
        self.locked_counter = 0
        self.last_switch_frame = -1000
        self.known_faces = []

    def get_target(self, face_candidates, frame_number, width):
        """
        Decides which face to focus on.
        face_candidates: list of {'box': [x,y,w,h], 'score': float}
        """
        current_candidates = []

        # 1. Match faces to known IDs (simple distance tracking)
        for face in face_candidates:
            x, y, w, h = face['box']
            center_x = x + w / 2

            best_match_id = -1
            min_dist = width * 0.15 # Reduced matching radius to avoid jumping in groups

            # Try to match with known faces seen recently
            for kf in self.known_faces:
                if frame_number - kf['last_frame'] > 30: # Forgot faces older than 1s (was 2s)
                    continue

                dist = abs(center_x - kf['center'])
                if dist < min_dist:
                    min_dist = dist
                    best_match_id = kf['id']

            # If no match, assign new ID
            if best_match_id == -1:
                best_match_id = self.next_id
                self.next_id += 1

            # Update known face
            self.known_faces = [kf for kf in self.known_faces if kf['id'] != best_match_id]
            self.known_faces.append({'id': best_match_id, 'center': center_x, 'last_frame': frame_number})

            current_candidates.append({
                'id': best_match_id,
                'box': face['box'],
                'score': face['score']
            })

        # 2. Update Scores with decay
        for pid in list(self.speaker_scores.keys()):
             self.speaker_scores[pid] *= 0.85 # Faster decay (was 0.9)
             if self.speaker_scores[pid] < 0.1:
                 del self.speaker_scores[pid]

        # Add new scores
        for cand in current_candidates:
            pid = cand['id']
            # Score is purely based on size (proximity) now that we don't have mouth
            raw_score = cand['score'] / (width * width * 0.05)
            self.speaker_scores[pid] = self.speaker_scores.get(pid, 0) + raw_score

        # 3. Determine Best Speaker
        if not current_candidates:
            # If no one found, maintain last active speaker if cooldown allows
            # to avoid black screen or jump to 0,0
            return None

        best_candidate = None
        max_score = -1

        for cand in current_candidates:
            pid = cand['id']
            total_score = self.speaker_scores.get(pid, 0)

            # Hysteresis: HUGE Bonus for current active speaker
            if pid == self.active_speaker_id:
                total_score *= 3.0 # Sticky factor

            if total_score > max_score:
                max_score = total_score
                best_candidate = cand

        # 4. Decide Switch
        if best_candidate:
            target_id = best_candidate['id']

            if target_id == self.active_speaker_id:
                self.locked_counter += 1
                return best_candidate['box']

            # New person. The cooldown must hold whether or not the current
            # speaker happens to be detected in THIS frame.
            #
            # It used to fall through and switch when the active speaker was
            # missing from the candidate list — a blink, a head turn or one
            # motion-blurred frame was enough. That is precisely when the
            # cooldown is needed, so it only ever fired when it wasn't: 3 of 7
            # target switches measured on a 12s clip (25-jul-2026) jumped the
            # cooldown this way, and every jump drags the camera across frame.
            #
            # Returning None holds instead: the caller only calls
            # update_target() on a truthy box, so the camera keeps its current
            # target and finishes whatever move it was making. The hold is
            # bounded by the cooldown itself — once it expires, a speaker who
            # really did leave the shot is switched away from normally.
            if frame_number - self.last_switch_frame < self.switch_cooldown:
                old_cand = next((c for c in current_candidates if c['id'] == self.active_speaker_id), None)
                return old_cand['box'] if old_cand else None

            self.active_speaker_id = target_id
            self.last_switch_frame = frame_number
            self.locked_counter = 0
            return best_candidate['box']

        return None

# Detectors never need full-resolution frames: MediaPipe returns relative
# coords and YOLO boxes are scaled back up. Running them on a ≤640px copy cuts
# per-frame preprocessing cost hard, which is what dominates CPU-only renders.
DETECT_MAX_WIDTH = 640
# The global MediaPipe graph and YOLO model are NOT thread-safe; clips render
# in parallel, so every inference goes through this lock. Contention is small
# (a few ms per call) — the ffmpeg renders are where the parallel time goes.
DETECT_LOCK = threading.Lock()
# Detect every Nth frame; SmoothedCameraman interpolates between updates.
DETECT_STRIDE = max(int(os.environ.get("DETECT_STRIDE", "4")), 1)
# YOLO fallback (no face found) is far heavier than MediaPipe — extra throttle.
YOLO_FALLBACK_STRIDE = DETECT_STRIDE * 2


def _detection_frame(frame):
    """Downscaled copy for detectors. Returns (small_frame, scale) with
    scale mapping small-frame pixel coords back to the original frame."""
    h, w = frame.shape[:2]
    if w <= DETECT_MAX_WIDTH:
        return frame, 1.0
    scale = w / DETECT_MAX_WIDTH
    small = cv2.resize(frame, (DETECT_MAX_WIDTH, max(int(h / scale), 2)),
                       interpolation=cv2.INTER_AREA)
    return small, scale


def detect_face_candidates(frame):
    """
    Returns list of all detected faces using lightweight FaceDetection.
    Boxes are in ORIGINAL frame coordinates (detection runs downscaled;
    MediaPipe's relative coords make the mapping exact).
    """
    height, width, _ = frame.shape
    small, _scale = _detection_frame(frame)
    rgb_frame = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    with DETECT_LOCK:
        results = face_detection.process(rgb_frame)

    candidates = []

    if not results.detections:
        return []

    for detection in results.detections:
        bboxC = detection.location_data.relative_bounding_box
        x = int(bboxC.xmin * width)
        y = int(bboxC.ymin * height)
        w = int(bboxC.width * width)
        h = int(bboxC.height * height)

        candidates.append({
            'box': [x, y, w, h],
            'score': w * h # Area as score
        })

    return candidates

def detect_person_yolo(frame):
    """
    Fallback: Detect largest person using YOLO when face detection fails.
    Returns [x, y, w, h] of the person's 'upper body' approximation, in
    ORIGINAL frame coordinates (inference runs on a downscaled copy).
    """
    small, scale = _detection_frame(frame)
    # Use the globally loaded model
    with DETECT_LOCK:
        results = model(small, verbose=False, classes=[0]) # class 0 is person

    if not results:
        return None

    best_box = None
    max_area = 0

    for result in results:
        boxes = result.boxes
        for box in boxes:
            x1, y1, x2, y2 = [int(i * scale) for i in box.xyxy[0]]
            w = x2 - x1
            h = y2 - y1
            area = w * h

            if area > max_area:
                max_area = area
                # Focus on the top 40% of the person (head/chest) for framing
                # This approximates where the face is if we can't detect it directly
                face_h = int(h * 0.4)
                best_box = [x1, y1, w, face_h]

    return best_box

def create_general_frame(frame, output_width, output_height):
    """
    Creates a 'General Shot' frame:
    - Background: Blurred zoom of original
    - Foreground: Original video scaled to fit width, centered vertically.
    """
    orig_h, orig_w = frame.shape[:2]

    # 1. Background (Fill Height)
    # Crop center to aspect ratio
    bg_scale = output_height / orig_h
    bg_w = int(orig_w * bg_scale)
    bg_resized = cv2.resize(frame, (bg_w, output_height), interpolation=cv2.INTER_LINEAR)

    # Crop center of background
    start_x = (bg_w - output_width) // 2
    if start_x < 0: start_x = 0
    background = bg_resized[:, start_x:start_x+output_width]
    if background.shape[1] != output_width:
        background = cv2.resize(background, (output_width, output_height), interpolation=cv2.INTER_LINEAR)

    # Blur background: blur at quarter resolution and scale back up — visually
    # identical for a defocused backdrop, an order of magnitude cheaper than a
    # 51px Gaussian at full size.
    small_bg = cv2.resize(background, (max(output_width // 4, 2), max(output_height // 4, 2)),
                          interpolation=cv2.INTER_AREA)
    small_bg = cv2.GaussianBlur(small_bg, (13, 13), 0)
    background = cv2.resize(small_bg, (output_width, output_height),
                            interpolation=cv2.INTER_LINEAR)

    # 2. Foreground (Fit Width)
    scale = output_width / orig_w
    fg_h = int(orig_h * scale)
    foreground = cv2.resize(frame, (output_width, fg_h), interpolation=cv2.INTER_LINEAR)

    # A source taller than the output fills the width at a height that does not
    # fit: centre-crop it instead of indexing the frame with a negative offset,
    # which raises rather than renders.
    if fg_h > output_height:
        top = (fg_h - output_height) // 2
        foreground = foreground[top:top + output_height, :]
        fg_h = output_height

    # 3. Overlay
    y_offset = (output_height - fg_h) // 2

    # Clone background to avoid modifying it
    final_frame = background.copy()
    final_frame[y_offset:y_offset+fg_h, :] = foreground

    return final_frame

# NOTE: a "route text-heavy scenes to GENERAL" rule was tried here and removed
# on 26-jul-2026. The problem it targets is real — a screencast that happens to
# contain one face gets cropped to the face and its headlines come out cut
# mid-word — but edge density is the wrong signal for it. Measured: a
# constructed talking-head-beside-a-chart scored 0.012 while the SAME shot
# without the panels scored 0.029, because a flat panel of text has far fewer
# edges than ordinary scene detail. Canny measures visual busyness, not text.
# A real fix needs an actual text detector (MSER/EAST) validated against clips
# that contain the failure mode; this corpus has almost none.


def analyze_scenes_strategy(video_path, scenes):
    """
    Analyzes each scene to determine if it should be TRACK (Single person) or GENERAL (Group/Wide).
    Returns list of strategies corresponding to scenes.
    """
    cap = cv2.VideoCapture(video_path)
    strategies = []

    if not cap.isOpened():
        return ['TRACK'] * len(scenes)

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    for start, end in tqdm(scenes, desc="   Analyzing Scenes"):
        s_f, e_f = start.get_frames(), end.get_frames()
        # Sample 5 frames spread across the scene, clamped inside it (the old
        # start+5/end-5 samples landed outside scenes shorter than ~10 frames).
        margin = min(2, max(0, (e_f - s_f - 1) // 2))
        frames_to_check = sorted(set(
            int(round(f)) for f in np.linspace(s_f + margin, e_f - 1 - margin, 5)
        ))

        face_counts = []
        for f_idx in frames_to_check:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret: continue

            # Near-black frames (fades, cut-to-black) carry no faces and used
            # to drag single-person scenes into GENERAL. Skip them.
            if frame.mean() < 16:
                continue

            # Detect faces
            candidates = detect_face_candidates(frame)
            face_counts.append(len(candidates))

        # Decision Logic
        if not face_counts:
            avg_faces = 0
        else:
            avg_faces = sum(face_counts) / len(face_counts)

        # Strategy:
        # 0 faces -> GENERAL (Landscape/B-roll)
        # 1 face -> TRACK
        # > 1.2 faces -> GENERAL (Group)

        if avg_faces > 1.2 or avg_faces < 0.5:
            strategies.append('GENERAL')
        else:
            strategies.append('TRACK')

    cap.release()

    # Hysteresis: a short scene whose two neighbors agree on the opposite
    # strategy is almost always a sampling miss (profile face, insert shot).
    # Each TRACK<->GENERAL flip is a full on-screen layout change, so flapping
    # is worse than an occasional wrong-but-stable choice.
    max_flip_frames = int(2.0 * fps)
    for i in range(1, len(strategies) - 1):
        dur = scenes[i][1].get_frames() - scenes[i][0].get_frames()
        if (dur < max_flip_frames
                and strategies[i - 1] == strategies[i + 1] != strategies[i]):
            strategies[i] = strategies[i - 1]

    return strategies

def detect_scenes(video_path):
    import scene_detection
    return scene_detection.detect_scenes(video_path)

def get_video_resolution(video_path):
    probe = cv2.VideoCapture(video_path)
    try:
        if not probe.isOpened():
            raise IOError(f"cannot open video: {video_path}")
        return (int(probe.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    finally:
        probe.release()


# Byte budget for the sanitized video title used as the stem of every derived
# file. Filesystems cap a name in BYTES (255 on ext4), not characters, and the
# pipeline decorates this stem: "_clip_10.mp4" (12), "subtitled_<ts>_" (21),
# "hooked_<ts>_" (18), "temp_hook_<hex8>_" (19), "autosubs_<ts>_" + ".ass" (24).
# Budgeting 120 bytes leaves room for all of them stacked (worst chain:
# subtitled_<ts>_hooked_<ts>_<stem>_clip_NN.mp4 ≈ 171 bytes) under the limit.
#
# The old cap was 100 CHARACTERS, which is 300 bytes of Bengali or Arabic — over
# the limit before any decoration. It surfaced as OSError 36 killing the hook
# endpoint in prod on 26-jul-2026.
MAX_TITLE_BYTES = 120


def truncate_bytes(text, max_bytes):
    """Trim ``text`` to a byte budget without splitting a multi-byte character."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore")


def sanitize_filename(filename):
    """Remove invalid characters from filename and bound it for the filesystem."""
    # "canción" has two Unicode spellings: a precomposed ó (NFC) or an o plus a
    # combining acute (NFD). yt-dlp hands over titles in either, and the name
    # becomes the clip file, the R2 key and the URL path. Measured 24-ago-2026:
    # a key carrying the combining form is fetchable by a <video> element but a
    # fetch() of the same URL comes back 503, which broke the download button on
    # every clip with a Spanish title. Normalising here fixes the whole chain at
    # its source, and is a no-op for the ASCII names that already worked.
    filename = unicodedata.normalize('NFC', filename)
    filename = re.sub(r'[<>:"/\\|?*#]', '', filename)
    filename = filename.replace(' ', '_')
    return truncate_bytes(filename, MAX_TITLE_BYTES)


def is_youtube_url(url):
    """True for the hosts the proxy chain exists for. Anything else (a CDN
    mp4, tmpfiles/catbox, an R2 link) has no IP ban to dodge and downloads
    5-10x faster from the server's own IP than through the ISP proxies."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    return host.endswith(("youtube.com", "youtu.be", "youtube-nocookie.com", "googlevideo.com"))


def plan_download_attempts(direct_first, statics, paid, have_hd, youtube=True):
    """Ordered (label, capped, proxy) download plan — pure, unit-tested.

    ``youtube=False`` (a direct file URL): the server's own IP first, then one
    static proxy as the only fallback; the paid per-GB proxy is never used.

    Cheapest bandwidth first: the server's own IP, then the flat-rate static
    ISP proxies (uncapped 1080p, free bytes), then the per-GB paid proxy
    (720p cost cap), and last the conservative fallback strategy through the
    paid proxy (or a static/direct when no paid proxy is configured).
    ``capped`` marks attempts whose bytes are billed per GB."""
    if not youtube:
        plan = [('direct', False, None)]
        if statics:
            plan.append(('static-fallback', False, statics[0]))
        return plan
    plan = []
    if direct_first:
        plan.append(('HD-direct', False, None))
    if have_hd:
        for i, s in enumerate(statics):
            plan.append((f'HD-static{i + 1}', False, s))
        plan.append(('HD', bool(paid), paid))
    plan.append(('fallback', bool(paid),
                 paid if paid else (statics[0] if statics else None)))
    return plan


def download_youtube_video(url, output_dir="."):
    """
    Downloads a YouTube video using yt-dlp.
    Returns the path to the downloaded video and the video title.
    """
    # SSRF guard: block non-http(s) schemes and private/loopback/metadata hosts
    # before handing the URL to yt-dlp.
    from security_utils import assert_public_url
    assert_public_url(url)
    # Throwaway hosts agents fall back to (tmpfiles.org) hand out short-lived
    # signed links; refresh through the host's page so yt-dlp gets the file.
    import file_hosts
    url = file_hosts.resolve(url)

    _dbg(f"🔍 yt-dlp version: {yt_dlp.version.__version__}")
    print("📥 Downloading video from YouTube...")
    step_start_time = time.time()

    cookies_path = '/app/cookies.txt'
    cookies_env = os.environ.get("YOUTUBE_COOKIES")
    if cookies_env:
        print("🍪 Found YOUTUBE_COOKIES env var, creating cookies file inside container...")
        try:
            with open(cookies_path, 'w') as f:
                f.write(cookies_env)
            if os.path.exists(cookies_path):
                 # Never print file CONTENT here: with a headerless cookies
                 # blob this would leak live YouTube session cookies to logs.
                 print("   🍪 Using YouTube cookies for download.")
        except Exception as e:
            print(f"⚠️ Failed to write cookies file: {e}")
            cookies_path = None
    else:
        cookies_path = None
        print("⚠️ YOUTUBE_COOKIES env var not found.")

    # Optional HTTP proxy. Set PROXY_URL to route downloads through it; unset
    # (self-host) goes direct as before.
    _proxy = os.environ.get("PROXY_URL", "").strip() or None
    if _proxy:
        print("🌐 Using proxy for download.")

    # Flat-rate static ISP proxies (STATIC_PROXY_URLS, comma-separated), tried
    # BEFORE the per-GB proxy: dedicated IPs with unlimited traffic, so their
    # bandwidth costs nothing per job and carries no 720p cost cap. Rotated per
    # job to spread load (and YouTube's attention) across the pool. PROXY_URL
    # stays the paid last resort — with STATIC_PROXY_URLS unset the behavior is
    # byte-identical to before.
    _statics = [p.strip() for p in
                os.environ.get("STATIC_PROXY_URLS", "").split(",") if p.strip()]
    if _statics:
        import random as _random
        k = _random.randrange(len(_statics))
        _statics = _statics[k:] + _statics[:k]
        print(f"🌐 {len(_statics)} static ISP proxies configured.")

    # Two download strategies, tried in order so a break in the HD path degrades
    # gracefully instead of failing the whole job: an HD attempt first, then a
    # conservative fallback (also the only strategy for self-host).
    _bgutil_http = os.environ.get("BGUTIL_BASE_URL", "").strip()
    _bgutil_script = os.environ.get("BGUTIL_SCRIPT_PATH", "").strip()
    if _bgutil_http:
        hd_args = {'youtubepot-bgutilhttp': {'base_url': [_bgutil_http]}}
    elif _bgutil_script:
        hd_args = {'youtubepot-bgutilscript': {'script_path': [_bgutil_script]}}
    else:
        hd_args = None
    fallback_args = {
        'youtube': {
            'player_client': ['tv_embed', 'android', 'mweb', 'web'],
            'player_skip': ['webpage', 'configs'],
        }
    }

    # Cap at 720p ONLY when the bytes actually go through the PER-GB paid proxy
    # — that cap exists to control bandwidth cost, and the direct attempt and
    # the flat-rate static proxies have none.
    #
    # This is per-attempt on purpose. Deciding it once from `_proxy` capped the
    # DIRECT attempt too, so with DIRECT_FIRST=1 (which serves most downloads)
    # every YouTube source arrived at 720p and, since the reframe inherits the
    # source height, 80% of delivered clips came out 406x720 (audited 25-jul-2026).
    def _hd_fmt_for(capped):
        if capped:
            return ('bestvideo[vcodec^=avc1][height<=720][ext=mp4]+bestaudio[ext=m4a]/'
                    'bestvideo[vcodec^=avc1][height<=720]+bestaudio/'
                    'best[height<=720][ext=mp4]/best[height<=720]/best')
        return ('bestvideo[vcodec^=avc1][height<=1080][ext=mp4]+bestaudio[ext=m4a]/'
                'bestvideo[vcodec^=avc1][height<=1080]+bestaudio/'
                'best[height<=1080][ext=mp4]/best[ext=mp4]/best')
    fallback_fmt = 'best[ext=mp4]/best'

    def _base_opts(extractor_args, proxy):
        return {
            'quiet': False, 'verbose': True, 'no_warnings': False,
            'cookiefile': cookies_path if cookies_path else None,
            'proxy': proxy, 'socket_timeout': 30, 'retries': 10, 'fragment_retries': 10,
            'nocheckcertificate': True, 'cachedir': False,
            'extractor_args': extractor_args,
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                ),
            },
        }

    # Wire bytes actually pulled through the (paid) proxy, summed across
    # fragments/streams. Reported to app.py via the PROXY_BYTES= line below.
    _dl_bytes = {"total": 0, "partial": 0}

    def _progress_hook(d):
        if d.get('status') == 'downloading':
            # Bytes of a fragment still in flight: a failed attempt has
            # already paid for these even though 'finished' never fires.
            _dl_bytes["partial"] = int(d.get('downloaded_bytes') or 0)
        elif d.get('status') == 'finished':
            _dl_bytes["partial"] = 0
            _dl_bytes["total"] += int(d.get('total_bytes')
                                      or d.get('total_bytes_estimate')
                                      or d.get('downloaded_bytes') or 0)

    def _attempt(extractor_args, fmt, proxy):
        _dl_bytes["total"] = 0
        _dl_bytes["partial"] = 0
        with yt_dlp.YoutubeDL(_base_opts(extractor_args, proxy)) as ydl:
            info = ydl.extract_info(url, download=False)
        sanitized = sanitize_filename(info.get('title', 'youtube_video'))
        expected = os.path.join(output_dir, f'{sanitized}.mp4')
        if os.path.exists(expected):
            os.remove(expected)
        dl_opts = {
            **_base_opts(extractor_args, proxy),
            'format': fmt,
            'outtmpl': os.path.join(output_dir, f'{sanitized}.%(ext)s'),
            'merge_output_format': 'mp4', 'overwrites': True,
            'progress_hooks': [_progress_hook],
        }
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            ydl.download([url])
        return sanitized

    # DIRECT_FIRST=1: try the server's own IP before spending proxy bandwidth.
    # Needs cookies + a PO-token provider — without both, YouTube flags the
    # datacenter IP after the first request (verified in prod, 21-jul-2026).
    _direct_first = (os.environ.get("DIRECT_FIRST", "").strip() == "1"
                     and (_proxy or _statics) and hd_args and cookies_path)

    attempts = [
        (label,
         fallback_args if label == 'fallback' else hd_args,
         fallback_fmt if label == 'fallback' else _hd_fmt_for(capped),
         proxy)
        for label, capped, proxy in plan_download_attempts(
            _direct_first, _statics, _proxy, bool(hd_args), youtube=is_youtube_url(url))
    ]
    if not is_youtube_url(url):
        print("🌐 Direct file URL: downloading from the server's own IP (no proxy).")

    sanitized_title = None
    last_err = None
    used_proxy = False
    # Every attempt, with its bytes and failure text: printed as PROXY_ROUTE=
    # below so app.py can keep a durable trail of WHY a job reached the paid
    # proxy (the container log rotates within the hour; see cloud/proxy_ledger).
    attempt_log = []
    for label, ea, fmt, proxy in attempts:
        # A 403 on the media fetch is usually transient: the googlevideo URL is
        # bound to the IP that extracted it, and the residential proxy rotates
        # its exit IP between requests. Retrying re-extracts and usually lands
        # on a consistent IP (3 of 62 downloads hit this on 22-jul-2026).
        for retry in range(2):
            try:
                print(f"📥 Download attempt: {label}" + (f" (retry {retry})" if retry else ""))
                sanitized_title = _attempt(ea, fmt, proxy)
                # Only bytes through the PER-GB proxy cost money; direct and
                # the flat-rate static proxies are free bandwidth for the
                # monthly counter's purposes.
                used_proxy = proxy is not None and proxy == _proxy
                attempt_log.append({"label": label, "ok": True,
                                    "bytes": _dl_bytes["total"] + _dl_bytes["partial"],
                                    "paid": used_proxy})
                print(f"✅ Download succeeded ({label}).")
                break
            except Exception as e:
                last_err = e
                attempt_log.append({"label": label, "ok": False,
                                    "bytes": _dl_bytes["total"] + _dl_bytes["partial"],
                                    "paid": proxy is not None and proxy == _proxy,
                                    "error": str(e)[:300]})
                print(f"⚠️  Download attempt '{label}' failed: {str(e)[:200]}")
                retryable = '403' in str(e) or 'Forbidden' in str(e)
                if not retryable or retry == 1:
                    break
                time.sleep(3)
        if sanitized_title is not None:
            break

    if sanitized_title is None:
        import sys
        error_msg = f"""
❌ ================================================================= ❌
❌ FATAL ERROR: YOUTUBE DOWNLOAD FAILED (all strategies)
❌ ================================================================= ❌
REASON: YouTube blocked the request or the download tooling is out of date.
👇 SOLUTION FOR USER: download the video manually and use the 'Upload Video' tab.
Technical Details: {str(last_err)}
"""
        print(error_msg, file=sys.stdout)
        print(error_msg, file=sys.stderr)
        sys.stdout.flush(); sys.stderr.flush()
        time.sleep(0.5)
        raise last_err

    downloaded_file = os.path.join(output_dir, f'{sanitized_title}.mp4')
    if not os.path.exists(downloaded_file):
        for f in os.listdir(output_dir):
            if f.startswith(sanitized_title) and f.endswith('.mp4'):
                downloaded_file = os.path.join(output_dir, f)
                break

    # Paid bytes across EVERY attempt that used the per-GB proxy, failed ones
    # included: a paid attempt that died after three 10 MB fragments was
    # billed for them even though a later attempt won.
    paid_bytes = sum(int(a.get("bytes") or 0) for a in attempt_log if a.get("paid"))
    print("PROXY_ROUTE=" + json.dumps({
        "winner": attempt_log[-1]["label"] if attempt_log and attempt_log[-1].get("ok") else None,
        "paid_bytes": paid_bytes,
        "attempts": attempt_log,
    }, ensure_ascii=False))
    if paid_bytes:
        # Machine-parseable marker consumed by app.py's log reader for the
        # monthly proxy-bandwidth counter. Not shown to clients (log filter).
        print(f"PROXY_BYTES={paid_bytes}")
    print(f"✅ Video downloaded in {time.time() - step_start_time:.2f}s: {downloaded_file}")
    return downloaded_file, sanitized_title

def finalize_clip_passthrough(input_video, final_output_video):
    """Keep the clip's native framing (for horizontal/16:9 output).

    The input is the freshly encoded cut, so a stream-copy remux is enough to
    add +faststart — re-encoding here would only cost time and quality.
    """
    if os.path.exists(final_output_video):
        os.remove(final_output_video)
    print(f"🎬 Passthrough (native framing): {input_video}")
    cmd = [
        'ffmpeg', '-y', '-i', input_video,
        '-c', 'copy', *METADATA_SCRUB, '-movflags', '+faststart',
        final_output_video,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    print(f"✅ Clip saved to {final_output_video}")
    return True


def auto_caption_clip(clip_path, transcript, clip_start, clip_end, split_ranges=None):
    """Burn the default caption style onto a finished clip.

    ``split_ranges``: (start, end) stretches, in clip seconds, rendered with
    the SPLIT layout; captions there sit on the seam between the two speakers
    instead of the bottom. None reads the render's own sidecar next to
    ``clip_path`` (layout_ranges), which is where recut hands it over.

    Captions are mandatory for short-form to land, but they were opt-in behind a
    modal and only 9% of delivered clips ever got them (prod audit, 25-jul-2026).
    So every clip now ships captioned by default.

    The captioned file is written ALONGSIDE the clip as
    ``subtitled_<ts>_<clip>.mp4`` — the same convention /api/subtitle uses — so
    the untouched original stays on disk and re-styling from the modal replaces
    the captions instead of burning a second layer over them.

    Returns the captioned path, or None when captions were skipped (silent
    video, no words in range, AUTO_CAPTIONS=0, or any failure — a caption
    problem must never cost the user the clip they already paid for).
    """
    if os.environ.get("AUTO_CAPTIONS", "1").strip() == "0":
        return None
    if not transcript or not transcript.get('segments'):
        return None  # silent video: nothing to caption
    try:
        import subtitles as _subs
        style = _subs.AUTO_CAPTION_STYLE
        output_dir = os.path.dirname(clip_path)
        stem = os.path.basename(clip_path)
        generation_id = int(time.time())
        # The output name MUST stay exactly "subtitled_<ts>_<clip filename>":
        # the modal's walk-back and _canonical_clip_file both reconstruct the
        # clean original from it, so trimming the stem here would orphan the
        # pair. Length is bounded upstream instead, by MAX_TITLE_BYTES at
        # download time. A legacy clip whose name predates that budget can still
        # overflow — that raises OSError 36, which the except below turns into
        # "ship the clip uncaptioned" rather than a broken filename.
        # The .ass path is interpolated INTO an ffmpeg filter string
        # (-vf ass='...'), where a literal apostrophe closes the quote and
        # breaks the filter. Titles carry apostrophes constantly in English
        # ("Earth's", "Don't"), so this name must stay free of the clip stem —
        # which is exactly why /api/subtitle has always used a neutral
        # "subs_<i>_<ts>.ass". Deriving it from the stem silently cost captions
        # on every apostrophe title until 29-jul-2026.
        #
        # The OUTPUT name still carries the stem, and must: the modal's
        # walk-back and _canonical_clip_file reconstruct the clean original
        # from it. That one is only ever passed as an argv element, never
        # inside a filter string, so quoting never applies to it.
        # Unique per clip, not just per second: clips render in parallel
        # (CLIP_WORKERS), so a bare timestamp would collide and let one clip
        # burn another's captions.
        ass_path = os.path.join(
            output_dir, f"autosubs_{generation_id}_{uuid.uuid4().hex[:8]}.ass")
        out_path = os.path.join(output_dir, f"subtitled_{generation_id}_{stem}")

        if split_ranges is None:
            import layout_ranges as _layouts
            split_ranges = _layouts.split_ranges(_layouts.read(clip_path))
        if not _subs.generate_ass(
                transcript, clip_start, clip_end, ass_path,
                split_ranges=split_ranges,
                max_chars=style["max_chars"], max_duration=style["max_duration"],
                alignment=style["alignment"], fontsize=style["font_size"],
                font_name=style["font_name"], font_color=style["font_color"],
                border_color=style["border_color"], border_width=style["border_width"],
                highlight_color=style["highlight_color"], effect=style["effect"],
                base_opacity=style["base_opacity"], uppercase=style["uppercase"]):
            print("   ℹ️ No words in range — clip ships without captions.")
            return None

        # First burn: fast Symbola via ffmpeg (parallel). Remotion animated emoji is for subtitle-window re-burn.
        _subs.burn_subtitles(
            clip_path, ass_path, out_path,
            alignment=style["alignment"], fontsize=style["font_size"],
            font_name=style["font_name"], font_color=style["font_color"],
            border_color=style["border_color"], border_width=style["border_width"])
        print(f"   💬 Captions burned: {os.path.basename(out_path)}")
        return out_path
    except Exception as e:
        print(f"   ⚠️ Auto-captions failed ({type(e).__name__}: {e}) — "
              f"delivering the clip without them.")
        return None


def auto_hook_clip(clip_path, clip):
    """Burn the clip's Gemini hook text as a DERIVED file (AUTO_HOOK=1).

    Writes ``hooked_<ts>_<clip filename>`` next to the canonical clip, exactly
    like captions write ``subtitled_<ts>_...``: the canonical stays clean, so
    the hook can later be replaced or removed by walking the prefix back
    (app.py `_strip_burned_hook`). Captions are then burned ON TOP of the
    hooked file, keeping the "captions are always the last layer" invariant.

    Returns (hooked_path, hook_config), or None when skipped or failed — a
    hook problem must never cost the user the clip itself (same fail-open
    contract as auto_caption_clip)."""
    text = (clip.get('viral_hook_text') or '').strip()
    if not text:
        return None
    style = os.environ.get("AUTO_HOOK_STYLE", "classic")
    try:
        seconds = float(os.environ.get("AUTO_HOOK_SECONDS", "5"))
    except ValueError:
        seconds = 5.0
    try:
        from hooks import add_hook_to_video, HOOK_STYLES
        if style not in HOOK_STYLES:
            style = "classic"
        output_dir = os.path.dirname(clip_path)
        out_path = os.path.join(
            output_dir, f"hooked_{int(time.time())}_{os.path.basename(clip_path)}")
        add_hook_to_video(clip_path, text, out_path, position="top",
                          duration=seconds, style=style)
        print(f"   🪝 Hook burned ({style}, {seconds:g}s): {text}")
        return out_path, {"text": text, "style": style, "position": "top",
                          "duration_seconds": seconds}
    except Exception as e:
        print(f"   ⚠️ Auto-hook failed ({type(e).__name__}: {e}) — "
              f"delivering the clip without it.")
        return None


def render_clip(input_video, final_output_video, output_format="auto",
                force_strategy=None, crop_overrides=None):
    """Route a cut clip through the right renderer for the chosen output format.
    vertical/auto -> 9:16 reframe, square -> 1:1 reframe, horizontal -> keep.
    ``force_strategy`` (e.g. 'WIDE'/'TRACK') pins every scene's layout — the
    clip editor's whole-clip framing override. ``crop_overrides`` positions
    individual scenes by hand (the per-scene reframing editor) and wins over
    ``force_strategy`` for the scenes it names."""
    if output_format == "horizontal":
        return finalize_clip_passthrough(input_video, final_output_video)
    aspect = 1.0 if output_format == "square" else ASPECT_RATIO
    return process_video_to_vertical(input_video, final_output_video, aspect_ratio=aspect,
                                     force_strategy=force_strategy,
                                     crop_overrides=crop_overrides)


# Watermark geometry, as fractions of the clip width/height.
#
# Vertical placement is the whole point: the top and bottom strips of a 9:16
# clip are either black bars or blurred filler (GENERAL layout), so a mark up
# there is cropped away without touching a single pixel of real footage. At 40%
# of the height it sits inside the content band — a 16:9 source letterboxed
# into 9:16 spans roughly 34%-66% — so removing the mark means cutting into the
# picture. Left-aligned, like OpusClip's.
WATERMARK_WIDTH_RATIO = 0.30
WATERMARK_MARGIN_RATIO = 0.05
WATERMARK_Y_RATIO = 0.40
WATERMARK_OPACITY = 0.85


def apply_watermark(video_path):
    """Burn the OpenShorts watermark into a finished clip (free plan).

    One re-encode pass on the final file so every output format (TRACK,
    GENERAL, horizontal passthrough) gets the mark, and later subtitle/hook
    re-encodes keep it — they re-encode the already-marked pixels.
    """
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "assets", "watermark.png")
    if not os.path.exists(logo_path):
        print(f"   ⚠️ Watermark asset missing ({logo_path}); clip kept unmarked.")
        return False

    # Scale the lockup from the clip's real width: overlay can't read the other
    # input's size, and computing it here avoids the deprecated scale2ref.
    try:
        probe = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", video_path],
            stderr=subprocess.STDOUT, timeout=60,
        ).decode().strip().split("x")
        vw, vh = int(probe[0]), int(probe[1])
    except Exception as e:
        print(f"   ⚠️ Could not probe clip for watermark ({e}); clip kept unmarked.")
        return False

    wm_w = max(80, int(vw * WATERMARK_WIDTH_RATIO))
    x = int(vw * WATERMARK_MARGIN_RATIO)
    y = int(vh * WATERMARK_Y_RATIO)
    filt = (
        f"[1:v]scale={wm_w}:-1,format=rgba,"
        f"colorchannelmixer=aa={WATERMARK_OPACITY}[wm];"
        f"[0:v][wm]overlay=x={x}:y={y}"
    )
    tmp_path = video_path + ".wm.mp4"
    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", logo_path,
           "-filter_complex", filt,
           *video_encode_args(QUALITY), "-c:a", "copy", *METADATA_SCRUB,
           "-movflags", "+faststart", tmp_path]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            timeout=1800)
    if result.returncode == 0 and os.path.exists(tmp_path):
        os.replace(tmp_path, video_path)
        return True
    err = (result.stderr or b"").decode(errors="ignore")[-300:]
    print(f"   ⚠️ Watermark pass failed (clip kept unmarked): {err}")
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    return False


def process_video_to_vertical(input_video, final_output_video, aspect_ratio=ASPECT_RATIO,
                              force_strategy=None, crop_overrides=None):
    """
    Core logic to reframe a horizontal video to a target aspect ratio using
    scene detection and Active Speaker Tracking (MediaPipe).
    aspect_ratio: width/height of the output (9/16 vertical, 1.0 square).
    force_strategy / crop_overrides pin layouts and scene crops by hand (v2
    engine only — the v1 loop below has no layout concept beyond its own
    classifier).
    """
    # v2 engine: analyze downscaled, render natively in ffmpeg. Any failure
    # falls back to the v1 frame loop below so a v2 edge case can't kill jobs.
    if os.environ.get("REFRAME_ENGINE", "v2").strip().lower() != "v1":
        try:
            import reframe_v2
            t0 = time.time()
            result = reframe_v2.render(input_video, final_output_video, aspect_ratio,
                                       force_strategy=force_strategy,
                                       crop_overrides=crop_overrides)
            print(f"   ⏱️ Reframe v2 total: {time.time() - t0:.1f}s")
            return result
        except Exception as e:
            # Only v2 honours hand-framed scenes and forced layouts. Falling
            # through to v1 would quietly return an automatically framed clip,
            # and the user would see their correction vanish with no reason
            # given — so surface the failure instead of discarding their input.
            if crop_overrides or force_strategy:
                raise RuntimeError(
                    f"manual framing needs the v2 reframe engine, which failed "
                    f"({type(e).__name__}: {e})") from e
            print(f"   ⚠️ Reframe v2 failed ({type(e).__name__}: {e}) — "
                  f"falling back to v1 frame loop")

    # The v1 loop stages its work next to the final file: a silent video track
    # first, then the source audio, muxed together at the end.
    stem = os.path.splitext(final_output_video)[0]
    silent_video_path = stem + ".v1video.mp4"
    audio_track_path = stem + ".v1audio.aac"
    for stale in (silent_video_path, audio_track_path, final_output_video):
        # isfile, not exists: a caller that hands us a directory should not
        # take an EACCES here, and must never have it deleted either.
        if os.path.isfile(stale):
            os.remove(stale)

    print(f"🎬 Processing clip: {input_video}")
    print("   Step 1: Detecting scenes...")
    scenes, fps = detect_scenes(input_video)

    if not scenes:
        # Scene detection found nothing: treat the whole video as one scene.
        print("   ❌ No scenes were detected. Using full video as one scene.")
        probe = cv2.VideoCapture(input_video)
        span = int(probe.get(cv2.CAP_PROP_FRAME_COUNT))
        probe.release()
        from scenedetect import FrameTimecode
        scenes = [(FrameTimecode(0, fps), FrameTimecode(span, fps))]

    print(f"   ✅ Found {len(scenes)} scenes.")

    print("\n   🧠 Step 2: Preparing Active Tracking...")
    original_width, original_height = get_video_resolution(input_video)

    # Same delivery floor as the v2 engine — a fallback render is still the clip
    # the user posts, so it must not ship sub-HD. The frame loop below already
    # resizes every cropped frame to these dims, so nothing else changes.
    from reframe_v2 import delivery_size
    OUTPUT_WIDTH, OUTPUT_HEIGHT = delivery_size(original_width, original_height,
                                                aspect_ratio)

    # Initialize Cameraman
    cameraman = SmoothedCameraman(OUTPUT_WIDTH, OUTPUT_HEIGHT, original_width, original_height, aspect_ratio=aspect_ratio)

    # --- New Strategy: Per-Scene Analysis ---
    print("\n   🤖 Step 3: Analyzing Scenes for Strategy (Single vs Group)...")
    scene_strategies = analyze_scenes_strategy(input_video, scenes)
    # scene_strategies is a list of 'TRACK' or 'General' corresponding to scenes

    print("\n   ✂️ Step 4: Processing video frames...")
    
    # Raw BGR frames stream down a pipe into ffmpeg, which encodes the silent
    # video track; the audio is muxed back in afterwards.
    encoder = subprocess.Popen(
        ['ffmpeg', '-y',
         '-f', 'rawvideo', '-pix_fmt', 'bgr24',
         '-video_size', f'{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}',
         '-framerate', str(fps), '-i', 'pipe:0',
         *video_encode_args(QUALITY_FAST), '-an', silent_video_path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    reader = cv2.VideoCapture(input_video)
    frame_total = int(reader.get(cv2.CAP_PROP_FRAME_COUNT))
    
    frame_number = 0
    current_scene_index = 0

    # Pre-calculate scene boundaries
    scene_boundaries = []
    for s_start, s_end in scenes:
        scene_boundaries.append((s_start.get_frames(), s_end.get_frames()))

    # Global tracker for single-person shots
    speaker_tracker = SpeakerTracker(cooldown_frames=30)

    # Per-stage wall time (server-side diagnostics; hidden from cloud logs).
    stage_seconds = {'detect': 0.0, 'write': 0.0}
    loop_started = time.time()

    with tqdm(total=frame_total, desc="   Processing", file=sys.stdout) as pbar:
        while reader.isOpened():
            ret, frame = reader.read()
            if not ret:
                break

            # Update Scene Index
            if current_scene_index < len(scene_boundaries):
                start_f, end_f = scene_boundaries[current_scene_index]
                if frame_number >= end_f and current_scene_index < len(scene_boundaries) - 1:
                    current_scene_index += 1

            # Determine Strategy for current frame based on scene
            current_strategy = scene_strategies[current_scene_index] if current_scene_index < len(scene_strategies) else 'TRACK'

            # Apply Strategy
            if current_strategy == 'GENERAL':
                # "Plano General" -> Blur Background + Fit Width
                output_frame = create_general_frame(frame, OUTPUT_WIDTH, OUTPUT_HEIGHT)

                # Reset cameraman/tracker so they don't drift while inactive
                cameraman.current_center_x = original_width / 2
                cameraman.target_center_x = original_width / 2

            else:
                # "Single Speaker" -> Track & Crop

                # Detect every Nth frame for performance (cameraman smooths in
                # between); the much heavier YOLO fallback gets its own stride.
                # Snap camera on scene change to avoid panning from previous scene position
                is_scene_start = (frame_number == scene_boundaries[current_scene_index][0])
                if is_scene_start and SCENE_CUT_RESET:
                    speaker_tracker.reset()
                    cameraman.begin_scene()

                # Always detect on a cut, whatever the stride: the new shot's
                # subject has to be found before the first frame is framed.
                if frame_number % DETECT_STRIDE == 0 or (is_scene_start and SCENE_CUT_RESET):
                    t_det = time.time()
                    candidates = detect_face_candidates(frame)
                    target_box = speaker_tracker.get_target(candidates, frame_number, original_width)
                    if target_box:
                        cameraman.update_target(target_box)
                    elif frame_number % YOLO_FALLBACK_STRIDE == 0 or (is_scene_start and SCENE_CUT_RESET):
                        person_box = detect_person_yolo(frame)
                        if person_box:
                            cameraman.update_target(person_box)
                    stage_seconds['detect'] += time.time() - t_det

                x1, y1, x2, y2 = cameraman.get_crop_box(force_snap=is_scene_start)

                # Crop
                if y2 > y1 and x2 > x1:
                    cropped = frame[y1:y2, x1:x2]
                    output_frame = cv2.resize(cropped, (OUTPUT_WIDTH, OUTPUT_HEIGHT), interpolation=cv2.INTER_LINEAR)
                else:
                    output_frame = cv2.resize(frame, (OUTPUT_WIDTH, OUTPUT_HEIGHT), interpolation=cv2.INTER_LINEAR)

            t_wr = time.time()
            encoder.stdin.write(output_frame.tobytes())
            stage_seconds['write'] += time.time() - t_wr
            frame_number += 1
            pbar.update(1)

    loop_total = time.time() - loop_started
    other = loop_total - stage_seconds['detect'] - stage_seconds['write']
    print(f"\n   ⏱️ Frame loop: {loop_total:.1f}s total — "
          f"detect {stage_seconds['detect']:.1f}s, "
          f"encode-wait {stage_seconds['write']:.1f}s, "
          f"decode+render {other:.1f}s ({frame_number} frames)")

    encoder.stdin.close()
    encode_log = encoder.stderr.read().decode()
    encoder.wait()
    reader.release()

    if encoder.returncode != 0:
        print("\n   ❌ FFmpeg frame processing failed.")
        print("   Stderr:", encode_log)
        return False

    print("\n   🔊 Step 5: Extracting audio...")
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-i', input_video, '-vn', '-c:a', 'copy', audio_track_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        print("\n   ❌ Audio extraction failed (maybe no audio?). Proceeding without audio.")

    print("\n   ✨ Step 6: Merging...")
    mux = ['ffmpeg', '-y', '-i', silent_video_path]
    if os.path.exists(audio_track_path):
        mux += ['-i', audio_track_path]
    mux += ['-c', 'copy', *METADATA_SCRUB, '-movflags', '+faststart', final_output_video]
    try:
        subprocess.run(mux, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f"   ✅ Clip saved to {final_output_video}")
    except subprocess.CalledProcessError as e:
        print("\n   ❌ Final merge failed.")
        print("   Stderr:", e.stderr.decode())
        return False

    for leftover in (silent_video_path, audio_track_path):
        if os.path.exists(leftover):
            os.remove(leftover)

    return True

# --- Transcript checkpoint (survive a redeploy without paying twice) ---------
# A job interrupted by a container restart is re-run from its resume manifest
# (app.py) with the SAME output directory. Transcription is the slow, paid part
# of the pipeline that ran before the interruption, so the finished transcript
# is left in the job directory and picked up by the re-run instead of
# transcribing again. Same shape and same validation as --transcript.
TRANSCRIPT_CHECKPOINT = ".transcript_checkpoint.json"


def _checkpoint_source_key(input_video, duration):
    """What ties a checkpoint to ONE source. Not the path: a resumed cloud job
    re-downloads to the same name, and the CLI may be pointed at a different
    file in a directory where an earlier run died. Name plus duration is what
    both the server and a careful CLI user keep stable across the two runs."""
    return {"name": os.path.basename(input_video), "duration": round(float(duration), 1)}


def save_transcript_checkpoint(output_dir, transcript, input_video, duration):
    """Best effort: a failure here must never fail the job."""
    try:
        payload = {"source": _checkpoint_source_key(input_video, duration),
                   "transcript": transcript}
        with open(os.path.join(output_dir, TRANSCRIPT_CHECKPOINT), "w") as f:
            json.dump(payload, f)
    except Exception as e:
        print(f"⚠️ Could not save transcript checkpoint: {e}")


def load_transcript_checkpoint(output_dir, input_video, duration):
    """The transcript an interrupted run left behind for THIS source, or None.

    A checkpoint for a different source (a CLI run that died, then a new video
    processed in the same directory) is ignored, not reused."""
    path = os.path.join(output_dir, TRANSCRIPT_CHECKPOINT)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            payload = json.load(f)
        source = payload.get("source") or {}
        expected = _checkpoint_source_key(input_video, duration)
        if source.get("name") != expected["name"] \
                or abs(float(source.get("duration", -1)) - expected["duration"]) > 0.5:
            print("⏭️ Transcript checkpoint belongs to another source — ignoring it.")
            return None
        transcript = payload.get("transcript") or {}
        if not transcript.get("segments"):
            raise ValueError("checkpoint has no segments")
        return transcript
    except Exception as e:
        print(f"⚠️ Ignoring unusable transcript checkpoint ({e}).")
        return None


def clear_transcript_checkpoint(output_dir):
    try:
        os.remove(os.path.join(output_dir, TRANSCRIPT_CHECKPOINT))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️ Could not remove transcript checkpoint: {e}")


def transcribe_video(video_path):
    print(f"🎙️ Transcribing audio… (Whisper, this can take a few minutes on long videos)")
    from transcribe_backends import transcribe_media

    transcript = transcribe_media(video_path)

    print(f"✅ Transcript ready — language '{transcript['language']}', {len(transcript['segments'])} segments")
    for segment in transcript['segments']:
        # Full transcript dump is diagnostics, not progress (progress % comes
        # from transcribe_backends itself).
        _dbg(f"   [{segment['start']:.2f}s -> {segment['end']:.2f}s] {segment['text']}")

    return transcript

def _run_gemini_stage(client, model_name, prompt, schema):
    """One schema-enforced Gemini call with transient-error backoff.
    Returns (parsed_dict, cost_analysis)."""
    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
    )
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config=config)
            # Policy blocks are deterministic — retrying only burns quota and
            # time, and the user deserves the real reason instead of a generic
            # "empty response" (prod 23-jul: PROHIBITED_CONTENT on every try).
            gemini_worker.raise_if_blocked(response)
            # Parsing lives inside the retry loop on purpose: Gemini sometimes
            # returns 200 with an empty body, which raises here rather than at
            # the call. Retrying that recovered every occurrence seen in prod
            # (22-jul-2026) — the same payload succeeds on the next attempt.
            parsed_obj = getattr(response, "parsed", None)
            if parsed_obj is not None:
                parsed = parsed_obj.model_dump() if hasattr(parsed_obj, "model_dump") else parsed_obj
            else:
                parsed = gemini_worker._parse_json_response_text(
                    gemini_worker._get_response_text(response))
            return parsed, gemini_worker._calculate_cost_analysis(response, model_name)
        except gemini_worker.GeminiBlockedError:
            raise  # deterministic policy block — never retry
        except Exception as e:
            msg = str(e)
            transient = any(tok in msg for tok in (
                '503', 'UNAVAILABLE', '429', 'RESOURCE_EXHAUSTED',
                '500', 'INTERNAL', 'overloaded', 'Deadline',
                'empty response body', 'did not contain a JSON object',
                'Failed to parse Gemini JSON response'))
            if attempt == max_attempts or not transient:
                raise
            wait = 5 * (2 ** (attempt - 1))
            print(f"⚠️ Gemini transient error (attempt {attempt}/{max_attempts}), retrying in {wait}s: {msg[:150]}")
            time.sleep(wait)



class VODMetadataResponse(BaseModel):
    vod_title: str
    vod_description: str


def _deep_config():
    """Resolve Deep independently from the main Clip Job provider."""
    job_provider = _get_ai_provider()
    selected = (os.environ.get("DEEP_AI_PROVIDER") or "same").lower().strip()

    if selected in ("", "same", "same_as_job", "same-as-job"):
        provider = job_provider
    elif selected in ("gemini", "openai"):
        provider = selected
    else:
        provider = job_provider

    if provider == "gemini":
        return {
            "provider": "gemini",
            "model": os.environ.get("DEEP_GEMINI_MODEL") or _get_gemini_model(),
            "api_key": os.environ.get("DEEP_GEMINI_API_KEY") or _get_gemini_api_key(),
            "base_url": None,
            "input_mode": "native_video",
        }

    return {
        "provider": "openai",
        "model": os.environ.get("DEEP_OPENAI_MODEL") or _get_openai_model(),
        "api_key": (os.environ.get("DEEP_OPENAI_API_KEY")
                     if os.environ.get("DEEP_OPENAI_API_KEY") is not None
                     else _get_openai_api_key()),
        "base_url": os.environ.get("DEEP_OPENAI_BASE_URL") or _get_openai_base_url(),
        "input_mode": "frames",
    }


def _format_full_transcript(transcript_result):
    """Format the complete Whisper transcript with absolute timestamps."""
    lines = []
    for seg in transcript_result.get("segments", []):
        start = float(seg.get("start", 0) or 0)
        end = float(seg.get("end", start) or start)
        text = str(seg.get("text", "")).strip()
        if text:
            lines.append(f"[{start:07.2f}-{end:07.2f}] {text}")
    if lines:
        return "\n".join(lines)
    return str(transcript_result.get("text") or "").strip()


def _clip_metadata_context(shorts):
    rows = []
    for i, clip in enumerate(shorts or [], 1):
        rows.append({
            "index": i,
            "start": round(float(clip.get("start", 0) or 0), 2),
            "end": round(float(clip.get("end", 0) or 0), 2),
            "score": int(float(clip.get("viral_score", clip.get("predicted_score", 0)) or 0)),
            "title": str(clip.get("video_title_for_youtube_short") or clip.get("title") or "").strip(),
            "description": str(clip.get("video_description_for_tiktok") or clip.get("description") or "").strip(),
            "reason": str(clip.get("reason") or "").strip(),
        })
    return json.dumps(rows, ensure_ascii=False)


def _load_game_profile_json(game_profile_id):
    if not (HAS_GAME_PROFILE_SUPPORT and game_profile_id):
        return "{}"
    try:
        from cloud.local_game_profiles import LocalGameProfileRepository
        profile = LocalGameProfileRepository().get_profile(game_profile_id)
        if not profile:
            return "{}"
        ai = profile.get("ai_analysis") or {}
        data = {
            "game_title": profile.get("game_title", ""),
            "game_type": profile.get("game_type") or ai.get("game_type", ""),
            "steam_description": profile.get("steam_description", ""),
            "custom_notes": profile.get("custom_notes") or profile.get("custom_description", ""),
            "active_weights": profile.get("active_weights") or profile.get("recommended_weights") or {},
            "ai_analysis": ai,
        }
        return json.dumps(data, ensure_ascii=False)
    except Exception as exc:
        print(f"⚠️ VOD GameProfile load failed: {exc}")
        return "{}"


def _sec_to_mmss(sec: float) -> str:
    try:
        s = float(sec)
    except Exception:
        return str(sec)
    if s < 0:
        s = 0
    m = int(s // 60)
    sec_r = int(round(s % 60))
    if sec_r == 60:
        m += 1
        sec_r = 0
    return f"{m:02d}:{sec_r:02d}"


def _normalize_vod_description(desc: str) -> str:
    import re
    if not desc:
        return desc
    # Convert absolute seconds like 0361.80, 1075.97, 1698.71 that appear as timestamps
    # Common in VOD descriptions: "0361.80 - Label" or "361.80 - Label" or "2056.36 - Label"
    # Only convert when seconds are followed by " - " / " – " / " — " to avoid scores
    def repl_sec(m):
        try:
            val = float(m.group(1))
            if 0 <= val < 10800:  # <3h plausible video time
                return _sec_to_mmss(val) + m.group(2)
        except Exception:
            pass
        return m.group(0)
    # seconds with decimal before " - "
    desc = re.sub(r'\b0*(\d{1,4}\.\d{1,2})\b(\s*[-–—]\s)', repl_sec, desc)
    # Also handle "at 361.80" style without dash? less common but cover " 361.80 " as standalone timestamp at line start
    # Second pass for any remaining 4-digit seconds that are clearly timestamps (not 3.14 etc)
    return desc


def _generate_vod_metadata(transcript_result, language, game_profile_id, shorts):
    """Generate VOD metadata independently of Deep clip discovery.

    For normal-length transcripts the full timestamped transcript is sent to the
    selected job LLM. For very long transcripts, preserve full coverage with a
    hierarchical pass: every chunk is analyzed, then a final synthesis receives
    all chunk findings plus the complete selected-clip context.
    """
    full_transcript = _format_full_transcript(transcript_result)
    if not full_transcript:
        return {"vod_title": "VOD Highlights", "vod_description": "Best moments from this stream."}

    game_json = _load_game_profile_json(game_profile_id)
    clips_json = _clip_metadata_context(shorts)
    provider = _get_ai_provider()
    if provider == "gemini":
        model = _get_gemini_model()
        api_key = _get_gemini_api_key()
        base_url = None
    else:
        model = _get_openai_model()
        api_key = _get_openai_api_key()
        base_url = _get_openai_base_url()

    def call(prompt):
        provider_obj = ai_provider.create_ai_provider(
            provider, model, api_key=api_key, base_url=base_url,
            temperature=0.2, max_tokens=1200, timeout=60,
        )
        try:
            result = provider_obj.generate_content(prompt, schema=VODMetadataResponse)
        except Exception as e:
            # LM Studio qwen often ignores json_schema and dumps hashtags outside JSON — retry without schema and extract
            msg = str(e)
            if "Failed to parse JSON" in msg or "did not contain a JSON" in msg:
                import re as _re_json, json as _js2
                try:
                    # retry raw without schema to get free-form text
                    provider_obj2 = ai_provider.create_ai_provider(
                        provider, model, api_key=api_key, base_url=base_url,
                        temperature=0.2, max_tokens=1200, timeout=60,
                    )
                    result2 = provider_obj2.generate_content(prompt + "\n\nReturn ONLY the JSON object {\"vod_title\":\"...\",\"vod_description\":\"...\"} with no extra text.", schema=None)
                    raw = ""
                    if isinstance(result2, dict):
                        raw = result2.get("text") or result2.get("response", {}).get("text", "") if isinstance(result2.get("response"), dict) else ""
                        if not raw:
                            raw = str(result2)[:8000]
                    # extract first { ... } block
                    m = _re_json.search(r'\{.*\}', raw, re.DOTALL) if raw else None
                    if m:
                        parsed = _js2.loads(m.group(0))
                        return VODMetadataResponse(**parsed).model_dump()
                except Exception:
                    pass
            raise
        parsed = result.get("response") or {}
        return VODMetadataResponse(**parsed).model_dump()

    base_rules = (
        "Return ONLY valid JSON matching the schema — no markdown, no hashtags outside the JSON values. All hashtags must be INSIDE the two string fields. "
        f"You are the VOD title and description editor for a gaming livestream. "
        f"The transcript language is {language}. ALL user-facing text must be in {language}. "
        "Use the full transcript as evidence, not as copy to rewrite verbatim. "
        "Identify the stream's real themes, funniest/most intense developments and game context. "
        "Use the supplied selected clips and their real timestamps as anchors, but never invent timestamps. "
        "vod_title: 6-10 words, maximum 100 characters, curiosity-driven and natural. "
        "vod_description: 3-5 readable sentences, then exactly 8-10 real highlight timestamps "
        "in mm:ss format (e.g., 06:01, 17:55, 42:30) with a short label, ordered by likely viewer interest, then 3-5 relevant hashtags. "
        "CRITICAL TIMESTAMP FORMAT: Use ONLY mm:ss for viewer-facing timestamps. NEVER output absolute seconds like 361.80, 1075.97 or 1698.71. Seconds are for internal FFMPEG only, not for humans browsing Twitch/YouTube. "
        "Do not mention Whisper, transcription, AI, or the fact that a transcript was provided. "
        "Do not dump the transcript or make a generic chronological summary."
    )

    try:
        # Qwen/LM Studio and older compatible endpoints often have much smaller
        # practical contexts.  Keep every portion represented instead of dropping
        # the tail of a long VOD.
        if len(full_transcript) <= 50000:
            prompt = (
                base_rules + "\n\nGAME PROFILE:\n" + game_json +
                "\n\nSELECTED CLIPS:\n" + clips_json +
                "\n\nFULL TIMESTAMPED TRANSCRIPT:\n" + full_transcript +
                "\n\nReturn ONLY the JSON object matching the schema."
            )
            res = call(prompt)
            if isinstance(res, dict) and res.get('vod_description'):
                res['vod_description'] = _normalize_vod_description(res['vod_description'])
            return res

        chunk_size = 16000
        findings = []
        for idx, start in enumerate(range(0, len(full_transcript), chunk_size), 1):
            chunk = full_transcript[start:start + chunk_size]
            prompt = (
                "Analyze this chronological transcript chunk from a gaming VOD. "
                f"Language={language}. Preserve concrete timestamps and memorable moments. "
                "Return concise JSON with: moments (timestamp, label, why_it_matters) and themes. "
                "Do not invent information.\n\n" + chunk
            )
            try:
                raw_provider = ai_provider.create_ai_provider(
                    provider, model, api_key=api_key, base_url=base_url,
                    temperature=0.3, max_tokens=700, timeout=60,
                )
                raw = raw_provider.generate_content(prompt, schema=None)
                parsed = raw.get("response") if isinstance(raw, dict) else None
                if parsed is None:
                    text = raw.get("text", "") if isinstance(raw, dict) else str(raw)
                    parsed = {"text": text}
                findings.append({"chunk": idx, "analysis": parsed})
            except Exception as exc:
                print(f"⚠️ VOD transcript chunk {idx} failed: {exc}")

        synthesis_prompt = (
            base_rules + "\n\nGAME PROFILE:\n" + game_json +
            "\n\nSELECTED CLIPS:\n" + clips_json +
            "\n\nCHRONOLOGICAL ANALYSIS COVERING THE ENTIRE TRANSCRIPT:\n" +
            json.dumps(findings, ensure_ascii=False) +
            "\n\nSynthesize the final VOD metadata now. Return ONLY the JSON object matching the schema."
        )
        res2 = call(synthesis_prompt)
        if isinstance(res2, dict) and res2.get('vod_description'):
            res2['vod_description'] = _normalize_vod_description(res2['vod_description'])
        return res2
    except Exception as exc:
        print(f"⚠️ VOD metadata generation failed ({type(exc).__name__}): {exc}")
        # One provider-level fallback: the Deep provider can still write metadata,
        # but discovery remains independent and does not affect clip selection.
        deep = _deep_config()
        if deep["provider"] != provider:
            try:
                provider_obj = ai_provider.create_ai_provider(
                    deep["provider"], deep["model"], api_key=deep["api_key"],
                    base_url=deep["base_url"], temperature=0.4, max_tokens=900, timeout=60,
                )
                prompt = (
                    base_rules + "\n\nGAME PROFILE:\n" + game_json +
                    "\n\nSELECTED CLIPS:\n" + clips_json +
                    "\n\nFULL TIMESTAMPED TRANSCRIPT:\n" + full_transcript[:50000] +
                    "\n\nReturn ONLY the JSON object matching the schema."
                )
                result = provider_obj.generate_content(prompt, schema=VODMetadataResponse)
                _res_wrap = VODMetadataResponse(**(result.get("response") or {})).model_dump()
                if _res_wrap.get('vod_description'):
                    _res_wrap['vod_description'] = _normalize_vod_description(_res_wrap['vod_description'])
                return _res_wrap
            except Exception as deep_exc:
                print(f"⚠️ VOD metadata fallback failed: {deep_exc}")
        return {"vod_title": "VOD Highlights", "vod_description": "Best moments from this gaming stream. #gaming"}


def _run_stage_split(client, model_name, items, build_prompt, schema, key, costs, label):
    """Run a Gemini stage over ``items``; on a policy block, bisect.

    Google's prompt filter (PROHIBITED_CONTENT) fires on some COMBINATIONS of
    transcript windows that pass individually (27-aug-2026: windows 5+6 of a
    software walkthrough blocked 3/3, each alone fine, all three models).
    A block is deterministic for a given prompt, so instead of failing the
    job the batch is split in halves until the offending combination is
    isolated; a single item that still blocks is dropped with a log line.
    Returns the merged list found under ``key`` in each response."""
    if not items:
        return []
    prompt = build_prompt(items)
    try:
        parsed, cost = _run_gemini_stage(client, model_name, prompt, schema)
        if cost:
            costs.append(cost)
        return list(parsed.get(key) or [])
    except gemini_worker.GeminiBlockedError as e:
        if len(items) == 1:
            print(f"   🚫 {label}: Gemini blocked window {items[0].get('id')} on its own; skipping it ({e})")
            return []
        mid = len(items) // 2
        print(f"   🚫 {label}: Gemini blocked a batch of {len(items)}; retrying as {mid} + {len(items) - mid}")
        return (_run_stage_split(client, model_name, items[:mid], build_prompt, schema, key, costs, label)
                + _run_stage_split(client, model_name, items[mid:], build_prompt, schema, key, costs, label))


def get_viral_clips(transcript_result, video_duration, game_profile_id=None, user_id=None, video_path=None):
    """Two-pass clip selection: score transcript windows, then detail the best.

    Windowing gives even coverage on long videos (a single call over the whole
    transcript clusters picks near the start), and the cheap scoring pass keeps
    the expensive detail reasoning focused on the shortlist. Cuts are snapped to
    word boundaries so clips don't start/end mid-word.
    """
    _provider = _get_ai_provider()
    if _provider == "openai":
        _label = "OpenAI"
        model_name = _get_openai_model()
        api_key = _get_openai_api_key()
        base_url = _get_openai_base_url()
    else:
        _label = "Gemini"
        model_name = _get_gemini_model()
        api_key = _get_gemini_api_key()
        base_url = None
        if not api_key:
            print("❌ GEMINI_API_KEY not configured.")
            return None

    language = str(transcript_result.get('language') or 'unknown')
    print(f"🤖 Analyzing with {_label} (2-pass: score → detail)...")
    if _provider == "openai":
        print(f"🤖 Model: {model_name} | provider={_provider} | base={base_url} | api_key={'configured' if api_key else 'empty'} | language={language}")
    else:
        print(f"🤖 Model: {model_name} | provider={_provider} | api_key=configured | language={language}")

    # Full word list — ground truth for snapping cut points.
    words = []
    for segment in transcript_result['segments']:
        for word in segment.get('words', []):
            words.append({'w': word['word'], 's': word['start'], 'e': word['end']})

    # Short VOD shortcut: 120s or less → whole clip as the candidate (user request).
    # Consider the whole clip as chosen candidate so it still gets captions + titles.
    # Safe float parse so non-numeric duration never crashes get_viral_clips for all clips.
    _short_dur = None
    try:
        _short_dur = float(video_duration) if video_duration is not None else None
    except Exception:
        _short_dur = None
    if _short_dur is not None and _short_dur <= 120 and _short_dur > 0:
        print(f"📏 Short video ({_short_dur:.1f}s ≤ 120s) — whole clip as candidate (detail → title/description like approved)")
        min_secs, max_secs = clip_duration_bounds()
        try:
            ns, ne = snap_clip_to_words(0.0, _short_dur, words, _short_dur,
                                        min_duration=min_secs, max_duration=max_secs)
        except Exception:
            ns, ne = 0.0, _short_dur
        if ne - ns < max(5.0, _short_dur * 0.5):
            ns, ne = 0.0, _short_dur
        # Build single window for detail so it gets LLM title/description like approved — handled like normal VOD
        # Build game profile JSON like normal flow (so title/description are game-aware)
        _wc_game_json = "{}"
        _wc_active_weights = None
        try:
            if HAS_GAME_PROFILE_SUPPORT and game_profile_id:
                from cloud.local_game_profiles import LocalGameProfileRepository as _L
                _r = _L()
                _g = _r.get_profile(game_profile_id)
                if _g:
                    _wc_active_weights = _g.get('active_weights') or _g.get('recommended_weights') or {}
                    _ai = _g.get('ai_analysis') or {}
                    _gd = {"game_title": _g.get('game_title',''), "game_type": _g.get('game_type','') or _ai.get('game_type',''), "steam_description": (_g.get('steam_description') or '')[:400], "custom_notes": (_g.get('custom_description') or '')[:400], "active_weights": _wc_active_weights, "ai_analysis": _ai}
                    _wc_game_json = json.dumps(_gd, ensure_ascii=False)[:2500]
        except Exception as _ge:
            print(f"⚠️ Whole-clip game profile load failed: {_ge}")
            _wc_game_json = "{}"
        try:
            whole_text = " ".join(w.get('w','') for w in words).strip()[:4000] if words else ""
            if not whole_text:
                whole_text = " ".join(seg.get('text','') for seg in transcript_result.get('segments', []))[:4000]
            # Phase 5 enriched even for whole-clip (word timestamps + audio/scene)
            try:
                _wc_ws = "; ".join(f"{ww['w']}@{ww['s']:.2f}" for ww in words[:28]) if words else "none"
                _wc_ev = ", ".join(f"{ee.get('timestamp',0):.1f}s:{ee.get('type','')}" for ee in (cheap_events or [])[:10]) if 'cheap_events' in locals() and cheap_events else "none"
            except Exception:
                _wc_ws = "none"
                _wc_ev = "none"
            payload = [{"id": "whole_clip", "start": float(ns), "end": float(ne), "text": whole_text, "surrounding_transcript": "none (whole video)", "word_timestamps": _wc_ws, "audio_events": _wc_ev, "scene_boundaries": _wc_ev}]
            _detail_prompt = gemini_worker.DETAIL_PROMPT_TEMPLATE.format(
                video_duration=_short_dur, language=language,
                game_profile_json=_wc_game_json,
                min_clips=1, max_clips=1,
                min_secs=min_secs, max_secs=max_secs,
                windows_json=json.dumps(payload, ensure_ascii=False))
            _provider = ai_provider.create_ai_provider(_provider, model_name, api_key=api_key, base_url=base_url, temperature=AI_TEMPERATURE, max_tokens=AI_MAX_TOKENS, timeout=min(30, AI_TIMEOUT))
            _res = _provider.generate_content(_detail_prompt, gemini_worker.DetailResponse)
            _parsed = _res.get("response") or {}
            _shorts = _parsed.get("shorts") or []
            if _shorts:
                for s in _shorts:
                    try:
                        s_ns, s_ne = snap_clip_to_words(s.get("start", ns), s.get("end", ne), words, _short_dur,
                                                        min_duration=min_secs, max_duration=max_secs)
                        s["start"], s["end"] = float(s_ns), float(s_ne)
                    except Exception:
                        s["start"], s["end"] = float(ns), float(ne)
                    # Ensure description exists like approved
                    if not s.get("description"):
                        s["description"] = s.get("reason") or "Full clip selected (≤120s) — ready for captions"
                print(f"✅ Whole-clip detail got title: {_shorts[0].get('title','')[:60]}")
                _vod_meta = _generate_vod_metadata(transcript_result, language, game_profile_id, _shorts)
                return {"shorts": _shorts, **_vod_meta}
            else:
                print(f"⚠️ Whole-clip detail returned no shorts, using generic")
        except Exception as _e:
            print(f"⚠️ Whole-clip detail failed, using generic title: {_e}")
            import traceback as _tb
            print(_tb.format_exc()[:800])
        # Fallback title like approved: from transcript + game profile, not generic
        _fb_title = "Viral short video"
        _fb_desc = "Full clip selected (≤120s) — ready for captions"
        try:
            _txt = " ".join(w.get('w','') for w in words).strip()
            _game = ""
            try:
                if '_wc_game_json' in locals() and _wc_game_json and _wc_game_json != "{}":
                    import json as _js
                    _gj = _js.loads(_wc_game_json)
                    _game = _gj.get('game_title') or ""
            except Exception:
                _game = ""
            if _txt:
                _words = _txt.split()[:8]
                _base = " ".join(_words).strip()
                if len(_base) > 60:
                    _base = _base[:60].rsplit(' ', 1)[0]
                _fb_title = (_base[:50] + (" — " + _game if _game else ""))[:70] if _base else _fb_title
                _fb_desc = (_txt[:120].rsplit(' ',1)[0] + "…") if len(_txt) > 120 else _txt
                if _game and _game.lower() not in _fb_desc.lower():
                    _fb_desc = f"{_game}: {_fb_desc}"
        except Exception:
            pass
        shorts = [{"start": float(ns), "end": float(ne), "title": _fb_title, "description": _fb_desc, "viral_score": 10.0, "reason": "Short video — whole clip selected (≤120s), auto-approved"}]
        _vod_meta = _generate_vod_metadata(transcript_result, language, game_profile_id, shorts)
        return {"shorts": shorts, **_vod_meta}

    try:
        # Scoring windows must be able to CONTAIN a max-length clip (the detail
        # prompt keeps clips inside their candidate window), so scale them with
        # the requested band — a user asking for 60-90s clips on the default
        # 90s windows would get clips squeezed against the window walls.
        min_secs, max_secs = clip_duration_bounds()
        windows = build_transcript_windows(
            transcript_result, video_duration,
            window_seconds=max(90, int(max_secs * 1.5)))
        print(f"🪟 Built {len(windows)} candidate window(s) from the transcript.")

        # --- Phase 4: Cheap multimodal candidate seeding ---
        # Merge transcript windows with cheap event windows so a viral moment
        # with meaningless transcript (silence -> jumpscare -> scream) still
        # becomes a candidate. Each enhancement is toggleable.
        cheap_events = []
        _deep_candidates = []
        try:
            from cheap_events import extract_cheap_events, events_to_candidate_windows
            cheap_enabled = ENABLE_SCENE_DETECTION or ENABLE_AUDIO_EVENTS or ENABLE_CHEAP_VISUAL
            if cheap_enabled and video_path and os.path.exists(video_path):
                cheap_events = extract_cheap_events(
                    video_path, transcript_result,
                    enable_scene=ENABLE_SCENE_DETECTION,
                    enable_audio=ENABLE_AUDIO_EVENTS,
                    enable_visual=ENABLE_CHEAP_VISUAL,
                )
                if cheap_events:
                    if DEBUG_LOGS:
                        timeline = ", ".join(f"{e['timestamp']:.1f}s {e['type']}" for e in cheap_events[:20])
                        print(f"   📡 Cheap events ({len(cheap_events)}): {timeline}" + (" ..." if len(cheap_events)>20 else ""))
                        print(f"   Toggles: scene={ENABLE_SCENE_DETECTION} audio={ENABLE_AUDIO_EVENTS} visual={ENABLE_CHEAP_VISUAL}")
                        # Verbose: per-type breakdown
                        from collections import Counter
                        cnt = Counter(e['type'] for e in cheap_events)
                        print(f"   📊 Breakdown: {dict(cnt)} | audio dominance check: {'⚠️ many audio' if cnt.get('audio_activity',0)>500 else 'ok'}")
                    else:
                        print(f"   📡 Cheap events: {len(cheap_events)} detected (scene={ENABLE_SCENE_DETECTION} audio={ENABLE_AUDIO_EVENTS} visual={ENABLE_CHEAP_VISUAL})")
            # E: Deep full-VOD scan with qwen3-vl-8b (toggle ENABLE_DEEP_ANALYSIS=1, OFF by default)
            _deep_candidates = []
            print(f"   [deep gate] ENABLE={ENABLE_DEEP_ANALYSIS} HAS={HAS_SEMANTIC_ANALYZER} video_path={bool(video_path)} dur={float(video_duration) if video_duration else 0:.1f} is_gemini={_get_ai_provider()=='gemini'}")
            if ENABLE_DEEP_ANALYSIS and HAS_SEMANTIC_ANALYZER and video_path and float(video_duration) > 60:
                try:
                    _deep_cfg = _deep_config()
                    _is_gemini = _deep_cfg["input_mode"] == "native_video"
                    _deep_provider_for_log = _deep_cfg["provider"]
                    # Gemini 16 frames (API limit), qwen 24 (16 uniform + 8 peak) — catches HUD 81
                    _peak_times = []
                    try:
                        _scream_peaks = [e["timestamp"] for e in cheap_events if e["type"] in ("scream","sudden_loudness","loud_spike")][:16]
                        _peak_times = sorted(set(_scream_peaks))[:8]
                    except Exception:
                        _peak_times = []
                    _deep_frames = None
                    _used_native_video = False
                    _deep_nframes = 16 if _is_gemini else 12
                    if _is_gemini:
                        # Always try native via low-res proxy (like autoshorts) — proxy shrinks 1GB -> ~15MB so size check is on proxy, not original
                        try:
                            import mimetypes
                            _mime, _ = mimetypes.guess_type(video_path)
                            _mime = _mime or "video/mp4"
                            _used_native_video = True
                        except Exception:
                            _used_native_video = False
                    if not _used_native_video:
                        _deep_frames = extract_frames_from_window(video_path, 0, float(video_duration), num_frames=_deep_nframes, peak_times=_peak_times if _peak_times else None)
                    if _used_native_video or (_deep_frames and len(_deep_frames) >= 8):
                        _ct = "\n".join(f"{e['timestamp']:.1f}s {e['type']}" for e in cheap_events[:30]) if cheap_events else "none"
                        _tx = " ".join(w.get('w','') for w in words) if words else ""  # full transcript for deep (user requested whole VOD)
                        # Game profile context for deep (same as scoring, so deep moments are game-aware)
                        _deep_gp = ""
                        try:
                            if game_profile_id:
                                from cloud.local_game_profiles import LocalGameProfileRepository as _LGPRd
                                _rpd = _LGPRd()
                                _gpd = _rpd.get_profile(game_profile_id)
                                if _gpd:
                                    _ai = _gpd.get('ai_analysis') or {}
                                    import json as _js_gp
                                    _deep_gp = _js_gp.dumps({"game_title": _gpd.get('game_title'), "game_type": _gpd.get('game_type') or _ai.get('game_type'), "custom_notes": _gpd.get('custom_notes') or "", "active_weights": _gpd.get('active_weights') or _gpd.get('recommended_weights') or _wc_active_weights or {}, "ai_analysis": _ai}, ensure_ascii=False)
                        except Exception:
                            _deep_gp = ""
                        _dur = float(video_duration)
                        if _deep_frames is not None and len(_deep_frames) > 0:
                            _step = _dur / len(_deep_frames)
                            _timestamps_note = ", ".join(f"{i*_step:.0f}s" for i in range(min(8, len(_deep_frames)))) + ("..." if len(_deep_frames)>8 else "")
                            _frame_note = f"Watch these {len(_deep_frames)} frames sampled across the VOD (approx timestamps: {_timestamps_note}, spread 0-{_dur:.0f}s). "
                        else:
                            _timestamps_note = "full video"
                            _frame_note = "Watch the full video continuously (native video). "
                        _deep_prompt = (
                            f"You are a viral clip hunter for TikTok/Reels/Shorts. {_frame_note}"
                            f"Cheap events timeline (scene/audio/visual spikes):\n{_ct}\n"
                            f"Transcript (full VOD): {_tx}\n"
                            f"Game profile context:{_deep_gp if _deep_gp else ' none (universal)'}\n"
                            f"Use active_weights to MODULATE the viral selection, not replace it. CUSTOM NOTES HAVE PRIORITY: when present, treat them as explicit user selection instructions. Strongly prioritize moments matching the custom notes before applying generic viral heuristics. Among moments that satisfy the custom notes, rank by viral potential. Do not ignore or override a custom note unless the requested moment does not exist in the VOD. Do not force a weak or irrelevant clip merely to satisfy a custom note when no genuine match exists. Cheap events and profile key moments are evidence only: a loudness spike, scene change, transcript phrase, or profile key moment is NOT automatically a good clip. Judge the actual video first.\n\n"
                            f"SELECTION: return 2-6 clips only. Select only genuinely strong standalone short-form moments; do not add weaker clips just to fill the range. Rank the returned clips from HIGHEST to LOWEST viral potential. The array order and score must agree with that ranking. Viral potential is primary; use game-profile weights as a bias among otherwise viable candidates. Evaluate the whole VOD before finalizing so early discoveries do not anchor the ranking.\n\n"
                            f"TIMING: prefer HOOK → SETUP → ESCALATION → PEAK → REACTION/PAYOFF when applicable. Start earlier when setup or anticipation improves the moment, but avoid dead time. Before choosing the end timestamp, inspect what happens immediately AFTER the peak. Keep the clip going when there is laughter, screaming, reaction, realization, follow-up dialogue, consequence, visual payoff, or another beat that makes the moment funnier, clearer, or more satisfying. Do NOT end immediately at the peak just because the main action stopped. End when the entertainment/payoff naturally finishes. Never cut mid-word, mid-sentence, before reaction/payoff, or immediately after the peak when the reaction/payoff is still happening. Try to keep every clip 15-60s, but can be a little longer if needed.\n\n"
                            f"SCORING: 90-100 exceptional standout; 80-89 very strong; 70-79 strong; 60-69 decent. Do not give a high score merely for technical skill or profile alignment. Score relative viral potential. Make the score/ranking meaningfully comparative across all selected deep clips.\n\n"
                            f"OUTPUT FORMAT — RETURN ONLY VALID JSON. Return a JSON object with a top-level \"moments\" array containing 2-6 selected clips, ordered from HIGHEST to LOWEST viral potential, plus top-level \"vod_title\" and \"vod_description\". Each moment MUST contain: \"start\" and \"end\" in ABSOLUTE SECONDS (0 <= start < end <= {_dur:.0f}), with every clip 15-80s; \"category\" as one of [action, funny, clutch, wtf, epic_fail, hype, skill]; \"score\" as an integer 0-100; \"reason\"; \"viral_title\" (max 100 chars, curiosity-driven, no fake claims); \"viral_description\" (1 sentence hook + 3-5 topical hashtags); and \"viral_hook\" (3-6 words for on-screen text). All user-facing copy MUST be in transcript language ({language}) — do NOT use Game Profile English. \"vod_title\" must be 6-10 words and max 100 chars. \"vod_description\" must be 3-5 sentences in {language}, Include timestamps for the 10 most viral moments/highlights from the full VOD, ordered by viral potential. Use the selected clips when appropriate, but also include other highly viral-worthy moments that were not selected as clips. Do not invent timestamps; each timestamp must correspond to a real moment in the VOD. End with 3–5 relevant, high-potential gaming/viral hashtags in {language}.. The array order and scores MUST agree with the viral ranking: higher viral potential = higher score. Return ONLY the JSON object, with no markdown or additional text. Example: {{\"moments\":[{{\"start\":120,\"end\":145,\"category\":\"hype\",\"score\":85,\"reason\":\"clutch reaction\",\"viral_title\":\"...\",\"viral_description\":\"... #gaming #clutch #epico\",\"viral_hook\":\"NON CI CREDO\"}}],\"vod_title\":\"...\",\"vod_description\":\"Fail epici e salvataggi incredibili — 02:14 fail, 05:46 clutch, 12:30 momento wtf... #gaming #twitch\"}}"
                        )
                        try:
                            import ai_provider as _ap
                            from gemini_worker import DeepResponse as _DeepResp
                            _deep_cfg = _deep_config()
                            _deep_provider = _deep_cfg["provider"]
                            _deep_model = _deep_cfg["model"]
                            _deep_api_key = _deep_cfg["api_key"]
                            _deep_base = _deep_cfg["base_url"]
                            _is_deep_gemini = _deep_cfg["input_mode"] == "native_video"
                            print(f"🧠 Deep full-VOD analysis started ({_deep_provider}, {_deep_model})…")
                            _dbg(f"   deep input={_deep_cfg['input_mode']} base={_deep_base or 'gemini-native'}")
                            _prov = _ap.create_ai_provider(_deep_provider, _deep_model, api_key=_deep_api_key, base_url=_deep_base, temperature=0.2, max_tokens=3000, timeout=90)
                            _res = None
                            _native_success = False
                            if _used_native_video:
                                # Gemini native video via low-res proxy like autoshorts (854x480, saves tokens, HUD still readable)
                                import pathlib as _pl, subprocess as _sp, tempfile as _tf, os as _os2
                                _proxy_path = None
                                _vbytes = b""
                                try:
                                    # Create low-res proxy with ffmpeg (ultrafast, crf 28, 64k audio) — fallback to original if ffmpeg fails
                                    # autoshorts proxy: 640:-2 fps=1, 1fps tiny file, then File API upload (not inline Part)
                                    print("🧠 Deep analysis: preparing a lightweight video copy for Gemini (encoding a low-res proxy)…")
                                    _proxy_path = _os2.path.join(_tf.gettempdir(), f"deep_proxy_{_os2.path.basename(video_path)}.mp4")
                                    _cmd = ["ffmpeg","-y","-i",video_path,"-vf","scale=640:-2,fps=1","-c:v","libx264","-preset","ultrafast","-crf","30","-c:a","aac","-b:a","32k","-ac","1",_proxy_path]
                                    _pr = _sp.run(_cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, timeout=180)
                                    if _pr.returncode == 0 and _os2.path.exists(_proxy_path) and _os2.path.getsize(_proxy_path) > 1024:
                                        print(f"   ✅ Lightweight copy ready ({_os2.path.getsize(_proxy_path)/1024/1024:.1f}MB) — uploading to Gemini…")
                                    else:
                                        print("   ⚠️ Lightweight copy failed — falling back to frame sampling")
                                        _proxy_path = None
                                except Exception as _pe:
                                    print(f"   ⚠️ Lightweight copy error ({_pe}) — falling back to frame sampling")
                                    _proxy_path = None
                                # File API upload like autoshorts (not inline) — handles large files via polling
                                _uploaded = None
                                _vpart = None
                                if _proxy_path and _os2.path.exists(_proxy_path):
                                    try:
                                        import time as _time2
                                        with open(_proxy_path, "rb") as _fh:
                                            _uploaded = _prov.client.files.upload(file=_fh, config={"mime_type": "video/mp4"})
                                        # poll until ACTIVE like autoshorts
                                        for _ in range(30):
                                            _st = _prov.client.files.get(name=_uploaded.name)
                                            if getattr(_st.state, "name", "") == "ACTIVE":
                                                break
                                            if getattr(_st.state, "name", "") == "FAILED":
                                                raise RuntimeError("Gemini file FAILED")
                                            _time2.sleep(2)
                                        print("   ✅ Upload processed by Gemini — starting deep analysis (this watches the full video, may take a while)…")
                                    except Exception as _ue:
                                        print(f"   ⚠️ Deep upload failed ({_ue}) — falling back to frame sampling")
                                        _uploaded = None
                                        try:
                                            if _proxy_path and _os2.path.exists(_proxy_path):
                                                _os2.remove(_proxy_path)
                                        except Exception:
                                            pass
                                _deep_prompt_native = _deep_prompt + " Watch the full video (not just frames) and read any HUD text (damage numbers, health, monster names)."
                                if _uploaded is not None:
                                    try:
                                        _cfg = _prov.genai_types.GenerateContentConfig(response_mime_type="application/json", response_schema=_DeepResp)
                                        _resp = _prov.client.models.generate_content(model=_prov.model_name, contents=[_uploaded, _deep_prompt_native], config=_cfg)
                                        import json as _js2
                                        _txt = getattr(_resp, "text", "") or ""
                                        if not _txt:
                                            _txt = str(getattr(_resp, "candidates", ""))[:2000]
                                        _parsed_native = None
                                        try:
                                            _parsed_native = _js2.loads(_txt) if _txt.strip().startswith("{") else None
                                        except Exception:
                                            _parsed_native = None
                                        if _parsed_native and "moments" in _parsed_native:
                                            _res = {"response": _parsed_native}
                                    except Exception as _ne:
                                        print(f"   ⚠️ Native video analysis failed ({_ne}) — falling back to frame sampling")
                                        _uploaded = None
                                    # native parse already attempted above; check success
                                    # cleanup uploaded file like autoshorts (always delete proxy too)
                                    try:
                                        if _uploaded is not None:
                                            _prov.client.files.delete(name=_uploaded.name)
                                    except Exception:
                                        pass
                                    try:
                                        if _proxy_path and __import__("os").path.exists(_proxy_path):
                                            __import__("os").remove(_proxy_path)
                                    except Exception:
                                        pass
                                    if _res is not None and isinstance(_res, dict) and "response" in _res:
                                        _native_success = True
                                        try:
                                            print(f"   Deep native video: parsed {len(_res['response'].get('moments',[]))} moments")
                                        except Exception:
                                            pass
                                    if not _native_success:
                                        # fallback to frames for this VOD
                                        if _deep_frames is None:
                                            _deep_frames = extract_frames_from_window(video_path, 0, _dur, num_frames=_deep_nframes, peak_times=_peak_times if _peak_times else None)
                                        _used_native_video = False
                            if not _native_success:
                                    _content = [{"type": "text", "text": _deep_prompt + " Also read any visible HUD text (damage numbers, health, monster names)."}]
                                    for _fr in _deep_frames:
                                        _content.append({"type": "image_url", "image_url": {"url": _fr["base64_image"]}})
                                    _dbg(f"   Deep prompt len {len(_deep_prompt)} gp={len(_deep_gp)} tx={len(_tx)} ct={len(_ct)} frames={len(_deep_frames) if _deep_frames else 0} | Deep sending {len(_content)} parts to {_deep_provider}/{_deep_model} (schema={_DeepResp.__name__})...")
                                    print("🧠 Deep analysis: sending sampled frames to the model (full-video pass)…")
                                    _res = _prov.generate_content(_deep_prompt, schema=_DeepResp, messages=[{"role": "user", "content": _content}])
                                    _parsed_tmp = _res.get("response", {}) if isinstance(_res, dict) else {}
                                    if (not _parsed_tmp or not _parsed_tmp.get("moments")) and _deep_provider != "gemini":
                                        print(f"   Deep schema empty (raw {_parsed_tmp}), retrying without json_schema for LM Studio...")
                                        try:
                                            _res_fb = _prov.generate_content(_deep_prompt + " Return ONLY JSON.", messages=[{"role": "user", "content": _content}])
                                            if isinstance(_res_fb, dict) and "response" in _res_fb and _res_fb["response"].get("moments"):
                                                _res = _res_fb
                                                print(f"   Deep fallback succeeded via response key")
                                            else:
                                                import json as _js_fb2
                                                _txt_fb2 = ""
                                                if isinstance(_res_fb, dict):
                                                    _txt_fb2 = _res_fb.get("text","") or _res_fb.get("response",{}).get("text","") if isinstance(_res_fb.get("response"),dict) else ""
                                                    if not _txt_fb2:
                                                        _txt_fb2 = str(_res_fb)[:4000]
                                                if isinstance(_txt_fb2, str) and "{" in _txt_fb2:
                                                    _s = _txt_fb2.find("{"); _e = _txt_fb2.rfind("}")
                                                    if _s!=-1 and _e!=-1:
                                                        _parsed_fb2 = _js_fb2.loads(_txt_fb2[_s:_e+1])
                                                        if "moments" in _parsed_fb2:
                                                            _res = {"response": _parsed_fb2}
                                                            print(f"   Deep fallback parsed {len(_parsed_fb2.get('moments',[]))} moments from text")
                                        except Exception as _fb_e2:
                                            print(f"   Deep fallback exception: {_fb_e2}")
                            try:
                                _deep_cost = _res.get("cost_analysis") if isinstance(_res, dict) else None
                                if _deep_cost:
                                    costs.append(_deep_cost)
                            except Exception:
                                pass
                            _parsed = _res.get("response", {}) if isinstance(_res, dict) else {}
                            # Deep clip discovery owns clip candidates only.
                            # VOD title/description are generated later by the
                            # provider-independent VOD metadata pass, so no per-process
                            # globals are used and jobs cannot leak metadata into one another.
                            _moments = _parsed.get("moments", []) if isinstance(_parsed, dict) else []
                            for _m in sorted(_moments, key=lambda m: float(m.get("score", 0) or 0), reverse=True)[:6]:
                                try:
                                    _s = float(_m.get("start", 0)); _e = float(_m.get("end", 0)); _cat = str(_m.get("category","hype")); _sc = int(_m.get("score", 70))
                                    _s = max(0, min(_s, max(0.0, _dur-15))); _e = max(_s+15, min(_e, _dur))
                                    if _e - _s < 15:
                                        _e = min(_dur, _s + 15)
                                    elif _e - _s > DEEP_MAX_CLIP_SECONDS:
                                        _e = min(_dur, _s + DEEP_MAX_CLIP_SECONDS)
                                    _vt = str(_m.get("viral_title") or _m.get("viralTitle") or f"{_cat} moment {int(_s)}s").strip()[:80]
                                    _vd = str(_m.get("viral_description") or _m.get("viralDescription") or _m.get("reason","")).strip()[:200]
                                    _vh = str(_m.get("viral_hook") or _m.get("viralHook") or "").strip()[:40]
                                    _deep_candidates.append({"start": round(_s,2), "end": round(_e,2), "center": round((_s+_e)/2,2), "events": [{"timestamp": round((_s+_e)/2,2), "type": f"deep_{_cat}", "score": _sc}], "reason": f"deep:{_cat}:{_sc} {_m.get('reason','')}"[:120], "heuristic_score": 3.0, "deep_title": _vt, "deep_description": _vd, "deep_hook": _vh, "deep_category": _cat, "deep_score": _sc})
                                except Exception:
                                    continue
                            print(f"🧠 Deep analysis found {len(_deep_candidates)} highlight moment(s) in the full video")
                            for _dc in _deep_candidates:
                                _dbg(f"      deep {_dc['reason']} { _dc['start']:.1f}-{_dc['end']:.1f}s")
                            if not _deep_candidates:
                                print("   ℹ️ Deep analysis found no standout moments — continuing with the standard pipeline")
                                if DEBUG_LOGS:
                                    print(f"   Deep returned no moments (raw {_parsed} resp_keys={list(_parsed.keys()) if isinstance(_parsed, dict) else type(_parsed).__name__})")
                                    if isinstance(_parsed, dict) and not _parsed:
                                        print(f"   Deep debug: prov={_deep_provider} model={_deep_model} frames={len(_deep_frames) if _deep_frames else 0} prompt_len={len(_deep_prompt)}")
                        except Exception as _pe:
                            print(f"   ⚠️ Deep vision call failed ({_pe}) — falling back to standard analysis")
                    else:
                        print("   ℹ️ Deep analysis skipped: not enough frames could be extracted")
                except Exception as _de:
                    print(f"Deep analysis failed: {_de}")
            cheap_windows = events_to_candidate_windows(cheap_events, video_duration)
            # Inject deep candidates as high-priority windows (if any real moments returned)
            if _deep_candidates:
                for dc in sorted(_deep_candidates, key=lambda x: float(x.get("deep_score", 0) or 0), reverse=True)[:6]:
                    cheap_windows.append(dc)
                print(f"🧠 Deep analysis: {len(_deep_candidates)} prioritized moment(s) added to the candidate pool")
            # Dynamic budget + diversity (autoshorts-inspired: 4*target + duration bonus, 70% densest + 30% random)
            import math as _m, random as _rnd
            try:
                from clip_selection import clip_count_targets as _cct
                _tgt = _cct(len(windows))[1]  # max clips ~ target
            except Exception:
                _tgt = 10
            _base = _tgt * 4
            _bonus = int((float(video_duration)/60)/30 * 5)  # +5 per 30min
            _cand = _base + _bonus
            _cand = max(max(_tgt*2, 10), min(_cand, 50, int(len(cheap_windows)*0.7) if cheap_windows else 50))
            # safety: if few cheap windows, keep all
            # B: when video motion profile exists, weight ranking 0.6 audio + 0.4 video via heuristic_score if present
            # Deep 1.3 tops vs 0-1 cheap (as requested)
            def _cheap_sort_key(x):
                if "heuristic_score" in x:
                    return float(x["heuristic_score"])
                # cheap proxy: len(events) normalized approx 0-1 (max ~10 events => 1.0)
                return min(1.0, len(x.get("events", [])) / 8.0)
            # autoshorts parity: when deep ON → ALL deep + top heuristic backups (no random), else 70/30
            if _deep_candidates:
                _deep_windows = [w for w in cheap_windows if float(w.get("heuristic_score", 0)) >= 3.0]
                _heur_windows = [w for w in cheap_windows if float(w.get("heuristic_score", 0)) < 3.0]
                needed = max(0, _cand - len(_deep_windows))
                if len(cheap_windows) > _cand:
                    _heur_windows.sort(key=_cheap_sort_key, reverse=True)
                    cheap_windows = _deep_windows + _heur_windows[:needed]
                    cheap_windows.sort(key=lambda x: x.get("center", x["start"]))
                    _dbg(f"   📊 Deep priority budget: {len(_deep_windows)} deep + {len(_heur_windows[:needed])} heuristic backups (capped {_cand}, from {len(_deep_windows)+len(_heur_windows)} candidates, video {float(video_duration)/60:.1f}min target {_tgt})")
                else:
                    cheap_windows.sort(key=_cheap_sort_key, reverse=True)
            elif len(cheap_windows) > _cand:
                cheap_windows.sort(key=_cheap_sort_key, reverse=True)
                _top = int(_cand * 0.7)
                _rand_n = _cand - _top
                _top_windows = cheap_windows[:_top]
                _pool = cheap_windows[_top:]
                _rnd.shuffle(_pool)
                _rand_windows = _pool[:max(0, _rand_n)]
                cheap_windows = _top_windows + _rand_windows
                cheap_windows.sort(key=lambda x: x.get("center", x["start"]))
                _dbg(f"   📊 Dynamic cheap budget: capped to {_cand} (70% densest {_top} + 30% random {_rand_n}, from {len(_top_windows)+len(_rand_windows)+len(_pool[_rand_n:]):d} candidates, video {float(video_duration)/60:.1f}min target {_tgt})")
            else:
                cheap_windows.sort(key=_cheap_sort_key, reverse=True)
            if cheap_windows:
                # Convert cheap windows to transcript-window shape for scoring
                # Use event descriptions as pseudo-text so LLM can reason about non-speech events
                existing = {(w["start"], w["end"]) for w in windows}
                added = 0
                for cw in cheap_windows:
                    key = (cw["start"], cw["end"])
                    # Avoid exact duplicates
                    if key in existing:
                        continue
                    # Check overlap >80% with existing
                    overlap = any(not (cw["end"] < w["start"] or cw["start"] > w["end"]) and \
                                  min(cw["end"], w["end"]) - max(cw["start"], w["start"]) > 0.95 * (cw["end"]-cw["start"]) for w in windows)
                    if overlap:
                        continue
                    # Build pseudo-text from events in window
                    evt_text = "; ".join(f"{e['timestamp']:.1f}s {e.get('description') or e.get('type','')}" for e in cw["events"][:6])
                    pseudo = f"[CHEAP EVENTS {cw['start']:.1f}-{cw['end']:.1f}s: {evt_text}] " + cw["reason"]
                    windows.append({
                        "id": f"cheap_{added}_{int(cw['center'])}",
                        "start": cw["start"],
                        "end": cw["end"],
                        "text": pseudo,
                    })
                    existing.add(key)
                    added += 1
                if added:
                    print(f"➕ {added} extra candidate window(s) from audio/scene signals — {len(windows)} total")
                    if DEBUG_LOGS:
                        print(f"   🔍 Cheap windows (why each was created):")
                        for cw in cheap_windows[:5]:
                            ev_types = ", ".join(sorted(set(e['type'] for e in cw['events'])))
                            ev_sample = ", ".join(f"{e['timestamp']:.1f}s:{e['type']}" for e in cw['events'][:4])
                            print(f"      {cw['start']:.1f}-{cw['end']:.1f}s ({cw['end']-cw['start']:.0f}s) center {cw['center']:.1f}s")
                            print(f"         types: {ev_types}")
                            print(f"         evidence: {ev_sample}")
                        if _deep_candidates:
                            print(f"   🧠 Deep candidates (all 4):")
                            for dc in _deep_candidates:
                                print(f"      {dc['start']:.1f}-{dc['end']:.1f}s {dc['reason']}")
                elif DEBUG_LOGS:
                    print(f"   ℹ️ No new cheap windows (all overlapped transcript windows — cheap signals confirm transcript candidates)")
            else:
                    print(f"   ℹ️ No audio/scene signals detected (scene={ENABLE_SCENE_DETECTION} audio={ENABLE_AUDIO_EVENTS} visual={ENABLE_CHEAP_VISUAL})")
        except Exception as e:
            print(f"⚠️ Cheap event seeding skipped: {e}")
        costs = []

        # Handle GameProfile integration (sync-safe: local repo is synchronous)
        active_weights = None
        if HAS_GAME_PROFILE_SUPPORT and game_profile_id:
            try:
                from cloud.local_game_profiles import LocalGameProfileRepository
                repo = LocalGameProfileRepository()
                gp_dict = repo.get_profile(game_profile_id)
                if gp_dict:
                    active_weights = gp_dict.get('active_weights') or gp_dict.get('recommended_weights') or {}
                else:
                    print(f"⚠️  GameProfile {game_profile_id} not found or not accessible.")
            except Exception as e:
                print(f"⚠️  Error resolving GameProfile {game_profile_id}: {e}")

        # --- Pass 1: score windows in batches, keep the highest-scoring ---
        scored = []
        SCORE_BATCH = 8
        # Build scoring context: game profile + cheap timeline (Phase 5)
        # Keep cheap timeline compact (first 30 events) and game profile minimal
        _game_profile_json = "{}"
        _cheap_timeline = "none"
        try:
            if HAS_GAME_PROFILE_SUPPORT and game_profile_id and active_weights is not None:
                _gp = None
                try:
                    from cloud.local_game_profiles import LocalGameProfileRepository
                    repo = LocalGameProfileRepository()
                    _gp = repo.get_profile(game_profile_id)
                except Exception:
                    pass
                if _gp:
                    # _gp is dict from local repo — include everything preview shows
                    _ai = _gp.get('ai_analysis') or {}
                    _gp_dict = {
                        "game_title": _gp.get('game_title', ''),
                        "game_type": _gp.get('game_type', '') or _ai.get('game_type', '') or '',
                        "steam_description": (_gp.get('steam_description') or '')[:400],
                        "custom_notes": (_gp.get('custom_description') or '')[:400],
                        "active_weights": _gp.get('active_weights') or _gp.get('recommended_weights') or active_weights or {},
                        "ai_analysis": _ai,
                    }
                    _game_profile_json = json.dumps(_gp_dict, ensure_ascii=False)[:2500]
            # Cheap timeline from earlier extraction (if available)
            if cheap_events:
                _cheap_timeline = "\n".join(f"{e['timestamp']:.1f}s {e['type']}: {e.get('description') or e.get('type','')}" for e in cheap_events[:30])
                if len(cheap_events) > 30:
                    _cheap_timeline += f"\n... +{len(cheap_events)-30} more"
        except Exception as _e:
            print(f"⚠️ Scoring context build skipped: {_e}")

        if DEBUG_LOGS:
            # Show exactly what GameProfile + cheap evidence the LLM will see
            try:
                _gp_preview = _game_profile_json if _game_profile_json != "{}" else "none (universal)"
                if len(_gp_preview) > 600:
                    _gp_preview = _gp_preview[:600] + "… (truncated)"
                print(f"   🎮 GameProfile sent to LLM: {_gp_preview}")
                _cheap_preview = _cheap_timeline if _cheap_timeline != "none" else "none"
                if len(_cheap_preview) > 500:
                    _cheap_preview = _cheap_preview[:500] + "… (truncated)"
                print(f"   📡 Cheap timeline sent to LLM ({len(cheap_events)} events): {_cheap_preview.replace(chr(10), ' | ')}")
            except Exception:
                pass
        _score_batches = (len(windows) + SCORE_BATCH - 1) // SCORE_BATCH
        if _score_batches:
            print(f"🧠 Scoring {_score_batches} batch(es) of {len(windows)} candidate windows with {AI_PROVIDER.title()}…")
        for b in range(0, len(windows), SCORE_BATCH):
            batch = windows[b:b + SCORE_BATCH]
            payload = [{"id": w["id"], "start": w["start"], "end": w["end"], "text": w["text"]} for w in batch]
            prompt = gemini_worker.SCORE_PROMPT_TEMPLATE.format(
                video_duration=video_duration, language=language,
                game_profile_json=_game_profile_json,
                cheap_timeline=_cheap_timeline,
                windows_json=json.dumps(payload, ensure_ascii=False))
            # Use the new provider abstraction
            ai_provider_instance = ai_provider.create_ai_provider(_provider, model_name, api_key=api_key, base_url=base_url, temperature=AI_TEMPERATURE, max_tokens=AI_MAX_TOKENS, timeout=AI_TIMEOUT)
            result = ai_provider_instance.generate_content(prompt, gemini_worker.ScoreResponse)
            parsed = result["response"]
            cost = result["cost_analysis"]
            if cost:
                costs.append(cost)
            scored.extend(parsed.get("windows") or [])
            if _score_batches:
                print(f"   ✅ Batch {b // SCORE_BATCH + 1}/{_score_batches} scored ({len(scored)} windows done)")

        # Vision analysis (Phase 6) — toggleable, runs even without GameProfile; GameProfile just biases scores
        if HAS_SEMANTIC_ANALYZER and ENABLE_VISION_ANALYSIS:
            # Normalize scores for consistent application of profile weights
            max_score = max(w.get("score", 0) for w in scored) if scored else 1.0
            if max_score == 0:
                max_score = 1.0

            # Apply multimodal semantic analysis to each window before scoring
            # This only happens when we have a GameProfile with active weights
            try:
                # Sync fetch for semantic context (list is truncated, need full)
                from cloud.local_game_profiles import LocalGameProfileRepository
                repo2 = LocalGameProfileRepository()
                gp_dict2 = repo2.get_profile(game_profile_id)
                # Build ai_analysis dict compatible with previous logic
                if gp_dict2 and gp_dict2.get('ai_analysis'):
                    # Build game profile context from existing data
                    _ai2 = gp_dict2.get('ai_analysis') or {}
                    game_type = gp_dict2.get('game_type') or _ai2.get('game_type', 'unknown')
                    gameplay_characteristics = _ai2.get('gameplay_characteristics', [])
                    key_moments = _ai2.get('key_moments', [])

                    # Create context dict for semantic analyzer
                    game_profile_context = {
                        "game_type": game_type,
                        "gameplay_characteristics": gameplay_characteristics,
                        "key_moments": key_moments
                    }
                else:
                    # Fallback to minimal context if profile data is not available
                    game_profile_context = {
                        "game_type": "unknown",
                        "gameplay_characteristics": [],
                        "key_moments": []
                    }

                if DEBUG_LOGS:
                    print(f"   👁️ Vision analysis ON — extracting 6 frames per window (peak-biased for screams) — {len(scored)} windows")
                print(f"👁️ Vision analysis: checking {len(scored)} window(s) for visual signals (frames + {AI_PROVIDER.title()})…")
                # Build lookup for original window text (scored windows have no 'text')
                _orig_text_by_id = {ow.get('id'): ow.get('text','') for ow in windows}
                _vision_done = 0
                # Process each scored window with semantic analysis
                for w in scored:
                    try:
                        window_id = w["id"]
                        window_start = w["start"]
                        window_end = w["end"]
                        transcript_text = w.get("text") or _orig_text_by_id.get(window_id, "")

                        # Extract 6 frames peak-biased (3 uniform + 3 around scream/loudness) for scared faces
                        # Build peak times from cheap events inside this window
                        _peak_times = []
                        try:
                            if cheap_events:
                                # Priority: scream > sudden_loudness > visual_activity > scene_change > others
                                _prio = {"scream": 0, "sudden_loudness": 1, "visual_activity": 2, "scene_change": 3, "laughter": 4}
                                _in_win = [e for e in cheap_events if window_start - 0.5 <= e.get("timestamp", 0) <= window_end + 0.5]
                                _in_win.sort(key=lambda e: (_prio.get(e.get("type",""), 5), e.get("timestamp", 0)))
                                _peak_times = [e["timestamp"] for e in _in_win]
                        except Exception:
                            _peak_times = []
                        frames = extract_frames_from_window(
                            video_path,
                            window_start,
                            window_end,
                            num_frames=6,
                            peak_times=_peak_times if _peak_times else None
                        )
                        if DEBUG_LOGS and frames:
                            print(f"      📷 {window_id}: {len(frames)} frames (peak-biased={bool(_peak_times)} peaks={len(_peak_times) if _peak_times else 0})")
                        elif DEBUG_LOGS:
                            print(f"      📷 {window_id}: 0 frames (extraction failed)")

                        # Only analyze if we have frames and transcript
                        if frames and transcript_text:
                            # Call the multimodal semantic analyzer
                            signals_response = analyze_semantic_window(
                                transcript=transcript_text,
                                frames=frames,
                                game_profile_context=game_profile_context,
                                provider=ai_provider_instance
                            )

                            # Get detected signals for scoring
                            detected_signals = signals_response.signals
                        else:
                            # If no frames or transcript, use empty signals
                            detected_signals = []

                        # Apply the semantic scoring modifier to this window score
                        existing_score = w.get("score", 0)
                        final_score = apply_game_profile_scoring(existing_score, active_weights, detected_signals)
                        w["score"] = final_score
                        _vision_done += 1
                        if _vision_done % 5 == 0 or _vision_done == len(scored):
                            print(f"   👁️ Vision progress: {_vision_done}/{len(scored)} windows analyzed")
                    except Exception as e:
                        # Log error but continue processing other windows
                        print(f"⚠️ Semantic analysis failed for window {window_id}: {e}")
                        # Keep existing score if analysis fails
                        existing_score = w.get("score", 0)
                        w["score"] = existing_score
            except Exception as e:
                # If there's an issue with GameProfile resolution, fall back to no semantic scoring
                print(f"⚠️ Error in GameProfile processing: {e}")
                # Continue with original scoring without semantic analysis
                pass
        elif HAS_GAME_PROFILE_SUPPORT and active_weights is not None:
            # Deterministic re-rank without vision (vision toggled off or unavailable)
            for w in scored:
                existing_score = w.get("score", 0)
                final_score = apply_game_profile_scoring(existing_score, active_weights, [])
                w["score"] = final_score
        if not ENABLE_VISION_ANALYSIS and DEBUG_LOGS:
            print(f"   ℹ️ Vision analysis OFF — skipping 6-frame extraction (toggle to enable)")
        elif HAS_SEMANTIC_ANALYZER and ENABLE_VISION_ANALYSIS:
            print(f"✅ Vision analysis finished — {_vision_done} window(s) checked")

        # Shortlist the top windows; scale with duration so long videos surface
        # more candidates without exploding the detail call.
        scored.sort(key=lambda w: w.get("score", 0), reverse=True)
        if DEBUG_LOGS:
            print(f"   📊 Scored {len(scored)} windows — top 5 (why they were chosen):")
            for w in scored[:5]:
                win_id = w.get("id","?")
                score = w.get("score",0)
                reason = (w.get("reason") or "").strip()
                # Smart truncate reason without cutting mid-word, keep full if short
                if len(reason) > 140:
                    reason = reason[:140].rsplit(' ', 1)[0] + "…"
                start = w.get("start",0)
                end = w.get("end",0)
                dur = end - start
                print(f"      {win_id}  score {score:5.1f}  [{start:.1f}-{end:.1f}s {dur:.0f}s]  {reason}")
            if active_weights is not None:
                print(f"   🎮 GameProfile weights: {active_weights}")
                print(f"   ℹ️ Scoring is deterministic — weight change re-ranks without re-LLM")
            else:
                print(f"   ℹ️ No GameProfile — universal weights only")
        if not DEBUG_LOGS and scored:
            # Single user-facing summary line (full top-5 dump is debug-only)
            _best = scored[0]
            print(f"🏆 Best candidate: {_best.get('start',0):.0f}-{_best.get('end',0):.0f}s — {( _best.get('reason') or '').strip()[:120]}")
        # Vision needs more candidates; 10 -> 25 when enabled for maximal choice
        if ENABLE_VISION_ANALYSIS and HAS_SEMANTIC_ANALYZER:
            target = max(8, min(25, int(video_duration // 45) + 6))
            if DEBUG_LOGS:
                print(f"   👁️ Vision ON — expanded shortlist to {target} (vs 10) for more choice")
        else:
            target = max(3, min(10, int(video_duration // 90) + 2))
        by_id = {w["id"]: w for w in windows}
        shortlist = [by_id[w["id"]] for w in scored[:target] if w.get("id") in by_id]
        if not shortlist:
            shortlist = windows[:target]  # scoring returned nothing usable
        print(f"🎯 {len(shortlist)} best candidate window(s) go to the detail pass.")
        if DEBUG_LOGS:
            print(f"   📋 Shortlist (these go to detail LLM):")
            for w in shortlist:
                txt = (w.get('text') or "").strip().replace('\n',' ')
                if len(txt) > 120:
                    txt = txt[:120].rsplit(' ', 1)[0] + "…"
                print(f"      → {w['id']}  {w['start']:.1f}-{w['end']:.1f}s  '{txt}'")

        # Fold vision-pass call costs into the job's cost accounting
        try:
            costs.extend(drain_semantic_costs())
        except Exception:
            pass

        # --- Pass 2: detailed clip extraction on the shortlist ---
        # Phase 5: enrich each window with surrounding transcript + word timestamps + audio/scene events
        # so Text LLM has full multimodal context per ROADMAP §12
        _by_id = {w["id"]: w for w in windows}
        _idx_by_id = {w["id"]: i for i, w in enumerate(windows)}
        payload = []
        for w in shortlist:
            wid = w["id"]
            s, e = float(w["start"]), float(w["end"])
            txt = w.get("text","")
            # surrounding transcript (prev + next window text up to 400 chars each)
            _surr_parts = []
            _idx = _idx_by_id.get(wid, -1)
            if _idx >= 1:
                _prev = windows[_idx-1].get("text","")[:400]
                if _prev:
                    _surr_parts.append(f"BEFORE [{windows[_idx-1]['start']:.1f}-{windows[_idx-1]['end']:.1f}s]: {_prev}")
            if _idx != -1 and _idx + 1 < len(windows):
                _nxt = windows[_idx+1].get("text","")[:400]
                if _nxt:
                    _surr_parts.append(f"AFTER [{windows[_idx+1]['start']:.1f}-{windows[_idx+1]['end']:.1f}s]: {_nxt}")
            surrounding = " | ".join(_surr_parts) if _surr_parts else "none"
            # word timestamps slice for this window (compact)
            _ws = []
            try:
                _ws = [ww for ww in words if s - 0.5 <= float(ww.get("s",0)) <= e + 0.5][:28]
                _words_compact = "; ".join(f"{ww['w']}@{ww['s']:.2f}" for ww in _ws)
            except Exception:
                _words_compact = ""
                _ws = []
            # audio/scene events inside window (from cheap_events)
            _ev_in = []
            try:
                if cheap_events:
                    _ev_in = [ee for ee in cheap_events if s - 0.5 <= float(ee.get("timestamp",0)) <= e + 0.5][:10]
            except Exception:
                _ev_in = []
            _ev_txt = ", ".join(f"{ee.get('timestamp',0):.1f}s:{ee.get('type','')}" for ee in _ev_in) if _ev_in else "none"
            # scene boundaries inside window: real visual cuts (scene_change
            # events from cheap_events) as explicit timestamps the detail LLM
            # can align clip starts/ends to
            _scene_in = []
            try:
                if cheap_events:
                    _scene_in = sorted({round(float(ee.get("timestamp", 0)), 1)
                                        for ee in cheap_events
                                        if ee.get("type") == "scene_change"
                                        and s - 0.5 <= float(ee.get("timestamp", 0)) <= e + 0.5})
            except Exception:
                _scene_in = []
            _scene_txt = ", ".join(f"{t:.1f}s" for t in _scene_in) if _scene_in else "none"
            entry = {"id": wid, "start": s, "end": e, "text": txt, "surrounding_transcript": surrounding, "word_timestamps": _words_compact or "none", "audio_events": _ev_txt, "scene_boundaries": _scene_txt}
            payload.append(entry)
        _raw_min_clips, _raw_max_clips = clip_count_targets(len(shortlist))
        # Product requirement: 2-6 selected clips, but respect TARGET_CLIPS (main window slider) when set
        try:
            _tgt_req = int(__import__("os").environ.get("TARGET_CLIPS","").strip())
            if 1 <= _tgt_req <= 10:
                min_clips = max(1, min(_tgt_req, len(shortlist)))
                max_clips = min_clips
            else:
                min_clips = min(2, len(shortlist))
                max_clips = min(6, len(shortlist))
        except Exception:
            min_clips = min(2, len(shortlist))
            max_clips = min(6, len(shortlist))
        if max_clips < min_clips:
            min_clips = max_clips
        prompt = gemini_worker.DETAIL_PROMPT_TEMPLATE.format(
            video_duration=video_duration, language=language,
            game_profile_json=_game_profile_json,
            min_clips=min_clips, max_clips=max_clips,
            min_secs=min_secs, max_secs=max_secs,
            windows_json=json.dumps(payload, ensure_ascii=False))
        # Use the new provider abstraction
        ai_provider_instance = ai_provider.create_ai_provider(_provider, model_name, api_key=api_key, base_url=base_url, temperature=AI_TEMPERATURE, max_tokens=AI_MAX_TOKENS, timeout=AI_TIMEOUT)
        print(f"🤖 Detail pass: asking {AI_PROVIDER.title()} to extract and time the final clips ({min_clips}-{max_clips})…")
        result = ai_provider_instance.generate_content(prompt, gemini_worker.DetailResponse)
        parsed = result["response"]
        cost = result["cost_analysis"]
        if cost:
            costs.append(cost)

        shorts = clip_quality.validate_clips(parsed.get("shorts") or [], video_duration)
        _dropped = len(parsed.get("shorts") or []) - len(shorts)
        if _dropped:
            print(f"   ⚠️ {AI_PROVIDER.title()} detail: {_dropped} malformed clip(s) discarded, {len(shorts)} kept")
        # Deep bypass: prepend deep clips (with their own viral titles) — always selected, not in vision pool
        try:
            _env_tgt2 = __import__("os").environ.get("TARGET_CLIPS", "").strip()
            _tgt_final = int(_env_tgt2) if _env_tgt2 and _env_tgt2.isdigit() else len(shorts)
            if _tgt_final < 1:
                _tgt_final = len(shorts) if shorts else 5
        except Exception:
            _tgt_final = len(shorts) if shorts else 5
        _deep_shorts = []
        if "_deep_candidates" in locals() and _deep_candidates:
            for dc in _deep_candidates[:6]:
                _dt = dc.get("deep_title") or (dc.get("deep_category","viral") + " @ " + str(int(dc["start"])) + "s")
                _dd = dc.get("deep_description") or dc.get("reason","")[:200]
                _dh = dc.get("deep_hook") or ""
                _deep_shorts.append({
                    "start": float(dc["start"]), "end": float(dc["end"]),
                    "title": _dt, "description": _dd, "viral_hook_text": _dh,
                    "video_title_for_youtube_short": _dt,
                    "video_description_for_tiktok": _dd,
                    "video_description_for_instagram": _dd,
                    "predicted_score": int(dc.get("deep_score", 85)),
                    "viral_score": float(dc.get("deep_score", 85)),
                    "reason": dc.get("reason","")[:120],
                    "source_window_id": "deep_" + str(int(dc["start"])),
                    "deep": True
                })
        # Deep clips remain priority selections. Fill remaining target slots with normal clips only if needed.
        _deep_ranked = sorted(
            _deep_shorts,
            key=lambda x: float(x.get("viral_score", x.get("predicted_score", 0)) or 0),
            reverse=True,
        )
        _normal_ranked = sorted(
            shorts,
            key=lambda x: float(x.get("viral_score", x.get("predicted_score", 0)) or 0),
            reverse=True,
        )
        if _deep_ranked:
            _remaining = max(0, _tgt_final - len(_deep_ranked))
            shorts = _deep_ranked[:_tgt_final] + _normal_ranked[:_remaining]
        else:
            shorts = _normal_ranked[:_tgt_final]
        print(f"🤖 Final ranking: {len(shorts)} clip(s) selected, best-to-worst viral potential")
        if shorts:
            shorts.sort(key=lambda x: float(x.get("viral_score", x.get("predicted_score", 0)) or 0), reverse=True)
        # Drop near-duplicate temporal spots (two windows finding the same
        # moment would otherwise both render). Best-ranked clip wins.
        _pre_dedup = len(shorts)
        shorts = clip_quality.iou_dedup(shorts, threshold=0.7)
        if len(shorts) < _pre_dedup:
            print(f"   🔂 Overlap dedup: {_pre_dedup} -> {len(shorts)} clips (IoU >= 0.7)")
        # Snap each proposed clip onto real word boundaries (+ a bit of silence).
        # Deep clips use their own duration ceiling; normal clips keep the existing pipeline limit.
        for s in shorts:
            _snap_max = DEEP_MAX_CLIP_SECONDS if s.get("deep") else max_secs
            ns, ne = snap_clip_to_words(s.get("start", 0), s.get("end", 0), words, video_duration,
                                        min_duration=min_secs, max_duration=_snap_max)
            s["start"], s["end"] = ns, ne

        # Aggregate cost across both passes.
        cost_analysis = None
        if costs:
            cost_analysis = {
                "input_tokens": sum(c.get("input_tokens", 0) for c in costs),
                "output_tokens": sum(c.get("output_tokens", 0) for c in costs),
                "total_cost": sum(c.get("total_cost", 0) for c in costs),
                "model": model_name,
            }
            print(f"\U0001f4b0 Total cost ({model_name}, 2-pass, {len(costs)} calls): ${cost_analysis['total_cost']:.6f}")

        if not shorts:
            print("⚠️ 2-pass returned no clips.")
            return None

        # VOD metadata is generated independently after clip selection. Deep may
        # still have produced metadata internally, but it is never required for
        # correctness of the VOD title/description path. This guarantees the same
        # editorial contract with Deep ON, Deep OFF, or Deep failure.
        _vod_meta = _generate_vod_metadata(
            transcript_result=transcript_result,
            language=language,
            game_profile_id=game_profile_id,
            shorts=shorts,
        )
        result = {"shorts": shorts,
                  "vod_title": _vod_meta.get("vod_title", "VOD Highlights"),
                  "vod_description": _vod_meta.get("vod_description", "Best moments from this gaming stream.")}
        print("   VOD title: " + str(result["vod_title"])[:100])
        if cost_analysis:
            result["cost_analysis"] = cost_analysis
        return result
    except gemini_worker.GeminiBlockedError as e:
        # Content-policy rejection: propagate so the job fails with the real
        # reason instead of a generic "no clips found".
        print(f"🚫 {e}")
        raise
    except Exception as e:
        print(f"❌ {AI_PROVIDER.title()} Error: {e}")
        return None


# --- Speech too sparse to clip by transcript -------------------------------
# The vision path used to fire only on a missing audio TRACK. A nursery-rhyme
# video or a dashcam drive has audio, so it went through transcription, came
# back as one segment ("Uh uh"), produced one scoring window and Gemini
# returned no clips — three failed jobs on 25-aug-2026, one user twice. Speech
# is ~120-160 words/min; below these floors there is nothing to clip by words.
MIN_SPEECH_WORDS_PER_MIN = float(os.environ.get("MIN_SPEECH_WORDS_PER_MIN", "5"))
MIN_SPEECH_WORDS = int(os.environ.get("MIN_SPEECH_WORDS", "8"))


def speech_is_sparse(transcript, duration):
    """True when the transcript is too thin to drive clip selection."""
    words = sum(len((seg.get("text") or "").split())
                for seg in (transcript or {}).get("segments", []))
    minutes = max(float(duration or 0) / 60.0, 1e-6)
    return words < MIN_SPEECH_WORDS or words / minutes < MIN_SPEECH_WORDS_PER_MIN


def get_visual_clips(video_path, video_duration, language="en"):
    """Clip a SILENT video by vision: Gemini watches the footage and picks the
    most engaging visual moments (no transcript). Returns the same
    {"shorts", "cost_analysis"} shape as get_viral_clips, or None."""
    print(f"🎥  Silent video — analyzing with {AI_PROVIDER.title()} vision (no transcript)...")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        import llm_client
        if llm_client.active_config() is not None:
            print("❌ This video has no usable speech, so it has to be clipped by "
                  "watching it, and that needs Gemini (a text-only LLM server "
                  "cannot see the footage). Add a GEMINI_API_KEY for silent videos.")
        else:
            print("❌ Error: GEMINI_API_KEY not found. Silent-video analysis "
                  "watches the footage on Gemini; the third-party LLM endpoint "
                  "cannot replace it.")
        return None
    client = genai.Client(api_key=api_key)
    model_name = os.environ.get("GEMINI_MODEL") or 'gemini-3.1-flash-lite'
    print(f"🎥  Model: {model_name} | uploading {os.path.basename(video_path)}…")

    file_upload = None
    try:
        file_upload = client.files.upload(file=video_path)
        deadline = time.time() + 180
        while True:
            info = client.files.get(name=file_upload.name)
            state = str(getattr(getattr(info, "state", info), "name", "")).upper()
            if state == "ACTIVE":
                break
            if state == "FAILED":
                print(f"❌ {AI_PROVIDER.title()} could not process the video.")
                return None
            if time.time() > deadline:
                print(f"❌ {AI_PROVIDER.title()} video processing timed out.")
                return None
            time.sleep(2)

        # The vision path has no scoring windows to derive a count from, so the
        # Keep the visual fallback aligned with the main product range: 3-15 clips.
        def _env_int(name, default):
            try:
                return max(1, int(os.environ.get(name, "")))
            except ValueError:
                return default
        v_min_clips = _env_int("CLIP_TARGET_MIN", 3)
        v_max_clips = max(v_min_clips, _env_int("CLIP_TARGET_MAX", 15))
        v_min_secs, v_max_secs = clip_duration_bounds()
        prompt = gemini_worker.VISUAL_PROMPT_TEMPLATE.format(
            video_duration=video_duration, language=language,
            min_clips=v_min_clips, max_clips=v_max_clips,
            min_secs=v_min_secs, max_secs=v_max_secs)
        # Silent-video path talks to Gemini directly (client built above);
        # the shared provider abstraction needs provider vars this function
        # never had — that used to be a latent NameError here.
        from google.genai import types as _genai_types
        _resp = client.models.generate_content(
            model=model_name, contents=[prompt],
            config=_genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=AI_TEMPERATURE,
            ))
        result = {"response": gemini_worker._parse_json_response_text(
            gemini_worker._get_response_text(_resp)), "cost_analysis": None}
        parsed = result["response"] or {}
        shorts = clip_quality.validate_clips(parsed.get("shorts") or [], video_duration)
        # Clamp to the real duration; drop anything degenerate.
        clean = []
        for s in shorts:
            s["start"] = max(0.0, float(s.get("start", 0)))
            s["end"] = min(float(video_duration), float(s.get("end", 0)))
            if s["end"] - s["start"] >= 1.0:
                clean.append(s)
        if not clean:
            print("⚠️ Vision pass returned no usable clips.")
            return None
        clean = clip_quality.iou_dedup(
            sorted(clean, key=lambda x: float(x.get("viral_score", x.get("predicted_score", 0)) or 0),
                   reverse=True), threshold=0.7)

        cost = result.get("cost_analysis") if isinstance(result, dict) else None
        if cost:
            print(f"💰 Vision cost ({model_name}): ${cost.get('total_cost', 0):.6f}")
        result = {"shorts": clean}
        if cost:
            result["cost_analysis"] = cost
        return result
    except gemini_worker.GeminiBlockedError as e:
        print(f"🚫 {e}")
        raise
    except Exception as e:
        print(f"❌ {AI_PROVIDER.title()} vision error: {e}")
        return None
    finally:
        if file_upload is not None:
            try:
                client.files.delete(name=file_upload.name)
            except Exception:
                pass


def _enhance_transcript_with_llm(transcript):
    """Correct Whisper text conservatively for arbitrarily long videos.

    Chunks are sized by character budget rather than segment count. A failed
    chunk never discards successful chunks or prevents later chunks from being
    processed. Whisper word timestamps are deliberately left untouched; the
    subtitle renderer adds synthetic timings only for newly introduced emojis.
    """
    import json as _json
    from pydantic import BaseModel as _BaseModel
    from typing import List as _List

    segs = transcript.get("segments") or []
    if not segs:
        return transcript

    print("✨ Polish pass: cleaning up the transcript with AI (fixing misheard words"
          + (", adding emojis" if os.environ.get("ENABLE_CAPTION_EMOJIS", "0").strip().lower() in ("1", "true", "yes", "on") else "")
          + ")…")

    class _EnhSeg(_BaseModel):
        i: int
        text: str
        emoji: str = ""

    class _EnhResp(_BaseModel):
        segments: _List[_EnhSeg]

    provider_name = (AI_PROVIDER or "gemini").lower().strip()
    model_name = (GEMINI_MODEL if provider_name == "gemini" else OPENAI_MODEL)
    api_key = (os.environ.get("GEMINI_API_KEY") if provider_name == "gemini"
               else os.environ.get("OPENAI_API_KEY")) or AI_API_KEY
    base_url = None if provider_name == "gemini" else OPENAI_BASE_URL
    emoji_on = os.environ.get("ENABLE_CAPTION_EMOJIS", "0").strip().lower() in ("1", "true", "yes", "on")
    language = str(transcript.get("language") or "unknown")

    # Build chunks around ~6k chars, allowing long/short segments naturally.
    max_chars = 6500
    chunks = []
    current = []
    current_chars = 0
    for idx, seg in enumerate(segs):
        payload = {"i": idx, "start": seg.get("start", 0), "end": seg.get("end", 0), "text": seg.get("text", "")}
        cost = len(_json.dumps(payload, ensure_ascii=False)) + 1
        if current and current_chars + cost > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(payload)
        current_chars += cost
    if current:
        chunks.append(current)

    _emoji_instr = (
        "Add one emoji to MOST segments where the spoken line has any emotion, reaction, humor, hype, surprise, or emphasis — be generous, not minimal. "
        "Aim for roughly one emoji every 3-4 segments on average (e.g., 30-40 emojis for a 30-minute VOD). "
        "Use at most one emoji per segment. "
        "Return the emoji ONLY in the dedicated 'emoji' field, never inside 'text'. "
        "Leave the emoji empty only for truly neutral/informational lines."
        if emoji_on else
        "Do NOT add emojis. Always return an empty string in the 'emoji' field."
    )

    corrected = {}
    total = len(chunks)
    _enhance_cost_sink: list = []

    def _enhance_chunk(chunk_no, chunk):
        """Correct one chunk of segments. Returns {index: {text, emoji}}.

        A chunk that returns fewer valid segments than it was given likely hit
        the output token cap - retried once with a bigger budget before the
        originals are kept for the missing pieces (per-segment, so a partial
        response still applies its valid part).
        """
        first_i = chunk[0]["i"]
        last_i = chunk[-1]["i"]
        context_before = segs[first_i - 2:first_i] if first_i > 0 else []
        context_after = segs[last_i + 1:last_i + 3]
        prompt = (
            f"You are correcting a Whisper transcript for subtitles. Language: {language}.\n"
            "Whisper commonly mishears: homophones (their/there, its/it's, your/you're, "
            "to/too, peace/piece), game jargon and proper nouns (weapon/character/map names "
            "from the game's vocabulary), numbers, and run-together words. Fix every such "
            "mistake you can infer from context.\n"
            "Correct ONLY transcription mistakes strongly inferable from context. "
            "Never invent dialogue, paraphrase, summarize, merge or split segments. Preserve slang, "
            "names, profanity and game terminology. Keep each segment close in length to the original. "
            "Punctuation/capitalization may be fixed. Timestamps are immutable. " + _emoji_instr + "\n"
            "Return ONLY JSON - you MUST return every segment index given below, even unchanged ones:\n"
            "{\"segments\":["
            "{\"i\":<original index>,"
            "\"text\":\"corrected text without emoji\","
            "\"emoji\":\"single emoji or empty string\"}"
            "]}\n\n"
            f"CONTEXT BEFORE:\n{_json.dumps(context_before, ensure_ascii=False)}\n\n"
            f"TARGET SEGMENTS {first_i}-{last_i}:\n{_json.dumps(chunk, ensure_ascii=False)}\n\n"
            f"CONTEXT AFTER:\n{_json.dumps(context_after, ensure_ascii=False)}"
        )
        chunk_indices = {item["i"] for item in chunk}
        attempt_max_tokens = 5000

        for attempt in (1, 2):
            try:
                provider = ai_provider.create_ai_provider(
                    provider_name, model_name, api_key=api_key, base_url=base_url,
                    temperature=0.25, max_tokens=attempt_max_tokens, timeout=90,
                )
                result = provider.generate_content(prompt, schema=_EnhResp)
                _c = result.get("cost_analysis") if isinstance(result, dict) else None
                if _c:
                    _enhance_cost_sink.append(_c)
                parsed = result.get("response") if isinstance(result, dict) else None
                if not parsed:
                    raw = result.get("text", "") if isinstance(result, dict) else str(result)
                    if raw and "{" in raw:
                        parsed = _json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
                returned = (parsed or {}).get("segments", []) if isinstance(parsed, dict) else []
                accepted = {}
                for item in returned:
                    try:
                        i = int(item.get("i"))
                        text = str(item.get("text", "")).strip()
                        emoji = str(item.get("emoji", "") or "").strip()
                    except Exception:
                        continue
                    if i in chunk_indices and text:
                        accepted[i] = {"text": text, "emoji": emoji}
                missing = len(chunk_indices) - len(accepted)
                print(f"   ✨ Polish pass: chunk {chunk_no}/{total} done ({len(accepted)}/{len(chunk)} segments)"
                      + (f" (attempt {attempt})" if attempt == 2 else ""))
                # Retry only when the response looks truncated (many missing)
                if missing <= max(1, len(chunk) // 4) or attempt == 2:
                    return accepted
                attempt_max_tokens = 9000
            except Exception as exc:
                if attempt == 2:
                    print(f"   ⚠️ Polish pass: chunk {chunk_no}/{total} failed: {type(exc).__name__}: {exc} — originals kept")
                else:
                    print(f"   ⚠️ Polish pass: chunk {chunk_no}/{total} attempt 1 failed ({type(exc).__name__}), retrying...")
        return {}

    # Chunks are independent (context comes from the original transcript), so
    # they run concurrently - this stage was N sequential LLM calls before.
    from concurrent.futures import ThreadPoolExecutor
    _enh_workers = min(4, max(1, len(chunks)))
    with ThreadPoolExecutor(max_workers=_enh_workers) as _pool:
        for _chunk, _res in zip(chunks, _pool.map(
                lambda args: _enhance_chunk(args[0], args[1]),
                enumerate(chunks, 1))):
            corrected.update(_res)

    # --- Materialize corrections + emoji into words (positional sync, B)
    # Each corrected token that matches a Whisper word inherits its timing;
    # inserted tokens (including emoji) get narrow synthetic slots.
    import re as _re_emoji_mat
    _emoji_re_mat = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\u2B50\u2764\U0001F900-\U0001F9FF]")

    def _norm_for_match(t):
        # strip punct, lower — emoji kept verbatim
        if _emoji_re_mat.search(t):
            return t.strip()
        return t.strip().lower().strip(".,!?;:'\"()[]{}")

    changed = 0
    for idx, seg in enumerate(segs):
        correction = corrected.get(idx)
        if not correction:
            continue
        raw_text = str(correction.get("text", "")).strip()
        emoji = str(correction.get("emoji", "") or "").strip()
        if not raw_text and not emoji:
            continue
        # Build corrected token list (emoji as its own token at end if not already present)
        corr_tokens = [t for t in raw_text.split() if t]
        if emoji:
            # emoji may be multi-codepoint (ZWJ) — keep as single token
            if emoji not in corr_tokens and emoji not in raw_text:
                corr_tokens.append(emoji)
            elif emoji not in corr_tokens:
                # ensure emoji considered even if inside raw_text attached
                # split already handles 'hello😁' -> will be one token containing emoji;
                # normalize by ensuring emoji token exists
                if not any(e == emoji or emoji in e for e in corr_tokens):
                    corr_tokens.append(emoji)
        if not corr_tokens:
            continue

        orig_words = seg.get("words") or []
        # Keep original segment bounds for synthetic placement
        try:
            seg_s = float(seg.get("start", orig_words[0].get("start", 0) if orig_words else 0))
            seg_e = float(seg.get("end", orig_words[-1].get("end", seg_s + 1) if orig_words else seg_s + 1))
        except Exception:
            seg_s, seg_e = 0.0, 1.0

        orig_norms = [_norm_for_match(str(w.get("word", "")).strip()) for w in orig_words]
        corr_norms = [_norm_for_match(t) for t in corr_tokens]

        m, n = len(orig_norms), len(corr_norms)
        # LCS DP with time-proximity bias: when scores tie, prefer match with smaller time distance
        # This prevents duplicate words like "no" at 1.2s snapping to 37.5s occurrence.
        seg_dur = max(0.5, seg_e - seg_s)
        dp = [[0]*(n+1) for _ in range(m+1)]
        # Also store best time distance for tie-breaking (lower is better)
        # We compute dp bottom-up; for equal scores we keep the one with smaller future distance
        for ii in range(m-1, -1, -1):
            oi = orig_norms[ii]
            row = dp[ii]
            row_next = dp[ii+1]
            ws_ii = float(orig_words[ii].get("start", seg_s)) if ii < len(orig_words) else seg_s
            exp_ii = seg_s + (seg_dur * 0.5)  # fallback, refined per jj
            for jj in range(n-1, -1, -1):
                if oi and oi == corr_norms[jj]:
                    row[jj] = row_next[jj+1] + 1
                else:
                    a = row_next[jj]
                    b = row[jj+1]
                    if a > b:
                        row[jj] = a
                    elif b > a:
                        row[jj] = b
                    else:
                        # equal score — prefer path with smaller time distance for future matches
                        # estimate: if we skip orig (a) vs skip corr (b), which keeps closer times?
                        # Use heuristic: picking the nearer word in time
                        # For simplicity, keep a (>=) but if b's next match is time-closer, pick b
                        # Compute distance if we were to match ii/jj
                        row[jj] = a  # default keep orig skip

        # Walk to collect matches (anchor pairs) with proximity check
        oi_idx = 0
        ci_idx = 0
        anchors = []  # list of (orig_i, corr_j)
        while oi_idx < m and ci_idx < n:
            if orig_norms[oi_idx] and orig_norms[oi_idx] == corr_norms[ci_idx]:
                # Time gate: reject match if word times are >4s apart from expected position
                ws = float(orig_words[oi_idx].get("start", seg_s))
                exp_t = seg_s + (seg_dur * (ci_idx / max(1, n)))  # expected time for this corr position
                if abs(ws - exp_t) > 4.0:
                    # Likely duplicate word from far segment — skip this orig occurrence
                    if dp[oi_idx+1][ci_idx] >= dp[oi_idx][ci_idx+1]:
                        oi_idx += 1
                    else:
                        ci_idx += 1
                    continue
                anchors.append((oi_idx, ci_idx))
                oi_idx += 1
                ci_idx += 1
            elif dp[oi_idx+1][ci_idx] >= dp[oi_idx][ci_idx+1]:
                oi_idx += 1
            else:
                ci_idx += 1

        # Build new_words preserving corrected order, inheriting timings for matches
        new_words = []
        # For gap handling, track last emitted end
        # We emit in corrected order: for each corr_j in 0..n-1
        anchor_map = {cj: oi for oi, cj in anchors}
        # Map corr_j -> orig_i for quick lookup
        for cj, tok in enumerate(corr_tokens):
            oi = anchor_map.get(cj)
            if oi is not None:
                ow = orig_words[oi]
                # Replace word text but keep timing; store with leading space like Whisper
                new_words.append({"word": " " + str(tok), "start": float(ow.get("start", seg_s)), "end": float(ow.get("end", seg_s + 0.2))})
            else:
                # Inserted token — synthetic timing with gap-aware emoji logic
                is_emoji_tok = bool(_emoji_re_mat.search(tok))
                want = 1.0 if is_emoji_tok else 0.22
                # Determine gap to next anchor or segment end
                prev_end = float(new_words[-1].get("end", seg_s)) if new_words else seg_s
                s_candidate = prev_end + 0.02 if new_words else seg_s + 0.02 + cj * (want + 0.02)
                # Find next anchor start
                next_start = None
                next_anchor_cj = None
                for aj in range(cj+1, n):
                    if aj in anchor_map:
                        next_anchor_cj = aj
                        break
                if next_anchor_cj is not None:
                    next_start = float(orig_words[anchor_map[next_anchor_cj]].get("start", seg_e))
                else:
                    next_start = seg_e
                gap = next_start - (prev_end + 0.02) if new_words else next_start - seg_s
                if is_emoji_tok:
                    if gap >= 1.0:
                        dur = 1.0
                        s = prev_end + 0.02 if new_words else seg_s + 0.02
                    elif gap >= 0.30:
                        dur = gap - 0.02  # use available
                        s = prev_end + 0.02 if new_words else seg_s + 0.02
                    else:
                        # Need to steal 0.30 - gap from previous word
                        if new_words:
                            need = 0.30 - max(0, gap)
                            prev = new_words[-1]
                            prev_s = float(prev.get("start", seg_s))
                            prev_e = float(prev.get("end", prev_s+0.2))
                            prev_dur = prev_e - prev_s
                            steal = min(need, max(0, prev_dur - 0.08))  # keep at least 80ms
                            prev["end"] = round(prev_e - steal, 3)
                            # adjust s after shrinking prev
                            s = float(prev["end"]) + 0.02
                            dur = 0.30
                            # ensure still inside segment and before next anchor
                            if s + dur + 0.02 > next_start:
                                s = max(seg_s, next_start - dur - 0.02)
                        else:
                            # No previous to steal from — just use gap (may be small)
                            dur = max(0.12, gap) if gap > 0 else 0.30
                            s = seg_s + 0.02
                    # Clamp dur inside remaining segment
                    if s + dur > seg_e:
                        dur = max(0.12, seg_e - s - 0.01)
                else:
                    dur = want
                    s = s_candidate
                    if s + dur + 0.02 > next_start:
                        s = max(seg_s, next_start - dur - 0.02)
                # Clamp inside segment
                if s < seg_s:
                    s = seg_s
                if s + dur > seg_e:
                    s = max(seg_s, seg_e - dur)
                    if s + dur > seg_e:
                        dur = max(0.08, seg_e - s - 0.01)
                e = s + dur
                if e <= s:
                    e = s + dur
                new_words.append({"word": " " + str(tok), "start": round(float(s), 3), "end": round(float(e), 3)})

        # Sort by start (synthetic inserts already in order, but ensure)
        new_words.sort(key=lambda w: float(w.get("start", 0)))
        # De-overlap: keep matched Whisper anchors fixed; shrink previous emoji instead of shifting next word
        for k in range(1, len(new_words)):
            prev_e = float(new_words[k-1].get("end", 0))
            cur_s = float(new_words[k].get("start", 0))
            if cur_s < prev_e + 0.01:
                overlap = (prev_e + 0.01) - cur_s
                prev = new_words[k-1]
                cur = new_words[k]
                # If previous is a synthetic emoji/insert, shrink it; else shift current (preserve anchor)
                prev_is_synthetic = bool(_emoji_re_mat.search(str(prev.get("word","")))) or float(prev.get("end",0)) - float(prev.get("start",0)) < 0.25
                if prev_is_synthetic:
                    # shrink previous to make room, keep current fixed
                    new_prev_e = float(prev.get("end", prev_e)) - overlap
                    # keep at least 80ms
                    if new_prev_e - float(prev.get("start",0)) >= 0.08:
                        prev["end"] = round(new_prev_e, 3)
                        continue
                # fallback: shift current forward (preserves previous, delays current slightly)
                shift = overlap
                new_words[k]["start"] = round(cur_s + shift, 3)
                new_words[k]["end"] = round(float(new_words[k].get("end", cur_s + 0.2)) + shift, 3)

        # Detect real change (text or word count)
        old_text = str(seg.get("text", "")).strip()
        new_text_joined = " ".join(corr_tokens)
        # Always update text to corrected joined form (emoji now a token)
        if new_text_joined != old_text or len(new_words) != len(orig_words) or any(
            str(new_words[k].get("word", "")).strip() != str(orig_words[k].get("word", "")).strip()
            for k in range(min(len(new_words), len(orig_words)))
        ):
            seg["text"] = new_text_joined
            seg["words"] = new_words
            changed += 1
            if emoji:
                print(f"   ✨ Emoji segment {idx}: {emoji} (words {len(orig_words)}->{len(new_words)})")
            else:
                print(f"   ✨ Corrected segment {idx}: words {len(orig_words)}->{len(new_words)}")

    if changed:
        transcript["text"] = " ".join(str(seg.get("text", "")).strip() for seg in segs if str(seg.get("text", "")).strip())
    print(f"✨ Polish pass finished: {changed}/{len(segs)} segments updated (word timings untouched)")
    try:
        _enhance_cost_drain.extend(_enhance_cost_sink)
    except Exception:
        pass
    return transcript


# Enhancement-call costs are captured into a module-level sink by
# _enhance_transcript_with_llm (it returns only the transcript) and drained by
# the pipeline right after the enhancement stage.
_enhance_cost_drain: list = []


def drain_enhance_costs() -> list:
    costs = list(_enhance_cost_drain)
    _enhance_cost_drain.clear()
    return costs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="AutoCrop-Vertical with Viral Clip Detection.")

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('-i', '--input', type=str, help="Path to the input video file.")
    input_group.add_argument('-u', '--url', type=str, help="YouTube URL to download and process.")

    parser.add_argument('-o', '--output', type=str, help="Output directory or file (if processing whole video).")
    parser.add_argument('--keep-original', action='store_true', help="Keep the downloaded YouTube video.")
    parser.add_argument('--skip-analysis', action='store_true', help="Skip AI analysis and convert the whole video.")
    parser.add_argument('--format', type=str, default="auto", choices=["auto", "vertical", "horizontal", "square"],
                        help="Output aspect: vertical/auto (9:16), horizontal (keep 16:9), square (1:1).")
    parser.add_argument('--game-profile-id', type=str, help="GameProfile ID to use for scoring.")
    parser.add_argument('--transcript', type=str,
                        help="Path to a precomputed transcript JSON (transcribe_media shape); skips transcription.")

    args = parser.parse_args()
    output_format = args.format

    script_start_time = time.time()

    def _ensure_dir(path: str) -> str:
        """Create directory if missing and return the same path."""
        if path:
            os.makedirs(path, exist_ok=True)
        return path

    # 1. Get Input Video
    if args.url:
        # For multi-clip runs, treat --output as an OUTPUT DIRECTORY (create it if needed).
        # For whole-video runs (--skip-analysis), --output can be a file path.
        if args.output and not args.skip_analysis:
            output_dir = _ensure_dir(args.output)
        else:
            # If output is a directory, use it; if it's a filename, use its directory; else default "."
            if args.output and os.path.isdir(args.output):
                output_dir = args.output
            elif args.output and not os.path.isdir(args.output):
                output_dir = os.path.dirname(args.output) or "."
            else:
                output_dir = "."

        input_video, video_title = download_youtube_video(args.url, output_dir)
    else:
        input_video = args.input
        video_title = os.path.splitext(os.path.basename(input_video))[0]

        if args.output and not args.skip_analysis:
            # For multi-clip runs, treat --output as an OUTPUT DIRECTORY (create it if needed).
            output_dir = _ensure_dir(args.output)
        else:
            # If output is a directory, use it; if it's a filename, use its directory; else default to input dir.
            if args.output and os.path.isdir(args.output):
                output_dir = args.output
            elif args.output and not os.path.isdir(args.output):
                output_dir = os.path.dirname(args.output) or os.path.dirname(input_video)
            else:
                output_dir = os.path.dirname(input_video)

    if not os.path.exists(input_video):
        print(f"❌ Input file not found: {input_video}")
        exit(1)

    # Layout choice is per SOURCE video, not per clip: one upload and one call
    # instead of one per clip, and the answer is a property of the material
    # ("this is a screencast"), which does not change between its own clips.
    # It runs before any render so the modules are switched on in time.
    if layout_picker.ENABLED:
        try:
            _cap = cv2.VideoCapture(input_video)
            _fps = _cap.get(cv2.CAP_PROP_FPS) or 30.0
            _duration = int(_cap.get(cv2.CAP_PROP_FRAME_COUNT)) / _fps
            _w = int(_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            _h = int(_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            _cap.release()
            from reframe_v2 import source_already_fits  # imports main back
            # A source already shot vertical has no width to reorganise, and
            # the render passes it through whatever the model says. Asking
            # anyway costs a Gemini call per upload to be ignored.
            if _w and _h and source_already_fits(_w, _h, ASPECT_RATIO):
                print(f"   ↕️  Source is {_w}x{_h} — already vertical, no layout to pick.")
            else:
                layout_picker.pick_and_apply(input_video, _duration)
        except Exception as e:
            print(f"⚠️ Layout choice skipped ({e}) — using the default layout.")

    # 2. Decision: Analyze clips or process whole?
    if args.skip_analysis:
        print("⏩ Skipping analysis, processing entire video...")
        # --output is documented as "directory or file". When it names a
        # directory we still need a filename: passing the directory through
        # ends up in os.remove() on it further down and dies with EACCES.
        output_file = args.output
        if (not output_file or os.path.isdir(output_file)
                or output_file.endswith(("/", os.sep))):
            output_file = os.path.join(output_dir, f"{video_title}_vertical.mp4")
        render_clip(input_video, output_file, output_format)
    else:
        # Get duration (needed by both the transcript and the vision path).
        cap = cv2.VideoCapture(input_video)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps
        cap.release()

        # 3. Transcribe — unless the video has no audio, in which case fall back
        # to Gemini vision (picks clips from the imagery instead of the speech).
        from transcribe_backends import NoAudioError
        transcript = None
        # Module handover (issue #68): another module already transcribed this
        # exact source with the same backend, so reuse its output. Any problem
        # with the file falls back to transcribing normally rather than failing.
        if args.transcript:
            try:
                with open(args.transcript, 'r') as f:
                    transcript = json.load(f)
                if not transcript.get('segments'):
                    raise ValueError("transcript has no segments")
                print(f"⏩ Reusing precomputed transcript "
                      f"({len(transcript['segments'])} segments) — skipping transcription.")
            except Exception as e:
                print(f"⚠️ Could not use precomputed transcript ({e}) — transcribing normally.")
                transcript = None
        if transcript is None:
            transcript = load_transcript_checkpoint(output_dir, input_video, duration)
            if transcript is not None:
                print(f"♻️ Reusing the transcript from the interrupted run "
                      f"({len(transcript['segments'])} segments) — skipping transcription.")
        if transcript is None:
            try:
                transcript = transcribe_video(input_video)
                save_transcript_checkpoint(output_dir, transcript, input_video, duration)
            except NoAudioError as e:
                print(f"🔇 {e} — switching to visual analysis.")

        # Music-only or wordless footage transcribes to a handful of words.
        # Clip it by what is on screen instead, like a video with no audio.
        if transcript is not None and speech_is_sparse(transcript, duration):
            n_words = sum(len((sg.get("text") or "").split()) for sg in transcript["segments"])
            print(f"🔇 Only {n_words} word(s) of speech in {duration:.0f}s — "
                  f"switching to visual analysis.")
            transcript = None

        # 3b. LLM subtitle enhancement (post-Whisper, pre-analysis).
        # The helper is chunked by character budget, so long VODs do not lose
        # their tail and a single failed chunk never aborts the entire pass.
        if transcript is not None:
            _enh_on = os.environ.get("ENABLE_SUBTITLE_ENHANCEMENT", "0") == "1"
            if _enh_on:
                try:
                    transcript = _enhance_transcript_with_llm(transcript)
                except Exception as _e_enh:
                    print(f"   ⚠️ Enhancement failed ({type(_e_enh).__name__}: {_e_enh}) — keeping original transcript")
            try:
                costs.extend(drain_enhance_costs())
            except Exception:
                pass

        # 4. Gemini Analysis (transcript-driven, or vision for silent videos)
        if transcript is not None:
            # Pass user_id from environment to get_viral_clips
            import os
            user_id = os.environ.get('USER_ID')
            clips_data = get_viral_clips(transcript, duration, args.game_profile_id or __import__('os').environ.get('GAME_PROFILE_ID'), user_id, video_path=input_video)
        else:
            clips_data = get_visual_clips(input_video, duration)

        if not clips_data or 'shorts' not in clips_data:
            # Deliberately fail instead of reframing the whole video: that path
            # wrote no metadata.json, so app.py marked the job failed anyway
            # (app.py:1087) after burning GPU on a render nobody could see.
            _err_provider = (AI_PROVIDER or os.environ.get("AI_PROVIDER") or "gemini").lower()
            _err_label = "OpenAI" if _err_provider == "openai" else "Gemini"
            raise RuntimeError(
                f"Clip detection failed — {_err_label} did not return usable clips for this video.")
        else:
            print(f"🔥 Found {len(clips_data['shorts'])} clips!")
            for _ci, _cc in enumerate(clips_data['shorts']):
                _orig = _cc.get('source_window_id') or _cc.get('source_window') or _cc.get('window_id') or '?'
                _is_deep = 'deep' in str(_orig).lower() or 'deep' in str(_cc.get('reason','')).lower()
                _tag = '🧠 DEEP' if _is_deep else '📦 cheap/transcript'
                _dbg(f"   {_tag} Clip {_ci+1}: {_cc['start']:.1f}-{_cc['end']:.1f}s {_orig} score={_cc.get('viral_score', _cc.get('predicted_score','?'))} reason={str(_cc.get('reason',''))[:100]}")

            # Save metadata. Silent videos have no transcript → no subtitles,
            # which is correct (there's no speech to caption).
            clips_data['transcript'] = transcript or {"language": "none", "segments": []}
            # The clip editor's re-render path needs to find the source video
            # again and reproduce the render settings, so record both. The
            # basename is enough — the file sits in the job dir (URL jobs with
            # --keep-original) or in uploads/ (upload jobs).
            clips_data['source_video'] = os.path.basename(input_video)
            clips_data['output_format'] = output_format
            metadata_file = os.path.join(output_dir, f"{video_title}_metadata.json")
            with open(metadata_file, 'w') as f:
                json.dump(clips_data, f, indent=2)
            print(f"   Saved metadata to {metadata_file}")

            # 5. Process clips in parallel: each worker cuts + renders one
            # clip. Renders are mostly ffmpeg subprocesses (parallelize well);
            # detector inference is serialized internally via DETECT_LOCK.
            def _process_one_clip(i, clip):
                start = clip['start']
                end = clip['end']
                _orig2 = clip.get('source_window_id') or clip.get('source_window') or '?'
                _is_deep2 = 'deep' in str(_orig2).lower() or 'deep' in str(clip.get('reason','')).lower()
                print(f"\n🎬 Processing Clip {i+1}: {start}s - {end}s {'[DEEP]' if _is_deep2 else ''} { _orig2}")
                print(f"   Title: {clip.get('video_title_for_youtube_short', 'No Title')}")

                clip_filename = f"{video_title}_clip_{i+1}.mp4"
                clip_temp_path = os.path.join(output_dir, f"temp_{clip_filename}")
                clip_final_path = os.path.join(output_dir, clip_filename)

                try:
                    # ffmpeg cut — re-encoding for precision on strict seconds
                    cut_command = [
                        'ffmpeg', '-y',
                        '-ss', str(start),
                        '-to', str(end),
                        '-i', input_video,
                        *video_encode_args(QUALITY_FAST),
                        *audio_encode_args(),
                        clip_temp_path
                    ]
                    subprocess.run(cut_command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

                    success = render_clip(clip_temp_path, clip_final_path, output_format)
                    # Layer order: watermark burns into the canonical (so any
                    # later hook replacement, which re-derives from it, keeps
                    # the branding), the hook is a derived hooked_ file, and
                    # captions go last on top of whichever is current. Each
                    # worker writes only its own clip dict, so the re-dump
                    # after the pool is race-free.
                    if success and os.environ.get("WATERMARK") == "1":
                        apply_watermark(clip_final_path)
                    deliver_path = clip_final_path
                    # Which stretches were stacked (SPLIT): captions go on the
                    # seam there, and /api/subtitle needs it again later.
                    import layout_ranges as _layouts
                    clip['layout_ranges'] = _layouts.read(clip_final_path)
                    # The hook was written from the transcript alone. When the
                    # render put this clip's meaning on the screen, rewrite hook
                    # and title from three of its frames BEFORE burning them.
                    if success and hook_grounding.wanted(clip['layout_ranges'], end - start):
                        hook_grounding.reground(clip_final_path, clip, transcript, start, end)
                    if success and os.environ.get("AUTO_HOOK") == "1":
                        hooked = auto_hook_clip(clip_final_path, clip)
                        if hooked:
                            deliver_path, clip['auto_hook'] = hooked
                    if success:
                        captioned = auto_caption_clip(
                            deliver_path, transcript, start, end,
                            split_ranges=_layouts.split_ranges(clip['layout_ranges']))
                        print(f"   ✅ Clip {i+1} ready: {clip_final_path}")
                        # Hand the API the file to actually serve for this clip.
                        # Without it the status poller guesses the clean reframe
                        # name, so a job in flight showed every clip stripped of
                        # its hook and captions until the WHOLE job finished and
                        # the result got rebuilt through _canonical_clip_file.
                        # Printed only after the full chain (reframe, watermark,
                        # hook, captions) so the file is complete when it is
                        # announced, never one that ffmpeg is still writing.
                        print(f"CLIP_READY {i} "
                              f"{os.path.basename(captioned or deliver_path)}")
                    return success
                finally:
                    if os.path.exists(clip_temp_path):
                        os.remove(clip_temp_path)

            clip_workers = max(int(os.environ.get("CLIP_WORKERS", "3")), 1)
            shorts = clips_data['shorts']
            with ThreadPoolExecutor(max_workers=min(clip_workers, len(shorts))) as pool:
                futures = {pool.submit(_process_one_clip, i, clip): i
                           for i, clip in enumerate(shorts)}
                for future in as_completed(futures):
                    i = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        print(f"   ❌ Clip {i+1} failed: {type(e).__name__}: {e}")

            # Persist per-clip render results added by the workers (auto_hook)
            # so the editor can see what is already burned into each clip.
            if any('auto_hook' in c or 'hook_grounding' in c for c in shorts):
                with open(metadata_file, 'w') as f:
                    json.dump(clips_data, f, indent=2)

    # Clean up original if requested
    if args.url and not args.keep_original and os.path.exists(input_video):
        os.remove(input_video)
        print(f"🗑️  Cleaned up downloaded video.")
    # The job finished: a later run in this directory must transcribe afresh.
    if not args.skip_analysis:
        clear_transcript_checkpoint(output_dir)

    total_time = time.time() - script_start_time
    print(f"\n⏱️  Total execution time: {total_time:.2f}s")
