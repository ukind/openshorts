"""Unit tests for ai_provider sampling-parameter passthrough (Phase 1).
Pins plan decisions D1 (per-call > stored > frozen precedence),
D7 (Gemini stores but never applies), and the factory forwarding contract.
The lazy `openai` SDK import is avoided by injecting a fake module through
sys.modules; factory tests stub __init__ so no SDK client is ever built.
"""
import base64
import inspect
import json
import pytest
import struct
import sys
from types import ModuleType, SimpleNamespace

import ai_provider
from ai_provider import (
    GeminiProvider,
    OpenAICompatibleProvider,
    _sanitize_max_tokens,
    _sanitize_temperature,
    _sanitize_timeout,
    create_ai_provider,
)


def _fake_openai_module(recorded):
    """A fake `openai` module whose OpenAI(...) returns a recorder client."""

    class _Completions:
        def create(self, **params):
            recorded.append(params)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps({"ok": True})))])

    fake = ModuleType("openai")
    fake.OpenAI = lambda api_key=None, base_url=None: SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions()))
    fake.BadRequestError = type("BadRequestError", (Exception,), {})
    return fake


class TestSanitizeTemperature:
    def test_none_is_none(self):
        assert _sanitize_temperature(None) is None

    def test_garbage_is_none(self):
        assert _sanitize_temperature("abc") is None
        assert _sanitize_temperature([]) is None

    def test_empty_is_none(self):
        assert _sanitize_temperature("") is None

    def test_nan_is_none(self):
        assert _sanitize_temperature(float("nan")) is None

    def test_exact_bounds_pass_through(self):
        assert _sanitize_temperature(0.0) == 0.0
        assert _sanitize_temperature(2.0) == 2.0

    def test_in_range_passes(self):
        assert _sanitize_temperature(0.5) == 0.5
        assert _sanitize_temperature("0.9") == 0.9

    def test_clamped_to_bounds(self):
        assert _sanitize_temperature(-3) == 0.0
        assert _sanitize_temperature(9) == 2.0


class TestSanitizeMaxTokens:
    def test_none_is_none(self):
        assert _sanitize_max_tokens(None) is None

    def test_garbage_is_none(self):
        assert _sanitize_max_tokens("abc") is None
        assert _sanitize_max_tokens([]) is None

    def test_empty_is_none(self):
        assert _sanitize_max_tokens("") is None

    def test_below_one_is_none(self):
        assert _sanitize_max_tokens(0) is None

    def test_exact_bounds_pass_through(self):
        assert _sanitize_max_tokens(1) == 1
        assert _sanitize_max_tokens(8192) == 8192

    def test_clamped_to_bounds(self):
        # 64k ceiling: reasoning models spend completion tokens thinking
        assert _sanitize_max_tokens(999_999) == 65536


class TestSanitizeTimeout:
    def test_none_is_none(self):
        assert _sanitize_timeout(None) is None

    def test_garbage_is_none(self):
        assert _sanitize_timeout("abc") is None

    def test_empty_is_none(self):
        assert _sanitize_timeout("") is None

    def test_non_positive_is_none(self):
        assert _sanitize_timeout(0) is None
        assert _sanitize_timeout(-5) is None

    def test_clamped_to_bounds(self):
        assert _sanitize_timeout(1) == 5.0
        assert _sanitize_timeout(10_000) == 600.0
        assert _sanitize_timeout(30) == 30.0
        assert _sanitize_timeout(float("inf")) == 600.0  # clamp, not unset


