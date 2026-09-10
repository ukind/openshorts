import screencast_layout
from screencast_layout import (
    content_bands,
    overlapping_width,
    screencast_filtergraph,
    speaker_crop,
)

import base64

import pytest

import ai_provider
import gemini_worker
import llm_client


class TestContentBands:
    def test_bands_fill_the_output_exactly(self):
        content_h, speaker_h = content_bands(1920, 1080, 1080, 1920)
        assert content_h + speaker_h == 1920

    def test_sixteen_by_nine_content_keeps_its_full_width(self):
        # 1080 wide at 16:9 is 608 tall. Anything else means the sides were cut,
        # which is the one thing this layout exists to prevent.
        content_h, _ = content_bands(1920, 1080, 1080, 1920)
        assert content_h == 608

    def test_bands_are_even(self):
        for src_h in (1080, 1000, 817):
            content_h, speaker_h = content_bands(1920, src_h, 1080, 1920)
            assert content_h % 2 == 0 and speaker_h % 2 == 0

    def test_tall_source_still_leaves_room_for_the_speaker(self):
        # A portrait source would want more height than the frame has.
        content_h, speaker_h = content_bands(1080, 1920, 1080, 1920)
        assert speaker_h >= 2
        assert content_h + speaker_h == 1920


class TestSpeakerCrop:
    def test_crop_stays_inside_the_source(self):
        w, h, x, y = speaker_crop(1920, 1080, 1080, 1312, (1748, 832))
        assert 0 <= x <= 1920 - w
        assert 0 <= y <= 1080 - h

    def test_crop_is_even(self):
        w, h, x, y = speaker_crop(1920, 1080, 1080, 1312, (777, 333))
        assert w % 2 == 0 and h % 2 == 0 and x % 2 == 0 and y % 2 == 0

    def test_crop_follows_the_face_horizontally(self):
        _, _, left_x, _ = speaker_crop(1920, 1080, 1080, 1312, (400, 500))
        _, _, right_x, _ = speaker_crop(1920, 1080, 1080, 1312, (1500, 500))
        assert left_x < right_x

    def test_corner_presenter_is_clamped_not_dropped(self):
        w, _h, x, _y = speaker_crop(1920, 1080, 1080, 1312, (1900, 1000))
        assert x == 1920 - w


class TestOverlappingWidth:
    def test_no_ranges_means_no_width(self):
        assert overlapping_width(0, 10, []) == 0.0

    def test_returns_the_width_of_an_overlapping_range(self):
        assert overlapping_width(5, 15, [(0, 20, "chart", 0.7)]) == 0.7

    def test_ignores_ranges_that_do_not_overlap(self):
        assert overlapping_width(30, 40, [(0, 20, "chart", 0.9)]) == 0.0

    def test_a_brush_of_overlap_does_not_count(self):
        # Under MIN_OVERLAP_SECONDS: a scene that merely touches the range.
        assert overlapping_width(19.9, 30, [(0, 20, "chart", 0.9)]) == 0.0

    def test_widest_overlapping_range_wins(self):
        ranges = [(0, 20, "ticker", 0.15), (0, 20, "spreadsheet", 0.95)]
        assert overlapping_width(5, 15, ranges) == 0.95

    def test_corner_ticker_width_is_reported_not_swallowed(self):
        # The gate lives in detect_content_ranges; this function must report
        # small widths faithfully so that gate can do its job.
        assert overlapping_width(5, 15, [(0, 20, "ticker", 0.15)]) == 0.15


class TestScreencastFiltergraph:
    def test_content_band_is_scaled_never_cropped(self):
        graph = screencast_filtergraph(1920, 1080, 1080, 1920, (1748, 832))
        # [-1]: the first "[ca]" is the split= output label, not the filter.
        content = graph.split("[ca]")[-1].split("[content]")[0]
        assert "crop" not in content
        assert "scale=1080:608" in content

    def test_graph_stacks_and_pads_to_the_output(self):
        graph = screencast_filtergraph(1920, 1080, 1080, 1920, (1748, 832))
        assert "vstack=inputs=2" in graph
        assert "pad=1080:1920" in graph
        assert graph.endswith("[v]")

    def test_speaker_band_is_cropped_around_the_face(self):
        graph = screencast_filtergraph(1920, 1080, 1080, 1920, (1748, 832))
        speaker = graph.split("[sa]")[-1].split("[speaker]")[0]
        assert "crop=" in speaker
        assert "scale=1080:1312" in speaker


