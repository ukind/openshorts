import json
import os
import shutil
import subprocess
import time
import types

import httpx
import pytest

import comfyui_client
import llm_client
import saasshorts
import saasshorts_local
import tts_client

FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _ok_json(payload):
    return httpx.Response(200, json=payload)


@pytest.fixture
def mock_comfyui(monkeypatch):
    def install(handler, base="http://comfy.test"):
        monkeypatch.setenv("COMFYUI_URL", base)
        transport = httpx.MockTransport(handler)
        # fresh client per call: the module's "with _client(base) as c" closes it,
        # so a shared instance would fail the second request
        monkeypatch.setattr(
            comfyui_client, "_client",
            lambda b: httpx.Client(base_url=base, transport=transport, timeout=10.0),
        )
    return install


@pytest.fixture
def mock_tts(monkeypatch):
    def install(handler, base="http://tts.test"):
        monkeypatch.setenv("TTS_BASE_URL", base)
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            tts_client, "_client",
            lambda b: httpx.Client(base_url=base, transport=transport, timeout=10.0),
        )
    return install


class TestComfyUIClient:
    def test_run_stage_happy_path(self, mock_comfyui, tmp_path, monkeypatch):
        polls = {"n": 0}
        monkeypatch.setattr(time, "sleep", lambda s: None)

        def handler(request):
            if request.url.path == "/prompt":
                body = json.loads(request.read())
                assert body["prompt"]["3"]["inputs"]["text"] == "hello"
                return _ok_json({"prompt_id": "p1"})
            if request.url.path == "/history/p1":
                polls["n"] += 1
                if polls["n"] < 3:
                    return _ok_json({})
                return _ok_json({"p1": {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {"10": {"images": [
                        {"filename": "out_00001_.png", "subfolder": "openshorts", "type": "output"}
                    ]}},
                }})
            if request.url.path == "/queue":
                return _ok_json({"queue_running": [["1", "p1"]], "queue_pending": []})
            if request.url.path == "/view":
                assert request.url.params["filename"] == "out_00001_.png"
                assert request.url.params["type"] == "output"
                return httpx.Response(200, content=b"PNGDATA")
            raise AssertionError(f"unexpected path {request.url.path}")

        mock_comfyui(handler)
        dest = tmp_path / "portrait.png"
        out = comfyui_client.run_stage("portrait", {"3": {"text": "hello"}}, str(dest))
        assert out == str(dest)
        assert dest.read_bytes() == b"PNGDATA"
        assert polls["n"] == 3  # poll continued while the prompt was running

    def test_error_status_raises_naming_url(self, mock_comfyui, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)

        def handler(request):
            return _ok_json({"p1": {
                "status": {"status_str": "error", "messages": [["execution_error", {"node": "9"}]]},
                "outputs": {},
            }})

        mock_comfyui(handler)
        with pytest.raises(comfyui_client.ComfyUIError) as ei:
            comfyui_client.wait_for_output("p1")
        assert "http://comfy.test" in str(ei.value)

    def test_connection_error_names_url(self, mock_comfyui):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        mock_comfyui(handler)
        with pytest.raises(comfyui_client.ComfyUIError) as ei:
            comfyui_client.submit_workflow({"1": {"class_type": "X", "inputs": {}}})
        assert "comfy.test" in str(ei.value)

    def test_poll_has_no_deadline(self, mock_comfyui, monkeypatch):
        sleeps = []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        calls = {"n": 0}

        def handler(request):
            # the poll loop makes two requests per iteration (history, queue):
            # count only the history probes, always serve the queue
            if request.url.path == "/queue":
                return _ok_json({"queue_running": [["1", "p1"]], "queue_pending": []})
            calls["n"] += 1
            if calls["n"] < 5:
                return _ok_json({})
            return _ok_json({"p1": {"status": {"status_str": "success"}, "outputs": {
                "n": {"videos": [{"filename": "v.mp4", "subfolder": "", "type": "output"}]}
            }}})

        mock_comfyui(handler)
        outs = comfyui_client.wait_for_output("p1")
        assert sleeps == [comfyui_client.POLL_INTERVAL] * 4  # keeps polling, never times out
        assert comfyui_client.output_files(outs)[0]["filename"] == "v.mp4"

    def test_missing_history_entry_raises(self, mock_comfyui, monkeypatch):
        # D11: vanished prompt (history entry gone, not queued) fails fast
        monkeypatch.setattr(time, "sleep", lambda s: None)

        def handler(request):
            if request.url.path == "/history/p1":
                return _ok_json({})  # both reads: entry gone for good
            if request.url.path == "/queue":
                return _ok_json({"queue_running": [], "queue_pending": []})
            raise AssertionError(f"unexpected path {request.url.path}")

        mock_comfyui(handler)
        with pytest.raises(comfyui_client.ComfyUIError) as ei:
            comfyui_client.wait_for_output("p1")
        assert "http://comfy.test" in str(ei.value)
        assert "p1" in str(ei.value)

    def test_upload_input_multipart(self, mock_comfyui, tmp_path):
        p = tmp_path / "a.png"
        p.write_bytes(b"x")
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["ct"] = request.headers.get("content-type", "")
            return _ok_json({"name": "a.png", "subfolder": ""})

        mock_comfyui(handler)
        assert comfyui_client.upload_input(str(p)) == ("a.png", "")
        assert seen["path"] == "/upload/image"
        assert "multipart/form-data" in seen["ct"]

    def test_submit_strips_top_level_metadata(self, mock_comfyui, capsys):
        captured = {}

        def handler(request):
            if request.url.path == "/prompt":
                captured["wf"] = json.loads(request.read())["prompt"]
                return _ok_json({"prompt_id": "p-meta"})
            return _ok_json({})

        mock_comfyui(handler)
        wf = {"_meta": {"title": "exporter notes"},
              "save": {"class_type": "VHS_VideoCombine", "inputs": {}}}
        comfyui_client.submit_workflow(wf, log=print)
        assert "_meta" not in captured["wf"]
        assert captured["wf"]["save"]["class_type"] == "VHS_VideoCombine"
        assert "non-node" in capsys.readouterr().out

    def test_vanish_race_rereads_history(self, mock_comfyui, tmp_path):
        history_calls = {"n": 0}

        def handler(request):
            path = request.url.path
            if path == "/prompt":
                return _ok_json({"prompt_id": "p-race"})
            if path.startswith("/history/"):
                history_calls["n"] += 1
                if history_calls["n"] >= 2:
                    return _ok_json({"p-race": {
                        "status": {"status_str": "success"},
                        "outputs": {"save": {"images": [
                            {"filename": "race.png", "subfolder": "",
                             "type": "output"}]}}}})
                return _ok_json({})
            if path == "/queue":
                return _ok_json({"queue_running": [], "queue_pending": []})
            if path == "/view":
                return httpx.Response(200, content=b"img")
            return _ok_json({})

        mock_comfyui(handler)
        dest = str(tmp_path / "race.png")
        out = comfyui_client.run_stage(
            "portrait", {"3": {"text": "x"}, "7": {"seed": 1}}, dest, print)
        assert out == dest
        assert history_calls["n"] >= 2  # the race re-read happened

    def test_image_ref(self):
        assert comfyui_client.image_ref("a.png", "") == "a.png"
        assert comfyui_client.image_ref("a.png", "sub") == "sub/a.png"

    def test_patch_workflow_missing_node_raises(self):
        with pytest.raises(comfyui_client.ComfyUIError):
            comfyui_client.patch_workflow({"1": {"inputs": {}}}, {"99": {"x": 1}})

    def test_template_env_override(self, monkeypatch):
        monkeypatch.setenv("COMFYUI_WORKFLOW_I2V", "D:/custom/i2v.json")
        assert comfyui_client.template_path("i2v") == "D:/custom/i2v.json"


