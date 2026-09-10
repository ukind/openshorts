"""The editor frames arm: the request-scoped OpenAI-compatible path behind
/api/edit and /api/effects/generate when no Gemini key resolves.

conftest.py pins BILLING_ENABLED=0, so the endpoint tests run self-host and
stop exactly at the gate (unknown job_id → the 404 just past it) — no
ffmpeg, no network, no job fixtures.
"""
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
import editor

OPENAI = {"X-OpenAI-Key": "sk-test",
          "X-OpenAI-Model": "qwen2.5vl",
          "X-OpenAI-Base-Url": "http://localhost:11434/v1"}


@pytest.fixture
def client():
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_slate(monkeypatch):
    for k in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
              "GEMINI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(k, raising=False)


class _FakeProvider:
    model_name = "qwen2.5vl"

    def __init__(self, response=None, error=None):
        self._response = response if response is not None else {}
        self._error = error
        self.calls = []

    def generate_content(self, prompt, schema=None, messages=None, **kw):
        self.calls.append({"prompt": prompt, "schema": schema,
                           "messages": messages})
        if self._error is not None:
            raise self._error
        return {"response": self._response, "cost_analysis": None}


def _stub_frames(monkeypatch, frames):
    import layout_picker
    monkeypatch.setattr(layout_picker, "sample_frames",
                        lambda path, n=None, width=None: frames)


def _stub_build(monkeypatch, filter_string, applied):
    monkeypatch.setattr(editor, "build_filter_string",
                        lambda edits, **kw: (filter_string, applied))


# --- the gate predicate ------------------------------------------------------

def test_configured_needs_a_key_or_a_real_base():
    assert editor.openai_configured("", "") is False
    assert editor.openai_configured("", "https://api.openai.com/v1") is False
    assert editor.openai_configured("sk-x", "https://api.openai.com/v1") is True
    assert editor.openai_configured("", "http://localhost:11434/v1") is True


def test_vision_arm_needs_a_configuration():
    out, err = editor.openai_vision_arm("", "m", "https://api.openai.com/v1")
    assert out is None and "no OpenAI-compatible endpoint" in err


def test_vision_arm_builds_and_probes_the_provider(monkeypatch):
    import ai_provider
    made, probes = {}, []
    prov = _FakeProvider()
    monkeypatch.setattr(ai_provider, "create_ai_provider",
                        lambda *a, **k: (made.update(args=a, kwargs=k) or prov))
    monkeypatch.setattr(ai_provider, "probe_vision_support",
                        lambda p: probes.append(p) or "vision_ok")

    out, err = editor.openai_vision_arm("sk-x", "qwen2.5vl", "http://l:1/v1")

    assert out is prov and err is None
    assert made["args"][0] == "openai" and made["args"][1] == "qwen2.5vl"
    assert made["kwargs"]["base_url"] == "http://l:1/v1"
    assert made["kwargs"]["api_key"] == "sk-x"
    assert probes == [prov]


