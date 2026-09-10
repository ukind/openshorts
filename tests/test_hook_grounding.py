"""Hook grounding: rewrite hook + title from a clip's frames when the render
put its meaning on the screen (hook_grounding.py).

The bug this fixes: a SCREENCAST clip of someone configuring an MCP
connector shipped with the hook "I automated my clips with AI", because the
detail pass only ever reads the transcript.
"""
import pytest

import gemini_worker

import base64
import ai_provider
import llm_client
import hook_grounding as hg


SCREEN = [{"start": 0, "end": 30, "layout": "screencast"}]
FACE = [{"start": 0, "end": 30, "layout": "track"}]
MIXED = [{"start": 0, "end": 5, "layout": "track"},
         {"start": 5, "end": 30, "layout": "wide"}]


# --- when to run -----------------------------------------------------------

def test_screen_layouts_trigger_and_face_layouts_do_not(monkeypatch):
    monkeypatch.setattr(hg, "screen_video", lambda: False)
    assert hg.wanted(SCREEN, 30) is True
    assert hg.wanted(MIXED, 30) is True
    assert hg.wanted(FACE, 30) is False
    assert hg.wanted([], 30) is False


def test_general_counts_as_screen_only_on_a_screencast_video(monkeypatch):
    # The reported case: picker said screencast, the render emitted
    # track/general only (stats cards have no face), grounding never ran.
    general = [{"start": 0, "end": 8, "layout": "track"},
               {"start": 8, "end": 30, "layout": "general"}]
    monkeypatch.setattr(hg, "screen_video", lambda: True)
    assert hg.wanted(general, 30) is True
    monkeypatch.setattr(hg, "screen_video", lambda: False)
    assert hg.wanted(general, 30) is False  # a group shot is not a screen


def test_a_blip_is_not_enough(monkeypatch):
    blip = [{"start": 0, "end": 2, "layout": "screencast"},
            {"start": 2, "end": 30, "layout": "track"}]
    assert hg.wanted(blip, 30) is False


def test_env_switch_disables(monkeypatch):
    monkeypatch.setenv("HOOK_GROUNDING", "0")
    assert hg.wanted(SCREEN, 30) is False


# --- inputs to the model ---------------------------------------------------

def test_frames_are_sampled_inside_the_screen_stretches():
    times = hg.sample_times(MIXED, 30, n=3)
    assert len(times) == 3
    assert all(5 <= t <= 30 for t in times)
    assert times == sorted(times)


def test_without_sidecar_frames_spread_over_the_clip():
    assert hg.sample_times([], 60, n=3) == [10, 30, 50]


def test_clip_words_uses_word_timestamps_and_falls_back_to_segment_text():
    transcript = {"segments": [
        {"start": 0, "end": 10, "text": "hello there",
         "words": [{"word": "hello", "start": 0, "end": 1},
                   {"word": "there", "start": 9, "end": 10}]},
        {"start": 10, "end": 20, "text": "no words here"},
        {"start": 40, "end": 50, "text": "outside"},
    ]}
    assert hg.clip_words(transcript, 8, 15) == "there no words here"


# --- the rewrite -----------------------------------------------------------

@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(hg, "frames_at", lambda path, times, width=None: [b"jpg"] * len(times))
    seen = {}

    def fake(frames, prompt, api_key):
        seen["frames"] = frames
        seen["prompt"] = prompt
        return {"on_screen": "Claude settings, MCP connector dialog for OpenShorts",
                "viral_hook_text": "Conectando OpenShorts a Claude por MCP",
                "video_title_for_youtube_short": "Así se conecta OpenShorts a Claude (MCP)"}

    monkeypatch.setattr(hg, "_ask_gemini", fake)
    return seen


def test_rewrites_hook_and_title_and_keeps_the_originals(gemini):
    clip = {"viral_hook_text": "He automatizado la creación de mis clips con IA.",
            "video_title_for_youtube_short": "Clips con IA",
            "layout_ranges": SCREEN}
    transcript = {"language": "es", "segments": [
        {"start": 100, "end": 110, "text": "añadimos el conector de OpenShorts"}]}

    changed = hg.reground("clip.mp4", clip, transcript, 100, 130)

    assert clip["viral_hook_text"] == "Conectando OpenShorts a Claude por MCP"
    assert clip["video_title_for_youtube_short"].startswith("Así se conecta")
    assert clip["hook_grounding"]["before"]["viral_hook_text"].startswith("He automatizado")
    assert changed["on_screen"].startswith("Claude settings")
    assert len(gemini["frames"]) == 3
    assert "añadimos el conector" in gemini["prompt"]
    assert "He automatizado" in gemini["prompt"]  # the model sees the current hook
    assert "TRANSCRIPT_LANGUAGE: es" in gemini["prompt"]