class TestGating:
    def test_disabled_module_detects_nothing(self, monkeypatch):
        monkeypatch.setattr(screencast_layout, "ENABLED", False)
        assert screencast_layout.detect_content_ranges("x.mp4", 10) == []

    def test_no_ranges_means_no_scenes_touched(self, monkeypatch):
        monkeypatch.setattr(screencast_layout, "ENABLED", True)
        assert screencast_layout.detect_screencast_scenes(
            "x.mp4", [], [], []) == {}


# --- the OpenAI-compatible frames arm (no Gemini key) ----------------------

class _FakeProvider:
    model_name = "qwen2.5vl"


@pytest.fixture
def openai_arm(monkeypatch):
    """No Gemini key; a keyless local endpoint in the job OPENAI_* env; the
    factory, probe and frame sampling stubbed so no network or ffmpeg is
    touched."""
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
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

    import layout_picker
    sampled = []

    def fake_sample(path, n=None, width=None):
        sampled.append({"path": path, "n": n, "width": width})
        return [b"jpg"] * (n or 12)

    monkeypatch.setattr(layout_picker, "sample_frames", fake_sample)
    return {"made": made, "probes": probes, "sampled": sampled}


def test_openai_arm_returns_gated_ranges(openai_arm, monkeypatch):
    seen = {}

    def fake(frames, prompt, provider):
        seen.update(frames=frames, prompt=prompt, provider=provider)
        return [
            {"start": 0, "end": 20, "what": "spreadsheet", "width_fraction": 0.95},
            {"start": 5, "end": 5.2, "what": "blip", "width_fraction": 0.9},   # < 0.5s
            {"start": 0, "end": 20, "what": "corner ticker", "width_fraction": 0.15},
        ]

    monkeypatch.setattr(screencast_layout, "_ask_openai_frames", fake)

    ranges = screencast_layout.detect_content_ranges("x.mp4", 30)

    # The width/duration gate applies to the frames arm exactly as to Gemini's.
    assert ranges == [(0.0, 20.0, "spreadsheet", 0.95)]
    assert len(seen["frames"]) == 12                # layout_picker's default count
    made = openai_arm["made"]
    assert made["create_args"][0] == "openai"
    assert made["create_args"][1] == "qwen2.5vl"
    assert made["create_kwargs"]["base_url"] == "http://localhost:11434/v1"
    assert made["create_kwargs"]["api_key"] is None
    assert seen["provider"] is made["provider"]
    assert openai_arm["probes"] == [made["provider"]]   # probed once, the built provider
    assert openai_arm["sampled"][0]["path"] == "x.mp4"