class TestTTSClient:
    def test_missing_base_url_names_var(self, monkeypatch):
        monkeypatch.delenv("TTS_BASE_URL", raising=False)
        with pytest.raises(tts_client.TTSError) as ei:
            tts_client.tts_base_url()
        assert "TTS_BASE_URL" in str(ei.value)

    def test_synthesize_body_and_default_voice(self, mock_tts, tmp_path):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.read()))
            return httpx.Response(200, content=b"MP3DATA")

        mock_tts(handler)
        out = tts_client.synthesize("hola mundo", str(tmp_path / "v.mp3"))
        assert (tmp_path / "v.mp3").read_bytes() == b"MP3DATA"
        assert bodies[0] == {"input": "hola mundo", "response_format": "mp3"}

    def test_synthesize_error_names_url(self, mock_tts, tmp_path):
        def handler(request):
            return httpx.Response(500, text="boom")

        mock_tts(handler)
        with pytest.raises(tts_client.TTSError) as ei:
            tts_client.synthesize("x", str(tmp_path / "v.mp3"), voice_id="v9")
        assert "http://tts.test" in str(ei.value)

    def test_list_voices_tolerates_shapes(self, mock_tts):
        def handler(request):
            assert request.url.path == "/v1/voices"
            return _ok_json({"voices": [{"id": "v1", "name": "Ana"}, {"voice_id": "v2"}]})

        mock_tts(handler)
        assert tts_client.list_voices() == [
            {"voice_id": "v1", "name": "Ana"},
            {"voice_id": "v2", "name": "v2"},
        ]


