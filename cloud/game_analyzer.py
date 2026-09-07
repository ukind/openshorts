"""AI analysis for GameProfile — classifies game type, traits, viral moments and
recommended scoring weights using Gemini (with deterministic fallback).

The 8 scoring dimensions are:
  emotional_reaction, surprise, tension, social_interaction, achievement, humor, outrage, novelty
Each weight is 0.0–1.0; they are not required to sum to 1 (they scale the
deterministic scorer).

If Gemini is unavailable, a fast heuristic based on genres/tags produces a
plausible classification so the UI remains functional offline.
"""
import json
import os
from typing import Dict, Any, List

TEXT_MODEL = os.environ.get("GEMINI_MODEL_THUMBNAIL") or os.environ.get("GEMINI_MODEL") or "gemini-3.1-flash-lite"

# Deterministic fallback table — genre/tag → weights
_FALLBACK_PROFILES = {
    "souls-like": {"game_type": "Souls-like Action RPG", "weights": {"emotional_reaction": 0.75, "surprise": 0.70, "tension": 0.95, "social_interaction": 0.30, "achievement": 0.85, "humor": 0.30, "outrage": 0.60, "novelty": 0.60}},
    "battle royale": {"game_type": "Battle Royale", "weights": {"emotional_reaction": 0.80, "surprise": 0.85, "tension": 0.90, "social_interaction": 0.50, "achievement": 0.75, "humor": 0.50, "outrage": 0.70, "novelty": 0.65}},
    "farming": {"game_type": "Farming / Life Sim", "weights": {"emotional_reaction": 0.55, "surprise": 0.40, "tension": 0.25, "social_interaction": 0.60, "achievement": 0.85, "humor": 0.45, "outrage": 0.20, "novelty": 0.40}},
    "moba": {"game_type": "MOBA", "weights": {"emotional_reaction": 0.85, "surprise": 0.75, "tension": 0.80, "social_interaction": 0.85, "achievement": 0.70, "humor": 0.40, "outrage": 0.75, "novelty": 0.60}},
    "fps": {"game_type": "FPS / Shooter", "weights": {"emotional_reaction": 0.75, "surprise": 0.80, "tension": 0.85, "social_interaction": 0.55, "achievement": 0.70, "humor": 0.35, "outrage": 0.65, "novelty": 0.60}},
    "horror": {"game_type": "Horror / Survival", "weights": {"emotional_reaction": 0.90, "surprise": 0.95, "tension": 0.95, "social_interaction": 0.40, "achievement": 0.50, "humor": 0.35, "outrage": 0.50, "novelty": 0.75}},
    "default": {"game_type": "General Action / Adventure", "weights": {"emotional_reaction": 0.65, "surprise": 0.65, "tension": 0.65, "social_interaction": 0.55, "achievement": 0.65, "humor": 0.50, "outrage": 0.50, "novelty": 0.55}},
}

def _heuristic_fallback(game_title: str, genres: List[str], tags: List[str]) -> Dict[str, Any]:
    text = " ".join([game_title] + genres + tags).lower()
    key = "default"
    if any(k in text for k in ["souls", "elden", "dark souls", "sekiro", "bloodborne"]):
        key = "souls-like"
    elif any(k in text for k in ["battle royale", "fortnite", "apex", "pubg", "warzone"]):
        key = "battle royale"
    elif any(k in text for k in ["farm", "stardew", "harvest", "farming", "agriculture"]):
        key = "farming"
    elif any(k in text for k in ["moba", "dota", "league of legends", "lol"]):
        key = "moba"
    elif any(k in text for k in ["fps", "shooter", "call of duty", "valorant", "counter-strike"]):
        key = "fps"
    elif any(k in text for k in ["horror", "survival horror", "scary"]):
        key = "horror"
    prof = _FALLBACK_PROFILES[key]
    return {
        "ai_analysis": {
            "game_type": prof["game_type"],
            "gameplay_characteristics": _fallback_characteristics(key),
            "key_moments": _fallback_key_moments(key),
        },
        "recommended_weights": prof["weights"],
    }

def _fallback_characteristics(key: str) -> List[str]:
    m = {
        "souls-like": ["high difficulty", "precise combat", "boss encounters", "exploration", "punishing deaths"],
        "battle royale": ["last-player-standing", "shrinking zone", "looting", "third-person combat", "squad play"],
        "farming": ["crop management", "daily routine", "crafting", "relationship building", "seasonal events"],
        "moba": ["team fights", "lane control", "hero abilities", "objective control", "comeback potential"],
        "fps": ["fast aiming", "map control", "clutch plays", "weapon economy", "positioning"],
        "horror": ["atmospheric tension", "limited resources", "jump scares", "stealth", "story discovery"],
        "default": ["progression", "exploration", "combat", "resource management", "social moments"],
    }
    return m.get(key, m["default"])