def test_vision_arm_refuses_a_text_only_model(monkeypatch):
    import ai_provider
    monkeypatch.setattr(ai_provider, "create_ai_provider",
                        lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(ai_provider, "probe_vision_support", lambda p: "no_vision")

    out, err = editor.openai_vision_arm("sk-x", "m", "http://l:1/v1")
    assert out is None and "cannot see images" in err


def test_vision_arm_degrades_on_a_missing_sdk(monkeypatch):
    import ai_provider

    def no_sdk(*a, **k):
        raise ImportError("No module named 'openai'")

    monkeypatch.setattr(ai_provider, "create_ai_provider", no_sdk)
    out, err = editor.openai_vision_arm("sk-x", "m", "http://l:1/v1")
    assert out is None and "SDK unavailable" in err


def test_the_arm_never_touches_the_satellite_client(monkeypatch):
    # No-double-route canary for this reroute: the arm is pipeline-family
    # (ai_provider + the resolved OPENAI_* triple); a leak into the satellite
    # client trips loudly. Kept here so tests/test_no_double_route.py stays
    # byte-unmodified.
    import llm_client

    def boom(*a, **k):
        raise AssertionError("the editor frames arm must not call the satellite client")

    monkeypatch.setattr(llm_client, "chat", boom)
    monkeypatch.setattr(llm_client, "active_config", boom)
    _stub_frames(monkeypatch, [b"jpg"])
    _stub_build(monkeypatch, None, [])
    prov = _FakeProvider(response={"edits": []})
    assert editor.frames_edit_plan(prov, "x.mp4", 10.0) == {
        "filter_string": None, "edits": []}


# --- the frames arms ---------------------------------------------------------

def test_frames_edit_plan_builds_the_same_filter_shape(monkeypatch):
    _stub_frames(monkeypatch, [b"jpg"] * 12)
    edits = [{"type": "punch_in", "start": 0.0, "end": 1.0,
              "strength": 0.1, "reason": "hook"}]
    prov = _FakeProvider(response={"edits": edits})
    built = {}

    def fake_build(e, **kw):
        built.update(edits=e, kw=kw)
        return "zoompan=1", edits

    monkeypatch.setattr(editor, "build_filter_string", fake_build)

    data = editor.frames_edit_plan(prov, "x.mp4", 10.0, fps=30, width=1080,
                                   height=1920, transcript=[{"text": "hi"}],
                                   has_captions=True)

    call = prov.calls[0]
    assert call["schema"] is editor.EditPlan
    assert call["prompt"].startswith('\n        You are a viral short-form')
    assert "CRITICAL: This video already has burned-in captions" in call["prompt"]
    content = call["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": call["prompt"]}
    assert len(content) == 13                       # the prompt + 12 frames
    assert content[1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(b"jpg").decode("ascii"))
    assert built["edits"] == edits                  # the model's list, untouched
    assert built["kw"]["has_captions"] is True
    assert data == {"filter_string": "zoompan=1", "edits": edits}


def test_frames_edit_plan_uses_the_shared_prompt_builder(monkeypatch):
    # One prompt, two transports: the frames arm must go through the SAME
    # builder the Gemini arm now uses, not a private copy.
    _stub_frames(monkeypatch, [b"jpg"])
    _stub_build(monkeypatch, None, [])
    prov = _FakeProvider(response={"edits": []})
    transcript = [{"text": "hi", "start": 0.0}]
    editor.frames_edit_plan(prov, "x.mp4", 10.0, fps=30, width=1080,
                            height=1920, transcript=transcript,
                            has_captions=True)
    expected = editor._edit_plan_prompt(10.0, 1080, 1920, transcript, True)
    assert prov.calls[0]["prompt"] == expected


def test_frames_edit_plan_degrades_without_frames(monkeypatch):
    _stub_frames(monkeypatch, [])
    prov = _FakeProvider()
    assert editor.frames_edit_plan(prov, "x.mp4", 10.0) == {
        "filter_string": None, "edits": []}
    assert prov.calls == []                         # never asked the model


def test_frames_edit_plan_empty_plan_keeps_the_clip(monkeypatch):
    _stub_frames(monkeypatch, [b"jpg"])
    _stub_build(monkeypatch, None, [])
    prov = _FakeProvider(response={"edits": []})
    assert editor.frames_edit_plan(prov, "x.mp4", 10.0) == {
        "filter_string": None, "edits": []}


def test_frames_effects_config_returns_segments(monkeypatch):
    _stub_frames(monkeypatch, [b"jpg"] * 12)
    segment = {"startSec": 0, "endSec": 3.5, "zoom": 1.05, "zoomCenterX": 0.5,
               "zoomCenterY": 0.5, "brightness": 1.0, "contrast": 1.0,
               "saturate": 1.0}
    prov = _FakeProvider(response={"segments": [segment]})

    cfg = editor.frames_effects_config(prov, "x.mp4", 10.0, fps=30,
                                       width=1080, height=1920)

    assert prov.calls[0]["schema"] is editor.EffectsPlan
    assert "Remotion-based renderer" in prov.calls[0]["prompt"]
    assert cfg == {"segments": [segment]}
    assert len(prov.calls[0]["messages"][0]["content"]) == 13


def test_frames_effects_config_without_frames_is_none(monkeypatch):
    _stub_frames(monkeypatch, [])
    prov = _FakeProvider()
    assert editor.frames_effects_config(prov, "x.mp4", 10.0) is None
    assert prov.calls == []


# --- the text-only repair ----------------------------------------------------

def test_openai_repair_filter_round_trips():
    prov = _FakeProvider(response={"filter_string": "eq=brightness=1.1"})
    out = editor.openai_repair_filter(prov, "broken", "err", 1080, 1920)
    assert out == "eq=brightness=1.1"
    assert prov.calls[0]["schema"] is editor.FilterRepair
    assert "FFMPEG ERROR" in prov.calls[0]["prompt"]
    assert "s=1080x1920" in prov.calls[0]["prompt"]


def test_openai_repair_filter_returns_none_on_junk():
    prov = _FakeProvider(response={"filter_string": "   "})
    assert editor.openai_repair_filter(prov, "b", "e", 1080, 1920) is None


def test_openai_repair_filter_never_raises():
    prov = _FakeProvider(error=RuntimeError("endpoint down"))
    assert editor.openai_repair_filter(prov, "b", "e", 1080, 1920) is None


# --- VideoEditor construction and repair routing ------------------------------

def test_video_editor_without_a_key_routes_repair_to_the_openai_arm(monkeypatch):
    routed = {}
    monkeypatch.setattr(editor, "openai_repair_filter",
                        lambda p, fs, err, w, h: routed.update(prov=p) or "fixed")
    prov = _FakeProvider()
    ed = editor.VideoEditor(api_key=None, openai_provider=prov)
    assert ed.client is None
    assert ed._repair_filter("broken", "err", 1080, 1920) == "fixed"
    assert routed["prov"] is prov


def test_video_editor_with_a_key_repairs_over_gemini(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a Gemini-keyed editor must not repair over the OpenAI arm")

    monkeypatch.setattr(editor, "openai_repair_filter", boom)
    ed = editor.VideoEditor(api_key="k")
    assert ed.client is not None

    sent = {}

    class _FakeModels:
        def generate_content(self, **kw):
            sent.update(kw)
            return SimpleNamespace(text='{"filter_string": "eq=1"}')

    ed.client = SimpleNamespace(models=_FakeModels())
    assert ed._repair_filter("broken", "err", 1080, 1920) == "eq=1"
    assert "FFMPEG ERROR" in sent["contents"]


def test_video_editor_with_a_key_keeps_its_client():
    ed = editor.VideoEditor(api_key="k")
    assert ed.client is not None and ed.openai_provider is None


# --- the endpoint gates --------------------------------------------------------

class TestEditGate:
    def test_no_backend_at_all_is_a_clean_400(self, client):
        r = client.post("/api/edit", json={"job_id": "nope", "clip_index": 0})
        assert r.status_code == 400
        assert r.json()["detail"] == "Missing X-Gemini-Key header"

    def test_a_refused_frames_arm_is_a_clean_400(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "openai_vision_arm",
                            lambda *a: (None, "the selected model cannot see images"))
        r = client.post("/api/edit", json={"job_id": "nope", "clip_index": 0},
                        headers=OPENAI)
        assert r.status_code == 400
        assert "cannot see images" in r.json()["detail"]

    def test_the_frames_arm_passes_the_gate(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "openai_vision_arm",
                            lambda *a: (_FakeProvider(), None))
        r = client.post("/api/edit", json={"job_id": "nope", "clip_index": 0},
                        headers=OPENAI)
        assert r.status_code == 404                 # past the gate; unknown job

    def test_a_gemini_key_wins_over_the_openai_headers(self, client, monkeypatch):
        def boom(*a):
            raise AssertionError("with a Gemini key the frames arm must stay off")

        monkeypatch.setattr(app_module, "openai_vision_arm", boom)
        r = client.post("/api/edit", json={"job_id": "nope", "clip_index": 0},
                        headers={**OPENAI, "X-Gemini-Key": "k"})
        assert r.status_code == 404                 # Gemini arm, past the gate


class TestEffectsGate:
    def test_no_backend_at_all_is_a_clean_400(self, client):
        r = client.post("/api/effects/generate",
                        json={"job_id": "nope", "clip_index": 0})
        assert r.status_code == 400
        assert r.json()["detail"] == "Missing X-Gemini-Key header"

    def test_a_refused_frames_arm_is_a_clean_400(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "openai_vision_arm",
                            lambda *a: (None, "the selected model cannot see images"))
        r = client.post("/api/effects/generate",
                        json={"job_id": "nope", "clip_index": 0},
                        headers=OPENAI)
        assert r.status_code == 400
        assert "cannot see images" in r.json()["detail"]

    def test_the_frames_arm_passes_the_gate(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "openai_vision_arm",
                            lambda *a: (_FakeProvider(), None))
        r = client.post("/api/effects/generate",
                        json={"job_id": "nope", "clip_index": 0},
                        headers=OPENAI)
        assert r.status_code == 404                 # past the gate; unknown job

    def test_a_gemini_key_wins_over_the_openai_headers(self, client, monkeypatch):
        def boom(*a):
            raise AssertionError("with a Gemini key the frames arm must stay off")

        monkeypatch.setattr(app_module, "openai_vision_arm", boom)
        r = client.post("/api/effects/generate",
                        json={"job_id": "nope", "clip_index": 0},
                        headers={**OPENAI, "X-Gemini-Key": "k"})
        assert r.status_code == 404                 # Gemini arm, past the gate
