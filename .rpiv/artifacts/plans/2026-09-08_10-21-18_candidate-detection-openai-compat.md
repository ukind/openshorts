---
date: 2026-09-08T10:21:18+0700
author: Yogiswara Utama
commit: 51c1861
branch: main
repository: openshorts
topic: "candidate detection works on OpenAI-compatible endpoints (Ollama Cloud)"
tags: [plan, ai-provider, openai-compatible, ollama, silent-video, vision-probe, cheap-events, params]
status: ready
parent: .rpiv/artifacts/research/2026-09-08_09-13-45_candidate-detection-openai-compat.md
phase_count: 6
phases:
  - { n: 1, title: Provider param passthrough, files: [ai_provider.py, tests/test_ai_provider.py], depends_on: [], status: locked }
  - { n: 2, title: Vision capability probe, files: [ai_provider.py, tests/test_ai_provider.py], depends_on: [1], status: locked }
  - { n: 3, title: Capability gates + visible warning, files: [main.py, log_view.py, tests/test_no_double_route.py, tests/test_log_view.py], depends_on: [2], status: locked }
  - { n: 4, title: Silent-video path rewrite, files: [main.py, tests/test_no_double_route.py], depends_on: [1, 2, 3], status: locked }
  - { n: 5, title: Cheap events for short videos, files: [main.py, tests/test_no_double_route.py], depends_on: [4], status: locked }
  - { n: 6, title: Docs sync, files: [README.md, CLAUDE.md], depends_on: [1, 2, 3, 4, 5], status: locked }
unresolved_phase_count: 0
last_updated: 2026-09-08T18:00:55+07:00
last_updated_by: Yogiswara Utama
---

# Candidate detection on OpenAI-compatible endpoints — Implementation Plan

## Overview

Four fixes make the candidate-detection panel's promises hold under `AI_PROVIDER=openai` (the OpenAI-compatible chat-completions protocol — e.g. Ollama Cloud, LM Studio, local Ollama): the silent-video path routes through `create_ai_provider` (no Gemini key wall, no raw `genai.Client`), a one-shot cached vision probe protects text-only models, the factory honors `temperature`/`max_tokens`/`timeout`, and cheap-signal extraction runs for videos of 120 s or less. The Gemini path stays unchanged except for one developer-approved defect fix: the silent path now attaches its uploaded file to the model call.

## Requirements

- FR1: A silent video clips successfully through the provider abstraction on an OpenAI-compatible endpoint. No `GEMINI_API_KEY` check, no raw `genai.Client`. The Gemini silent path keeps its native upload, with the upload attached to the call (approved defect fix).
- FR2: One image-capability probe per (provider, model, base_url, key), verdict tri-state (`no_vision` / `unknown` / `vision_ok`). Gates sit before deep frame extraction and before the vision loop. Zero frame extractions after a `no_vision` verdict. The skip warning is visible in the dashboard job log on self-host and cloud.
- FR3: `create_ai_provider` forwards `temperature` / `max_tokens` / `timeout` to both provider constructors. Precedence: per-call kwarg > stored value > frozen defaults (0.7 / 4096 / no timeout). Gemini stores but never applies (wire byte-identical).
- FR4: Cheap-signal extraction runs for videos of 120 s or less. The short-path detail payload splits hints per type (`audio_events` / `scene_boundaries`). Deep stays off for short videos.
- Docs: five stale spots stop claiming silent-video is Gemini-only, in the same change: README.md:571 (env-table row), README.md:614 (capability matrix row), README.md:406-410 ("What still needs Gemini" bullet), README.md:148 (mermaid flow node) and CLAUDE.md:149 ("Stays Gemini" list).
- Acceptance endpoints: Ollama Cloud + one local server. The Gemini suite baseline (889 passed / 10 pre-existing env failures on this machine) and `tests/test_no_double_route.py` pins must not regress.

## Current State Analysis

The video pipeline (`main.py`) dispatches through `ai_provider.create_ai_provider`, but the silent-video path bypasses it entirely, and the factory silently drops the sampling kwargs that all 9 production call sites already pass.

### Key Discoveries

- `get_visual_clips` (`main.py:2888`) is a Gemini island: key wall at `main.py:2893-2904` (with a self-contradictory entry print at `:2892`), raw `genai.Client(api_key=...)` at `main.py:2905`. Sole caller: `main.py:3548`; failure surfaces as `RuntimeError("Clip detection failed — <Gemini|OpenAI> did not return usable clips")` at `main.py:3550-3556`.
- **Defect (approved fix)**: the Gemini silent call at `main.py:2943-2948` sends `contents=[prompt]` only — the uploaded file is polled to ACTIVE and then never attached. The model invents clip timestamps from prompt text alone.
- All 9 factory call sites pass kwargs to the factory; the factory drops them (`ai_provider.py:450-474`). The per-call kwargs layer inside `OpenAICompatibleProvider.generate_content` (`ai_provider.py:305-310`, `:327-329`) works but no production site uses it, so every OpenAI-compatible request runs 0.7 / 4096 / no timeout today. `AI_TEMPERATURE`/`AI_MAX_TOKENS`/`AI_TIMEOUT` (`main.py:115-117`) have zero effect on OpenAI-compatible jobs.
- `GeminiProvider.generate_content` (`ai_provider.py:96-250`) reads no kwargs and builds config from `response_mime_type` + optional `response_schema` only (`:189-195`). Storing params at `__init__` without applying them leaves the Gemini wire byte-identical.
- The deep scan demonstrates Gemini-native-without-raw-genai: upload via `_prov.client.files.upload` (`main.py:2197`), call via `_prov.client.models.generate_content` with `_prov.genai_types.GenerateContentConfig` (`main.py:2218-2219`).
- Deep runs BEFORE scoring (`main.py:2095` vs `:2493`), so the probe needs two gate sites sharing one cached verdict: before deep frame extraction (`main.py:2120`, fallback `:2255`) and before the vision loop (`main.py:2513`, extraction `:2574-2580`).
- `main.py:2502` sits inside the score batch loop — per-instance probe caching cannot work. One process per job (`app.py:2560` spawns `main.py`) makes a module-level cache job-scoped.
- The `[toggles]`/`[cheap]` lines are parent-process prints that never reach the job log. Only child (`main.py`) stdout lands in `jobs[job_id]['logs']` (`app.py:1890`). Cloud hides everything not matched by `log_view.py:15-25`; the verbatim rule at `:18-19` is `^`-anchored, so a subprocess timestamp defeats it — the new rule must be unanchored.
- Cheap-events block: `main.py:2069-2091` (init, import, toggle, guard, extract, log). The ≤120 s branch returns at `main.py:1957-2052` before the block runs; the guard `'cheap_events' in locals()` at `:1991` is always False, so the short-path payload sends `"none"` in both hint fields.
- Trap: moving the whole seeding `try` (`main.py:2071-2424`) instead of exactly `:2069-2091` would silently enable deep for 61-120 s videos.
- `tests/test_no_double_route.py` is the hard boundary: `_no_genai` (`:67-72`) patches the `google.genai.Client` CLASS, so it also trips on `GeminiProvider.__init__` (`ai_provider.py:79-87`). No test drives the silent path today. `fake_create` (`:122-125`) records `(provider_type, model_name)` only.
- Deep's cost append is dead code (`main.py:2291` appends before `costs = []` at `:2425`; `UnboundLocalError` swallowed at `:2292-2293`). The probe must not repeat this pattern.
- Known false-positive risk: a server that accepts an image part and ignores it returns 200 → false `vision_ok`. The per-window failure block (`cloud/semantic_analyzer.py:240-245`) stays as backstop.

## Desired End State

Self-host, OpenAI-compatible endpoint (Ollama Cloud), silent music-montage video, vision model selected:

```
POST /api/process (file, X-AI-Provider: openai, X-OpenAI-* headers)
→ job log: "🎥 Silent video — analyzing with Openai vision (no transcript)..."
→ job log: one probe call, then 12 frames over the full duration
→ clips render; no GEMINI_API_KEY required anywhere in the flow
```

Same video, text-only model selected:

```
→ job log: "👁️ Vision analysis skipped: the selected model cannot see images (text-only)."
   (one tiny probe call, zero screenshots taken)
→ job fails with: "Clip detection failed — OpenAI-compatible endpoint did not
   return usable clips" only after the honest skip message, not before it
```

Short video (≤120 s) with scene/audio toggles on:

```
→ cheap events extracted before the whole-clip shortcut
→ detail prompt payload carries "audio_events": "12.0s:scream, ..."
   and "scene_boundaries": "5.0s, ..." instead of "none"/"none"
```

Gemini job (any path): byte-identical behavior, except the silent path now attaches its uploaded file so Gemini actually watches the footage.

## What We're NOT Doing

- Cost accounting for the silent path stays `None` (discover decision; excluded).
- Schema-400 resilience beyond the existing response_format retry (excluded).
- Deep scan for videos of 61-120 s stays off; the dead `> 60` conjunct at `main.py:2095` stays (flagged, not fixed).
- Deep's native-video inline config (`main.py:2218`) does not receive stored deep params (flagged, out of plain FR3).
- The `_wc_active_weights = None` NameError artifact (`main.py:1970` vs `:2134`) stays untouched.
- Dashboard files untouched (discover decision: UI stays as-is).
- No new env keys: the probe is runtime state; the env namespace rule (`LLM_*` satellites, `AI_PROVIDER`/`OPENAI_*`/`GEMINI_*` pipeline) holds.

## Decisions

### D1: Param precedence — per-call > stored > frozen defaults

Directional confirm + advisor verdict. `llm_client.py:443-446` idiom (`if x is not None`). Hardening (advisor): garbage or set-but-empty values sanitize to unset at construction; bounds temperature 0-2, max_tokens 1-8192, timeout 5-600 s. The `timeout` key appears in the request only when a value exists (today's shape at `ai_provider.py:327-329`).

### D2: Silent openai call — deep-scan shape

Directional confirm. 12 frames over the full duration (`main.py:2109` precedent), `schema=VisualResponse` (`gemini_worker.py:81-83`), the existing response_format retry (`ai_provider.py:377-385`) absorbs servers without json_schema. No bespoke silent-path parser.

### D3: Probe pre-flight in the silent openai branch

Directional confirm + advisor riders. The silent path consults the probe before frame extraction (openai branch only — Gemini exempt: native upload, always vision-capable, `input_mode: "native_video"` at `main.py:1631`). Verdict cached per identity. Probe cost never enters the job cost total (OpenAI-compatible `generate_content` returns `cost_analysis: None` structurally — `ai_provider.py:341-344`).

### D4: Gemini silent path attaches the uploaded file

Developer decision (checkpoint). `contents=[file_upload, prompt]` at the rewrite of `main.py:2943-2948` — the feature's stated purpose becomes true. Rest of the Gemini path byte-identical; no test drives this path, so the suite cannot catch the choice either way.

### D5: Probe design details (design-stage deferrals, resolved)

Prompt demands a JSON reply (`Reply with exactly this JSON: {"ok": true}`) so a success path survives `generate_content`'s unconditional `json.loads` (`ai_provider.py:330-341`). Tiny 8x8px data-URL image. Per-call `max_tokens=16`, `timeout=15`. Classification is transient-FIRST with the exact token list from `ai_provider.py:420-424`; image-reject substring `"does not support image"` matches both verified wire texts (Ollama Cloud 400 and LM Studio).

### D6: Cheap events move exactly `main.py:2069-2091`

Inherited from research (developer-confirmed). Above the short-branch gate at `main.py:1957`, wrapped in its own `try/except` printing the same "⚠️ Cheap event seeding skipped" message. Short-path payload splits: all cheap events in-window → `audio_events` (long-path shape, `main.py:2719`), `scene_change` timestamps → `scene_boundaries` (`:2732`) — the detail prompt's SCENE-CUT ALIGNMENT rule (`gemini_worker.py:352-357`) reads `scene_boundaries` and would misfire on audio spikes.

### D7: Gemini params stored-but-not-applied

Inherited from research (developer-confirmed). `GeminiProvider.generate_content` keeps reading no kwargs. Forwarding acceptance test passes against the constructor, not the wire.

### D8: Warning surface — one child print + one unanchored rule

Inherited from research (developer-confirmed: cloud too). One `print` in `main.py` (child stdout → job log). One unanchored rule in `log_view.py:_RULES` so the cloud whitelist keeps it. User-facing line carries no model name or URL (whitelist philosophy); details ride `_dbg()`.

### D9: Docs in the same change

Directional confirm. README.md:571, README.md:614, CLAUDE.md:149 updated in the final phase. Precedent lesson: docs that lag the code lie within days.

### D10: Test placement

New `tests/test_ai_provider.py` for probe + params unit tests (stubbed SDK objects — avoids the lazy `openai`/`google.genai` imports, mirrors `tests/test_llm_client.py` fixture style). Boundary tests (silent path, short-video payload) extend `tests/test_no_double_route.py` reusing `_install_pipeline_provider` (`:107-125`). Log rule test extends `tests/test_log_view.py`.

## Phase 1: Provider param passthrough

### Overview

Foundation: the factory stores sampling params on both constructors with sanitize bounds; the OpenAI-compatible request honors per-call > stored > frozen defaults. Depends on nothing; Phases 2-4 build on it.

### Changes Required:

#### 1. ai_provider.py
**File**: ai_provider.py
**Changes**: MODIFY — sanitize helpers; `AIProvider.__init__` stores three Optional params; both constructor signatures pass them through; `OpenAICompatibleProvider.generate_content` precedence chain; factory forwards kwargs.

```python
# (a) NEW — module-level sanitize helpers, placed after the imports, before AIProviderError:
# --- Sampling-parameter sanitization ----------------------------------------
# Garbage, set-but-empty, and None values sanitize to None (= unset). At
# request time an unset value falls through to the stored constructor value,
# then to the frozen default. Bounds: temperature 0-2, max_tokens 1-8192,
# timeout 5-600 seconds.

def _sanitize_temperature(value) -> Optional[float]:
    """Coerce a temperature setting to a bounded float, or None if unset."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:  # NaN
        return None
    return max(0.0, min(2.0, num))


def _sanitize_max_tokens(value) -> Optional[int]:
    """Coerce a max_tokens setting to a bounded int, or None if unset."""
    if value is None:
        return None
    try:
        num = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if num < 1:
        return None
    return min(8192, num)


def _sanitize_timeout(value) -> Optional[float]:
    """Coerce a timeout setting to a bounded float, or None if unset."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:  # NaN
        return None
    if num <= 0:
        return None
    return max(5.0, min(600.0, num))


# (b) MODIFY — AIProvider.__init__ (currently stores model_name + api_key only):
    def __init__(self, model_name: str, api_key: Optional[str] = None,
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 timeout: Optional[float] = None):
        self.model_name = model_name
        self.api_key = api_key
        # Sampling params are stored sanitized. Applying them is the
        # subclass's job: OpenAI-compatible applies them at request time;
        # Gemini stores but never applies (wire stays byte-identical, D7).
        self.temperature = _sanitize_temperature(temperature)
        self.max_tokens = _sanitize_max_tokens(max_tokens)
        self.timeout = _sanitize_timeout(timeout)


# (c) MODIFY — GeminiProvider.__init__ signature + first line (rest unchanged:
#     lazy google.genai import, client construction; generate_content untouched):
    def __init__(self, model_name: str = "gemini-2.5-flash",
                 api_key: Optional[str] = None,
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 timeout: Optional[float] = None):
        super().__init__(model_name, api_key,
                         temperature=temperature, max_tokens=max_tokens,
                         timeout=timeout)


# (d) MODIFY — OpenAICompatibleProvider.__init__ signature + first line (rest
#     unchanged: base_url storage, lazy openai import, client construction):
    def __init__(self, model_name: str = "gpt-4", api_key: Optional[str] = None,
                 base_url: str = "https://api.openai.com/v1",
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 timeout: Optional[float] = None):
        super().__init__(model_name, api_key,
                         temperature=temperature, max_tokens=max_tokens,
                         timeout=timeout)


# (e) MODIFY — OpenAICompatibleProvider.generate_content: the params-dict setup
#     (temperature = kwargs.get("temperature", 0.7), max_tokens = kwargs.get(
#     "max_tokens", 4096)) becomes the D1 precedence chain:
        # Sampling-parameter precedence (D1): per-call kwarg > stored
        # constructor value > frozen default. An explicit-None kwarg counts
        # as unset, so callers can opt back into the stored/default value.
        temperature = kwargs.get("temperature")
        if temperature is None:
            temperature = self.temperature
        if temperature is None:
            temperature = 0.7

        max_tokens = kwargs.get("max_tokens")
        if max_tokens is None:
            max_tokens = self.max_tokens
        if max_tokens is None:
            max_tokens = 4096

        params = {
            "model": self.model_name,
            "messages": final_messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }

# ... and the timeout block (if "timeout" in kwargs: params["timeout"] = ...)
# becomes:
        # Timeout appears in the request only when a value exists (today's
        # wire shape). The stored constructor value applies when no per-call
        # kwarg is given.
        timeout = kwargs.get("timeout")
        if timeout is None:
            timeout = self.timeout
        if timeout is not None:
            params["timeout"] = timeout

# (f) MODIFY — create_ai_provider: both branches forward the three kwargs;
#     base_url handling unchanged:
    if provider_type.lower() == "gemini":
        model = model_name or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        return GeminiProvider(
            model, api_key,
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens"),
            timeout=kwargs.get("timeout"),
        )
    elif provider_type.lower() == "openai":
        model = model_name or os.getenv("OPENAI_MODEL", "gpt-4")
        base_url = kwargs.get("base_url") or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return OpenAICompatibleProvider(
            model, api_key, base_url,
            temperature=kwargs.get("temperature"),
            max_tokens=kwargs.get("max_tokens"),
            timeout=kwargs.get("timeout"),
        )
```

#### 2. tests/test_ai_provider.py
**File**: tests/test_ai_provider.py
**Changes**: NEW — sanitize bounds, factory forwarding to both constructors (stubbed `__init__`), request precedence (fake `openai` module via `sys.modules`), Gemini never applies.

```python
"""Unit tests for ai_provider sampling-parameter passthrough (Phase 1).
Pins plan decisions D1 (per-call > stored > frozen precedence),
D7 (Gemini stores but never applies), and the factory forwarding contract.
The lazy `openai` SDK import is avoided by injecting a fake module through
sys.modules; factory tests stub __init__ so no SDK client is ever built.
"""
import inspect
import json
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
        assert _sanitize_max_tokens(999_999) == 8192


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
```

### Success Criteria:

#### Automated Verification:
- [x] `python -m pytest tests/test_ai_provider.py -q` — all new tests pass (sanitize bounds, factory forwarding, request precedence, Gemini never-applies pin)
- [x] `python -m pytest tests/test_no_double_route.py tests/test_log_view.py tests/test_llm_client.py -q` — boundary pins and neighboring suites green (Phase 1 touches none of these files; green proves no incidental regression)
- [x] Full-suite acceptance: `python -m pytest -q` — no NEW failures vs the 889-passed / ~10 pre-existing env-failure baseline (this run: 916 passed = 889 baseline + 27 new, same 10 pre-existing env failures)

#### Manual Verification:
- None required beyond the suite for this phase: the factory change touches all 9 production call sites' OpenAI-compatible wire behavior, and both new behaviors (precedence chain, sanitize bounds) are pinned by unit tests. The Gemini wire stays byte-identical, enforced by the D7 source pin plus the unchanged existing suite.
## Phase 2: Vision capability probe

### Overview

The probe: one tiny-image call per provider identity, tri-state verdict, transient-first classification, module-level cache. Depends on Phase 1 (per-call kwargs carry the probe's small `max_tokens`/`timeout`).

### Changes Required:

#### 2. ai_provider.py
**File**: ai_provider.py
**Changes**: MODIFY — `_VISION_PROBE_CACHE`, `_PROBE_IMAGE` data URL, `probe_vision_support()` with tri-state classification.

```python
# --- Vision capability probe (plan Phase 2) ---------------------------------
# Placement: NEW section AFTER the OpenAICompatibleProvider class definition
# (needs the class for isinstance) and BEFORE create_ai_provider.
#
# One tiny-image call per provider identity. Module-level cache: the provider
# is rebuilt per score batch (main.py:2502) and one process per job
# (app.py:2560) makes module state job-scoped. Verdict is tri-state:
#   "no_vision"  — the endpoint rejects image parts; skip vision/deep frames
#   "unknown"    — probe failed transiently or ambiguously; proceed as today
#   "vision_ok"  — the endpoint accepted the image part
_VISION_PROBE_CACHE: Dict[tuple, str] = {}

# 8x8 gray PNG as a data URL — the probe payload (D5). Validity is pinned by
# test_probe_image_is_a_valid_png (decode, IHDR len 13, 8x8).
_PROBE_IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAAAAADhZOFXAAAADklEQVR4nGNogAIGyhgAgIQgAQ2AJGEAAAAASUVORK5CYII="

# The probe demands a JSON reply so a success path survives
# generate_content's unconditional json.loads (ai_provider.py:330-341).
_PROBE_PROMPT = 'Reply with exactly this JSON: {"ok": true}'

# Exact transient token list from OpenAICompatibleProvider.generate_content
# (ai_provider.py:420-424). Classification is transient-FIRST (D5): a
# transient failure is never misread as no_vision, even if its text happens
# to mention images (e.g. a 500 whose body mentions image handling).
_PROBE_TRANSIENT_TOKENS = (
    '503', 'UNAVAILABLE', '429', 'RESOURCE_EXHAUSTED',
    '500', 'INTERNAL', 'overloaded', 'Deadline',
    'timeout', 'connection', 'retry'
)

# Verified wire texts: Ollama Cloud 400 "this model does not support image
# input"; LM Studio "Model does not support images. Please use a model that
# does". The substring matches both.
_PROBE_IMAGE_REJECT = "does not support image"


def _classify_probe_failure(message: str) -> str:
    """Classify a probe failure; transient-first (D5)."""
    msg = str(message)
    if any(tok in msg for tok in _PROBE_TRANSIENT_TOKENS):
        return "unknown"
    if _PROBE_IMAGE_REJECT in msg.lower():
        return "no_vision"
    return "unknown"


def probe_vision_support(provider) -> str:
    """Probe once per provider identity whether the endpoint accepts images.

    Returns "no_vision" / "unknown" / "vision_ok". Non-OpenAI-compatible
    providers (Gemini: native video upload) are always vision-capable and
    never probed. The probe's cost stays out of the job total structurally:
    OpenAICompatibleProvider.generate_content returns cost_analysis=None.
    """
    if not isinstance(provider, OpenAICompatibleProvider):
        return "vision_ok"
    key = (provider.get_provider_name(), provider.model_name,
           provider.base_url, provider.api_key)
    if key in _VISION_PROBE_CACHE:
        return _VISION_PROBE_CACHE[key]
    try:
        provider.generate_content(
            _PROBE_PROMPT,
            schema=None,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROBE_PROMPT},
                    {"type": "image_url", "image_url": {"url": _PROBE_IMAGE}},
                ],
            }],
            max_tokens=16,
            timeout=15,
        )
        verdict = "vision_ok"
    except AIProviderError as exc:
        verdict = _classify_probe_failure(exc.message)
    except Exception as exc:  # defensive: a probe bug must never crash a gate
        verdict = _classify_probe_failure(str(exc))
    _VISION_PROBE_CACHE[key] = verdict
    return verdict
```

#### 2. tests/test_ai_provider.py
**File**: tests/test_ai_provider.py
**Changes**: MODIFY — probe classification (no_vision / unknown / vision_ok), cache hit (one call per identity), transient-first ordering.

```python
# --- Phase 2 additions (append below the Phase 1 test classes) --------------
# Also add to the imports at the top of the file (Phase 1's locked file has
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
```

### Success Criteria:

#### Automated Verification:
- [x] `python -m pytest tests/test_ai_provider.py -q` — Phase 1 tests still green + all new probe tests pass (classification tri-state incl. transient-first, cache-per-identity, Gemini exemption, probe-PNG validity pin)
- [x] `python -m pytest tests/test_no_double_route.py tests/test_log_view.py tests/test_llm_client.py -q` — boundary pins unchanged (Phase 2 touches none of these files)
- [x] Full-suite acceptance: `python -m pytest -q` — no NEW failures vs the 889-passed / ~10 pre-existing env-failure baseline (this run: 926 passed = 916 after Phase 1 + 10 new probe tests, same 10 pre-existing env failures verified to fail identically without this phase's diff)

#### Manual Verification:
- Covered by the Phase 3/4 live-run gates (silent video + text-only model → one probe call, zero screenshots, clear skip message). No standalone manual step for Phase 2.

## Phase 3: Capability gates + visible warning

### Overview

Two gate sites in `main.py` share the cached verdict: deep before its extraction, vision before its loop. One child-print warning per skipped stage; one unanchored `log_view.py` rule keeps it visible on cloud. Depends on Phase 2.

### Changes Required:

#### 3. main.py
**File**: main.py
**Changes**: MODIFY — deep gate inside the `ENABLE_DEEP_ANALYSIS` block before frame extraction; vision gate condition at the vision block; warning prints + `_dbg` detail lines.

```python
# (a) Deep gate — main.py, inside the deep try: block, inserted right AFTER
#     `_deep_nframes = 16 if _is_gemini else 12` (after `_used_native_video = False`,
#     BEFORE `if _is_gemini:`), 20-space indent:
                    # FR2 gate (D3/D8): probe the deep provider once before
                    # ANY deep frame extraction. Gemini (native video) is
                    # exempt — always vision-capable. The verdict is cached
                    # per identity in ai_provider, so deep and vision share
                    # one probe call when identities match.
                    _deep_probe_ok = True
                    if not _is_gemini:
                        _deep_probe_verdict = ai_provider.probe_vision_support(
                            ai_provider.create_ai_provider(
                                _deep_cfg["provider"], _deep_cfg["model"],
                                api_key=_deep_cfg["api_key"],
                                base_url=_deep_cfg["base_url"]))
                        if _deep_probe_verdict == "no_vision":
                            print("👁️ Deep scan skipped: the selected model cannot see images (text-only).")
                            _dbg(f"   deep skip: probe no_vision (provider={_deep_cfg['provider']} model={_deep_cfg['model']})")
                            _deep_probe_ok = False
# (b) The extraction line immediately before `_deep_frames = extract_frames_from_window(...)`:
                    if not _used_native_video and _deep_probe_ok:
#     (was: `if not _used_native_video:`). When the gate trips: no extraction,
#     `_deep_frames` stays None, `_used_native_video` stays False → the
#     `if _used_native_video or (_deep_frames and len(_deep_frames) >= 8):`
#     branch is False → deep call skipped cleanly, flow falls through to the
#     heuristic candidate path. No misleading error print (see (c)).

# (c) The deep-skip fallback print at the else: of that branch (currently prints
#     "   ℹ️ Deep analysis skipped: not enough frames could be extracted" —
#     factually wrong when the gate tripped, no extraction was attempted):
                    elif _deep_probe_ok:
                        print("   ℹ️ Deep analysis skipped: not enough frames could be extracted")
#     (was `else:` — semantically identical when the gate did not trip.)

# (d) Vision gate — the line pair at main.py:2512-2513 (8-space indent):
        # Vision analysis (Phase 6) — toggleable, runs even without GameProfile; GameProfile just biases scores
        _vision_probe_ok = True
        if HAS_SEMANTIC_ANALYZER and ENABLE_VISION_ANALYSIS and scored:
            _vision_probe_ok = ai_provider.probe_vision_support(ai_provider_instance) != "no_vision"
            if not _vision_probe_ok:
                print("👁️ Vision analysis skipped: the selected model cannot see images (text-only).")
                _dbg(f"   vision skip: probe no_vision (provider={_provider} model={model_name})")
        if HAS_SEMANTIC_ANALYZER and ENABLE_VISION_ANALYSIS and _vision_probe_ok:
#     The entire existing vision body below the gate line stays UNCHANGED —
#     same indentation, no re-indent. The `scored` guard keeps the probe off
#     the no-windows path (where `ai_provider_instance` from the score-batch
#     loop at main.py:2502 would be unbound).
```

#### 3. log_view.py
**File**: log_view.py
**Changes**: MODIFY — one unanchored rule matching the skip warning.

```python
    # FR2 capability skip warnings: child prints carry an HH:MM:SS prefix in
    # the job log, so this rule must stay UNANCHORED (a ^ anchor would never
    # match). Both skip lines (deep + vision) match; the capturing template
    # drops the timestamp so the curated view matches the other friendly lines.
    # Appended as the LAST entry of _RULES (after the `Clip (\d+) ready` rule).
    (re.compile(r'(?:\d{2}:\d{2}:\d{2} )?(👁️ .*skipped: .*)'), '{0}'),
```

#### 3. tests/test_log_view.py
**File**: tests/test_log_view.py
**Changes**: MODIFY — the warning line survives `friendly_logs`.

```python
def test_capability_skip_warnings_survive_the_cloud_whitelist():
    # Child prints arrive timestamp-prefixed; the unanchored rule matches and
    # the capturing template strips the timestamp.
    assert friendly_log_line(
        "15:42:10 👁️ Vision analysis skipped: the selected model cannot see images (text-only)."
    ) == "👁️ Vision analysis skipped: the selected model cannot see images (text-only)."
    assert friendly_log_line(
        "15:42:11 👁️ Deep scan skipped: the selected model cannot see images (text-only)."
    ) == "👁️ Deep scan skipped: the selected model cannot see images (text-only)."
```

#### 3. tests/test_no_double_route.py
**File**: tests/test_no_double_route.py
**Changes**: MODIFY — gate tests: no_vision verdict skips deep and vision with zero frame extractions (extraction monkeypatched to count).

```python
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
```

### Success Criteria:

#### Automated Verification:
- [x] `python -m pytest tests/test_no_double_route.py tests/test_log_view.py -q` — all boundary pins + new gate/log tests pass (no_vision → zero extractions + both skip lines; vision_ok → extraction proceeds; timestamp-prefixed skip lines survive the cloud whitelist with the timestamp stripped)
- [x] `python -m pytest tests/test_ai_provider.py -q` — Phase 1+2 tests still green
- [x] Full-suite acceptance: `python -m pytest -q` — no NEW failures vs the 889-passed / ~10 pre-existing env-failure baseline

#### Manual Verification:
- Self-host dashboard (Ollama Cloud + one local server): run a job against a text-only OpenAI-compatible model with vision enabled → the job log shows "👁️ Vision analysis skipped: the selected model cannot see images (text-only)." exactly ONCE per stage, with zero screenshot extractions (deep too when deep enabled). Probe count: exactly ONE probe call per provider identity per job — verify via the DEBUG_LOGS detail lines or the server's request log (the probe itself carries no user-facing print by design, D8).
- Cloud dashboard: the same line visible under the curated whitelist (covered by the log_view rule test).
- A vision-capable model job proceeds unchanged (extraction happens).

## Phase 4: Silent-video path rewrite

### Overview

`get_visual_clips` routes through the provider abstraction: provider resolution via `_get_*` getters; Gemini branch keeps the native upload through `_prov.client` and ATTACHES it; openai branch probes, then sends 12 frames with `VisualResponse`. Key wall and raw `genai.Client` deleted. Depends on Phases 1-3.

### Changes Required:

#### 4. main.py
**File**: main.py
**Changes**: MODIFY — full rewrite of `get_visual_clips` (`main.py:2888-2986`); caller at `main.py:3548` error label: ONE-LINE EDIT (developer checkpoint 2026-09-08) — `_err_label` becomes `"OpenAI-compatible endpoint" if _err_provider == "openai" else "Gemini"` so the FRD acceptance string is literally true. Plus: drop the now-dead `from google import genai` module import (main.py:21 — raw Client call deleted; KEEP line 22 `genai_types`, still used at main.py:1567).

```python
# (a) NEW helper, placed immediately BEFORE get_visual_clips:
def _silent_visual_prompt(video_duration, language):
    """Shared silent-video prompt. No scoring windows to derive a count from,
    so the visual fallback stays aligned with the main product range: 3-15 clips."""
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
    return v_min_clips, v_max_clips, prompt

# (b) FULL REWRITE of get_visual_clips (replaces main.py:2888-2986):
def get_visual_clips(video_path, video_duration, language="en"):
    """Clip a SILENT video by vision: the configured provider watches the
    footage and picks the most engaging visual moments (no transcript).
    Gemini watches the native video upload; an OpenAI-compatible endpoint
    inspects 12 sampled frames. Returns {"shorts"} on success (the silent
    path contributes no cost data — no cost_analysis key), or None."""
    provider_type = _get_ai_provider()
    print(f"🎥  Silent video — analyzing with {provider_type.title()} vision (no transcript)...")

    prov = None
    file_upload = None
    try:
        if provider_type == "gemini":
            api_key = _get_gemini_api_key()
            if not api_key:
                print("❌ GEMINI_API_KEY not configured. Silent-video analysis on Gemini "
                      "needs a key; or select an OpenAI-compatible vision model in the "
                      "provider card.")
                return None
            prov = ai_provider.create_ai_provider("gemini", _get_gemini_model(), api_key=api_key)
            print(f"🎥  Model: {prov.model_name} | uploading {os.path.basename(video_path)}…")
            # Native upload through the provider-held client (no raw genai.Client).
            file_upload = prov.client.files.upload(file=video_path)
            deadline = time.time() + 180
            while True:
                info = prov.client.files.get(name=file_upload.name)
                state = str(getattr(getattr(info, "state", info), "name", "")).upper()
                if state == "ACTIVE":
                    break
                if state == "FAILED":
                    print(f"❌ {provider_type.title()} could not process the video.")
                    return None
                if time.time() > deadline:
                    print(f"❌ {provider_type.title()} video processing timed out.")
                    return None
                time.sleep(2)
            _v_min, _v_max, prompt = _silent_visual_prompt(video_duration, language)
            # D4 defect fix: the upload is ATTACHED to the call — Gemini now
            # actually watches the footage instead of inventing timestamps
            # from prompt text alone.
            _resp = prov.client.models.generate_content(
                model=prov.model_name,
                contents=[file_upload, prompt],
                config=prov.genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=AI_TEMPERATURE,
                ))
            # Developer checkpoint 2026-09-08: blocked-content now surfaces
            # properly (the old except GeminiBlockedError handler was dead —
            # the silent path never called raise_if_blocked).
            gemini_worker.raise_if_blocked(_resp)
            parsed = gemini_worker._parse_json_response_text(
                gemini_worker._get_response_text(_resp)) or {}
        else:
            if not HAS_SEMANTIC_ANALYZER:
                # Same degradation as the deep gate: no frame extractor.
                _dbg("   silent openai pass unavailable: semantic analyzer not installed")
                return None
            # Deep-scan param shape (D2): stored params, applied via the
            # Phase 1 precedence chain.
            prov = ai_provider.create_ai_provider(
                "openai", _get_openai_model(),
                api_key=_get_openai_api_key(),
                base_url=_get_openai_base_url(),
                temperature=0.2, max_tokens=3000, timeout=90)
            # FR2 pre-flight (D3/D5): one tiny-image probe BEFORE any
            # screenshot is taken. Cached per identity (Phase 2).
            if ai_provider.probe_vision_support(prov) == "no_vision":
                print("👁️ Vision analysis skipped: the selected model cannot see images (text-only).")
                return None
            _frames = extract_frames_from_window(video_path, 0, float(video_duration), num_frames=12)
            if not _frames:
                print("⚠️ Could not extract frames for the silent-video pass.")
                return None
            _v_min, _v_max, prompt = _silent_visual_prompt(video_duration, language)
            _content = [{"type": "text", "text": prompt}]
            for _fr in _frames:
                _content.append({"type": "image_url", "image_url": {"url": _fr["base64_image"]}})
            _dbg(f"   silent pass: {len(_frames)} frames over the full duration (schema=VisualResponse)…")
            result = prov.generate_content(
                prompt, schema=gemini_worker.VisualResponse,
                messages=[{"role": "user", "content": _content}])
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

        # The silent path contributes no cost data today (unchanged). The
        # OpenAI-compatible provider returns cost_analysis=None structurally,
        # so the probe's tiny call never enters a job total either.
        return {"shorts": clean}
    except gemini_worker.GeminiBlockedError as e:
        print(f"🚫 {e}")
        raise
    except Exception as e:
        print(f"❌ {provider_type.title()} vision error: {e}")
        return None
    finally:
        if file_upload is not None:
            try:
                prov.client.files.delete(name=file_upload.name)
            except Exception:
                pass
# (The old cost print block is dropped — cost was hardcoded None and the
# return shape when cost is None is identical: {"shorts": clean}.)

# (c) Caller error label — main.py:3554-3555 (developer checkpoint 2026-09-08):
            _err_label = "OpenAI-compatible endpoint" if _err_provider == "openai" else "Gemini"
#     (was: _err_label = "OpenAI" if ... else "Gemini". Same line serves both
#     paths; transcript-path openai failures now carry the clearer label too.)
```

#### 4. tests/test_no_double_route.py
**File**: tests/test_no_double_route.py
**Changes**: MODIFY — silent-path tests: openai silent job through the fake provider with `_no_genai` + `_canary_llm_client` installed; gemini no-key graceful `None`; gemini native branch attaches the upload (stubbed client).

```python
# --- Phase 4 test additions -------------------------------------------------
# (a) EXTEND _install_pipeline_provider._Provider.generate_content — add this
# branch BEFORE the final `return {"response": {}, "cost_analysis": None}`:
            if name == "VisualResponse":
                return {"response": {"shorts": [
                    {"start": 10.0, "end": 55.0, "predicted_score": 90,
                     "video_description_for_tiktok": "d",
                     "video_description_for_instagram": "i",
                     "video_title_for_youtube_short": "The moment",
                     "viral_hook_text": "h"}]},
                    "cost_analysis": None}

# (b) NEW class appended (SimpleNamespace is imported at the top of the file):
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

# (c) EXTEND TestAppLayerEnv (existing class with the client fixture +
# _clean_slate autouse) — precedent lesson (25222f5/56707b7): job gates
# break, not LLM code. Pin that a KEYLESS openai silent-video job queues
# with a complete env (no GEMINI_API_KEY required at launch):
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
```

### Success Criteria:

#### Automated Verification:
- [x] `python -m pytest tests/test_no_double_route.py -q` — all existing pins (two-pass, short-video, capability gates) + new silent-path tests pass; `_no_genai` proves NO genai.Client is constructed on an openai silent job; `_canary_llm_client` proves llm_client is never called (this run: 13 passed)
- [x] `python -m pytest tests/test_log_view.py tests/test_ai_provider.py tests/test_llm_client.py -q` — neighboring suites green (this run: 103 passed)
- [x] Full-suite acceptance: `python -m pytest -q` — no NEW failures vs the 889-passed / ~10 pre-existing env-failure baseline (this run: 934 passed = 929 after Phase 3 + 5 new silent-path/env tests, same 10 pre-existing env failures verified identical in name and count: game_profiles ×3, generation_controls ×3, semantic_analyzer ×2, subtitles ×2)

#### Manual Verification:
- (FRD live-run gates, run outside this plan) Ollama Cloud + one local server: silent video + vision model → job log shows "🎥 Silent video — analyzing with Openai vision (no transcript)…", one probe call, 12 frames over the full duration, clips render, NO GEMINI_API_KEY anywhere in the flow. WATCH ITEM (Phase 1 note (c)): the first live run must also confirm the local server accepts the now-present `timeout` request field — if a server rejects it, that is a Phase 1 wire-shape regression to raise before proceeding.
- Gemini job: unchanged behavior (suite + pins), except the silent path now attaches the uploaded file (D4 — accepted ~300 video tokens/s billing) and blocked videos show the 🚫 message.

## Phase 5: Cheap events for short videos

### Overview

The seeding block moves above the ≤120 s shortcut (DELETE `main.py:2065-2069` + `:2072-2091`, KEEPING the seeding `try:` opener at `:2071` — the deep-scan body continues inside it to ~:2422; corrected off-by-one per slice-verifier), wrapped in its own try/except; the short-path detail payload splits hints per type. Deep stays off short videos. Depends on Phase 4 (sequential edits to the same function; the test-file anchor below is valid only once locked Phase 4 has landed).

### Changes Required:

#### 5. main.py
**File**: main.py
**Changes**: MODIFY — relocate the seeding block (DELETE `main.py:2065-2069` comments+init and `:2072-2091` body; KEEP `:2070` `_deep_candidates = []` and the `try:` opener at `:2071`; the remaining try wraps only the deep scan/merge, handler `:2423-2424` untouched) to immediately BEFORE the short-branch gate `if _short_dur is not None and _short_dur <= 120 ...` (~:1957, after the `_short_dur` parse); replace the dead `'cheap_events' in locals()` guard and split the short-path payload fields.

```python
# (a) RELOCATED cheap-events seeding block — DELETE live lines :2065-2069
# (comment block + `cheap_events = []`) and :2072-2091 (the try body through
# the log block); KEEP :2070 `_deep_candidates = []` and the `try:` opener at
# :2071 (deep-scan body continues inside it; handler at :2423-2424 untouched).
# INSERT below at function-body level (4-space indent), immediately BEFORE
# `if _short_dur is not None and _short_dur <= 120 and _short_dur > 0:` (~:1957):
    # --- Cheap multimodal candidate seeding (relocated above the ≤120 s
    # shortcut, D6) so SHORT videos get cheap signals in the detail payload.
    # Merge transcript windows with cheap event windows so a viral moment
    # with meaningless transcript (silence -> jumpscare -> scream) still
    # becomes a candidate. Each enhancement is toggleable.
    cheap_events = []
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
    except Exception as _ce:
        print(f"⚠️ Cheap event seeding skipped: {_ce}")
        _dbg(f"   cheap seeding error: {_ce}")
# (Own try/except — the block sits outside the big seeding try now. The big
# try's handler at :2423-2424 keeps its existing message; it now guards only
# deep-scan/candidate-merge failures. DECLARED DELTA: a cheap-seeding failure
# no longer aborts the deep scan — the deep scan now runs on cheap failure,
# and if the cheap import itself failed, the merge call at :2329 raises a
# caught NameError printing a second "skipped" line. Accepted: deep/cheap
# decoupling is an improvement; renaming the :2424 handler message is a
# Phase 6/follow-up candidate.)
# (b) SHORT-PATH payload split — replace main.py:1989-1995 (try: opener 1989,
# _wc_ws 1990, _wc_ev dead guard 1991, except 1992-1994, payload 1995) inside
# the short branch.
# OLD (dead guard — always False here):
#     _wc_ev = ", ".join(...) if 'cheap_events' in locals() and cheap_events else "none"
# NEW:
            try:
                _wc_ws = "; ".join(f"{ww['w']}@{ww['s']:.2f}" for ww in words[:28]) if words else "none"
                # D6 split: audio_events carries every in-window cheap event
                # (long-path shape, main.py:2717-2719); scene_boundaries carries
                # ONLY scene_change timestamps (long-path shape, :2728-2732) — the
                # detail prompt's SCENE-CUT ALIGNMENT rule reads
                # scene_boundaries and would misfire on audio spikes.
                _ev_in = [ee for ee in (cheap_events or [])
                          if ns - 0.5 <= float(ee.get("timestamp", 0)) <= ne + 0.5][:10]
                _wc_audio = ", ".join(f"{ee.get('timestamp',0):.1f}s:{ee.get('type','')}"
                                      for ee in _ev_in) if _ev_in else "none"
                _scene_in = sorted({round(float(ee.get("timestamp", 0)), 1)
                                    for ee in (cheap_events or [])
                                    if ee.get("type") == "scene_change"
                                    and ns - 0.5 <= float(ee.get("timestamp", 0)) <= ne + 0.5})
                _wc_scene = ", ".join(f"{t:.1f}s" for t in _scene_in) if _scene_in else "none"
            except Exception:
                _wc_ws = "none"
                _wc_audio = "none"
                _wc_scene = "none"
            payload = [{"id": "whole_clip", "start": float(ns), "end": float(ne), "text": whole_text, "surrounding_transcript": "none (whole video)", "word_timestamps": _wc_ws, "audio_events": _wc_audio, "scene_boundaries": _wc_scene}]
# (Everything else in the short branch unchanged, including the detail call
# with stored params.)
```

#### 5. tests/test_no_double_route.py
**File**: tests/test_no_double_route.py
**Changes**: MODIFY — short-video test with a real temp `video_path` and monkeypatched `cheap_events.extract_cheap_events`; prompt capture asserts the split fields; existing pins stay green unchanged.

```python
# APPEND INSIDE TestVideoSide, immediately AFTER test_short_video_whole_clip_path_uses_the_same_provider (same class, 4-space method indent):
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
```

### Success Criteria:
#### Automated Verification:
- [x] `python -m pytest tests/test_no_double_route.py -q` — existing pins UNCHANGED and green (two-pass; short-video-without-video_path still asserts the same behavior — hints stay "none"/"none"; capability gates; silent path) + the new split-field test passes (this run: 14 passed = 13 after Phase 4 + 1 new)
- [x] `python -m pytest tests/test_log_view.py tests/test_ai_provider.py tests/test_llm_client.py -q` — neighboring suites green (this run: 103 passed)
- [x] Full-suite acceptance: `python -m pytest -q` — no NEW failures vs the 889-passed / ~10 pre-existing env-failure baseline (this run: 935 passed = 934 after Phase 4 + 1 new, same 10 pre-existing env failures identical in name and count: game_profiles ×3, generation_controls ×3, semantic_analyzer ×2, subtitles ×2)

#### Manual Verification:
- (FRD live-run gate) Short video ≤120 s with scene/audio toggles on → cheap events extracted BEFORE the whole-clip shortcut; the detail payload carries `audio_events: "12.0s:scream, ..."` and `scene_boundaries: "5.0s, ..."` instead of "none"/"none". Deep stays off for short videos (dead `> 60` conjunct untouched).


## Phase 6: Docs sync

### Overview

Five stale doc spots stop claiming silent-video is Gemini-only (README ×4 incl. the mermaid flow node — the mermaid node was surfaced by the slice-verifier; the "What still needs Gemini" bullet at README:406-410 is a fourth spot found during slice prep, in scope per D9's same-change lesson), plus the whole-plan verification block. Depends on Phases 1-5.

### Changes Required:

#### 6. README.md
**File**: README.md
**Changes**: MODIFY — line 571 (GEMINI_API_KEY row), line 614 (capability matrix row), lines 406-410 ("What still needs Gemini" bullet; re-anchored per slice-verifier — the bullet starts at 406, NOT 404), and line 148 (mermaid flow node) — all reflect that an OpenAI-compatible endpoint handles silent videos with a vision model via the pipeline provider.

```text
# (a) README.md:571 — GEMINI_API_KEY table row becomes:
| `GEMINI_API_KEY` | Google Gemini — not needed for the clip pipeline when `AI_PROVIDER=openai` + `OPENAI_BASE_URL` are set; the `LLM_*` triple covers only the text stages. Still needed for thumbnail image generation, editor effects and grounded web research |
# (b) README.md:614 — capability matrix row becomes:
| Silent-video clip detection (vision) | yes — via the pipeline provider, with a vision model | the endpoint inspects 12 sampled frames across the video; a one-shot probe skips text-only models with a clear message. Gemini still watches the native upload |

# (c) README.md:406-410 — the "What still needs Gemini" bullet becomes:
- **What still needs Gemini.** Thumbnail image generation, editor
  effects, and SaaS grounded web research. Silent-video detection runs on
  your OpenAI-compatible endpoint with a vision model (it inspects 12
  sampled frames; text-only models are skipped with a clear message).
  Gemini keeps watching the native video upload when it is selected. The
  layout picker and the thumbnail text stages reroute to the `LLM_*`
  endpoint. Add a Gemini key alongside and you get both.

# (d) README.md:148 — mermaid flow node becomes:
    C -- yes --> V[Provider watches the video\nvisual clip picking]
```

#### 6. CLAUDE.md
**File**: CLAUDE.md
**Changes**: MODIFY — line 149: remove silent-video from the "Stays Gemini" list.

```text
# CLAUDE.md:149-150 — the "Stays Gemini" bullet becomes (silent-video removed;
# two-line wrap preserved, line 150 byte-identical):
- Stays Gemini: image gen, editor effects, SaaS grounded
  research, cloud/managed.
```

### Success Criteria:

#### Automated Verification:
- [x] `python -m pytest tests/test_no_double_route.py tests/test_log_view.py tests/test_ai_provider.py tests/test_llm_client.py -q` — all suites green (docs-only phase; proves no incidental code regression)
- [x] Full-suite acceptance: `python -m pytest -q` — no NEW failures vs the 889-passed / ~10 pre-existing env-failure baseline (this run: 935 passed, same 10 pre-existing env failures identical in name and count: game_profiles ×3, generation_controls ×3, semantic_analyzer ×2, subtitles ×2)
- [x] Repo grep proves no stale claim remains: `grep -rn "silent" README.md CLAUDE.md` returns NO line pairing silent-video with Gemini-only/cannot-watch, AND no remaining "Gemini watches the full video" claim (`grep -n "Gemini watches" README.md` hits nothing outside the D4-accurate native-upload wording)

#### Manual Verification:
- Read README.md:571, :614, :406-410, :148 and CLAUDE.md:149 in the rendered docs — each now matches the locked Phase 1-5 behavior (12 frames / probe / skip message / native Gemini upload intact).

## Ordering Constraints

- Phase 1 → 2 → 3 → 4 sequential: the probe stores per-call kwargs (1), gates call the probe (2→3), the silent path reuses both (4).
- Phase 5 edits `get_viral_clips` after Phase 3 edited it (deep gate) — sequential, same-function discipline: each phase's fence is its own change set.
- Phase 6 last: docs describe behavior that only exists after 1-5.
- No phase touches `app.py`, `cloud/semantic_analyzer.py`, or `cheap_events.py`.
- Phases are NOT parallelizable (single-file overlap in `main.py` across 3/4/5).

## Verification Notes

- Suite baseline on this machine: 889 passed / 10 pre-existing env failures (cp1252 reads without `encoding="utf-8"`, AsyncMock session issues). Acceptance = no NEW failures vs the main baseline; run new/changed tests explicitly.
- `tests/test_no_double_route.py` pins: after Phase 4, a silent openai job constructs NO `genai.Client` (`_no_genai` patches the class — it also catches `GeminiProvider.__init__`) and calls NO `llm_client` function (`_canary_llm_client`).
- Live-run gates (acceptance, from the FRD): Ollama Cloud + one local server. (a) Silent video + vision model → clips. (b) Silent video + text-only model → one probe, zero screenshots, clear message. (c) Short video ≤120 s with toggles → hints in the detail payload. (d) Gemini job → unchanged behavior (suite + pins).
- Trap: do NOT move the whole seeding `try` (`main.py:2071-2424`) — deep would silently enable for 61-120 s videos.
- Trap: the probe's cost must not enter the job cost total (dead deep-cost append at `main.py:2291` is the cautionary example). OpenAI-compatible `generate_content` returns `cost_analysis: None`, which satisfies this structurally — keep it that way.
- Trap: cloud log visibility — the `log_view.py` rule must be UNANCHORED (child lines carry `HH:MM:SS ` prefixes; the verbatim rule at `:18-19` is `^`-anchored and would never match).
- Windows box: write test fixture files with `encoding="utf-8"`; no `docker compose` on this machine — live acceptance runs happen outside this plan.
- Graph note: `dashboard/src/App.jsx` has parse_partial ranges (1800, 2047, 2461); no phase touches it.
- Phase 1 slice-verifier resolutions (2026-09-08): (a) subtitle-polish escalation at `main.py:3122` passes 9000 tokens — it now clamps to the advisor bound 8192 at the wire; the escalation was dead before (9000 dropped, wire got 4096), so this is strictly better. (b) Per-call kwargs are INTENTIONALLY not sanitized at request time — D1 sanitizes at construction only; Phase 2's probe passes fixed in-bounds per-call values (max_tokens=16, timeout=15). (c) After Phase 1, every OpenAI-compatible request's wire shape changes (temperature 0.7→0.2-0.4, max_tokens 4096→700-3000, a `timeout` key present where none ever was) — the FIRST live acceptance run must watch for a local server rejecting the `timeout` field. (d) An explicit-None per-call kwarg now falls through to stored/default (previously `kwargs.get("temperature", 0.7)` sent `null`); no production caller does this, but it is a wire-shape change. (e) The D7 never-applies pin is source-text based (`inspect.getsource`); Phase 4 must not add kwargs/stored-param reads to `GeminiProvider.generate_content` (it edits `main.py` only — safe).
- Phase 1 acceptance: `tests/test_ai_provider.py` green; `tests/test_no_double_route.py`, `tests/test_log_view.py`, `tests/test_llm_client.py` green; full suite no NEW failures vs 889-passed baseline.
- Phase 3 slice-verifier resolutions (2026-09-08): (a) the skip-warning log rule uses a CAPTURING template (`'{0}'`) so the cloud view strips the `HH:MM:SS ` prefix — matching the other friendly lines; the timestamped raw line still matches because `friendly_log_line` uses `.search()`. (b) The deep-skip fallback print ("not enough frames could be extracted") is guarded by `elif _deep_probe_ok:` so a gate trip doesn't print a factually wrong message. (c) Gate tests pass NO file at `video_path` — gates need it truthy only, and a nonexistent path makes the cheap-events guard skip cleanly. (d) PRE-EXISTING BUG SURFACED (out of scope, record only): every `^`-anchored verbatim rule in `log_view.py:_RULES` (`Job started`, `Process finished/failed`, `Execution error`, `No metadata`, `❌`) NEVER matches production lines because every job-log line carries an `HH:MM:SS ` prefix (`app.py:1890`) — those lines are invisible on cloud today. Candidate for Phase 6 docs note or a follow-up issue.
- Phase 3 watch item: the vision gate calls the real `probe_vision_support` whenever `scored` is non-empty — safe today (the only tests driving `get_viral_clips` use the recording fake provider, which short-circuits to `vision_ok` with no network), but any FUTURE test constructing a real `OpenAICompatibleProvider` would fire a real 15 s-timeout network probe.
- Phase 4 slice-verifier resolutions + developer checkpoints (2026-09-08): (a) caller error label — `_err_label` becomes "OpenAI-compatible endpoint" for openai jobs (developer call; FRD acceptance string now literal; affects transcript-path failures too). (b) `gemini_worker.raise_if_blocked(_resp)` added before the silent-path parse (developer call; the `except GeminiBlockedError` handler was pre-existing dead code — blocked videos now show the 🚫 message). (c) Dead `from google import genai` (main.py:21) dropped in the same change; `genai_types` (line 22) stays. (d) Provider construction moved INSIDE the try in the rewrite — a construction failure now degrades to graceful `None` instead of propagating (delta from the old code, accepted). (e) The gemini no-key test carries `_canary_llm_client` too (wall gone — pinned on both paths).
- Phase 5 slice-verifier resolutions (2026-09-08): (a) the relocation spec was off-by-one against live source — the seeding `try:` opener at main.py:2071 must STAY (the deep-scan body continues inside it to ~:2422); only `:2065-2069` (comments+init) and `:2072-2091` (cheap body) are deleted. (b) The long-path `audio_events` shape carries ALL in-window events including scene_change — the short-path split mirrors that verbatim (the detail prompt's SCENE-CUT ALIGNMENT rule reads only `scene_boundaries`). (c) In the short path, VOD metadata is generated AFTER the detail call through the same factory, so a prompt-capturing test must select the detail prompt by its `"id": "whole_clip"` payload marker, not by position. (d) DECLARED DELTA: a cheap-seeding failure no longer aborts the deep scan (deep/cheap decoupled — improvement); if the cheap import itself failed, the merge call at main.py:2329 raises a caught NameError printing a second "skipped" line. (e) Pre-existing, out of scope: the big-try handler at :2424 still says "Cheap event seeding skipped" while now guarding only deep/merge failures — Phase 6/follow-up rename candidate.
- Phase 6 slice-verifier resolutions (2026-09-08): (a) the README "What still needs Gemini" bullet anchors at :406-410 (not 404-409 — the edit as first drafted would corrupt the neighboring num_ctx bullet). (b) A FIFTH stale spot was adopted into the slice: the README:148 mermaid flow node ("Gemini watches the full video" → "Provider watches the video"); the slice-verifier noted the same-line grep criterion would have missed it — the success-criterion grep also checks "Gemini watches". (c) README:614 cell tightened to "yes — via the pipeline provider, with a vision model" to match the table's column semantics (it is NOT an LLM_* reroute). (d) FOLLOW-UP CANDIDATES (recorded, out of every phase's scope): main.py:2424 handler message rename (now guards only deep/merge failures); the ^-anchored log_view.py verbatim rules that never match timestamped production lines (pre-existing cloud bug, Verification Notes Phase 3 (d)); NOTE — the "stale code comments main.py:2889/2940" candidate recorded earlier is MOOT: both comments sit inside `get_visual_clips`, which Phase 4 fully rewrites with provider-accurate wording. Repo-wide sweep confirmed no other silent-video-is-Gemini-only claims (dashboard UI has zero such copy; docs/ holds only DSA/DMCA notices).
- Step 8 coverage review (2026-09-08): 0 blockers, 1 concern, 5 suggestions. The concern was ADOPTED: `TestAppLayerEnv` gains `test_keyless_openai_job_env_allows_silent_videos` (Phase 4 fence block (c)) — pins that a keyless openai silent-video job queues with a complete env and no GEMINI_API_KEY, closing the "job gates break, not LLM code" precedent gap (25222f5/56707b7). Adopted amendments: the Phase 1 `timeout` wire-shape watch item is now operationalized in Phase 4's manual gate; Phase 3's manual gate names the endpoints (Ollama Cloud + one local server) and the one-probe-per-identity count check; Phase 5's Success Criteria regained its missing `#### Automated Verification:` header; the moot main.py:2889/2940 comment candidate is marked moot (both sit inside `get_visual_clips`, fully rewritten by Phase 4).
- Step 8 code review (2026-09-08): 1 blocker, 2 concerns, 9 suggestions, all verified against HEAD with simulated execution. ADOPTED: (blocker) `test_probe_image_is_a_valid_png` asserted `header == "data:image/png"` but `split(",", 1)[0]` returns `"data:image/png;base64"` — assertion fixed to keep the `;base64` suffix (the PNG bytes themselves were verified valid). ADOPTED (concerns): the Phase 5 test method now names its class placement (inside `TestVideoSide`, after the existing short-video pin); the Phase 5 replace-range cite corrected to main.py:1989-1995. ADOPTED (suggestions): the rewritten docstring no longer claims a `cost_analysis` key it never returns (implementers reconciling the trailing comment could otherwise "restore" it); the caller-label cite corrected to main.py:3554-3555; Performance Considerations notes the deep gate's throwaway probe client. RECORDED (prose cite drifts, content verified correct): AI_TEMPERATURE/AI_MAX_TOKENS/AI_TIMEOUT at main.py:116-118 (plan says 115-117); the 9000 escalation at :3123 (plan says :3122); D5's json.loads cite is :344-353 (plan says :330-341); D3's cost-None return at :371-374 (plan says :341-344); Phase 2's two Changes subsections both numbered `#### 2.` (cosmetic). DECLINED (recorded): a visible print when HAS_SEMANTIC_ANALYZER is False on the silent openai path — deep-gate parity keeps `_dbg`-only degradation as the codebase convention.

## Performance Considerations

- The probe adds ONE network call per provider identity per job (~1-2 s; `max_tokens=16`, `timeout=15`). At most two identities per job (job + deep override). Note: the deep gate constructs a throwaway provider instance purely to probe (client construction is local/offline, no network); when the deep identity matches the job identity the second construction is unavoidable but the probe call itself is a cache hit.
- A `no_vision` verdict SAVES work: deep skips its 12-frame extraction and call; vision skips N per-window (6-frame extraction + call each).
- Cheap events on ≤120 s videos: the same local cv2/librosa cost the long path already pays per minute; short videos are cheap.
- The Gemini silent path now bills video tokens (~300 tokens/s of video) — the approved cost of D4. No duration gate exists today; none added.

## Migration Notes

Not applicable — no persisted schema, no new env keys, no config migration. Rollback = revert; every phase is independently revertible in order.

## Pattern References

- `llm_client.py:443-446` — conditional param forwarding (`if x is not None`).
- `main.py:2258-2287` — deep frames call shape: content parts + schema + response_format retry.
- `main.py:1921-1933` — provider resolution + print block to mirror.
- `main.py:2197`, `:2218-2219` — Gemini native upload/call through the provider-held client (no raw `genai.Client`).
- `cloud/semantic_analyzer.py:199-215` — image_url message contract (`data:` URLs).
- `tests/test_no_double_route.py:107-125` — `fake_create` recording fake.
- `tests/test_llm_client.py:588-598` — `seen`-dict kwargs assertions.
- `log_view.py:15-25` — rule list (unanchored pattern needed).

## Developer Context

**Directional confirms (batched, Step 4):**
- Q (param order): Which sampling value wins? → **Per-call > stored > frozen defaults** (advisor verdict: "per-call kwargs must outrank stored constructor values; both outrank a frozen default tuple", hardening: set-but-empty = unset, clamp bounds).
- Q (silent call): Which answer format for silent-video clip picking? → **Strict form + auto fallback** (deep-scan shape; `ai_provider.py:377-385` retry).
- Q (silent probe): Tiny test picture first? → **Yes** (advisor riders: Gemini exempt, cached per model, probe cost out of the job total).
- Q (docs sync): Fix the three stale lines in-change? → **Yes**.
- First draft of these four came back "i dont understand, re explain" ×3 — re-asked in button-press terms anchored to the provider card and job log (learned rule); all four then landed on the recommended options.

**Genuine ambiguity (one-at-a-time, Step 4):**
- Q (`main.py:2943-2948`): The Gemini silent call never attaches its uploaded file — the model invents timestamps from prompt text. Keep byte-identical, or attach? → **Attach the file** (approved defect fix; suite unaffected; video tokens now bill).

**Design summary**: confirmed (Proceed). **Decomposition**: approved as proposed (6 slices).

**Advisor key guidance (Step 4)**: precedence chain stays three layers deep; sanitize at construction (garbage/empty → unset; temperature 0-2, max_tokens 1-8192, timeout 5-600); deep-scan shape for the silent call without modification; probe = pre-flight in `get_visual_clips`' openai branch only, cached per identity, cost-bypassed; docs in the same change.

## Plan History

- Phase 1: Provider param passthrough — LOCKED 2026-09-08T15:42:23+07:00 (developer-approved at 6.3; slice-verifier 0 blockers, 3 concerns resolved and recorded in Verification Notes; fences + Success Criteria filled)
- Phase 2: Vision capability probe — LOCKED 2026-09-08T15:56:06+07:00 (developer-approved at 6.3; slice-verifier 2 blockers FIXED — probe PNG replaced with a sandbox-verified valid 8x8 PNG + validity pin test, time.sleep patched in transient tests — and 1 concern adopted — probe catches Exception defensively → unknown; fences + Success Criteria filled)
- Phase 3: Capability gates + visible warning — LOCKED 2026-09-08T16:09:01+07:00 (developer-approved at 6.3; slice-verifier ran against a live venv: 1 blocker FIXED — skip-warning log rule switched to a capturing template that strips the timestamp prefix — plus 2 concerns adopted (misleading deep-skip print guarded; gate tests use a nonexistent video_path) and 1 pre-existing cloud bug recorded in Verification Notes; fences + Success Criteria filled)
- Phase 4: Silent-video path rewrite — LOCKED 2026-09-08T16:22:32+07:00 (developer-approved at 6.3 with two checkpoint calls: error label → "OpenAI-compatible endpoint", raise_if_blocked added; slice-verifier 0 blockers, 3 suggestions adopted/recorded — see Verification Notes; fences + Success Criteria filled)
- Phase 5: Cheap events for short videos — LOCKED 2026-09-08T16:37:04+07:00 (developer-approved at 6.3; slice-verifier 3 blockers FIXED — delete-range off-by-one corrected (keep the seeding try: opener), audio_events assertion corrected to the long-path shape, detail prompt selected by payload marker; 2 concerns adopted (failure reason stays in the print; deep/cheap decoupling declared as an accepted delta) — see Verification Notes; fences + Success Criteria filled)
- Phase 6: Docs sync — LOCKED 2026-09-08T16:46:27+07:00 (developer-approved at 6.3; slice-verifier 1 blocker FIXED — README bullet re-anchored 404-409 → 406-410 — plus a FIFTH stale spot adopted into the slice: the README:148 mermaid flow node; :614 cell tightened to "via the pipeline provider"; fences + Success Criteria filled)
- FINAL TRIAGE 2026-09-08T18:00:55+07:00: developer approved — status flipped in-review → ready. Step 8 merged results: coverage review 0 blockers / 1 concern (adopted) / 5 suggestions (amendments applied, moot candidate corrected); code review 1 blocker (PNG header assertion) + 2 concerns (test-class placement, range cite) + 9 suggestions — all resolved or recorded (see Verification Notes). Plan is implement-ready at HEAD 51c1861.

## References

- `.rpiv/artifacts/research/2026-09-08_09-13-45_candidate-detection-openai-compat.md` — parent research (verified line map; cite these, not the FRD's)
- `.rpiv/artifacts/discover/2026-09-08_08-39-20_candidate-detection-openai-compat.md` — source FRD
- `.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md` — provider plan: capability matrix (`:2176`), llm_client forwarding pattern (`:507-510`), GeminiBlockedError near-miss (`:2337`)
- `.rpiv/artifacts/plans/2026-09-06_14-41-23_marsic-fork-parity-merge.md` — merge plan: "Stays Gemini" list (`:1823`), README clause (`:1936`)