class TestVoiceoverAdapter:
    def test_elevenlabs_default_resolves_to_server_default(self, monkeypatch, tmp_path):
        seen = {}

        def fake_synth(text, dest, voice_id=None, log=print):
            seen["voice"] = voice_id
            return dest

        monkeypatch.setattr(tts_client, "synthesize", fake_synth)
        saasshorts_local.generate_voiceover_local(
            "hi", str(tmp_path / "v.mp3"), voice_id="21m00Tcm4TlvDq8ikWAM"
        )
        assert seen["voice"] is None


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe required")
class TestMuxAndAtomicity:
    @staticmethod
    def _make_media(tmp_path):
        vid = tmp_path / "silent.mp4"
        aud = tmp_path / "tone.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x568:d=2:r=16",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:a", "libmp3lame", str(aud)],
            check=True, capture_output=True,
        )
        return vid, aud

    def test_muxed_head_has_audio_stream(self, tmp_path):
        vid, aud = self._make_media(tmp_path)
        out = tmp_path / "head.mp4"
        saasshorts_local._mux_audio(str(vid), str(aud), str(out))
        assert saasshorts_local._ffprobe_has_audio(str(out))

    def test_failed_lipsync_leaves_head_absent(self, tmp_path, monkeypatch):
        vid, aud = self._make_media(tmp_path)
        head = tmp_path / "x_head.mp4"

        def fake_run_stage(stage, patches, dest, log=print):
            if stage == "i2v":
                shutil.copy(vid, dest)  # the "wan" output is a silent clip
                return dest
            if stage == "lipsync":
                raise comfyui_client.ComfyUIError(
                    "ComfyUI prompt error - service: ComfyUI at http://comfy.test"
                )
            raise AssertionError(f"unexpected stage {stage}")

        monkeypatch.setattr(comfyui_client, "upload_input", lambda p, log=print: ("f", ""))
        monkeypatch.setattr(comfyui_client, "run_stage", fake_run_stage)
        with pytest.raises(comfyui_client.ComfyUIError):
            saasshorts_local.generate_talking_head_local(str(vid), str(aud), str(head))
        assert not head.exists()                              # final path never written (D6)
        assert (tmp_path / "x_head_wan_cache.mp4").exists()   # retryable intermediates kept
        assert (tmp_path / "x_head_loop_cache.mp4").exists()

    def test_head_pipeline_caches_each_stage(self, tmp_path, monkeypatch):
        vid, aud = self._make_media(tmp_path)
        head = tmp_path / "x_head.mp4"
        stages = []

        def fake_run_stage(stage, patches, dest, log=print):
            stages.append(stage)
            shutil.copy(vid, dest)
            return dest

        monkeypatch.setattr(comfyui_client, "upload_input", lambda p, log=print: ("f", ""))
        monkeypatch.setattr(comfyui_client, "run_stage", fake_run_stage)
        saasshorts_local.generate_talking_head_local(str(vid), str(aud), str(head))
        assert stages == ["i2v", "lipsync"]
        assert head.exists() and saasshorts_local._ffprobe_has_audio(str(head))

        stages.clear()  # second run: every ComfyUI stage cached, only the mux repeats
        saasshorts_local.generate_talking_head_local(str(vid), str(aud), str(head))
        assert stages == []

    def test_broll_local_ken_burns_has_audio(self, tmp_path, monkeypatch):
        def fake_run_stage(stage, patches, dest, log=print):
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x960:d=1",
                 "-frames:v", "1", str(dest)],
                check=True, capture_output=True,
            )
            return dest

        monkeypatch.setattr(comfyui_client, "run_stage", fake_run_stage)
        out = tmp_path / "b.mp4"
        saasshorts_local.generate_broll_local("test shot", str(out), "1")
        assert out.exists() and out.stat().st_size > 0
        assert saasshorts_local._ffprobe_has_audio(str(out))  # anullsrc track (D8 shape)


class TestTemplates:
    def test_all_templates_valid_json(self):
        here = os.path.dirname(os.path.abspath(saasshorts_local.__file__))
        wf_dir = os.path.join(here, "workflows")
        names = [n for n in os.listdir(wf_dir) if n.endswith(".json")]
        assert len(names) == 5  # portrait, broll, i2v, lipsync, faceswap
        for name in names:
            with open(os.path.join(wf_dir, name), encoding="utf-8") as f:
                graph = json.load(f)
            assert any(isinstance(v, dict) and "class_type" in v for v in graph.values()), name

    def test_patch_contract_anchors_exist(self):
        here = os.path.dirname(os.path.abspath(saasshorts_local.__file__))
        wf_dir = os.path.join(here, "workflows")
        with open(os.path.join(wf_dir, "flux_dev_portrait_api.json"), encoding="utf-8") as f:
            assert "3" in json.load(f)  # positive-text node the portrait patches
        with open(os.path.join(wf_dir, "wan22_i2v_api.json"), encoding="utf-8") as f:
            i2v = json.load(f)
            assert {"5", "9", "13"} <= set(i2v)          # text, seed, LoadImage nodes
            assert i2v["8"]["inputs"]["start_image"] == ["13", 0]  # wired via link
        with open(os.path.join(wf_dir, "latentsync_lipsync_api.json"), encoding="utf-8") as f:
            lipsync = json.load(f)
            assert {"load_video", "load_audio"} <= set(lipsync)  # documented skeleton contract
            assert isinstance(lipsync["load_video"]["inputs"]["video"], str)  # string ref, not a link list

# ═══════════════════════════════════════════════════════════════════════
# Slice 2: dispatch + gates + validation
# ═══════════════════════════════════════════════════════════════════════