class TestFactoryForwarding:
    def test_openai_branch_forwards_params(self, monkeypatch):
        captured = {}

        def fake_init(self, model_name, api_key=None, base_url="",
                      temperature=None, max_tokens=None, timeout=None):
            captured.update(model=model_name, key=api_key, base_url=base_url,
                            temperature=temperature, max_tokens=max_tokens,
                            timeout=timeout)

        monkeypatch.setattr(ai_provider.OpenAICompatibleProvider,
                            "__init__", fake_init)
        create_ai_provider("openai", "m-1", "k-1", base_url="http://b.test/v1",
                           temperature=0.2, max_tokens=3000, timeout=90)
        assert captured == {"model": "m-1", "key": "k-1",
                            "base_url": "http://b.test/v1",
                            "temperature": 0.2, "max_tokens": 3000,
                            "timeout": 90}

    def test_openai_branch_forwards_unset_as_none(self, monkeypatch):
        captured = {}

        def fake_init(self, model_name, api_key=None, base_url="",
                      temperature=None, max_tokens=None, timeout=None):
            captured.update(model=model_name, temperature=temperature,
                            max_tokens=max_tokens, timeout=timeout)

        monkeypatch.setattr(ai_provider.OpenAICompatibleProvider,
                            "__init__", fake_init)
        create_ai_provider("openai", "m-2", "k-2")
        assert captured == {"model": "m-2", "temperature": None,
                            "max_tokens": None, "timeout": None}

    def test_gemini_branch_forwards_params(self, monkeypatch):
        captured = {}

        def fake_init(self, model_name, api_key=None,
                      temperature=None, max_tokens=None, timeout=None):
            captured.update(model=model_name, key=api_key,
                            temperature=temperature, max_tokens=max_tokens,
                            timeout=timeout)

        monkeypatch.setattr(ai_provider.GeminiProvider, "__init__", fake_init)
        create_ai_provider("gemini", "g-1", "gk",
                           temperature=0.2, max_tokens=3000, timeout=90)
        assert captured == {"model": "g-1", "key": "gk",
                            "temperature": 0.2, "max_tokens": 3000,
                            "timeout": 90}


class TestRequestPrecedence:
    def _run(self, monkeypatch, ctor=None, call_kwargs=None):
        recorded = []
        monkeypatch.setitem(sys.modules, "openai",
                            _fake_openai_module(recorded))
        prov = OpenAICompatibleProvider("model-x", "key-x",
                                        "http://x.test/v1", **(ctor or {}))
        prov.generate_content("prompt", **(call_kwargs or {}))
        return prov, recorded[-1]

    def test_per_call_kwarg_wins(self, monkeypatch):
        prov, params = self._run(
            monkeypatch, ctor={"temperature": 0.9},
            call_kwargs={"temperature": 0.2, "max_tokens": 3000,
                         "timeout": 90})
        assert params["temperature"] == 0.2
        assert params["max_tokens"] == 3000
        assert params["timeout"] == 90

    def test_stored_value_applies_without_kwarg(self, monkeypatch):
        prov, params = self._run(
            monkeypatch, ctor={"temperature": 0.25, "timeout": 60})
        assert params["temperature"] == 0.25
        assert params["timeout"] == 60

    def test_frozen_defaults_when_nothing_set(self, monkeypatch):
        prov, params = self._run(monkeypatch)
        assert params["temperature"] == 0.7
        assert params["max_tokens"] == 4096
        assert "timeout" not in params

    def test_explicit_none_kwarg_counts_as_unset(self, monkeypatch):
        prov, params = self._run(
            monkeypatch, ctor={"temperature": 0.3},
            call_kwargs={"temperature": None, "max_tokens": None})
        assert params["temperature"] == 0.3
        assert params["max_tokens"] == 4096

    def test_garbage_constructor_values_sanitize_to_unset(self, monkeypatch):
        prov, params = self._run(
            monkeypatch,
            ctor={"temperature": "abc", "max_tokens": "", "timeout": 0})
        assert prov.temperature is None
        assert prov.max_tokens is None
        assert prov.timeout is None
        assert params["temperature"] == 0.7
        assert params["max_tokens"] == 4096
        assert "timeout" not in params


class TestGeminiNeverApplies:
    def test_generate_content_reads_no_kwargs_and_no_stored_params(self):
        src = inspect.getsource(GeminiProvider.generate_content)
        assert "kwargs.get" not in src
        assert "kwargs[" not in src
        assert "self.temperature" not in src
        assert "self.max_tokens" not in src
        assert "self.timeout" not in src


# --- Phase 2 additions (append below the Phase 1 test classes) --------------
# Also added to the imports at the top of the file (Phase 1's locked file has
# no pytest import — it was unused there; base64/struct pin the probe image):
#   import base64
#   import struct
#   import pytest


@pytest.fixture(autouse=True)
def _clean_probe_cache(monkeypatch):
    monkeypatch.setattr(ai_provider, "_VISION_PROBE_CACHE", {})


