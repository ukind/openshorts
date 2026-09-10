"""Thumbnail Studio pieces that run without Gemini or a video."""
import io
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import thumbnail  # noqa: E402

import base64

import httpx
from fastapi.testclient import TestClient

import app as app_module  # noqa: E402


def test_burn_text_wraps_and_stays_in_its_half():
    img = Image.new("RGB", (1280, 720), (20, 20, 20))
    out = thumbnail.burn_thumbnail_text(img, "0 A 10K EN 30 DÍAS", "left", "yellow")
    px = out.convert("RGB")
    # Something got drawn on the left half...
    left = px.crop((0, 0, 640, 720))
    assert max(left.getextrema()[0]) > 200
    # ...and the right half (where the subject lives) is untouched.
    right = px.crop((700, 0, 1280, 720))
    assert max(right.getextrema()[0]) < 60


def test_burn_text_no_text_is_a_noop():
    img = Image.new("RGB", (1280, 720), (0, 0, 0))
    assert thumbnail.burn_thumbnail_text(img, "  ", "left") is img


def test_finalize_cover_crops_to_1280x720_under_2mb(tmp_path):
    # A 2K-ish image with a different aspect ratio, full of noise so JPEG is heavy.
    import random
    noisy = Image.effect_noise((2048, 1536), 80).convert("RGB")
    out = tmp_path / "t.jpg"
    thumbnail.finalize_thumbnail(noisy, str(out))
    with Image.open(out) as saved:
        assert saved.size == (1280, 720)
    assert out.stat().st_size <= thumbnail.THUMB_MAX_BYTES


def test_rank_frame_rejects_tiny_faces():
    assert thumbnail.rank_frame(0, 0.0, None, [0, 0, 50, 50], (1920, 1080), 100) is None
    big = thumbnail.rank_frame(0, 0.0, None, [0, 0, 400, 400], (1920, 1080), 100)
    assert big and big["score"] > 0


def test_pick_spread_keeps_picks_apart_in_time():
    total = 1000
    # Three near-identical best frames at 500/505/510 and a weaker one far away.
    scored = [
        {"idx": 500, "score": 1.0}, {"idx": 505, "score": 0.99}, {"idx": 510, "score": 0.98},
        {"idx": 100, "score": 0.5}, {"idx": 900, "score": 0.4},
    ]
    picked = thumbnail.pick_spread(scored, 3, total)
    assert [p["idx"] for p in picked] == [100, 500, 900]


def test_normalise_concepts_clamps_and_pads():
    raw = [
        {"text": "esto funciona", "text_position": "diagonal", "text_color": "red", "scene": "a desk"},
        {"scene": ""},  # dropped
        "garbage",
    ]
    out = thumbnail.normalise_concepts(raw, 3, "My title", thumbnail_text_hint="hint")
    assert len(out) == 3
    assert out[0] == {"text": "ESTO FUNCIONA", "text_position": "left", "text_color": "white",
                      "scene": "a desk", "why": ""}
    assert out[1]["why"] == "fallback" and out[1]["text"] == "HINT"


def test_parse_json_tolerates_fences_and_prose():
    assert thumbnail._parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_ignores_trailing_garbage():
    assert thumbnail._parse_json('{"a": [1]}\n  ]\n}') == {"a": [1]}

# --- new section at end of file -------------------------------------------------
# --- the OpenAI-compatible images arm (D14; no Gemini key required) -------------

IMG_CONFIG = {"api_key": "sk-x", "model": "gpt-image-1",
              "base_url": "http://localhost:4891/v1"}

OPENAI_HEADERS = {"X-OpenAI-Key": "sk-test",
                  "X-OpenAI-Model": "qwen2.5vl",
                  "X-OpenAI-Base-Url": "http://localhost:11434/v1"}