class TestDispatchMatrix:
    """generate_full_video mode arms: local -> only local adapters (zero cloud
    calls, silent-spend guard); premium/lowcost -> only their cloud helpers."""

    SCRIPT = {
        "title": "Dispatch Probe",
        "full_narration": "hello local mode",
        "segments": [{"start": 0, "end": 5, "visual": "broll", "broll_prompt": "desk shot"}],
    }

    @pytest.fixture
    def pipeline(self, monkeypatch, tmp_path):
        import saasshorts

        calls, intervals = [], {}

        def make(name, result):
            def fake(*args, **kwargs):
                t0 = time.monotonic()
                calls.append(name)
                time.sleep(0.01)
                intervals[name] = (t0, time.monotonic())
                return result
            return fake

        actor_png = str(tmp_path / "dispatch_probe_actor.png")
        voice_mp3 = str(tmp_path / "dispatch_probe_voice.mp3")
        head_mp4 = str(tmp_path / "dispatch_probe_head.mp4")

        # cloud helpers (patched on the saasshorts module namespace)
        monkeypatch.setattr(saasshorts, "generate_actor_image", make("actor:cloud", actor_png))
        monkeypatch.setattr(saasshorts, "generate_voiceover", make("voice:cloud", voice_mp3))
        monkeypatch.setattr(saasshorts, "generate_talking_head", make("head:premium", head_mp4))
        monkeypatch.setattr(saasshorts, "generate_talking_head_lowcost", make("head:lowcost", head_mp4))
        monkeypatch.setattr(saasshorts, "generate_broll", make("broll:cloud", None))
        # local adapters (patched on the shared saasshorts_local module object)
        monkeypatch.setattr(saasshorts_local, "generate_actor_image_local", make("actor:local", actor_png))
        monkeypatch.setattr(saasshorts_local, "generate_actor_angles_local",
                            make("angles:local", [actor_png]))
        monkeypatch.setattr(saasshorts_local, "generate_voiceover_local", make("voice:local", voice_mp3))
        monkeypatch.setattr(saasshorts_local, "generate_talking_head_local", make("head:local", head_mp4))
        monkeypatch.setattr(saasshorts_local, "generate_broll_local", make("broll:local", None))
        # tail stages: subtitles, composite, AI marking, duration
        monkeypatch.setattr(saasshorts, "generate_tiktok_subs", make("subs", None))
        monkeypatch.setattr(saasshorts, "composite_video", make("composite", str(tmp_path / "f.mp4")))
        monkeypatch.setattr(saasshorts, "mark_ai_generated", make("mark", None))
        monkeypatch.setattr(saasshorts, "_get_media_duration", make("dur", 5.0))
        return types.SimpleNamespace(calls=calls, intervals=intervals)

    def test_local_calls_only_local_adapters(self, pipeline, tmp_path):
        import saasshorts

        result = saasshorts.generate_full_video(
            self.SCRIPT,
            {"video_mode": "local"},  # no keys at all - the local contract
            str(tmp_path),
            log=lambda m: None,
        )
        for name in ("actor:local", "voice:local", "head:local", "broll:local"):
            assert name in pipeline.calls
        cloud = {"actor:cloud", "voice:cloud", "head:premium", "head:lowcost", "broll:cloud"}
        assert not set(pipeline.calls) & cloud  # silent-spend guard (advisor 4a)
        assert result["cost_estimate"]["total"] == 0.0
        assert all(v == 0 for k, v in result["cost_estimate"].items() if k != "total")

    def test_local_serializes_steps_1_and_2(self, pipeline, tmp_path):
        import saasshorts

        saasshorts.generate_full_video(
            self.SCRIPT, {"video_mode": "local"}, str(tmp_path), log=lambda m: None
        )
        a0, a1 = pipeline.intervals["actor:local"]
        v0, v1 = pipeline.intervals["voice:local"]
        assert a1 <= v0  # D7: actor image fully done before voiceover starts

    def test_premium_unchanged(self, pipeline, tmp_path):
        import saasshorts

        result = saasshorts.generate_full_video(
            self.SCRIPT,
            {"video_mode": "premium", "fal_key": "k", "elevenlabs_key": "k"},
            str(tmp_path),
            log=lambda m: None,
        )
        assert {"actor:cloud", "voice:cloud", "head:premium", "broll:cloud"} <= set(pipeline.calls)
        assert not any(c.endswith(":local") for c in pipeline.calls)
        assert result["cost_estimate"]["talking_head_kling"] == round(5.0 * 0.056, 2)

    def test_lowcost_unchanged(self, pipeline, tmp_path):
        import saasshorts

        result = saasshorts.generate_full_video(
            self.SCRIPT,
            {"video_mode": "lowcost", "fal_key": "k", "elevenlabs_key": "k"},
            str(tmp_path),
            log=lambda m: None,
        )
        assert "head:lowcost" in pipeline.calls and "head:premium" not in pipeline.calls
        assert not any(c.endswith(":local") for c in pipeline.calls)
        assert result["cost_estimate"]["veed_lipsync"] == 0.20