def test_without_a_gemini_key_the_transcript_hook_stands(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old" and "hook_grounding" not in clip


def test_model_failure_never_touches_the_clip(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(hg, "frames_at", lambda *a, **k: [b"jpg"])
    monkeypatch.setattr(hg, "_ask_gemini", lambda *a: (_ for _ in ()).throw(RuntimeError("503")))
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old"


def test_empty_answer_keeps_the_old_hook(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(hg, "frames_at", lambda *a, **k: [b"jpg"])
    monkeypatch.setattr(hg, "_ask_gemini", lambda *a: {"viral_hook_text": ""})
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old"


# --- the OpenAI-compatible arm (no Gemini key) ------------------------------

class _FakeProvider:
    model_name = "qwen2.5vl"


@pytest.fixture
def openai_arm(monkeypatch):
    """No Gemini key; a keyless local endpoint in the job OPENAI_* env; the
    factory, probe and frame extraction stubbed so no network is touched."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5vl")
    made = {}
    prov = _FakeProvider()

    def fake_create(*a, **k):
        made["create_args"] = a
        made["create_kwargs"] = k
        made["provider"] = prov
        return prov

    probes = []

    def fake_probe(p):
        probes.append(p)
        return "vision_ok"

    monkeypatch.setattr(ai_provider, "create_ai_provider", fake_create)
    monkeypatch.setattr(ai_provider, "probe_vision_support", fake_probe)
    monkeypatch.setattr(hg, "frames_at",
                        lambda path, times, width=None: [b"jpg"] * len(times))
    return {"made": made, "probes": probes}


def test_openai_arm_regrounds_the_hook(openai_arm, monkeypatch):
    seen = {}

    def fake(frames, prompt, provider):
        seen.update(frames=frames, prompt=prompt, provider=provider)
        return {"on_screen": "Ollama model list, qwen2.5vl downloading",
                "viral_hook_text": "El modelo local que ve tus clips",
                "video_title_for_youtube_short": "qwen2.5vl, el que sí ve"}

    monkeypatch.setattr(hg, "_ask_openai", fake)
    clip = {"viral_hook_text": "old", "video_title_for_youtube_short": "old title",
            "layout_ranges": SCREEN}

    changed = hg.reground("clip.mp4", clip, {"language": "es", "segments": []}, 0, 30)

    assert clip["viral_hook_text"] == "El modelo local que ve tus clips"
    assert clip["video_title_for_youtube_short"] == "qwen2.5vl, el que sí ve"
    assert clip["hook_grounding"]["before"]["viral_hook_text"] == "old"
    assert changed["on_screen"].startswith("Ollama model list")
    assert len(seen["frames"]) == 3
    made = openai_arm["made"]
    assert made["create_args"][0] == "openai"
    assert made["create_args"][1] == "qwen2.5vl"
    assert made["create_kwargs"]["base_url"] == "http://localhost:11434/v1"
    assert made["create_kwargs"]["api_key"] is None
    assert seen["provider"] is made["provider"]
    assert openai_arm["probes"] == [made["provider"]]   # probed once, the built provider


def test_openai_arm_skips_a_text_only_model(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setattr(ai_provider, "create_ai_provider",
                        lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(ai_provider, "probe_vision_support",
                        lambda p: "no_vision")

    def boom(*a, **k):
        raise AssertionError("a text-only model must never be asked")

    monkeypatch.setattr(hg, "_ask_openai", boom)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old" and "hook_grounding" not in clip


def test_injected_default_base_is_not_a_configuration(monkeypatch):
    # FIX 1 pin: app.py's job-env handover (both provider branches) writes the
    # OPENAI_* defaults — OPENAI_BASE_URL falls back to https://api.openai.com/v1,
    # the key to "" — into EVERY job env. A job gated only by the LLM_* family
    # must skip cleanly: no provider built, no probe, no per-clip calls
    # against the default endpoint.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    def boom(*a, **k):
        raise AssertionError("the injected default must not build a provider")

    monkeypatch.setattr(ai_provider, "create_ai_provider", boom)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old" and "hook_grounding" not in clip


def test_missing_openai_sdk_skips_cleanly(monkeypatch):
    # The openai import happens inside create_ai_provider (ai_provider.py:334)
    # and reground's caller is unguarded (main.py:3700), so a missing SDK must
    # degrade to a skip, never raise out of reground.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    def no_sdk(*a, **k):
        raise ImportError("No module named 'openai'")

    monkeypatch.setattr(ai_provider, "create_ai_provider", no_sdk)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is None
    assert clip["viral_hook_text"] == "old" and "hook_grounding" not in clip


def test_openai_arm_never_touches_the_satellite_client(openai_arm, monkeypatch):
    # No-double-route canary for this reroute (design ordering constraint):
    # the arm is pipeline-family (ai_provider + job OPENAI_* env); a leak into
    # the satellite client trips loudly. Mirrors _canary_llm_client in
    # tests/test_no_double_route.py, kept here so that file stays unmodified.
    def boom(*a, **k):
        raise AssertionError("hook grounding must not call the satellite client")

    monkeypatch.setattr(llm_client, "chat", boom)
    monkeypatch.setattr(llm_client, "active_config", boom)
    monkeypatch.setattr(hg, "_ask_openai", lambda *a: {
        "on_screen": "x", "viral_hook_text": "new hook",
        "video_title_for_youtube_short": "new title"})
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    changed = hg.reground("clip.mp4", clip, {"segments": []}, 0, 30)
    assert changed is not None
    assert clip["viral_hook_text"] == "new hook"


def test_a_gemini_key_wins_over_the_openai_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(hg, "frames_at", lambda *a, **k: [b"jpg"])
    monkeypatch.setattr(hg, "_ask_gemini", lambda *a: {
        "on_screen": "g", "viral_hook_text": "gemini hook",
        "video_title_for_youtube_short": "t"})

    def boom(*a, **k):
        raise AssertionError("with a Gemini key the OpenAI arm must stay off")

    monkeypatch.setattr(hg, "_ask_openai", boom)
    clip = {"viral_hook_text": "old", "layout_ranges": SCREEN}
    assert hg.reground("clip.mp4", clip, {"segments": []}, 0, 30) is not None
    assert clip["viral_hook_text"] == "gemini hook"


def test_ask_openai_sends_frames_as_image_parts():
    calls = {}

    class FakeProv:
        def generate_content(self, prompt, schema=None, messages=None, **kw):
            calls.update(prompt=prompt, schema=schema, messages=messages)
            return {"response": {"on_screen": "s", "viral_hook_text": "h",
                                 "video_title_for_youtube_short": "t"},
                    "cost_analysis": None}

    out = hg._ask_openai([b"jpg1", b"jpg2"], "PROMPT", FakeProv())

    assert out["viral_hook_text"] == "h"          # result["response"] — the _ask_gemini shape
    assert calls["schema"] is gemini_worker.GroundedHook
    content = calls["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "PROMPT"}
    assert content[1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(b"jpg1").decode("ascii"))
    assert len(content) == 3                      # one text part + two frames


def test_openai_provider_needs_a_key_or_a_real_base(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert hg._openai_provider() is None          # nothing configured → no arm
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert hg._openai_provider() is None          # the injected default is not one either

    fake = _FakeProvider()
    calls = []

    def fake_create(*a, **k):
        calls.append((a, k))
        return fake

    monkeypatch.setattr(ai_provider, "create_ai_provider", fake_create)

    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")  # keyless local
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5vl")
    prov = hg._openai_provider()
    assert prov is fake
    (a, k), = calls
    assert a[0] == "openai" and a[1] == "qwen2.5vl"
    assert k["base_url"] == "http://localhost:11434/v1"
    assert k["api_key"] is None

    calls.clear()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")  # default + key
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert hg._openai_provider() is fake         # a key on the default endpoint is a config
    (a, k), = calls
    assert k["base_url"] == "https://api.openai.com/v1"
    assert k["api_key"] == "sk-x"


# --- the cheap half: the detail prompt itself -------------------------------

def test_detail_prompt_forbids_topic_summary_hooks():
    assert "ABOUT THIS MOMENT, NOT THE VIDEO" in gemini_worker.DETAIL_PROMPT_TEMPLATE
    assert "{on_screen" not in gemini_worker.DETAIL_PROMPT_TEMPLATE  # no stray placeholders
    gemini_worker.GROUNDED_HOOK_PROMPT.format(
        language="es", current_hook="a", current_title="b", transcript="c")
