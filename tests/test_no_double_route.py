"""The stage-ownership boundary contract: one LLM system per stage.

After the parity merge the video pipeline (main.py) dispatches through
ai_provider and the satellite text stages (thumbnail / saasshorts /
layout_picker) dispatch through the third-party endpoint client. Both systems
configured at once is a legal deployment, so this file pins that no stage can
fire two calls: with AI_PROVIDER=openai + OPENAI_* AND a complete LLM_* triple
set together, the video side counts provider calls and trips a canary on the
satellite client, the satellite side does the mirror image, and the app layer
asserts the job env carries both families to the subprocess.

Ports the boundary intent of the deleted pipeline-branch tests, inverted: the
pipeline must now reach its calls with the satellite client counting ZERO.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as app_module
import llm_client

# Both systems on at once — the adversarial persona. If any stage could
# double-route, this is the config that shows it.
OPENAI_ENV = {"AI_PROVIDER": "openai",
              "OPENAI_API_KEY": "pipeline-key",
              "OPENAI_MODEL": "pipeline-model",
              "OPENAI_BASE_URL": "http://pipeline.test/v1"}
LLM_ENV = {"LLM_BASE_URL": "https://provider.test/v1",
           "LLM_API_KEY": "k",
           "LLM_MODEL": "satellite-model"}
BYOK = {"X-LLM-Base-Url": "https://byok.test/v1",
        "X-LLM-Key": "byok-secret-key",
        "X-LLM-Model": "byok-model"}


def _both_systems(monkeypatch):
    for k, v in {**OPENAI_ENV, **LLM_ENV}.items():
        monkeypatch.setenv(k, v)
    for k in ("GEMINI_API_KEY", "GEMINI_MODEL", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)


def _sat_cfg():
    return llm_client.LlmConfig(base_url=LLM_ENV["LLM_BASE_URL"],
                                api_key=LLM_ENV["LLM_API_KEY"],
                                model=LLM_ENV["LLM_MODEL"])


# --- canaries: each side trips loudly if the other system leaks in ----------

def _canary_llm_client(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the video pipeline must not call the satellite client")
    monkeypatch.setattr(llm_client, "chat", boom)
    monkeypatch.setattr(llm_client, "active_config", boom)


def _canary_ai_provider(monkeypatch):
    import ai_provider

    def boom(*a, **k):
        raise AssertionError("satellite stages must not build a pipeline provider")
    monkeypatch.setattr(ai_provider, "create_ai_provider", boom)


def _no_genai(monkeypatch):
    import google.genai as _g

    def boom(*a, **k):
        raise AssertionError("genai.Client must not be constructed here")
    monkeypatch.setattr(_g, "Client", boom)


# --- video side: main.get_viral_clips owns the ai_provider dispatch ---------

def _words(duration, step=5.0):
    # Their pipeline reads word['word']/['start']/['end'] (main.py words loop);
    # their own tests pin this shape (tests/test_process_handover.py:27).
    out, t, i = [], 0.0, 0
    while t + step < duration:
        out.append({"word": f"w{i}", "start": t, "end": t + step})
        t += step
        i += 1
    return out


def _transcript(duration):
    text = " ".join(w["word"] for w in _words(duration))
    return {"language": "en", "text": text,
            "segments": [{"start": 0.0, "end": duration, "text": text,
                          "words": _words(duration)}]}


def _install_pipeline_provider(monkeypatch, calls, schemas):
    import ai_provider

    class _Provider:
        def generate_content(self, prompt, schema=None, **kw):
            name = getattr(schema, "__name__", str(schema))
            schemas.append(name)
            if name == "ScoreResponse":
                return {"response": {"windows": [
                    {"id": "window_001", "start": 5.0, "end": 65.0,
                     "score": 90, "reason": "hook", "text": "hook"}]},
                        "cost_analysis": {"input_tokens": 1, "output_tokens": 1,
                                          "total_cost": 0.001}}
            if name == "DetailResponse":
                return {"response": {"shorts": [
                    {"start": 10.0, "end": 55.0, "title": "The moment",
                     "video_title_for_youtube_short": "The moment",
                     "predicted_score": 90}]},
                        "cost_analysis": {"input_tokens": 2, "output_tokens": 2,
                                          "total_cost": 0.002}}
            if name == "VODMetadataResponse":
                return {"response": {"vod_title": "T", "vod_description": "D"},
                        "cost_analysis": None}
            if name == "VisualResponse":
                return {"response": {"shorts": [
                    {"start": 10.0, "end": 55.0, "predicted_score": 90,
                     "video_description_for_tiktok": "d",
                     "video_description_for_instagram": "i",
                     "video_title_for_youtube_short": "The moment",
                     "viral_hook_text": "h"}]},
                    "cost_analysis": None}
            return {"response": {}, "cost_analysis": None}

    def fake_create(provider_type, model_name=None, **kw):
        calls.append((provider_type, model_name))
        return _Provider()

    monkeypatch.setattr(ai_provider, "create_ai_provider", fake_create)


class TestVideoSide:

    def test_two_pass_scores_and_details_through_the_pipeline_provider(self, monkeypatch):
        main = pytest.importorskip("main")
        _both_systems(monkeypatch)
        _canary_llm_client(monkeypatch)
        _no_genai(monkeypatch)
        calls, schemas = [], []
        _install_pipeline_provider(monkeypatch, calls, schemas)

        result = main.get_viral_clips(_transcript(300.0), 300.0)

        assert result and result["shorts"], "the two-pass must produce clips through the fake"
        assert calls, "the pipeline must actually call its provider"
        assert {p for p, _ in calls} == {"openai"}
        assert {"ScoreResponse", "DetailResponse",
                "VODMetadataResponse"} <= set(schemas)

    def test_short_video_whole_clip_path_uses_the_same_provider(self, monkeypatch):
        main = pytest.importorskip("main")
        _both_systems(monkeypatch)
        _canary_llm_client(monkeypatch)
        _no_genai(monkeypatch)
        calls, schemas = [], []
        _install_pipeline_provider(monkeypatch, calls, schemas)

        result = main.get_viral_clips(_transcript(20.0), 20.0)

        assert result and result["shorts"]
        assert calls and {p for p, _ in calls} == {"openai"}
        assert "DetailResponse" in schemas


    def test_short_video_cheap_events_fill_the_detail_payload(self, monkeypatch, tmp_path):
        main = pytest.importorskip("main")
        import cheap_events
        import ai_provider
        _both_systems(monkeypatch)
        _canary_llm_client(monkeypatch)
        _no_genai(monkeypatch)
        calls, schemas = [], []
        _install_pipeline_provider(monkeypatch, calls, schemas)
        video_path = tmp_path / "short.mp4"
        video_path.write_bytes(b"placeholder")  # exists → the guard admits it
        fake_events = [
            {"timestamp": 5.0, "type": "scene_change"},
            {"timestamp": 12.0, "type": "scream"},
        ]
        monkeypatch.setattr(cheap_events, "extract_cheap_events",
                            lambda *a, **k: list(fake_events))
        prompts = []
        _patched_create = ai_provider.create_ai_provider
        def recording_create(*a, **kw):
            prov = _patched_create(*a, **kw)
            real_gc = prov.generate_content

            def gc(prompt, schema=None, **k2):
                prompts.append(prompt)
                return real_gc(prompt, schema, **k2)

            prov.generate_content = gc
            return prov

        monkeypatch.setattr(ai_provider, "create_ai_provider", recording_create)

        result = main.get_viral_clips(_transcript(20.0), 20.0, video_path=str(video_path))

        assert result and result["shorts"]
        assert prompts, "the detail pass must run"
        # prompts[-1] is the VOD-metadata prompt (it also goes through the
        # factory) — select the detail prompt by its payload marker.
        detail_prompt = next(p for p in prompts if '"id": "whole_clip"' in p)
        # audio_events carries ALL in-window events (long-path shape).
        assert '"audio_events": "5.0s:scene_change, 12.0s:scream"' in detail_prompt
        assert '"scene_boundaries": "5.0s"' in detail_prompt
        assert '"audio_events": "none"' not in detail_prompt

# --- satellite side: text stages own the third-party client -----------------

class TestSatelliteSide:

    def test_thumbnail_stages_stay_on_the_thirdparty_client(self, monkeypatch):
        thumb = pytest.importorskip("thumbnail")
        import layout_picker
        _canary_ai_provider(monkeypatch)
        _no_genai(monkeypatch)
        calls = []

        def fake_chat(prompt, schema=None, *, config=None, **kw):
            calls.append(getattr(config, "model", None))
            if "art director" in prompt:  # plan_thumbnail_concepts (json mode)
                return json.dumps({"concepts": [
                    {"text": "WOW", "text_position": "left",
                     "text_color": "yellow",
                     "scene": "a desk with a phone showing vertical clips",
                     "why": "curiosity"}]}), None
            if "Brainstorm" in prompt:  # analyze pass 1
                return json.dumps({"transcript_summary": "s",
                                   "candidates": ["T1", "T2"]}), None
            return json.dumps({"titles": ["Best T"], "thumbnail_texts": ["WOW"],
                               "recommended": []}), None

        monkeypatch.setattr(llm_client, "chat", fake_chat)
        monkeypatch.setattr(layout_picker, "sample_frames",
                            lambda *a, **k: [b"\xff\xd8fake"])
        cfg = _sat_cfg()

        analyzed = thumb.analyze_video_for_titles(
            None, "/nonexistent.mp4",
            transcript={"language": "en", "text": "hi",
                        "segments": [{"start": 0, "end": 5, "text": "hi",
                                      "words": []}]},
            llm_config=cfg)
        assert analyzed["titles"] == ["Best T"]

        refined = thumb.refine_titles(None, analyzed, "make them shorter",
                                      llm_config=cfg)
        assert refined["titles"] == ["Best T"]

        concepts = thumb.plan_thumbnail_concepts(None, "Best T", 1,
                                                 video_context="a video",
                                                 llm_config=cfg)
        assert concepts and concepts[0]["text"] == "WOW"
        assert calls and all(m == "satellite-model" for m in calls)

    def test_saas_stages_stay_on_the_thirdparty_client(self, monkeypatch):
        saas = pytest.importorskip("saasshorts")
        _canary_ai_provider(monkeypatch)
        _no_genai(monkeypatch)
        cfg = _sat_cfg()
        scraped = {"url": "https://x.test", "title": "T",
                   "meta_description": "", "headings": [],
                   "main_content": "c", "additional_pages": []}

        monkeypatch.setattr(
            llm_client, "chat",
            lambda prompt, schema=None, *, config=None, **kw:
            (json.dumps({"product_name": "P", "pain_points": []}), None))
        out = saas.analyze_saas(scraped, None, llm_config=cfg)
        assert out["product_name"] == "P"

        monkeypatch.setattr(
            llm_client, "chat",
            lambda prompt, schema=None, *, config=None, **kw:
            (json.dumps([{"title": "s1", "style": "ugc", "duration_seconds": 23,
                          "target_platform": "tiktok", "hook_text": "h",
                          "segments": []}]), None))
        scripts = saas.generate_scripts({"product_name": "P"}, None,
                                        llm_config=cfg)
        assert isinstance(scripts, list) and scripts[0]["hook_text"] == "h"

    def test_layout_pick_stays_on_the_thirdparty_client(self, monkeypatch):
        lp = pytest.importorskip("layout_picker")
        monkeypatch.setattr(lp, "ENABLED", True)  # import-time env read
        _canary_ai_provider(monkeypatch)
        _no_genai(monkeypatch)
        for k, v in LLM_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setattr(
            llm_client, "chat",
            lambda prompt, schema, *, config=None, images=None, **kw:
            ({"layout": "screencast", "confidence": 0.9,
              "why": "wide spreadsheet"}, None))
        monkeypatch.setattr(lp, "sample_frames",
                            lambda *a, **k: [b"\xff\xd8fake"])

        assert lp.pick("/nonexistent.mp4", 300.0) == "screencast"


# --- app layer: the job env carries both families to the subprocess ---------

class TestAppLayerEnv:

    @pytest.fixture
    def client(self):
        return TestClient(app_module.app, raise_server_exceptions=False)

    @pytest.fixture(autouse=True)
    def _clean_slate(self, monkeypatch):
        for k in (*OPENAI_ENV, *LLM_ENV, "GEMINI_API_KEY", "GEMINI_MODEL"):
            monkeypatch.delenv(k, raising=False)

    def test_job_env_carries_both_key_families(self, client, monkeypatch):
        # The wrapper is the only consumer that could spawn the subprocess;
        # pin it to a no-op so this test only inspects the queued job.
        async def _parked(job_id):
            return None
        monkeypatch.setattr(app_module, "run_job_wrapper", _parked)

        res = client.post(
            "/api/process",
            data={"acknowledged": "true"},
            files={"file": ("source.mp4", b"placeholder bytes", "video/mp4")},
            headers={"X-AI-Provider": "openai",
                     "X-OpenAI-Key": "pipeline-key",
                     "X-OpenAI-Model": "pipeline-model",
                     "X-OpenAI-Base-Url": "http://pipeline.test/v1",
                     **BYOK})
        assert res.status_code == 200, res.text
        job_id = res.json()["job_id"]
        env = app_module.jobs[job_id]["env"]

        # The pipeline family (provider branch of the env writer).
        assert env["AI_PROVIDER"] == "openai"
        assert env["OPENAI_MODEL"] == "pipeline-model"
        assert env["OPENAI_BASE_URL"] == "http://pipeline.test/v1"
        # The satellite triple rides along: one system does not erase the other.
        assert env["LLM_BASE_URL"] == "https://byok.test/v1"
        assert env["LLM_API_KEY"] == "byok-secret-key"
        assert env["LLM_MODEL"] == "byok-model"
        # No Gemini key was offered, so none may leak into the child env.
        assert "GEMINI_API_KEY" not in env

        # Tidy: drop the queued job and its placeholder upload.
        app_module.jobs.pop(job_id, None)
        import glob as _glob
        import os as _os
        for p in _glob.glob(_os.path.join(app_module.UPLOAD_DIR, f"{job_id}_*")):
            _os.remove(p)

    # Precedent lesson (25222f5/56707b7): job gates break, not LLM code. Pin
    # that a KEYLESS openai silent-video job queues with a complete env (no
    # GEMINI_API_KEY required at launch).
    def test_keyless_openai_job_env_allows_silent_videos(self, client, monkeypatch):
        async def _parked(job_id):
            return None
        monkeypatch.setattr(app_module, "run_job_wrapper", _parked)
        res = client.post(
            "/api/process",
            data={"acknowledged": "true"},
            files={"file": ("source.mp4", b"placeholder bytes", "video/mp4")},
            headers={"X-AI-Provider": "openai",
                     "X-OpenAI-Model": "vision-model",
                     "X-OpenAI-Base-Url": "http://pipeline.test/v1",
                     **BYOK})
        assert res.status_code == 200, res.text
        job_id = res.json()["job_id"]
        env = app_module.jobs[job_id]["env"]
        assert env["AI_PROVIDER"] == "openai"
        assert env["OPENAI_MODEL"] == "vision-model"
        assert env["OPENAI_BASE_URL"] == "http://pipeline.test/v1"
        assert "GEMINI_API_KEY" not in env
        # Tidy: drop the queued job and its placeholder upload.
        app_module.jobs.pop(job_id, None)
        import glob as _glob
        import os as _os
        for p in _glob.glob(_os.path.join(app_module.UPLOAD_DIR, f"{job_id}_*")):
            _os.remove(p)


class TestCapabilityGates:
    def _long_video_env(self, monkeypatch, tmp_path, verdict):
        main = pytest.importorskip("main")
        import ai_provider
        _both_systems(monkeypatch)
        _canary_llm_client(monkeypatch)
        _no_genai(monkeypatch)
        calls, schemas = [], []
        _install_pipeline_provider(monkeypatch, calls, schemas)
        monkeypatch.setattr(main, "ENABLE_DEEP_ANALYSIS", True)
        monkeypatch.setattr(main, "ENABLE_VISION_ANALYSIS", True)
        # NOTE: no file is written at video_path — the gates only need the
        # path TRUTHY, and a nonexistent path makes the cheap-events guard
        # skip cleanly (no cv2 probing of a placeholder file).
        seen = {"extract": 0}
        def fake_extract(*a, **k):
            seen["extract"] += 1
            return []

        monkeypatch.setattr(main, "extract_frames_from_window", fake_extract)
        monkeypatch.setattr(ai_provider, "probe_vision_support",
                            lambda prov: verdict)
        return main, seen

    def test_no_vision_skips_deep_and_vision_with_zero_extractions(
            self, monkeypatch, tmp_path, capsys):
        main, seen = self._long_video_env(monkeypatch, tmp_path, "no_vision")
        result = main.get_viral_clips(_transcript(300.0), 300.0,
                                      video_path=str(tmp_path / "clip.mp4"))
        assert result and result["shorts"], "pipeline still completes via heuristics"
        assert seen["extract"] == 0, "zero frame extractions after no_vision"
        out = capsys.readouterr().out
        assert "👁️ Deep scan skipped" in out
        assert "👁️ Vision analysis skipped" in out

    def test_vision_ok_verdict_still_extracts_frames(self, monkeypatch, tmp_path):
        main, seen = self._long_video_env(monkeypatch, tmp_path, "vision_ok")
        result = main.get_viral_clips(_transcript(300.0), 300.0,
                                      video_path=str(tmp_path / "clip.mp4"))
        assert result and result["shorts"]
        assert seen["extract"] >= 1, "a vision_ok verdict must not block extraction"


# --- Phase 4: silent-video path owns the pipeline provider ---------------

class TestSilentPath:

    def test_silent_openai_job_routes_through_the_pipeline_provider(
            self, monkeypatch, tmp_path, capsys):
        main = pytest.importorskip("main")
        import ai_provider
        _both_systems(monkeypatch)
        _canary_llm_client(monkeypatch)
        _no_genai(monkeypatch)
        calls, schemas = [], []
        _install_pipeline_provider(monkeypatch, calls, schemas)
        monkeypatch.setattr(ai_provider, "probe_vision_support", lambda prov: "vision_ok")
        monkeypatch.setattr(
            main, "extract_frames_from_window",
            lambda *a, **k: [{"frame_index": i,
                              "base64_image": "data:image/jpeg;base64,AAAA"}
                             for i in range(12)])

        result = main.get_visual_clips(str(tmp_path / "silent.mp4"), 300.0)

        assert result and result["shorts"], "the silent pass must produce clips through the fake"
        assert calls and {p for p, _ in calls} == {"openai"}
        assert "VisualResponse" in schemas
        for _c in result["shorts"]:
            assert 0.0 <= _c["start"] < _c["end"] <= 300.0
        out = capsys.readouterr().out
        assert "Silent video — analyzing with Openai vision" in out

    def test_silent_openai_text_only_model_skips_honestly(
            self, monkeypatch, tmp_path, capsys):
        main = pytest.importorskip("main")
        import ai_provider
        _both_systems(monkeypatch)
        _canary_llm_client(monkeypatch)
        _no_genai(monkeypatch)
        calls, schemas = [], []
        _install_pipeline_provider(monkeypatch, calls, schemas)
        monkeypatch.setattr(ai_provider, "probe_vision_support", lambda prov: "no_vision")
        seen = {"extract": 0}

        def fake_extract(*a, **k):
            seen["extract"] += 1
            return []

        monkeypatch.setattr(main, "extract_frames_from_window", fake_extract)

        result = main.get_visual_clips(str(tmp_path / "silent.mp4"), 300.0)

        assert result is None
        assert seen["extract"] == 0, "zero screenshots after a no_vision verdict"
        assert "👁️ Vision analysis skipped" in capsys.readouterr().out

    def test_silent_gemini_without_key_returns_none_gracefully(
            self, monkeypatch, capsys):
        main = pytest.importorskip("main")
        _no_genai(monkeypatch)
        _canary_llm_client(monkeypatch)  # the wall is gone — pin it on gemini too
        monkeypatch.setenv("AI_PROVIDER", "gemini")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        assert main.get_visual_clips("/nonexistent.mp4", 60.0) is None
        assert "GEMINI_API_KEY not configured" in capsys.readouterr().out

    def test_silent_gemini_attaches_the_uploaded_file(
            self, monkeypatch, tmp_path, capsys):
        main = pytest.importorskip("main")
        import google.genai as _g
        monkeypatch.setenv("AI_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        recorded = {}

        class _FakeFiles:
            def upload(self, file=None):
                recorded["uploaded"] = file
                return SimpleNamespace(name="files/abc")

            def get(self, name=None):
                return SimpleNamespace(state=SimpleNamespace(name="ACTIVE"))

            def delete(self, name=None):
                recorded["deleted"] = name

        class _FakeModels:
            def generate_content(self, model=None, contents=None, config=None):
                recorded["contents"] = list(contents)
                recorded["model"] = model
                return SimpleNamespace(text='{"shorts": [{"start": 10.0, "end": 55.0, '
                                              '"predicted_score": 90, '
                                              '"video_title_for_youtube_short": "The moment"}]}')

        fake_client = SimpleNamespace(files=_FakeFiles(), models=_FakeModels())
        monkeypatch.setattr(_g, "Client", lambda *a, **k: fake_client)

        video_path = tmp_path / "silent.mp4"
        video_path.write_bytes(b"placeholder")

        result = main.get_visual_clips(str(video_path), 300.0)

        assert result and result["shorts"]
        assert recorded["uploaded"] == str(video_path)
        # D4 pin: the upload is ATTACHED — contents[0] is the upload handle.
        assert recorded["contents"][0].name == "files/abc"
        assert isinstance(recorded["contents"][1], str) and recorded["contents"][1]
        assert recorded["deleted"] == "files/abc"  # finally-cleanup still runs