APP_RESULT = {
    "video_path": "o/x_final.mp4",
    "video_filename": "x_final.mp4",
    "srt_path": "o/x_subs.ass",
    "actor_image": "o/x_actor.png",
    "duration": 5.0,
    "cost_estimate": {"total": 0.0},
}


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    import app as app_module
    from fastapi.testclient import TestClient

    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "generate_full_video", lambda *a, **k: dict(APP_RESULT))
    return TestClient(app_module.app, raise_server_exceptions=False)


class TestRouteModeMatrix:
    def test_generate_local_without_keys_is_accepted(self, app_client):
        r = app_client.post(
            "/api/saasshorts/generate",
            json={"script": TestDispatchMatrix.SCRIPT, "video_mode": "local"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "processing" and body["job_id"]

    def test_generate_premium_without_keys_is_400(self, app_client):
        r = app_client.post(
            "/api/saasshorts/generate",
            json={"script": TestDispatchMatrix.SCRIPT, "video_mode": "premium"},
        )
        assert r.status_code == 400
        assert "fal.ai" in r.json()["detail"]

    def test_generate_premium_missing_elevenlabs_is_400(self, app_client):
        r = app_client.post(
            "/api/saasshorts/generate",
            json={"script": TestDispatchMatrix.SCRIPT, "video_mode": "premium"},
            headers={"X-Fal-Key": "k"},
        )
        assert r.status_code == 400
        assert "ElevenLabs" in r.json()["detail"]

    def test_generate_invalid_mode_is_422(self, app_client):
        r = app_client.post(
            "/api/saasshorts/generate",
            json={"script": TestDispatchMatrix.SCRIPT, "video_mode": "premiun"},
        )
        assert r.status_code == 422  # never silently runs premium

    def test_actor_options_local_without_keys_serves_videos_urls(self, monkeypatch, app_client):
        monkeypatch.setattr(
            saasshorts_local,
            "generate_actor_images_local",
            lambda description, output_dir, title_slug, num_options=3,
                   product_description=None, log=print: [
                os.path.join(output_dir, f"{title_slug}_actor_option_{i}.png")
                for i in range(num_options)
            ],
        )
        r = app_client.post(
            "/api/saasshorts/actor-options",
            json={"actor_description": "young dev", "video_mode": "local"},
        )
        assert r.status_code == 200
        images = r.json()["images"]
        assert len(images) == 3
        assert all(u.startswith("/videos/saas_actors_") and u.endswith(".png") for u in images)

    def test_actor_options_cloud_without_keys_is_400(self, app_client):
        r = app_client.post(
            "/api/saasshorts/actor-options",
            json={"actor_description": "young dev", "video_mode": "lowcost"},
        )
        assert r.status_code == 400

    def test_actor_options_invalid_mode_is_422(self, app_client):
        r = app_client.post(
            "/api/saasshorts/actor-options",
            json={"actor_description": "young dev", "video_mode": "locall"},
        )
        assert r.status_code == 422


class TestVoicesLocalBranch:
    def test_local_lists_tts_voices(self, monkeypatch, app_client):
        monkeypatch.setattr(
            saasshorts_local,
            "get_local_tts_voices",
            lambda log=print: [{"voice_id": "v1", "name": "Ana"}],
        )
        r = app_client.get("/api/saasshorts/voices", params={"video_mode": "local"})
        assert r.status_code == 200
        assert r.json() == {
            "voices": [{"voice_id": "v1", "name": "Ana", "category": "local"}],
            "source": "local",
        }

    def test_local_tts_down_returns_empty_with_error(self, monkeypatch, app_client):
        def boom(log=print):
            raise tts_client.TTSError(
                "TTS voice list failed - service: TTS server at http://127.0.0.1:8000"
            )

        monkeypatch.setattr(saasshorts_local, "get_local_tts_voices", boom)
        r = app_client.get("/api/saasshorts/voices", params={"video_mode": "local"})
        assert r.status_code == 200  # picker renders the error; generation still works
        body = r.json()
        assert body["voices"] == [] and body["source"] == "local"
        assert "TTS server" in body["error"]

    def test_cloud_mode_unchanged_without_key(self, monkeypatch, app_client):
        monkeypatch.delenv("TTS_BASE_URL", raising=False)
        body = app_client.get("/api/saasshorts/voices").json()
        assert body["source"] == "defaults"
        assert body["voices"][0]["category"] == "default"


class TestSeoLocalSurface:
    META = {
        "title": "Mode Probe", "caption": "cap", "full_narration": "",
        "video_url": "/videos/x.mp4", "actor_url": "/videos/a.png",
        "video_id": "modeprobe", "duration": 5, "video_mode": "local",
        "product_name": "pn", "product_url": "", "language": "en",
        "hashtags": [], "cost_estimate": {"total": 0}, "created_at": "",
        "actor_description": "",
    }

    def test_gallery_and_detail_render_local(self, monkeypatch, app_client):
        import app as app_module

        monkeypatch.setattr(app_module, "list_video_gallery", lambda limit=100: [dict(self.META)])
        gallery = app_client.get("/gallery").text
        assert ">LOCAL<" in gallery
        # badge markup only: the static gallery subtitle legitimately names the cloud modes
        assert ">PREMIUM<" not in gallery and ">LOW COST<" not in gallery
        detail = app_client.get("/video/modeprobe").text
        assert "Local" in detail
        assert "Premium" not in detail and "Low Cost" not in detail


class TestLocalArmStartupProbe:
    def test_names_unreachable_url_and_stays_silent_unset(self, monkeypatch):
        import app as app_module
        import httpx as _httpx

        def refused(*args, **kwargs):
            raise _httpx.ConnectError("refused")

        monkeypatch.setattr(_httpx, "get", refused)
        monkeypatch.setenv("COMFYUI_URL", "http://comfy.test:59999")
        monkeypatch.delenv("TTS_BASE_URL", raising=False)
        warns = app_module._local_arm_startup_warnings()
        assert len(warns) == 1 and "COMFYUI_URL" in warns[0]
        monkeypatch.delenv("COMFYUI_URL", raising=False)
        assert app_module._local_arm_startup_warnings() == [];


class TestLipsyncChunking:
    """LatentSync's wrapper holds the whole input video as one GPU tensor:
    the adapter chunks the looped clip so any narration length stays inside
    the 10 GB envelope. The plan math is pure."""

    def test_single_pass_when_it_fits(self):
        assert saasshorts_local._lipsync_chunk_plan(80, 96) == [80]

    def test_even_chunks(self):
        assert saasshorts_local._lipsync_chunk_plan(240, 96) == [80, 80, 80]

    def test_uneven_chunks(self):
        plan = saasshorts_local._lipsync_chunk_plan(200, 96)
        assert plan == [67, 67, 66]
        assert sum(plan) == 200

    def test_no_coverage(self):
        assert saasshorts_local._lipsync_chunk_plan(0, 96) == []

    def test_chunks_never_exceed_max(self):
        for n in (95, 96, 97, 191, 192, 193, 500, 1200):
            plan = saasshorts_local._lipsync_chunk_plan(n, 96)
            assert all(0 < c <= 96 for c in plan), (n, plan)
            assert sum(plan) == n

    @pytest.mark.skipif(not FFMPEG, reason="ffmpeg not on PATH")
    def test_cut_and_concat_roundtrip(self, tmp_path):
        src = str(tmp_path / "loop.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", "testsrc=size=704x1280:rate=16:duration=3", "-pix_fmt",
             "yuv420p", "-c:v", "libx264", src],
            check=True, capture_output=True)
        c1 = saasshorts_local._cut_media(src, 0.0, 1.5, str(tmp_path / "c1.mp4"),
                                         video_only=True)
        c2 = saasshorts_local._cut_media(src, 1.5, 1.5, str(tmp_path / "c2.mp4"),
                                         video_only=True)
        out = saasshorts_local._concat_media([c1, c2], str(tmp_path / "full.mp4"))
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams",
             "v:0", "-show_entries", "stream=nb_read_frames", "-of",
             "default=noprint_wrappers=1:nokey=1", out],
            capture_output=True, text=True)
        assert int(probe.stdout.strip()) == 48