class TestProbeClassification:
    def _provider_with_error(self, monkeypatch, recorded, exc):
        fake = _fake_openai_module(recorded)

        def boom(**params):
            raise exc

        # The retry loop sleeps 5s/10s on transient failures; patch it so
        # transient tests finish in milliseconds.
        monkeypatch.setattr(ai_provider.time, "sleep", lambda _s: None)
        fake.OpenAI = lambda api_key=None, base_url=None: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
        monkeypatch.setitem(sys.modules, "openai", fake)
        return OpenAICompatibleProvider("m", "k", "http://x.test/v1")

    def test_ollama_cloud_400_text_is_no_vision(self, monkeypatch):
        recorded = []
        prov = self._provider_with_error(
            monkeypatch, recorded,
            Exception("Error code: 400 - {'error': 'this model does not support image input'}"))
        assert ai_provider.probe_vision_support(prov) == "no_vision"

    def test_lm_studio_text_is_no_vision(self, monkeypatch):
        recorded = []
        prov = self._provider_with_error(
            monkeypatch, recorded,
            Exception("Model does not support images. Please use a model that does"))
        assert ai_provider.probe_vision_support(prov) == "no_vision"

    def test_transient_error_is_unknown(self, monkeypatch):
        recorded = []
        prov = self._provider_with_error(
            monkeypatch, recorded, Exception("HTTP 500 INTERNAL server error"))
        assert ai_provider.probe_vision_support(prov) == "unknown"

    def test_transient_first_beats_image_text(self, monkeypatch):
        # A 500 whose body mentions images must NOT classify as no_vision.
        recorded = []
        prov = self._provider_with_error(
            monkeypatch, recorded,
            Exception("500 INTERNAL: handler for does not support image crashed"))
        assert ai_provider.probe_vision_support(prov) == "unknown"

    def test_non_json_success_reply_is_unknown_not_vision_ok(self, monkeypatch):
        # A vision-capable model replying plain "yes" raises the JSON-parse
        # error; the verdict must degrade to unknown, never no_vision.
        recorded = []
        fake = _fake_openai_module(recorded)
        fake.OpenAI = lambda api_key=None, base_url=None: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(
                create=lambda **p: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content="yes"))]))))
        monkeypatch.setitem(sys.modules, "openai", fake)
        prov = OpenAICompatibleProvider("m", "k", "http://x.test/v1")
        assert ai_provider.probe_vision_support(prov) == "unknown"


class TestProbeMechanics:
    def test_probe_sends_tiny_json_demanding_request(self, monkeypatch):
        recorded = []
        monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(recorded))
        prov = OpenAICompatibleProvider("m", "k", "http://x.test/v1")
        assert ai_provider.probe_vision_support(prov) == "vision_ok"
        params = recorded[0]
        assert params["max_tokens"] == 16
        assert params["timeout"] == 15
        assert "response_format" not in params
        content = params["messages"][0]["content"]
        assert any(p.get("type") == "image_url" for p in content)
        assert any(p.get("type") == "text" and '{"ok": true}' in p.get("text", "")
                   for p in content)

    def test_cache_hits_once_per_identity(self, monkeypatch):
        recorded = []
        monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(recorded))
        prov = OpenAICompatibleProvider("m", "k", "http://x.test/v1")
        assert ai_provider.probe_vision_support(prov) == "vision_ok"
        assert ai_provider.probe_vision_support(prov) == "vision_ok"
        assert len(recorded) == 1  # second lookup is a cache hit

    def test_different_identity_probes_again(self, monkeypatch):
        recorded = []
        monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(recorded))
        a = OpenAICompatibleProvider("m", "k", "http://x.test/v1")
        b = OpenAICompatibleProvider("m", "k2", "http://x.test/v1")
        ai_provider.probe_vision_support(a)
        ai_provider.probe_vision_support(b)
        assert len(recorded) == 2

    def test_gemini_and_non_openai_providers_are_never_probed(self):
        gem = GeminiProvider.__new__(GeminiProvider)  # no SDK import, no client
        assert ai_provider.probe_vision_support(gem) == "vision_ok"
        assert ai_provider._VISION_PROBE_CACHE == {}

    def test_probe_image_is_a_valid_png(self):
        header, encoded = ai_provider._PROBE_IMAGE.split(",", 1)
        assert header == "data:image/png;base64"  # split(",",1)[0] keeps the ;base64 suffix
        raw = base64.b64decode(encoded)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">I", raw[8:12])[0] == 13  # IHDR length
        assert struct.unpack(">II", raw[16:24]) == (8, 8)
