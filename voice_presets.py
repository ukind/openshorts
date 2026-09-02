"""
Voice & Style Presets for the OpenShorts VoiceOver feature.

Voice presets are 12-dimension persona descriptions used by Qwen3-TTS
VoiceDesign ("instruct") to shape the generated voice. Style presets are
caption-generation prompts sent to Gemini/OpenAI.

Seed data is ported verbatim from the AutoShorts reference implementation
(C:/AI_Studio/autoshorts, src/ai_providers.py) so OpenShorts ships with the
same voices/styles out of the box. Persistence mirrors the local Game
Profiles repository: one JSON file per preset in voice_presets/<kind>/,
atomic writes, seeded on first use.
"""

import json
import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PRESETS_ROOT = Path(os.environ.get("VOICEOVER_PRESETS_DIR", "voice_presets"))

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

# ---------------------------------------------------------------------------
# Seed voice presets — verbatim Qwen3-TTS VoiceDesign personas from AutoShorts
# (src/ai_providers.py: ClipScore.VOICE_PRESET_MAP / CAPTION_STYLE_VOICE_MAP).
# ---------------------------------------------------------------------------

SEED_VOICE_PRESETS = [
    {
        "id": "action",
        "title": "Action Hype (fallback)",
        "prompt": """gender: Male.
pitch: Mid-range male pitch with sharp upward inflections during exciting moments.
speed: Very fast-paced, rapid-fire delivery matching gaming action.
volume: Loud and projecting, nearly shouting during intense plays.
age: Young adult, 20s to early 30s.
clarity: Highly articulate, every word distinct even at speed.
fluency: Extremely fluent with no hesitations, continuous flow.
accent: American English, neutral.
texture: Bright, energetic vocal quality with slight rasp.
emotion: Intense excitement, hype, constant enthusiasm.
tone: Upbeat, authoritative, commanding attention.
personality: Confident, extroverted, engaging, competitive edge.""",
        "kind": "generic",
        "tags": ["gaming", "auto"],
    },
    {
        "id": "gaming",
        "title": "Gaming Hype",
        "prompt": """gender: Male.
pitch: Mid-range male pitch with sharp upward inflections during exciting moments.
speed: Very fast-paced, rapid-fire delivery matching gaming action.
volume: Loud and projecting, nearly shouting during intense plays.
age: Young adult, 20s to early 30s.
clarity: Highly articulate, every word distinct even at speed.
fluency: Extremely fluent with no hesitations, continuous flow.
accent: American English, neutral.
texture: Bright, energetic vocal quality with slight rasp.
emotion: Intense excitement, hype, constant enthusiasm.
tone: Upbeat, authoritative, commanding attention.
personality: Confident, extroverted, engaging, competitive edge.""",
        "kind": "generic",
        "tags": ["gaming"],
    },
    {
        "id": "dramatic",
        "title": "Dramatic Cinematic",
        "prompt": """gender: Male.
pitch: Deep, resonant bass with powerful projection.
speed: Slow, deliberate pacing with strategic pauses for impact.
volume: Loud, commanding presence filling the space.
age: Mature adult, 40s to 50s.
clarity: Perfect diction, every word pronounced with gravitas.
fluency: Flawless delivery with cinematic timing.
accent: American English, neutral broadcast quality.
texture: Rich, velvety depth with cinematic warmth.
emotion: Inspiring, epic, grandiose.
tone: Heroic, momentous, like narrating legends.
personality: Authoritative, wise, larger-than-life presence.""",
        "kind": "generic",
        "tags": ["dramatic"],
    },
    {
        "id": "funny",
        "title": "Funny Commentary",
        "prompt": """gender: Male.
pitch: Mid to slightly high male pitch with playful variations.
speed: Moderate pace with deliberate pauses for comedic timing.
volume: Conversational, occasionally louder for punchlines.
age: Young adult, early to mid 20s.
clarity: Clear but relaxed, not overly precise.
fluency: Fluent with intentional hesitations for humor, occasional 'uh', 'like'.
accent: American English, casual GenZ cadence.
texture: Smooth, light vocal quality with natural warmth.
emotion: Amused, ironic, playfully sarcastic.
tone: Laid-back, chill, slightly deadpan with smirk energy.
personality: Witty, self-aware, relatable, gently mocking.""",
        "kind": "generic",
        "tags": ["funny"],
    },
    {
        "id": "minimal",
        "title": "Minimal Calm",
        "prompt": """gender: Male.
pitch: Low, steady pitch with minimal variation.
speed: Slow to moderate, unhurried and calm.
volume: Quiet to moderate, intimate and close.
age: Young to middle-aged adult, 25 to 35.
clarity: Clear but understated, effortless articulation.
fluency: Smooth and easy, natural flow.
accent: American English, neutral and unassuming.
texture: Soft, gentle vocal quality without harshness.
emotion: Calm, composed, subtly confident.
tone: Understated, reserved, quiet assurance.
personality: Introverted, thoughtful, self-contained.""",
        "kind": "generic",
        "tags": ["minimal"],
    },
    {
        "id": "genz",
        "title": "GenZ Vibes",
        "prompt": """gender: Male or Female (androgynous lean).
pitch: Mid-range with frequent upward inflections and vocal fry.
speed: Fast with casual slurring, modern speech patterns.
volume: Moderate, conversational social media energy.
age: Late teens to early 20s, Gen Z demographic.
clarity: Casual clarity, some words blend together naturally.
fluency: Very fluent but with filler words, 'literally', 'like', 'bruh'.
accent: American English, internet-influenced speech.
texture: Bright, youthful, slightly nasal quality.
emotion: Ironic detachment mixed with genuine enthusiasm.
tone: Casual, meme-aware, chronically online vibes.
personality: Self-aware, ironic, effortlessly cool, relatable chaos.""",
        "kind": "generic",
        "tags": ["genz"],
    },
    {
        "id": "story_news",
        "title": "Esports Newscaster",
        "prompt": """gender: Male.
pitch: Mid-range male pitch, energetic and dynamic.
speed: Fast-paced, building excitement with rapid-fire delivery.
volume: Loud, projected, arena-filling energy.
age: Young adult, late 20s to early 30s.
clarity: Sharp, punchy enunciation, esports casting precision.
fluency: Rapid-fire with hype pauses, building momentum.
accent: American English, energetic gaming culture.
texture: Bright, electric vocal quality with infectious enthusiasm.
emotion: Excited, hyped, passionate about the plays.
tone: Enthusiastic, analytical, building tension and release.
personality: Charismatic caster, knowledgeable, gets hyped with the audience.""",
        "kind": "generic",
        "tags": ["story_news"],
    },
    {
        "id": "story_roast",
        "title": "Roast Comedian",
        "prompt": """gender: Male.
pitch: Mid to high pitch with sarcastic inflections and exaggerated tones.
speed: Variable, speeding up for punchlines, slowing for emphasis.
volume: Moderate to loud, performative and theatrical.
age: Young adult, mid to late 20s.
clarity: Very clear, ensuring every barb lands perfectly.
fluency: Smooth with comedic pauses and timing.
accent: American English, comedy podcast energy.
texture: Bright with playful edge, slight smirk audible.
emotion: Amused mockery, playful cruelty, entertained.
tone: Sarcastic, teasing, roast-comedy style.
personality: Quick-witted, sharp-tongued, charismatic instigator.""",
        "kind": "generic",
        "tags": ["story_roast"],
    },
    {
        "id": "story_creepypasta",
        "title": "Creepypasta Narrator",
        "prompt": """gender: Male.
pitch: Deep, low pitch with minimal variation, ominous undertones.
speed: Very slow, deliberate, each word drawn out for tension.
volume: Quiet to moderate, intimate and unsettling closeness.
age: Middle-aged to older adult, 40s to 50s.
clarity: Crystal clear whisper-level articulation.
fluency: Controlled, methodical pacing building dread.
accent: American English, neutral with slight rasp.
texture: Dark, gravelly with shadowy depth.
emotion: Foreboding, sinister, building unease.
tone: Ominous, creeping horror, inevitable dread.
personality: Mysterious, unsettling, knows something you don't.""",
        "kind": "generic",
        "tags": ["story_creepypasta"],
    },
    {
        "id": "story_dramatic",
        "title": "Epic Storyteller",
        "prompt": """gender: Female.
pitch: Rich, resonant mid-range with expressive depth.
speed: Measured, deliberate pacing with dramatic pauses for impact.
volume: Commanding presence, clear projection with emotional range.
age: Mature adult, late 30s to 40s.
clarity: Perfect diction, every word delivered with intention.
fluency: Flawless delivery with cinematic timing and gravitas.
accent: American English, theatrical broadcast quality.
texture: Warm, velvety depth with captivating allure.
emotion: Intense, evocative, drawing listeners into the story.
tone: Epic, momentous, like narrating legends and tragedies.
personality: Wise, commanding, magnetic storyteller presence.""",
        "kind": "generic",
        "tags": ["story_dramatic"],
    },
]