class TestAnalyzeSchemaContract:
    """generate_scripts requests structured outputs (json_schema) so small
    local models cannot emit malformed JSON; a provider that honors
    json_object (an object, not our array) falls back to the legacy bare
    call, and a context truncation re-raises instead of retrying."""

    SCRIPT = {
        "title": "t", "style": "ugc", "duration_seconds": 23,
        "target_platform": "tiktok", "hook_text": "hook",
        "segments": [{"type": "hook", "start": 0, "end": 5,
                      "narration": ("You know that feeling when you sit down to work and two hours "
                                    "disappear into nothing at all because your phone keeps buzzing and "
                                    "your browser has forty tabs open and every single one of them is "
                                    "more interesting than the thing you actually promised yourself you "
                                    "would finish today before dinner. So today I want to show you the "
                                    "little app that finally fixed that for me and it does it with one "
                                    "timer one task and a streak counter that makes you feel genuinely "
                                    "guilty when you skip a day at the gym or at your desk. That is the hook."),
                      "visual": "actor_talking",
                      "broll_prompt": None, "emotion": "excited",
                      "subtitle_text": "s"}],
        "full_narration": "n", "actor_description": "a 26 year old woman",
        "hashtags": ["#x"], "caption": "c",
    }

    @pytest.fixture
    def llm_env(self, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "http://llm.test")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "test-model")

    def _post(self, app_client):
        return app_client.post("/api/saasshorts/analyze", json={
            "description": "artisan coffee roastery in Madrid",
            "num_scripts": 3, "style": "ugc", "language": "en",
            "actor_gender": "female"})

    def test_schema_passed_and_list_short_circuits(self, app_client, llm_env,
                                                   monkeypatch):
        calls = []

        def fake_chat(prompt, config=None, schema=None, strict=None,
                      max_tokens=None):
            calls.append({"schema": schema, "strict": strict,
                          "max_tokens": max_tokens})
            return {"scripts": [dict(self.SCRIPT)]}, None

        monkeypatch.setattr(llm_client, "chat", fake_chat)
        res = self._post(app_client)
        assert res.status_code == 200
        assert len(res.json()["scripts"]) == 1
        assert calls and calls[0]["schema"] is saasshorts.SaasScriptSet
        assert calls[0]["max_tokens"] == 8192
        assert calls[0]["strict"] is True

    def test_object_provider_falls_back_to_bare_call(self, app_client,
                                                     llm_env, monkeypatch):
        calls = []

        def fake_chat(prompt, config=None, schema=None, strict=None,
                      max_tokens=None):
            calls.append(schema)
            if schema is not None:
                raise llm_client.LlmTransientError(
                    "LLM provider response failed schema validation: x")
            return {"scripts": [dict(self.SCRIPT)]}, None

        monkeypatch.setattr(llm_client, "chat", fake_chat)
        res = self._post(app_client)
        assert res.status_code == 200
        assert len(res.json()["scripts"]) == 1
        assert len(calls) == 2
        assert calls[0] is not None and calls[1] is None

    def test_truncation_is_reraised_not_retried(self, app_client, llm_env,
                                                monkeypatch):
        calls = []

        def fake_chat(prompt, config=None, schema=None, strict=None,
                      max_tokens=None):
            calls.append(schema)
            raise llm_client.LlmTransientError(
                "LLM provider response was truncated (finish_reason=length).")

        monkeypatch.setattr(llm_client, "chat", fake_chat)
        res = self._post(app_client)
        assert res.status_code == 500
        assert len(calls) == 1
        assert "truncated" in res.json()["detail"].lower()