def _fallback_key_moments(key: str) -> List[str]:
    m = {
        "souls-like": ["epic boss kill", "unexpected death", "parry / dodge clutch", "rare loot drop", "co-op rescue"],
        "battle royale": ["clutch 1v3", "sniper headshot", "zone outplay", "unexpected betrayal", "victory royale"],
        "farming": ["rare crop harvest", "festival win", "gift success / fail", "mine discovery", "first marriage / event"],
        "moba": ["team wipe", "baron steal", "comeback throw", "pentakill", "perfect ultimate"],
        "fps": ["ace clutch", "no-scope", "defuse under pressure", "eco round win", "wallbang"],
        "horror": ["jump scare reaction", "close escape", "puzzle solve", "monster reveal", "unexpected death"],
        "default": ["surprise win", "funny fail", "achievement unlock", "emotional reaction", "team celebration"],
    }
    return m.get(key, m["default"])


def analyze_game(api_key: str, game_title: str, steam_description: str = "", steam_genres: List[str] = None, steam_tags: List[str] = None, provider: str = "gemini", openai_key: str = None, openai_model: str = None, openai_base_url: str = None) -> Dict[str, Any]:
    """Classify game and recommend weights via Gemini or OpenAI (same prompt). Falls back to heuristic."""
    steam_genres = steam_genres or []
    steam_tags = steam_tags or []
    fallback = _heuristic_fallback(game_title, steam_genres, steam_tags)
    provider = (provider or "gemini").lower()
    # OpenAI path
    if provider == "openai":
        # allow empty key for local servers (LM Studio / host.docker.internal)
        if openai_model is None:
            import os
            openai_model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        if openai_base_url is None:
            import os
            openai_base_url = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        if openai_key is None:
            openai_key = api_key  # fallback: reuse api_key if openai_key not provided
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key or "sk-local", base_url=openai_base_url)
            prompt_oa = f"""You are an expert game analyst for a viral clip editor. Given Steam metadata, classify the game and recommend how to weight 8 scoring dimensions for auto-detecting viral moments.

Game title: {game_title}
Description: {steam_description[:1200]}
Genres: {", ".join(steam_genres)}
Tags: {", ".join(steam_tags[:20])}

SCORING DIMENSIONS (0.0-1.0 each, independent — 8 total):
- emotional_reaction: strong feelings (joy, rage, shock, laughter)
- surprise: unexpected/out-of-context events
- tension: suspense, danger, close calls
- social_interaction: chat, teamwork, co-op, funny banter
- achievement: skill, wins, rare loot, progression
- humor: comedy, funny fails, banter, absurdity
- outrage: anger, controversy, rage moments
- novelty: unique, bizarre, never-seen-before

For this game, which dimensions matter most? Example: Souls-like → tension 0.95, surprise 0.70, achievement 0.85, social low. Farming sim → achievement 0.85, emotional 0.55, tension 0.25.

OUTPUT STRICT JSON (no markdown, no explanation outside JSON):
{{
  "ai_analysis": {{
    "game_type": "e.g. Souls-like Action RPG / Farming Life Sim / Battle Royale",
    "gameplay_characteristics": ["4-8 short traits, e.g. high difficulty, boss encounters, exploration"],
    "key_moments": ["4-8 viral moment types for this game, e.g. epic boss kill, unexpected death, rare loot"]
  }},
  "recommended_weights": {{
    "emotional_reaction": 0.0-1.0,
    "surprise": 0.0-1.0,
    "tension": 0.0-1.0,
    "social_interaction": 0.0-1.0,
    "achievement": 0.0-1.0,
    "humor": 0.0-1.0,
    "outrage": 0.0-1.0,
    "novelty": 0.0-1.0
  }}
}}
Weights: use one decimal place (e.g. 0.75). Keep them realistic — don't default to all 0.5."""
            # LM Studio / local servers don't support json_object — retry without it on 400
            try:
                resp = client.chat.completions.create(
                    model=openai_model,
                    messages=[{"role": "user", "content": prompt_oa}],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                )
            except Exception as _e:
                if "response_format" in str(_e) or "400" in str(_e):
                    print(f"[game_analyzer] json_object not supported, retrying without response_format: {_e}")
                    resp = client.chat.completions.create(
                        model=openai_model,
                        messages=[{"role": "user", "content": prompt_oa}],
                        temperature=0.3,
                    )
                else:
                    raise
            text_oa = (resp.choices[0].message.content or "").strip()
            import json as _json
            s, e = text_oa.find("{"), text_oa.rfind("}")
            if s != -1 and e != -1:
                text_oa = text_oa[s:e+1]
            data = _json.loads(text_oa)
            ai = data.get("ai_analysis", {})
            rw = data.get("recommended_weights", {})
            required_weights = ["emotional_reaction", "surprise", "tension", "social_interaction", "achievement", "humor", "outrage", "novelty"]
            if not all(k in rw for k in required_weights):
                raise ValueError("Missing weights")
            for k in required_weights:
                v = float(rw[k])
                rw[k] = round(max(0.0, min(1.0, v)), 2)
            ai["game_type"] = str(ai.get("game_type") or fallback["ai_analysis"]["game_type"])[:80]
            ai["gameplay_characteristics"] = [str(x)[:60] for x in (ai.get("gameplay_characteristics") or [])][:8]
            ai["key_moments"] = [str(x)[:60] for x in (ai.get("key_moments") or [])][:8]
            if not ai["gameplay_characteristics"]:
                ai["gameplay_characteristics"] = fallback["ai_analysis"]["gameplay_characteristics"]
            if not ai["key_moments"]:
                ai["key_moments"] = fallback["ai_analysis"]["key_moments"]
            print(f"[game_analyzer] OpenAI success: model={openai_model} base={openai_base_url} weights={rw}")
            return {"ai_analysis": ai, "recommended_weights": rw, "source": "openai"}
        except Exception as e:
            print(f"[game_analyzer] OpenAI failed, using fallback: {e}")
            return {**fallback, "source": "heuristic"}

    # Gemini path
    if not api_key:
        return {**fallback, "source": "heuristic"}

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)

        prompt = f"""You are an expert game analyst for a viral clip editor. Given Steam metadata, classify the game and recommend how to weight 8 scoring dimensions for auto-detecting viral moments.

Game title: {game_title}
Description: {steam_description[:1200]}
Genres: {", ".join(steam_genres)}
Tags: {", ".join(steam_tags[:20])}

SCORING DIMENSIONS (0.0-1.0 each, independent — 8 total):
- emotional_reaction: strong feelings (joy, rage, shock, laughter)
- surprise: unexpected/out-of-context events
- tension: suspense, danger, close calls
- social_interaction: chat, teamwork, co-op, funny banter
- achievement: skill, wins, rare loot, progression
- humor: comedy, funny fails, banter, absurdity
- outrage: anger, controversy, rage moments
- novelty: unique, bizarre, never-seen-before

For this game, which dimensions matter most? Example: Souls-like → tension 0.95, surprise 0.70, achievement 0.85, social low. Farming sim → achievement 0.85, emotional 0.55, tension 0.25.

OUTPUT STRICT JSON (no markdown, no explanation outside JSON):
{{
  "ai_analysis": {{
    "game_type": "e.g. Souls-like Action RPG / Farming Life Sim / Battle Royale",
    "gameplay_characteristics": ["4-8 short traits, e.g. high difficulty, boss encounters, exploration"],
    "key_moments": ["4-8 viral moment types for this game, e.g. epic boss kill, unexpected death, rare loot"]
  }},
  "recommended_weights": {{
    "emotional_reaction": 0.0-1.0,
    "surprise": 0.0-1.0,
    "tension": 0.0-1.0,
    "social_interaction": 0.0-1.0,
    "achievement": 0.0-1.0,
    "humor": 0.0-1.0,
    "outrage": 0.0-1.0,
    "novelty": 0.0-1.0
  }}
}}
Weights: use one decimal place (e.g. 0.75). Keep them realistic — don't default to all 0.5."""

        response = client.models.generate_content(
            model=TEXT_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        text = (response.text or "").strip()
        # strip code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        # isolate JSON object
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1:
            text = text[s:e+1]
        data = json.loads(text)

        # Validate shape
        ai = data.get("ai_analysis", {})
        rw = data.get("recommended_weights", {})
        required_weights = ["emotional_reaction", "surprise", "tension", "social_interaction", "achievement", "humor", "outrage", "novelty"]
        if not all(k in rw for k in required_weights):
            raise ValueError("Missing weights")
        # Clamp 0-1 and round
        for k in required_weights:
            v = float(rw[k])
            rw[k] = round(max(0.0, min(1.0, v)), 2)
        # Clean arrays
        ai["game_type"] = str(ai.get("game_type") or fallback["ai_analysis"]["game_type"])[:80]
        ai["gameplay_characteristics"] = [str(x)[:60] for x in (ai.get("gameplay_characteristics") or [])][:8]
        ai["key_moments"] = [str(x)[:60] for x in (ai.get("key_moments") or [])][:8]
        if not ai["gameplay_characteristics"]:
            ai["gameplay_characteristics"] = fallback["ai_analysis"]["gameplay_characteristics"]
        if not ai["key_moments"]:
            ai["key_moments"] = fallback["ai_analysis"]["key_moments"]

        return {"ai_analysis": ai, "recommended_weights": rw, "source": "gemini"}

    except Exception as e:
        print(f"[game_analyzer] Gemini failed, using fallback: {e}")
        return {**fallback, "source": "heuristic"}