def test_openai_arm_skips_a_text_only_model(monkeypatch):
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setattr(ai_provider, "create_ai_provider",
                        lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(ai_provider, "probe_vision_support",
                        lambda p: "no_vision")

    def boom(*a, **k):
        raise AssertionError("a text-only model must never be asked")

    monkeypatch.setattr(screencast_layout, "_ask_openai_frames", boom)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_injected_default_base_is_not_a_configuration(monkeypatch):
    # app.py's job-env handover writes the OPENAI_* defaults — the base falls
    # back to https://api.openai.com/v1, the key to "" — into EVERY job env.
    # A job gated only by the LLM_* family must stay a silent no-op: no
    # provider built, no probe, no frames sampled against the default endpoint.
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    def boom(*a, **k):
        raise AssertionError("the injected default must not build a provider")

    monkeypatch.setattr(ai_provider, "create_ai_provider", boom)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_missing_openai_sdk_skips_cleanly(monkeypatch):
    # The openai import happens inside create_ai_provider, and the contract is
    # never-raise: a missing SDK degrades to [], not a crash.
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")

    def no_sdk(*a, **k):
        raise ImportError("No module named 'openai'")

    monkeypatch.setattr(ai_provider, "create_ai_provider", no_sdk)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_provider_failure_degrades_to_empty(openai_arm, monkeypatch):
    def broken(frames, prompt, provider):
        raise RuntimeError("boom: endpoint refused")

    monkeypatch.setattr(screencast_layout, "_ask_openai_frames", broken)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_no_readable_frames_degrades_to_empty(openai_arm, monkeypatch):
    import layout_picker
    monkeypatch.setattr(layout_picker, "sample_frames",
                        lambda path, n=None, width=None: [])
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == []


def test_openai_arm_never_touches_the_satellite_client(openai_arm, monkeypatch):
    # No-double-route canary for this reroute: the arm is pipeline-family
    # (ai_provider + job OPENAI_* env); a leak into the satellite client trips
    # loudly. Kept here so tests/test_no_double_route.py stays byte-unmodified.
    def boom(*a, **k):
        raise AssertionError("screencast detection must not call the satellite client")

    monkeypatch.setattr(llm_client, "chat", boom)
    monkeypatch.setattr(llm_client, "active_config", boom)
    monkeypatch.setattr(screencast_layout, "_ask_openai_frames",
                        lambda *a: [{"start": 0, "end": 9, "what": "slide",
                                     "width_fraction": 0.9}])
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == [
        (0.0, 9.0, "slide", 0.9)]


def test_a_gemini_key_wins_over_the_openai_env(monkeypatch):
    monkeypatch.setattr(screencast_layout, "ENABLED", True)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(screencast_layout, "_ask_gemini_files",
                        lambda api_key, model, path, prompt: [
                            {"start": 1, "end": 2, "what": "chart",
                             "width_fraction": 0.8}])

    def boom(*a, **k):
        raise AssertionError("with a Gemini key the frames arm must stay off")

    monkeypatch.setattr(screencast_layout, "_ask_openai_frames", boom)
    assert screencast_layout.detect_content_ranges("x.mp4", 10) == [
        (1.0, 2.0, "chart", 0.8)]


def test_ask_openai_frames_sends_frames_as_image_parts():
    calls = {}

    class FakeProv:
        def generate_content(self, prompt, schema=None, messages=None, **kw):
            calls.update(prompt=prompt, schema=schema, messages=messages)
            return {"response": {"ranges": [{"start": 0, "end": 1,
                                             "what": "slide",
                                             "width_fraction": 0.9}]},
                    "cost_analysis": None}

    out = screencast_layout._ask_openai_frames([b"jpg1", b"jpg2"], "PROMPT", FakeProv())

    assert out == [{"start": 0, "end": 1, "what": "slide", "width_fraction": 0.9}]
    assert calls["schema"] is gemini_worker.WideContentResponse
    content = calls["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "PROMPT"}
    assert content[1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(b"jpg1").decode("ascii"))
    assert len(content) == 3                      # one text part + two frames


def test_openai_provider_needs_a_key_or_a_real_base(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert screencast_layout._openai_provider() is None    # nothing configured
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert screencast_layout._openai_provider() is None    # the injected default is not one either

    fake = _FakeProvider()
    calls = []

    def fake_create(*a, **k):
        calls.append((a, k))
        return fake

    monkeypatch.setattr(ai_provider, "create_ai_provider", fake_create)

    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")  # keyless local
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5vl")
    prov = screencast_layout._openai_provider()
    assert prov is fake
    (a, k), = calls
    assert a[0] == "openai" and a[1] == "qwen2.5vl"
    assert k["base_url"] == "http://localhost:11434/v1"
    assert k["api_key"] is None

    calls.clear()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")  # default + key
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert screencast_layout._openai_provider() is fake   # a key on the default endpoint is a config
    (a, k), = calls
    assert k["base_url"] == "https://api.openai.com/v1"
    assert k["api_key"] == "sk-x"