class TestScriptWordCount:
    """The narration-length gate that keeps weak local script models from
    shipping 13-second videos against the 60-second prompt."""

    def test_dict_shape_counts_words(self):
        raw = {"scripts": [{"segments": [
            {"narration": "one two three four five"},
            {"narration": "six seven"},
            {"narration": ""},
        ]}]}
        assert saasshorts._script_word_count(raw) == 7

    def test_fenced_str_shape_counts_words(self):
        raw = '```json\n{"scripts": [{"segments": [{"narration": "a b c"}]}]}\n```'
        assert saasshorts._script_word_count(raw) == 3

    def test_garbage_is_zero(self):
        assert saasshorts._script_word_count("no json here at all") == 0

    def test_empty_scripts_is_zero(self):
        assert saasshorts._script_word_count({"scripts": []}) == 0


class TestPromoShape:
    """The 5-beat promo layout is a template: weak local models supply the
    words, the code imposes the structure."""

    def test_compliant_script_untouched(self):
        good = {"segments": [
            {"type": "hook", "visual": "actor_talking", "narration": "a", "start": 0, "end": 12},
            {"type": "problem", "visual": "broll", "narration": "b", "start": 12, "end": 24, "broll_prompt": "x"},
            {"type": "solution", "visual": "actor_talking", "narration": "c", "start": 24, "end": 36},
            {"type": "demo", "visual": "broll", "narration": "d", "start": 36, "end": 48, "broll_prompt": "y"},
            {"type": "cta", "visual": "actor_talking", "narration": "e", "start": 48, "end": 60},
        ]}
        import copy
        before = copy.deepcopy(good)
        assert saasshorts._enforce_promo_shape(good, "Bottle") is good
        assert good == before

    def test_two_segment_script_rebuilt(self):
        lazy = {"full_narration": (
            "You forget to drink water all day and feel exhausted by three. "
            "This smart bottle cleans itself with UV light every two hours. "
            "The app tracks every sip you take and glows when you fall behind. "
            "It keeps your drink ice cold for a full twenty four hours straight. "
            "Grab yours today with the code PULSE20 at the link in my bio."),
            "segments": [
                {"type": "hook", "visual": "actor_talking", "narration": "You forget to drink water all day.", "start": 0, "end": 30},
                {"type": "cta", "visual": "actor_talking", "narration": "Grab yours today.", "start": 30, "end": 60},
            ]}
        out = saasshorts._enforce_promo_shape(lazy, "AquaPulse")
        segs = out["segments"]
        assert len(segs) == 5
        assert [s["visual"] for s in segs] == ["actor_talking", "broll", "actor_talking", "broll", "actor_talking"]
        assert all(s["broll_prompt"] for s in segs if s["visual"] == "broll")
        assert "AquaPulse" in segs[1]["broll_prompt"]
        assert out["duration_seconds"] == 60
        assert segs[0]["narration"]  # model's words kept
        assert all(s["start"] == i * 12 for i, s in enumerate(segs))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe required")