def _png_bytes(size=(64, 48)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def _b64_png():
    return base64.b64encode(_png_bytes()).decode("ascii")


def _concept():
    return thumbnail.normalise_concepts(
        [{"text": "0 to 10k", "text_position": "right", "text_color": "yellow",
          "scene": "a counter climbing over a desk", "why": "the number is the hook"}],
        1, "How I got 10k subs")[0]


def _stub_images_post(monkeypatch, item):
    calls = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [item]}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers or {}})
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def _boom(msg):
    def _raise(*a, **k):
        raise AssertionError(msg)
    return _raise


def test_generate_one_openai_posts_prompt_and_saves(tmp_path, monkeypatch):
    calls = _stub_images_post(monkeypatch, {"b64_json": _b64_png()})
    out = tmp_path / "t.jpg"
    thumbnail._generate_one_openai(IMG_CONFIG, _concept(), str(out), burn_text=True)

    assert calls[0]["url"] == "http://localhost:4891/v1/images/generations"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-x"
    body = calls[0]["json"]
    assert body["model"] == "gpt-image-1" and body["n"] == 1
    assert "a counter climbing over a desk" in body["prompt"]
    assert "negative space" in body["prompt"]          # the burn_text rule rides along
    with Image.open(out) as saved:
        assert saved.size == (1280, 720)               # finalize cover-cropped the 64x48 reply


def test_generate_one_openai_handles_url_replies(tmp_path, monkeypatch):
    _stub_images_post(monkeypatch, {"url": "http://img.test/x.png"})
    monkeypatch.setattr(httpx, "get",
                        lambda url, timeout=None, follow_redirects=False:
                        type("R", (), {"content": _png_bytes()})())
    out = tmp_path / "t.jpg"
    thumbnail._generate_one_openai(IMG_CONFIG, _concept(), str(out), burn_text=False)
    assert out.stat().st_size > 0


def test_generate_one_openai_raises_without_image_data(monkeypatch):
    _stub_images_post(monkeypatch, {"revised_prompt": "..."})   # no b64_json, no url
    with pytest.raises(RuntimeError, match="no image data"):
        thumbnail._generate_one_openai(IMG_CONFIG, _concept(), "unused.jpg", False)


def test_generate_one_openai_http_errors_propagate(monkeypatch):
    # The per-concept catch in generate_thumbnail turns this into the
    # endpoint's clean-unavailable 400 (app.py) — never a hang, never a 500.
    req = httpx.Request("POST", "http://localhost:4891/v1/images/generations")
    resp = httpx.Response(404, request=req)

    def refused(*a, **k):
        raise httpx.HTTPStatusError("404 Not Found", request=req, response=resp)

    monkeypatch.setattr(httpx, "post", refused)
    with pytest.raises(httpx.HTTPStatusError):
        thumbnail._generate_one_openai(IMG_CONFIG, _concept(), "unused.jpg", False)


def test_generate_one_openai_swaps_the_chat_model_default(tmp_path, monkeypatch):
    calls = _stub_images_post(monkeypatch, {"b64_json": _b64_png()})
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)

    def run_with(model, key, name):
        cfg = dict(IMG_CONFIG, model=model, api_key=key)
        thumbnail._generate_one_openai(cfg, _concept(), str(tmp_path / name), False)

    run_with("gpt-4o-mini", "sk-x", "a.jpg")
    assert calls[-1]["json"]["model"] == thumbnail.OPENAI_IMAGE_MODEL
    run_with("", "", "b.jpg")
    assert calls[-1]["json"]["model"] == thumbnail.OPENAI_IMAGE_MODEL
    assert "Authorization" not in calls[-1]["headers"]     # keyless local endpoint
    run_with("dall-e-3", "sk-x", "c.jpg")
    assert calls[-1]["json"]["model"] == "dall-e-3"        # an explicit model wins