# ---------------------------------------------------------------------------
# Seed style presets — verbatim caption-generation style guides from AutoShorts
# (src/ai_providers.py: _get_caption_prompt style_guides).
# ---------------------------------------------------------------------------

SEED_STYLE_PRESETS = [
    {
        "id": "gaming",
        "title": "Gaming",
        "kind": "generic",
        "prompt": """Generate short, punchy captions like gaming content creators use.
Examples: "HEADSHOT!", "clutch play incoming...", "wait for it...", "GG EZ", "POV: you're cracked"
Keep captions 1-5 words. Use ALL CAPS for emphasis on action moments.""",
    },
    {
        "id": "dramatic",
        "title": "Dramatic",
        "kind": "generic",
        "prompt": """Generate dramatic, cinematic captions.
Examples: "The final stand.", "Everything changed.", "No turning back now."
Keep captions short and impactful. Use lowercase for tension, CAPS for climax.""",
    },
    {
        "id": "funny",
        "title": "Funny",
        "kind": "generic",
        "prompt": """Generate humorous, meme-style captions.
Examples: "skill issue tbh", "when the plan works (it never does)", "*chuckles* I'm in danger"
Be self-aware and slightly chaotic. Gen-Z humor welcome.""",
    },
    {
        "id": "minimal",
        "title": "Minimal",
        "kind": "generic",
        "prompt": """Generate minimal, understated captions.
Examples: "nice.", "oh.", "well then."
Keep it subtle. Less is more. Max 3 words per caption.""",
    },
    {
        "id": "genz",
        "title": "GenZ Mode",
        "kind": "genz",
        "prompt": """Generate GenZ slang-heavy reactions and commentary.
Examples: "bruh 💀", "no cap this is insane", "he's locked in rn", "finna go crazy", "ate and left no crumbs"
Use modern slang naturally: bruh, no cap, finna, fr fr, ate, slay, lowkey, highkey, bussin, mid, L, W, rizz
Keep captions 1-6 words. Be authentic to GenZ internet culture. Emojis encouraged (💀🔥😭).""",
    },
    {
        "id": "story_news",
        "title": "Story: News",
        "kind": "story",
        "prompt": """Generate professional esports broadcaster narrative.
Examples: "And we're witnessing championship-level gameplay here.", "The positioning is absolutely impeccable.", "This could be the defining moment of the match."
Write 2-3 sentences per caption. Professional tone, clear analysis, building excitement. Fewer captions (2-4 max) with longer text.""",
    },
    {
        "id": "story_roast",
        "title": "Story: Roast",
        "kind": "story",
        "prompt": """Generate sarcastic, playful roasting commentary.
Examples: "Oh no. Oh no no no.", "Someone's definitely uninstalling after this.", "The audacity. The absolute audacity of this play."
Write 2-3 sentences per caption. Sarcastic but not mean, comedic timing, playful mockery. Fewer captions (2-4 max).""",
    },
    {
        "id": "story_creepypasta",
        "title": "Story: Creepypasta",
        "kind": "story",
        "prompt": """Generate horror-style tension narrative.
Examples: "Something felt wrong.", "The game knew what was about to happen.", "And then... it did."
Write 2-3 sentences per caption. Build tension, ominous tone, slow reveals. Fewer captions (2-4 max). Use ellipses for suspense.""",
    },
    {
        "id": "story_dramatic",
        "title": "Story: Dramatic",
        "kind": "story",
        "prompt": """Generate epic cinematic narration.
Examples: "In the arena of champions, legends are born.", "The crowd holds their breath.", "One shot. One chance. Immortality awaits."
Write 2-3 sentences per caption. Epic tone, powerful delivery, movie trailer style. Fewer captions (2-4 max).""",
    },
    {
        "id": "auto",
        "title": "Auto",
        "kind": "auto",
        "prompt": """Detect the video's vibe yourself (action hype, comedy, dramatic, chill) and generate captions that best fit it.
Examples: adapt to what you see — hype ALL CAPS for intense action, meme-style for fails, cinematic lines for dramatic moments.
Pick ONE consistent style across all captions and commit to it.""",
    },
]