class TestPromoShots:
    """Real-promo grammar: actor beats cut between angles of the same person
    every ~5 s, and b-roll beats are Wan2.2 motion shots, not a panned still."""

    def test_split_span_near_equal(self):
        assert saasshorts_local._split_span(12.0, 3) == [4.0, 4.0, 4.0]
        parts = saasshorts_local._split_span(7.0, 2)
        assert len(parts) == 2 and abs(sum(parts) - 7.0) < 1e-9

    def test_angle_prompts_shape(self):
        extra = saasshorts_local._ACTOR_ANGLE_PROMPTS
        assert extra[0] == "" and len(extra) >= 3
        assert all(isinstance(p, str) and p != extra[0] for p in extra[1:])

    def test_faceswap_workflow_patches(self, monkeypatch, tmp_path):
        ref = tmp_path / "ref.png"; ref.write_bytes(b"p")
        raw = tmp_path / "raw.png"; raw.write_bytes(b"p")
        dest = tmp_path / "t_angle_1.png"
        uploads, stages = [], []

        def fake_upload(path, log=print):
            uploads.append(path)
            return (f"{path}", "")

        def fake_run_stage(stage, patches, out, log=print):
            stages.append((stage, sorted(patches.keys())))
            shutil.copy(raw, out)
            return out

        monkeypatch.setattr(comfyui_client, "upload_input", fake_upload)
        monkeypatch.setattr(comfyui_client, "run_stage", fake_run_stage)
        angles = saasshorts_local.generate_actor_angles_local(
            str(ref), "a 26 year old woman", str(tmp_path), "t", log=print)
        assert angles[0] == str(ref)
        assert angles[1] == str(dest)
        assert (tmp_path / "t_angle_2.png").exists()
        assert len(angles) == 3                       # canonical + 2 angles
        assert [s for s, _ in stages] == ["portrait", "faceswap"] * 2
        swap_patches = [p for s, p in stages if s == "faceswap"]
        assert swap_patches == [["10", "11"], ["10", "11"]]

    def test_broll_builds_motion_shots_to_duration(self, monkeypatch, tmp_path):
        out = tmp_path / "t_broll_0.mp4"
        stages = []

        def fake_run_stage(stage, patches, dest, log=print):
            stages.append(stage)
            if stage == "broll":
                shutil.copy(str(vid := tmp_path / "still.png"), dest) if False else None
                # a 64x64 png still
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                                "color=c=red:s=64x64", "-frames:v", "1", dest],
                               check=True, capture_output=True)
            else:
                subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                                "testsrc=s=64x64:d=5:r=24", dest],
                               check=True, capture_output=True)
            return dest

        monkeypatch.setattr(comfyui_client, "run_stage", fake_run_stage)
        monkeypatch.setattr(comfyui_client, "upload_input", lambda p, log=print: (p, ""))
        saasshorts_local.generate_broll_local("a smart bottle on a desk", str(out), "12", log=print)
        assert stages == ["broll"] + ["i2v"] * 3      # 12 s beat -> 3 motion shots
        assert abs(saasshorts_local._ffprobe_duration(str(out)) - 12.0) < 1.5
        assert saasshorts_local._ffprobe_has_audio(str(out))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe required")
class TestMultiShotHead:
    def test_shot_plan_scales_to_real_duration(self):
        marks = [{"start": 0, "end": 4}, {"start": 4, "end": 8}]
        spans = saasshorts_local._shot_plan(marks, narration_dur=2.0, planned_duration=8.0)
        assert len(spans) == 2
        assert spans[0]["start"] == 0.0
        assert spans[-1]["end"] == 2.0
        assert all(s["end"] > s["start"] for s in spans)

    def test_shot_plan_fallbacks(self):
        one = saasshorts_local._shot_plan(None, 3.0, None)
        assert one == [{"start": 0.0, "end": 3.0}]
        no_marks = saasshorts_local._shot_plan([], 3.0, None)
        assert no_marks == [{"start": 0.0, "end": 3.0}]
        bad = saasshorts_local._shot_plan([{"start": 0, "end": 0}], 3.0, 0)
        assert bad == [{"start": 0.0, "end": 3.0}]

    def test_seamless_loop_roundtrip(self, tmp_path):
        src = tmp_path / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x568:d=2:r=16",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
            check=True, capture_output=True,
        )
        out = tmp_path / "loop.mp4"
        saasshorts_local._loop_clip_to_duration(str(src), 5.0, str(out))
        assert saasshorts_local._ffprobe_duration(str(out)) >= 4.9

    def test_head_multishot_runs_i2v_per_segment(self, tmp_path, monkeypatch):
        vid, aud = TestMuxAndAtomicity._make_media(tmp_path)
        head = tmp_path / "m_head.mp4"
        stages = []

        def fake_run_stage(stage, patches, dest, log=print):
            stages.append((stage, patches.get("5", {}).get("text", "")))
            shutil.copy(vid, dest)
            return dest

        monkeypatch.setattr(comfyui_client, "upload_input", lambda p, log=print: ("f", ""))
        monkeypatch.setattr(comfyui_client, "run_stage", fake_run_stage)
        out = saasshorts_local.generate_talking_head_local(
            str(vid), str(aud), str(head), log=print,
            shot_marks=[{"start": 0, "end": 1}, {"start": 1, "end": 2}],
            planned_duration=2.0,
        )
        assert out == str(head)
        i2v_texts = [text for stage, text in stages if stage == "i2v"]
        assert len(i2v_texts) == 2                       # one Wan shot per segment
        assert i2v_texts[0] != i2v_texts[1]              # distinct motion prompts
        assert saasshorts_local._ffprobe_has_audio(str(head))
        assert (tmp_path / "m_head_wan_cache_0.mp4").exists()
        assert (tmp_path / "m_head_wan_cache_1.mp4").exists()