def test_generate_thumbnail_runs_the_images_arm_without_a_gemini_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)                        # output/ lands in tmp
    monkeypatch.setattr(thumbnail.genai, "Client", _boom(
        "no Gemini key — the client must not be built"))
    monkeypatch.setattr(thumbnail, "_generate_one", _boom(
        "the Gemini image arm must stay off"))
    monkeypatch.setattr(thumbnail, "plan_thumbnail_concepts",
                        lambda client, title, count, **k:
                        [_concept() for _ in range(count)])
    seen = {}

    def fake_arm(config, concept, out_path, burn_text):
        seen.update(config=config, burn_text=burn_text)
        Image.new("RGB", (64, 48)).save(out_path, "JPEG")
        return out_path

    monkeypatch.setattr(thumbnail, "_generate_one_openai", fake_arm)

    out = thumbnail.generate_thumbnail(None, "T", "sess_img", count=1,
                                       openai_image_config=IMG_CONFIG)

    assert out[0]["url"].startswith("/thumbnails/sess_img/")
    assert seen["config"] is IMG_CONFIG and seen["burn_text"] is True


def test_generate_thumbnail_gemini_key_wins_over_the_images_arm(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class _FakeClient:
        pass

    monkeypatch.setattr(thumbnail.genai, "Client", lambda **k: _FakeClient())
    monkeypatch.setattr(thumbnail, "_generate_one_openai", _boom(
        "with a Gemini key the images arm must stay off"))
    seen = {}

    def fake_gemini(client, concept, refs, out_path, burn_text):
        seen.update(client=client)
        Image.new("RGB", (64, 48)).save(out_path, "JPEG")
        return out_path

    monkeypatch.setattr(thumbnail, "_generate_one", fake_gemini)
    monkeypatch.setattr(thumbnail, "plan_thumbnail_concepts",
                        lambda client, title, count, **k:
                        [_concept() for _ in range(count)])

    out = thumbnail.generate_thumbnail("k", "T", "sess_g", count=1,
                                       openai_image_config=IMG_CONFIG)
    assert out[0]["url"].startswith("/thumbnails/sess_g/")
    assert isinstance(seen["client"], _FakeClient)     # the Gemini arm ran, refs or not


def test_generate_thumbnail_drops_references_on_the_images_arm(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    face = tmp_path / "face.jpg"
    Image.new("RGB", (40, 40)).save(face, "JPEG")
    monkeypatch.setattr(thumbnail.genai, "Client", _boom(
        "no Gemini key — the client must not be built"))
    planned = {}

    def fake_plan(client, title, count, **k):
        planned["has_person"] = k.get("has_person")
        return [_concept() for _ in range(count)]

    monkeypatch.setattr(thumbnail, "plan_thumbnail_concepts", fake_plan)
    monkeypatch.setattr(thumbnail, "_generate_one_openai",
                        lambda config, concept, out_path, burn_text:
                        Image.new("RGB", (64, 48)).save(out_path, "JPEG") or out_path)

    thumbnail.generate_thumbnail(None, "T", "sess_d", face_image_path=str(face),
                                 count=1, openai_image_config=IMG_CONFIG)

    assert planned["has_person"] is False              # the concept prompt stays honest
    assert "honored by the Gemini arm only" in capsys.readouterr().out


def test_generate_thumbnail_without_any_backend_raises(monkeypatch):
    monkeypatch.setattr(thumbnail.genai, "Client", _boom(
        "no backend — the client must not be built"))
    with pytest.raises(RuntimeError, match="No image backend"):
        thumbnail.generate_thumbnail(None, "T", "sess_x", count=1)


def test_the_images_arm_never_touches_the_satellite_client(monkeypatch, tmp_path):
    # No-double-route canary for this reroute (design ordering constraint):
    # the images arm routes the resolved OPENAI_* triple through
    # /v1/images/generations; a leak into the satellite client trips loudly.
    # Kept here so tests/test_no_double_route.py stays byte-unmodified.
    import llm_client

    monkeypatch.setattr(llm_client, "chat", _boom("must not call the satellite client"))
    monkeypatch.setattr(llm_client, "active_config",
                        _boom("must not read the satellite config"))
    monkeypatch.setattr(thumbnail.genai, "Client", _boom(
        "no Gemini key — the client must not be built"))
    monkeypatch.setattr(thumbnail, "_generate_one_openai",
                        lambda config, concept, out_path, burn_text:
                        Image.new("RGB", (64, 48)).save(out_path, "JPEG") or out_path)

    # llm_config=None + no Gemini key: the generic fallback concepts, no
    # llm_client call anywhere on the path.
    out = thumbnail.generate_thumbnail(None, "T", "sess_c", count=1,
                                       openai_image_config=IMG_CONFIG)
    assert out[0]["url"].startswith("/thumbnails/sess_c/")


# --- the /api/thumbnail/generate gate --------------------------------------------

@pytest.fixture
def api_client():
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
              "GEMINI_API_KEY", "GEMINI_MODEL",
              "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_MODEL_THUMBNAIL"):
        monkeypatch.delenv(k, raising=False)


class TestGenerateGate:
    def test_no_backend_at_all_is_a_clean_400(self, api_client, clean_env):
        r = api_client.post("/api/thumbnail/generate",
                            data={"session_id": "nope", "title": "T"})
        assert r.status_code == 400
        assert r.json()["detail"] == "Missing X-Gemini-Key header"

    def test_the_images_endpoint_passes_the_gate(self, api_client, clean_env, monkeypatch):
        captured = {}

        def fake_generate(api_key, title, session_id, face, bg, extra, count,
                          video_context, **kw):
            captured.update(api_key=api_key, count=count, kwargs=kw)
            return [{"url": "/t/1.jpg", "text": "X", "why": ""}]

        monkeypatch.setattr(app_module, "generate_thumbnail", fake_generate)
        r = api_client.post("/api/thumbnail/generate",
                            data={"session_id": "nope", "title": "T", "count": 2},
                            headers=OPENAI_HEADERS)
        assert r.status_code == 200
        assert r.json() == {"thumbnails": [{"url": "/t/1.jpg", "text": "X", "why": ""}]}
        assert not captured["api_key"]                 # no Gemini key anywhere (self-host resolve_gemini returns "")
        assert captured["count"] == 2
        assert captured["kwargs"]["openai_image_config"] == {
            "api_key": "sk-test", "model": "qwen2.5vl",
            "base_url": "http://localhost:11434/v1"}
        assert captured["kwargs"]["llm_config"] is None   # cleaned env → no satellite leg

    def test_a_gemini_key_wins_over_the_openai_headers(self, api_client, clean_env, monkeypatch):
        captured = {}

        def fake_generate(api_key, title, session_id, face, bg, extra, count,
                          video_context, **kw):
            captured.update(api_key=api_key, openai=kw.get("openai_image_config"))
            return [{"url": "/t/1.jpg", "text": "X", "why": ""}]

        monkeypatch.setattr(app_module, "generate_thumbnail", fake_generate)
        r = api_client.post("/api/thumbnail/generate",
                            data={"session_id": "nope", "title": "T"},
                            headers={**OPENAI_HEADERS, "X-Gemini-Key": "k"})
        assert r.status_code == 200
        assert captured["api_key"] == "k"
        assert captured["openai"] is None              # the images arm stayed off

    def test_a_refused_images_endpoint_is_a_clean_400(self, api_client, clean_env, monkeypatch):
        def refused(*a, **kw):
            raise RuntimeError("All thumbnail generations failed. "
                               "Last error: 404 Client Error 'Not Found'")

        monkeypatch.setattr(app_module, "generate_thumbnail", refused)
        r = api_client.post("/api/thumbnail/generate",
                            data={"session_id": "nope", "title": "T"},
                            headers=OPENAI_HEADERS)
        assert r.status_code == 400
        assert "Image generation is not available" in r.json()["detail"]