def _atomic_write_json(filepath: Path, data: Dict[str, Any]) -> None:
    """Write JSON atomically (temp file + rename), same as game profiles."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", dir=filepath.parent,
                                     delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp, indent=2, ensure_ascii=False)
        tmp_name = tmp.name
    os.replace(tmp_name, filepath)


class PresetRepository:
    """JSON-file-backed CRUD for one preset kind (voice or style).

    Seed presets keep their stable ids (gaming, genz, story_news, ...);
    user-created presets get UUID ids. Files live in <root>/<kind>/<id>.json.
    """

    def __init__(self, kind: str, seeds: List[Dict[str, Any]],
                 allowed_tags: Optional[Any] = None):
        self.kind = kind
        self.dir = PRESETS_ROOT / kind
        self.seeds = seeds
        # Optional callable returning valid tag ids (used by the voice repo to
        # constrain tags to existing style preset ids).
        self.allowed_tags = allowed_tags
        self.dir.mkdir(parents=True, exist_ok=True)
        self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        """Write seeds on first run; on later runs, migrate built-in metadata.

        Migration refreshes seed `kind`/`tags` (e.g. classic->generic, voice
        style-tag rollout) while preserving any user edits to title/prompt.
        """
        seed_by_id = {s["id"]: s for s in self.seeds}
        existing = set()
        for f in self.dir.glob("*.json"):
            existing.add(f.stem)
            seed = seed_by_id.get(f.stem)
            if seed is None:
                continue
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (json.JSONDecodeError, IOError):
                continue
            migrated = dict(data)
            migrated["kind"] = seed.get("kind", data.get("kind"))
            if "tags" in seed:
                migrated["tags"] = seed["tags"]
            if migrated != data:
                migrated["updated_at"] = datetime.now().isoformat()
                _atomic_write_json(f, migrated)
        for seed in self.seeds:
            if seed["id"] in existing:
                continue
            now = datetime.now().isoformat()
            record = {**seed, "created_at": now, "updated_at": now}
            _atomic_write_json(self.dir / f"{seed['id']}.json", record)

    @staticmethod
    def _normalize_kind(kind: Any) -> str:
        """Slugify a user-supplied group name; legacy names map to generic."""
        k = re.sub(r"[^a-z0-9_-]+", "-", str(kind or "").lower()).strip("-")
        k = re.sub(r"-{2,}", "-", k)[:24]
        return k or "generic"

    def _validate_tags(self, tags: Any) -> List[str]:
        """Keep only tags that are ids of existing style presets."""
        if not isinstance(tags, list):
            return []
        if self.allowed_tags is None:
            return [str(t) for t in tags if str(t).strip()]
        valid = set(self.allowed_tags())
        return [str(t) for t in tags if str(t) in valid]

    def _safe_id(self, preset_id: str) -> str:
        if not _SAFE_ID.match(str(preset_id or "")):
            raise ValueError(f"Invalid preset ID: {preset_id}")
        return f"{preset_id}.json"

    def list_presets(self) -> List[Dict[str, Any]]:
        presets = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                data.setdefault("id", f.stem)
                # Legacy group names -> generic (read-time migration so old
                # files keep working even without a write pass). story/genz
                # remain valid style groups; the voice repo no longer uses them.
                legacy = ("classic", "custom") if self.kind != "voice" \
                    else ("classic", "custom", "story", "genz")
                if data.get("kind") in legacy:
                    data["kind"] = "generic"
                presets.append(data)
            except (json.JSONDecodeError, IOError):
                continue
        # Seeds first in canonical order, then user presets by creation time
        seed_order = {s["id"]: i for i, s in enumerate(self.seeds)}
        presets.sort(key=lambda p: (
            0 if p["id"] in seed_order else 1,
            seed_order.get(p["id"], 999),
            p.get("created_at", ""),
        ))
        return presets

    def get_preset(self, preset_id: str) -> Optional[Dict[str, Any]]:
        try:
            path = self.dir / self._safe_id(preset_id)
        except ValueError:
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("id", preset_id)
            return data
        except (json.JSONDecodeError, IOError):
            return None

    def create_preset(self, data: Dict[str, Any]) -> Dict[str, Any]:
        title = str(data.get("title") or "").strip()
        prompt = str(data.get("prompt") or "").strip()
        if not title:
            raise ValueError("Preset title is required")
        if not prompt:
            raise ValueError("Preset prompt is required")
        preset_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        record = {
            "id": preset_id,
            "title": title,
            "prompt": prompt,
            "kind": self._normalize_kind(data.get("kind")),
            "created_at": now,
            "updated_at": now,
        }
        # Voice presets: tags must reference existing style preset ids.
        tags = self._validate_tags(data.get("tags"))
        if self.kind == "voice":
            if not tags:
                raise ValueError("Voice presets need at least one style tag")
            record["tags"] = tags
        for key in ("elevenlabs_voice_id",):
            if data.get(key):
                record[key] = data[key]
        _atomic_write_json(self.dir / f"{preset_id}.json", record)
        return record

    def update_preset(self, preset_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        existing = self.get_preset(preset_id)
        if existing is None:
            return None
        merged = {**existing, **{k: v for k, v in data.items() if k != "id"}}
        merged["id"] = preset_id
        if "kind" in data and data["kind"] is not None:
            merged["kind"] = self._normalize_kind(data["kind"])
        if self.kind == "voice":
            tags = self._validate_tags(data.get("tags"))
            if data.get("tags") is not None:
                if not tags:
                    raise ValueError("Voice presets need at least one style tag")
                merged["tags"] = tags
        elif "tags" in merged:
            # Tags are a voice-only concept; don't persist them on styles.
            merged.pop("tags", None)
        merged["updated_at"] = datetime.now().isoformat()
        _atomic_write_json(self.dir / self._safe_id(preset_id), merged)
        return merged

    def delete_preset(self, preset_id: str) -> bool:
        # Seed presets are recreated on startup, so deleting them only makes
        # sense as a reset — allow it; they return on next server start is
        # NOT desirable, so block deletion of seeds instead.
        if any(s["id"] == preset_id for s in self.seeds):
            raise ValueError("Built-in presets cannot be deleted (they can be edited or duplicated)")
        try:
            path = self.dir / self._safe_id(preset_id)
        except ValueError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False

    def duplicate_preset(self, preset_id: str) -> Optional[Dict[str, Any]]:
        original = self.get_preset(preset_id)
        if original is None:
            return None
        copy = {k: v for k, v in original.items() if k not in ("id", "created_at", "updated_at")}
        copy["title"] = f"{copy.get('title', 'Preset')} (copy)"
        return self.create_preset(copy)

    def export_all(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "kind": self.kind,
            "exported_at": datetime.now().isoformat(),
            "count": len(self.list_presets()),
            f"{self.kind}_presets": self.list_presets(),
        }

    def import_presets(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        items = (payload.get(f"{self.kind}_presets")
                 or payload.get("presets")
                 or (payload if isinstance(payload, list) else []))
        if isinstance(items, dict):
            items = [items]
        imported, skipped, errors = 0, [], []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title or not str(item.get("prompt") or "").strip():
                errors.append(title or "(untitled)")
                continue
            existing_titles = {p["title"] for p in self.list_presets()}
            if title in existing_titles:
                skipped.append(title)
                continue
            self.create_preset(item)
            imported += 1
        return {"imported": imported, "skipped": skipped, "errors": errors}


style_presets_repo = PresetRepository("style", SEED_STYLE_PRESETS)
# Voice tags must be one or more existing style preset ids — resolved live so
# user-created styles immediately become valid voice tags too.
voice_presets_repo = PresetRepository(
    "voice", SEED_VOICE_PRESETS,
    allowed_tags=lambda: [s["id"] for s in style_presets_repo.list_presets()])
