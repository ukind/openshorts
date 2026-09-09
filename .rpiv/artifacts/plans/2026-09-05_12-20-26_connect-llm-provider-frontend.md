---
date: 2026-09-05T12:20:26+0700
author: Yogiswara Utama
commit: 35e9d7e
branch: main
repository: openshorts
topic: "Connect the OpenAI-compatible LLM provider to the dashboard frontend"
tags: [plan, llm-provider, dashboard, byok, openai-compatible, frontend, settings, probe-endpoint]
status: ready
parent: .rpiv/artifacts/designs/2026-09-05_06-44-25_connect-llm-provider-frontend.md
phase_count: 5
phases:
  - { n: 1, title: "Backend — status channel + connection probe", files: [llm_client.py, app.py, tests/test_llm_client.py, tests/test_llm_endpoints.py], depends_on: [] }
  - { n: 2, title: "Browser state — storage, migration, server-status wiring", files: [dashboard/src/lib/llm.js, dashboard/src/App.jsx, dashboard/src/contexts/AuthContext.jsx, dashboard/src/components/ResultCard.jsx], depends_on: [1] }
  - { n: 3, title: "The AI Provider settings card", files: [dashboard/src/components/LlmProviderCard.jsx, dashboard/src/App.jsx], depends_on: [2] }
  - { n: 4, title: "Unblock the app — headers, gates, copy", files: [dashboard/src/App.jsx], depends_on: [2, 3] }
  - { n: 5, title: "Per-feature capability split", files: [dashboard/src/components/ThumbnailStudio.jsx, dashboard/src/components/SaaShortsTab.jsx], depends_on: [2, 4] }
last_updated: 2026-09-05T13:18:05+0700
last_updated_note: "Lint-green per-phase rework: Phase 2 defers llmHeaders (P4) and llmConfigured/llmModel/llmBaseUrl/setLlmConfig (P3) to their first consumers; two grep-zero checks pin the deferral"
last_updated_by: Yogiswara Utama
---

# Connect the OpenAI-compatible LLM provider to the dashboard frontend — Implementation Plan

## Overview

This plan implements a new AI Provider card in self-host Settings that collects an OpenAI-compatible endpoint URL, API key and model, stores them as one encrypted blob in the browser, and sends them as the `X-LLM-*` header triple built per component. The app's front door widens from "has a Gemini key" to "has any AI backend". A new `POST /api/llm/test` drives a "Test connection" button with status-code discrimination and key redaction.

Reference design: `.rpiv/artifacts/designs/2026-09-05_06-44-25_connect-llm-provider-frontend.md` (status: ready, 5 slices, 14 decisions D1–D14, 62 success criteria). The backend LLM core (`resolve_llm`, `llm_client.py`) is shipped at HEAD `35e9d7e`; the frontend provider code is not. The design adopts the parent design (D1–D9) with four research corrections: status-code discrimination via `startswith("LLM provider")`, AuthContext config-fetch retry, hardened decrypt migration guard with a JSON-canary blob, and a `_redact()` closure on the probe endpoint.

## Desired End State

A self-hoster with no Gemini key and an Ollama Cloud account:

```
1. Settings → AI Provider
     quick fill: [ollama cloud]  → endpoint pre-filled
     API key:    paste
     model:      gpt-oss:120b
     [Test connection] → "✓ responded in 840ms"
     [Save]

2. The "Required API keys missing" banner drops the AI-backend half.
   Clip Generator, AI Shorts and YouTube Studio all open.

3. YouTube Studio → analyze, titles, describe all work.
   Step 3 shows: "⚠ AI image generation needs a Gemini key —
   your provider handles text only." Generate is disabled;
   nothing else on the page is.
```

Every outgoing request from those surfaces carries:

```
X-LLM-Base-Url: https://ollama.com/v1
X-LLM-Key:      <key>
X-LLM-Model:    gpt-oss:120b
```

A self-hoster whose server already has `LLM_*` env sees, with no inputs demanded:

```
Settings → AI Provider
  ● Configured on the server
    https://ollama.com/v1
    model: gpt-oss:120b
  [ override with my own endpoint ]
```

## What We're NOT Doing

- **Server-side persistence of provider config.** Self-host has no auth, so a write endpoint would let anyone reachable redirect the install's AI traffic. Browser + headers only.
- **Centralising BYOK headers in `apiFetch`.** Would send the provider key on uploads and social posting. Per-component construction is the house style.
- **`ResultCard.jsx` beyond one dead line.** Its endpoints are Gemini-pinned; the client-side gate already fails fast with a truthful message.
- **Any cloud/billing provider surface.** `resolve_llm` returns `None` under `BILLING_ENABLED`.
- **Fixing the unbounded `_clients` cache.** Pre-existing, orthogonal to this feature.
- **Reordering `cloud/alerts.py::_classify_failure`.** Corrected twice in production; the `"LLM provider"` prefix strings stay verbatim.
- **Rewording any `llm_client` error message.** `"LLM provider"` is a reserved, alert-classifying string.
- **Blocking private or loopback probe targets.** The probe reaches any URL the caller names — a reviewed SSRF row, rate-capped but not reach-capped.

---

## Phase 1: Backend — status channel + connection probe

### Overview

This phase adds the server-side pieces: a short-timeout `probe()` in `llm_client.py`, the `_require_usable()` helper lifted out of `chat()`, the `/api/config` LLM fields (`llmConfigured`, `llmModel`, `llmBaseUrl`), and `POST /api/llm/test` with status-code discrimination and a `_redact()` closure. Tests pin both the client and the HTTP-level contract.

### Changes Required:

#### 1. llm_client.py — MODIFY
**File**: `llm_client.py`
**Changes**: Add `_PROBE_TIMEOUT`, a throwaway `_probe_client()`, lift the inline guard out of `chat()` into `_require_usable()`, and append `probe()`.

```python
# --- beside the existing _TIMEOUT (llm_client.py:130) ---------------------------

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)
# An interactive "test connection" must fail fast: a black-holed URL on
# _TIMEOUT would hold a browser tab for five minutes.
_PROBE_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


# --- beside the existing _http_client (llm_client.py:137-147) -------------------

def _probe_client(base_url: str) -> httpx.Client:
    """A throwaway client for probe(). Deliberately NOT the _clients cache:
    probed URLs come from a user, and caching one per attempt would grow that
    dict without bound."""
    try:
        return httpx.Client(base_url=base_url.rstrip("/"),
                            timeout=_PROBE_TIMEOUT, follow_redirects=True)
    except httpx.InvalidURL as e:
        raise LlmError("LLM provider endpoint URL is malformed (%s): %s"
                       % (base_url, e))


# --- before chat (after _json_contract) ----------------------------------------

def _require_usable(config: LlmConfig) -> str:
    """The base_url of a config that can actually be called, else LlmError.

    Lifted verbatim out of chat()'s prologue so probe() cannot drift from it:
    a green probe must mean the same config is callable for real work."""
    base_url = (config.base_url or "").strip()
    host = base_url.split("//", 1)[-1].split("/")[0] if "//" in base_url else ""
    if not base_url.startswith(("http://", "https://")) or not host:
        raise LlmError(
            "The third-party LLM endpoint URL is missing or malformed — it "
            "must be a full http(s) URL. Fix LLM_BASE_URL or the "
            "X-LLM-Base-Url header.")
    if not config.model:
        raise LlmError(
            "The third-party LLM endpoint is configured but no model is "
            "set. Set LLM_MODEL, or a per-task LLM_MODEL_THUMBNAIL / "
            "LLM_MODEL_SAAS.")
    return base_url


# --- inside chat(), replacing the inline guard at llm_client.py:362-374 ---------

    base_url = _require_usable(config)


# --- appended after chat() ------------------------------------------------------

def probe(config: LlmConfig) -> None:
    """One minimal live call, to verify an endpoint before a real job runs.

    Reuses _post's status-to-error mapping, so a rejection, transient failure
    or block surfaces the same message a real job would see. A green result
    means the endpoint accepted the request; it does not validate the
    response body (probe() skips _assistant_text, so a 200 with an error
    body or empty content still passes)."""
    base_url = _require_usable(config)
    client = _probe_client(base_url)
    try:
        _post(client, {"Authorization": "Bearer " + config.api_key,
                       "Content-Type": "application/json"},
              {"model": config.model,
               "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 1})
    finally:
        client.close()
```

#### 2. app.py — MODIFY
**File**: `app.py`
**Changes**: Add `_env_llm_config()` (reports the server's own `LLM_*` config), replace `get_config()` to expose the three LLM fields, and add `POST /api/llm/test` with status-code discrimination and `_redact()`.

```python
# --- before get_config (before line 1868) --------------------------------------

def _env_llm_config():
    """The server's own LLM_* config, or None — what /api/config reports.

    Asks by TASK, never with task=None: config_from resolves
    LLM_MODEL_<TASK> or LLM_MODEL, so "thumbnail" alone already covers a
    plain LLM_MODEL server, while a task=None probe on a server configured
    only with LLM_MODEL_THUMBNAIL would trip llm_client's once-only
    "the third-party backend stays inactive" warning on a perfectly healthy
    setup. Reporting such a server as unconfigured would also make the
    dashboard demand a key it does not need."""
    if BILLING_ENABLED:
        return None
    try:
        import llm_client
    except Exception:
        return None
    for task in ("thumbnail", "saas"):
        cfg = llm_client.active_config(task)
        if cfg is not None:
            return cfg
    return None


# --- replaces get_config (line 1868-1875) ---------------------------------------

@app.get("/api/config")
async def get_config():
    llm_cfg = _env_llm_config()
    return {
        "youtubeUrlEnabled": not DISABLE_YOUTUBE_URL,
        "billingEnabled": BILLING_ENABLED,
        "googleAuthEnabled": bool(BILLING_ENABLED and cloud.settings.google_auth_enabled),
        "jobRetentionSeconds": JOB_RETENTION_SECONDS,
        # Never the key. LlmConfig marks api_key repr=False and the dashboard
        # only needs to know that a backend exists and which one it is; this
        # endpoint is served before auth.
        "llmConfigured": llm_cfg is not None,
        "llmModel": llm_cfg.model if llm_cfg else None,
        "llmBaseUrl": llm_cfg.base_url if llm_cfg else None,
    }


# --- after get_config ------------------------------------------------------------

@app.post("/api/llm/test")
async def llm_test(request: Request):
    """Self-host only: one minimal live call against the resolved provider.

    Resolves exactly like a real request — the BYOK header triple when both
    base and key are present, else the server's LLM_* env — so a green result
    means the next job talks to that same endpoint. Loops the known tasks
    ("thumbnail", "saas") for the env fallback, matching _env_llm_config:
    a server with only LLM_MODEL_THUMBNAIL must resolve. Shares the
    metadata-probe limiter, keyed by client host because self-host has no
    user model."""
    if BILLING_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    _check_probe_rate(request.client.host if request.client else "anon")
    cfg = None
    for task in ("thumbnail", "saas"):
        cfg = await resolve_llm(request, task=task)
        if cfg is not None:
            break
    if cfg is None:
        raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
    import llm_client
    started = time.monotonic()
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, llm_client.probe, cfg)
    except Exception as e:
        # Belt-and-suspenders (D13): a provider's error body can echo the
        # Authorization header verbatim. Strip the key from the detail.
        detail = str(e)
        if cfg.api_key and cfg.api_key in detail:
            detail = detail.replace(cfg.api_key, "***")
        # Status-code discrimination (D10): a validation error (malformed
        # URL, missing model) does NOT start with "LLM provider" and is a
        # 400 — the caller's config is wrong, not the upstream. Everything
        # else (upstream rejection, transient, blocked) starts with the
        # prefix and is a 502. Never 402: apiFetch's 402 branch fires before
        # apiJson sees the body and would render an OpenShorts top-up prompt.
        if isinstance(e, llm_client.LlmError) and not str(e).startswith("LLM provider"):
            raise HTTPException(status_code=400, detail=detail)
        raise HTTPException(status_code=502, detail=detail)
    return {"ok": True, "model": cfg.model,
            "latencyMs": int((time.monotonic() - started) * 1000)}
```

#### 3. tests/test_llm_client.py — MODIFY
**File**: `tests/test_llm_client.py`
**Changes**: Add `probe()` tests — success, rejected key, 5xx transient, no shared-cache pollution, malformed URL / missing model, and `_require_usable` parity with `chat()`.

```python
# --- probe() (connection test) --------------------------------------------------
# Monkeypatches _probe_client, mirroring how this file already stands in for
# _http_client. No server, no network.
# Add after the json_mode test and before the alert-class tests.

def _probe_transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler),
                        base_url="https://provider.test/v1")


def test_probe_succeeds_on_a_healthy_endpoint(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _ok()

    monkeypatch.setattr(llm_client, "_probe_client",
                        lambda base_url: _probe_transport(handler))
    assert llm_client.probe(CFG) is None
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["max_tokens"] == 1


def test_probe_maps_a_rejected_key_to_llm_error(monkeypatch):
    monkeypatch.setattr(llm_client, "_probe_client",
                        lambda base_url: _probe_transport(
                            lambda r: _err(401, "invalid api key")))
    with pytest.raises(llm_client.LlmError) as exc:
        llm_client.probe(CFG)
    assert "LLM provider" in str(exc.value)


def test_probe_maps_a_5xx_to_transient(monkeypatch):
    monkeypatch.setattr(llm_client, "_probe_client",
                        lambda base_url: _probe_transport(
                            lambda r: _err(503, "overloaded")))
    with pytest.raises(llm_client.LlmTransientError):
        llm_client.probe(CFG)


def test_probe_never_populates_the_shared_client_cache(monkeypatch):
    # _clients is unbounded and keyed by base_url; probed URLs are user-supplied.
    monkeypatch.setattr(llm_client, "_probe_client",
                        lambda base_url: _probe_transport(lambda r: _ok()))
    before = dict(llm_client._clients)
    llm_client.probe(CFG)
    assert llm_client._clients == before


def test_probe_rejects_a_malformed_url_and_a_missing_model():
    with pytest.raises(llm_client.LlmError):
        llm_client.probe(llm_client.LlmConfig(
            base_url="provider.test", api_key="k", model="m"))
    with pytest.raises(llm_client.LlmError):
        llm_client.probe(llm_client.LlmConfig(
            base_url="https://provider.test/v1", api_key="k", model=""))


def test_require_usable_accepts_a_valid_config():
    # chat()'s prologue was refactored to call _require_usable; both paths
    # must accept the same valid configs.
    base = llm_client._require_usable(CFG)
    assert base == CFG.base_url
```

#### 4. tests/test_llm_endpoints.py — NEW
**File**: `tests/test_llm_endpoints.py`
**Changes**: HTTP-level contract tests for the `/api/config` LLM fields and `POST /api/llm/test` — config reporting, key never echoed, BYOK triple probed, env fallback, status-code discrimination (400 vs 502), key redaction, task-loop resolution.

```python
"""The two dashboard-facing LLM endpoints: the capability fields on
/api/config, and POST /api/llm/test.

Kept out of test_llm_client.py because these need the real app object and
that module is deliberately import-light (httpx.MockTransport only).
conftest.py pins BILLING_ENABLED=0, so everything here runs self-host.
"""
import pytest
from fastapi.testclient import TestClient

import app as app_module
import llm_client

BYOK = {"X-LLM-Base-Url": "https://byok.test/v1",
        "X-LLM-Key": "byok-secret-key",
        "X-LLM-Model": "byok-model"}


@pytest.fixture
def client():
    return TestClient(app_module.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_slate(monkeypatch):
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
              "LLM_MODEL_THUMBNAIL", "LLM_MODEL_SAAS"):
        monkeypatch.delenv(k, raising=False)
    app_module._probe_times.clear()


class TestConfigFields:
    def test_unconfigured_server_reports_no_provider(self, client):
        cfg = client.get("/api/config").json()
        assert cfg["llmConfigured"] is False
        assert cfg["llmModel"] is None and cfg["llmBaseUrl"] is None

    def test_configured_server_reports_model_and_base_url(self, client, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "secret-key")
        monkeypatch.setenv("LLM_MODEL", "gpt-oss:120b")
        cfg = client.get("/api/config").json()
        assert cfg["llmConfigured"] is True
        assert cfg["llmModel"] == "gpt-oss:120b"
        assert cfg["llmBaseUrl"] == "https://ollama.test/v1"

    def test_the_api_key_never_appears_in_the_payload(self, client, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "secret-key")
        monkeypatch.setenv("LLM_MODEL", "gpt-oss:120b")
        assert "secret-key" not in client.get("/api/config").text

    def test_a_task_only_model_still_counts_as_configured(self, client, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL_THUMBNAIL", "thumb-model")
        cfg = client.get("/api/config").json()
        assert cfg["llmConfigured"] is True
        assert cfg["llmModel"] == "thumb-model"

    def test_half_configured_env_reports_unconfigured(self, client, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        assert client.get("/api/config").json()["llmConfigured"] is False

    def test_existing_fields_are_unchanged(self, client):
        cfg = client.get("/api/config").json()
        for key in ("youtubeUrlEnabled", "billingEnabled",
                    "googleAuthEnabled", "jobRetentionSeconds"):
            assert key in cfg


class TestConnectionCheck:
    def test_probes_the_byok_header_triple(self, client, monkeypatch):
        seen = {}
        monkeypatch.setattr(llm_client, "probe",
                            lambda cfg: seen.update(base=cfg.base_url,
                                                    key=cfg.api_key,
                                                    model=cfg.model))
        res = client.post("/api/llm/test", headers=BYOK)
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert res.json()["model"] == "byok-model"
        assert seen == {"base": "https://byok.test/v1",
                        "key": "byok-secret-key", "model": "byok-model"}

    def test_falls_back_to_the_server_env(self, client, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL", "env-model")
        monkeypatch.setattr(llm_client, "probe", lambda cfg: None)
        assert client.post("/api/llm/test").json()["model"] == "env-model"

    def test_no_config_is_a_400_with_the_shared_hint(self, client):
        res = client.post("/api/llm/test")
        assert res.status_code == 400
        assert res.json()["detail"] == app_module.LLM_ENDPOINT_HINT

    def test_a_provider_failure_surfaces_its_own_message(self, client, monkeypatch):
        def boom(cfg):
            raise llm_client.LlmError(
                "LLM provider rejected the request (HTTP 401): bad key")
        monkeypatch.setattr(llm_client, "probe", boom)
        res = client.post("/api/llm/test", headers=BYOK)
        assert res.status_code == 502
        assert "LLM provider" in res.json()["detail"]

    def test_the_key_is_never_echoed_back(self, client, monkeypatch):
        monkeypatch.setattr(llm_client, "probe", lambda cfg: None)
        assert "byok-secret-key" not in client.post(
            "/api/llm/test", headers=BYOK).text

    def test_a_malformed_url_returns_400_not_502(self, client, monkeypatch):
        def boom(cfg):
            raise llm_client.LlmError(
                "The third-party LLM endpoint URL is missing or malformed")
        monkeypatch.setattr(llm_client, "probe", boom)
        res = client.post("/api/llm/test", headers=BYOK)
        assert res.status_code == 400

    def test_a_missing_model_returns_400_not_502(self, client, monkeypatch):
        def boom(cfg):
            raise llm_client.LlmError(
                "The third-party LLM endpoint is configured but no model is set")
        monkeypatch.setattr(llm_client, "probe", boom)
        res = client.post("/api/llm/test", headers=BYOK)
        assert res.status_code == 400

    def test_a_transient_failure_returns_502(self, client, monkeypatch):
        def boom(cfg):
            raise llm_client.LlmTransientError(
                "LLM provider timeout (retryable): connect timed out")
        monkeypatch.setattr(llm_client, "probe", boom)
        res = client.post("/api/llm/test", headers=BYOK)
        assert res.status_code == 502

    def test_the_key_is_redacted_from_error_detail(self, client, monkeypatch):
        def boom(cfg):
            raise llm_client.LlmError(
                "LLM provider rejected the request (HTTP 401): " + cfg.api_key)
        monkeypatch.setattr(llm_client, "probe", boom)
        res = client.post("/api/llm/test", headers=BYOK)
        assert res.status_code == 502
        assert "byok-secret-key" not in res.json()["detail"]
        assert "***" in res.json()["detail"]

    def test_a_thumbnail_only_server_resolves_via_task_loop(self, client, monkeypatch):
        # A server with only LLM_MODEL_THUMBNAIL must resolve: _env_llm_config
        # reports llmConfigured:true, and the probe must not 400.
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL_THUMBNAIL", "thumb-only-model")
        monkeypatch.setattr(llm_client, "probe", lambda cfg: None)
        res = client.post("/api/llm/test")
        assert res.status_code == 200
        assert res.json()["model"] == "thumb-only-model"
```

### Success Criteria:

#### Automated Verification:
- [x] `pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` passes (both in one process)
- [x] `grep -c "def probe" llm_client.py` returns 1
- [x] `grep -c "_probe_client" llm_client.py` returns at least 2 (definition + call in probe)
- [x] `grep -c "_require_usable" llm_client.py` returns at least 3 (definition + call in chat + call in probe)
- [x] `grep -c "/api/llm/test" app.py` returns 1
- [x] `grep -c "llmConfigured" app.py` returns at least 1
- [x] Phase committed: `git log -1 --oneline` names the phase; `git status --porcelain` clean

#### Manual Verification:
- [ ] `POST /api/llm/test` with a BYOK triple probes the endpoint and returns `{ok: true, model, latencyMs}`
- [ ] `POST /api/llm/test` with no config returns 400 with `LLM_ENDPOINT_HINT`
- [ ] `POST /api/llm/test` under `BILLING_ENABLED` returns 404
- [ ] `/api/config` includes `llmConfigured`, `llmModel`, `llmBaseUrl` fields
- [ ] The API key never appears in any `/api/config` or `/api/llm/test` response body
- [ ] A server with only `LLM_MODEL_THUMBNAIL` resolves on the probe (task loop)

---

## Phase 2: Browser state — storage, migration, server-status wiring

### Overview

This phase builds the browser-side foundation: `lib/llm.js` (the `X-LLM-*` header builder and completeness check), the `gemini_key` → `geminiKey_v1` migration with a JSON-canary decrypt guard, the `llmConfig` state, AuthContext config-fetch retry with the three new passthrough fields, and the dead `gemini_key` fallback removal in `ResultCard.jsx`.

### Changes Required:

#### 1. dashboard/src/lib/llm.js — NEW
**File**: `dashboard/src/lib/llm.js`
**Changes**: The `X-LLM-*` header triple builder and the completeness check, both per-component (never inside `apiFetch`).

```js
// The X-LLM-* header triple for the OpenAI-compatible provider.
//
// Built per call site — the same shape X-Gemini-Key uses — never inside
// apiFetch, which would leak the provider key to uploads and social
// posting (design D1).

// D2: llm_client.config_from goes silently inert on a half-configured
// provider, so the UI must never treat one as ready. All three fields,
// trimmed.
export const llmConfigComplete = (llmConfig) =>
  !!(llmConfig?.baseUrl?.trim() && llmConfig?.apiKey?.trim() && llmConfig?.model?.trim());

// D9: X-LLM-Model is omitted, never sent empty — a whitespace-only model
// falls back to the server's env chain (tests/test_llm_client.py:707).
// Base + key alone still resolve server-side, so they travel whenever set.
export const llmHeaders = (llmConfig) => {
  const baseUrl = llmConfig?.baseUrl?.trim();
  const apiKey = llmConfig?.apiKey?.trim();
  if (!baseUrl || !apiKey) return {};
  const model = llmConfig?.model?.trim();
  const headers = { 'X-LLM-Base-Url': baseUrl, 'X-LLM-Key': apiKey };
  if (model) headers['X-LLM-Model'] = model;
  return headers;
};
```

#### 2. dashboard/src/App.jsx — MODIFY (storage, migration, state, effects)
**File**: `dashboard/src/App.jsx`
**Changes**: Import `llmConfigComplete` from the llm lib (`llmHeaders` is deferred to Phase 4, its first consumer); replace the `apiKey` initializer with the `geminiKey_v1` migration (JSON canary); add the `llmConfig` state value-only (the setter lands in Phase 3 with the card's Save); add the two persistence effects. The useAuth LLM fields are deferred to Phase 3 — every binding declared here is used within this phase, so it commits lint-clean.

```jsx
// --- imports: after the other lib imports near the top of the file -------------
// llmHeaders is imported in Phase 4 (handleProcess), its first consumer —
// importing it here fails lint (no-unused-vars) in phase isolation.
import { llmConfigComplete } from './lib/llm';

// --- apiKey initializer (replaces line 211) — legacy plaintext → geminiKey_v1 ---
// One-way migration, once per browser (Migration Notes). The blob stores
// JSON ({key: ...}), not the bare key: decrypt never throws, and under a
// rotated VITE_ENCRYPTION_KEY a bare blob decrypts to valid-base64 garbage
// that re-encrypts byte-identically (XOR involution) — but garbage never
// survives JSON.parse. The JSON shape is the rotation guard (D12).
const [apiKey, setApiKey] = useState(() => {
  let value = '';
  try {
    const stored = localStorage.getItem('geminiKey_v1');
    if (stored) {
      const parsed = JSON.parse(decrypt(stored));
      if (parsed && typeof parsed === 'object' && typeof parsed.key === 'string') {
        value = parsed.key;
      }
    }
    if (!value) value = localStorage.getItem('gemini_key') || '';
  } catch (_) { /* unreadable storage or rotated-key garbage — start empty */ }
  // The plaintext key is gone after this line, adopted or not. The
  // persistence effect below writes the encrypted JSON form on this mount.
  try { localStorage.removeItem('gemini_key'); } catch (_) { /* ignore */ }
  return value;
});

// --- llmConfig state — inserted after the falKey initializer (~line 233) --------
// One encrypted JSON blob, not three keys: one localStorage read, one write.
// The JSON parse is the corruption guard — every bad path (bad base64,
// key-rotated garbage, non-JSON, wrong shape) lands in the catch and starts
// from the empty triple. Nothing here blocks the app from booting.
// The setter lands in Phase 3 (the card's Save is its only writer); this
// phase reads and persists the value only.
const [llmConfig] = useState(() => {
  try {
    const stored = localStorage.getItem('llmConfig_v1');
    if (stored) {
      const parsed = JSON.parse(decrypt(stored));
      if (parsed && typeof parsed === 'object') {
        return {
          baseUrl: typeof parsed.baseUrl === 'string' ? parsed.baseUrl : '',
          apiKey: typeof parsed.apiKey === 'string' ? parsed.apiKey : '',
          model: typeof parsed.model === 'string' ? parsed.model : '',
        };
      }
    }
  } catch (_) { /* corrupt or key-rotated blob — start empty */ }
  return { baseUrl: '', apiKey: '', model: '' };
});

// --- useAuth destructure: deferred to Phase 3 --------------------------------------
// llmConfigured / llmModel / llmBaseUrl are first consumed there (card props);
// destructuring them here fails lint (no-unused-vars) in phase isolation.

// --- persistence effects (replace the plaintext effect at lines 571-575) --------
// --- apiKey persistence — encrypted JSON like the other keys --------------------
useEffect(() => {
  if (apiKey) {
    try { localStorage.setItem('geminiKey_v1', encrypt(JSON.stringify({ key: apiKey }))); } catch (_) { /* ignore */ }
  }
}, [apiKey]);

// --- llmConfig persistence — same effect shape as the other keys (D8) -----------
// Guarded like the sibling keys: only a complete triple is persisted, so
// deleting the blob (and saving nothing after) restores the pre-feature
// state. Half-configured values live in memory only (D2).
useEffect(() => {
  if (llmConfigComplete(llmConfig)) {
    try { localStorage.setItem('llmConfig_v1', encrypt(JSON.stringify(llmConfig))); } catch (_) { /* ignore */ }
  }
}, [llmConfig]);
```

#### 3. dashboard/src/contexts/AuthContext.jsx — MODIFY
**File**: `dashboard/src/contexts/AuthContext.jsx`
**Changes**: Replace the one-shot mount config fetch with a retry loop (3 attempts, backoff); expose `llmConfigured`, `llmModel`, `llmBaseUrl` through `useAuth()`.

```jsx
// --- config fetch with retry (D11) — replaces the mount effect (lines 101-113) --
// A single bare fetch left config at its 2-key init forever on any transient
// failure — llmConfigured would stay false on a fully env-configured server
// and the banner would fire permanently. Three attempts, 500ms then 1s
// backoff; the happy path is still one fetch, once per mount.
const fetchConfig = useCallback(async () => {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(getApiUrl('/api/config'));
      if (res.ok) return await res.json();
    } catch (_) { /* network error — retry */ }
    if (attempt < 2) await new Promise((r) => setTimeout(r, 500 * (attempt + 1)));
  }
  return null; // stay on the init defaults (BYOK, no LLM fields)
}, []);

useEffect(() => {
  (async () => {
    const cfg = await fetchConfig();
    if (cfg) {
      setConfig(cfg);
      if (cfg.billingEnabled) {
        const handled = await handleAuthHash();
        if (!handled) await refreshMe();
      }
    }
    setLoading(false);
  })();
}, [fetchConfig, handleAuthHash, refreshMe]);

// --- /api/config LLM fields (D3): consts before the `value` object --------------
const llmConfigured = !!config.llmConfigured;
const llmModel = config.llmModel || null;
const llmBaseUrl = config.llmBaseUrl || null;

// --- inside `const value = { ... }`, next to `jobRetentionSeconds:` -------------
    llmConfigured,
    llmModel,
    llmBaseUrl,
```

#### 4. dashboard/src/components/ResultCard.jsx — MODIFY
**File**: `dashboard/src/components/ResultCard.jsx`
**Changes**: Remove the dead `localStorage.getItem('gemini_key')` fallback in `handleAutoEdit` — `App.jsx` always passes `geminiApiKey`, and the migration removes the key that fallback read.

```jsx
// --- handleAutoEdit (line 284): dead fallback removed ---------------------------
// Was: const apiKey = geminiApiKey || a dead localStorage fallback (the
// legacy plaintext key). App.jsx always passes geminiApiKey, and the
// geminiKey_v1 migration removes the key that fallback used to find.
const apiKey = geminiApiKey;
```

### Success Criteria:

#### Automated Verification:
- [x] `cd dashboard && npm run lint` passes with zero warnings
- [x] `grep -c "llmConfigured" dashboard/src/contexts/AuthContext.jsx` returns at least 2
- [x] `grep -rn "gemini_key" dashboard/src/` returns exactly 2 matches, both in App.jsx's initializer (one `getItem` + one `removeItem`)
- [x] `grep -c "export const" dashboard/src/lib/llm.js` returns 2
- [x] `grep -c "llmConfig_v1" dashboard/src/App.jsx` returns 2 (initializer read + persistence write)
- [x] `grep -c "llmHeaders" dashboard/src/App.jsx` returns 0 (deferred to Phase 4, its first consumer)
- [x] `grep -c "setLlmConfig" dashboard/src/App.jsx` returns 0 (deferred to Phase 3, its first consumer)
- [x] Phase committed: `git log -1 --oneline` names the phase; `git status --porcelain` clean

#### Manual Verification:
- [ ] With a plaintext `gemini_key` in localStorage, reload → Settings shows the key, `gemini_key` removed, `geminiKey_v1` holds an `ENC:`-prefixed blob
- [ ] With `llmConfig_v1` set to garbage (`ENC:AAAA`), reload → app boots to the empty provider state, no console crash
- [ ] Rotation guard: with `geminiKey_v1` holding a blob encrypted under a different `VITE_ENCRYPTION_KEY`, reload → the Gemini key starts empty (no garbage adopted), app healthy
- [ ] "auto edit" on a result clip still sends `X-Gemini-Key` when a Gemini key is set (Network tab)
- [ ] With the API server stopped, reload → dashboard renders after ~1.5s of retries, no permanent spinner

---

## Phase 3: The AI Provider settings card

### Overview

This phase builds `LlmProviderCard.jsx` — the Settings card with three fields, quick-fill presets, triple validation, a "Test connection" button, and a server-status display — and mounts it in the self-host branch of `App.jsx` between the Gemini key card and Social Integration.

### Changes Required:

#### 1. dashboard/src/components/LlmProviderCard.jsx — NEW
**File**: `dashboard/src/components/LlmProviderCard.jsx`
**Changes**: The full settings card — three inputs with quick-fill presets, masked key field, Save (disabled until complete), Test connection (400 = config error, 502 = provider refused), and the server-configured status block with an override button.

```jsx
import React, { useState } from 'react';
import { Cpu, Eye, EyeOff, Check, Loader2, AlertTriangle } from 'lucide-react';
import { apiJson } from '../lib/api';
import { llmConfigComplete, llmHeaders } from '../lib/llm';

// The OpenAI-compatible AI provider card. Mounted only in the self-host
// Settings branch (App.jsx), so the surface is structurally absent on cloud.
//
// D2: llm_client goes silently inert on a half-configured provider, so Save
// stays disabled until all three fields have content. "Test connection" posts
// the typed-but-unsaved values to the connection-check endpoint, which
// resolves them exactly like a real job would; D10: its 400 means this
// config is wrong (malformed URL, no model) and 502 means the provider
// refused or failed — never 402.
// D9: the header builder omits the model header when empty, so a model-less
// test falls back to the server's env chain.

const PRESETS = [
  { id: 'ollama-cloud', label: 'ollama cloud', baseUrl: 'https://ollama.com/v1' },
  { id: 'ollama-local', label: 'local ollama', baseUrl: 'http://localhost:11434/v1' },
  { id: 'openrouter', label: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1' },
];

export default function LlmProviderCard({ savedConfig, onConfigSet, llmConfigured, llmModel, llmBaseUrl }) {
  const [form, setForm] = useState(() => ({
    baseUrl: savedConfig?.baseUrl || '',
    apiKey: savedConfig?.apiKey || '',
    model: savedConfig?.model || '',
  }));
  const [saved, setSaved] = useState(false);
  const [isVisible, setIsVisible] = useState(false);
  // null | {phase:'running'} | {phase:'ok', latencyMs, model} | {phase:'error', kind, detail}
  const [test, setTest] = useState(null);
  // The server-status block stands in for the form until the user asks for it.
  // Derived, not stored: the config read lands after mount, so a stored
  // flag would freeze on the pre-fetch value. A complete saved config IS an
  // override, so with one saved the form is the primary surface.
  const [overrideRequested, setOverrideRequested] = useState(false);
  const serverConfigured = !!llmConfigured && !llmConfigComplete(savedConfig);
  const showForm = overrideRequested || !serverConfigured;

  const setField = (name) => (e) => {
    setForm((f) => ({ ...f, [name]: e.target.value }));
    setSaved(false);
  };

  const handleSave = () => {
    if (!llmConfigComplete(form)) return;
    // Stored trimmed: the card is the only writer of the config, and the
    // header builder trims anyway — storing clean costs nothing.
    onConfigSet({ baseUrl: form.baseUrl.trim(), apiKey: form.apiKey.trim(), model: form.model.trim() });
    setSaved(true);
  };

  const handleTest = async () => {
    setTest({ phase: 'running' });
    try {
      const data = await apiJson('/api/llm/test', { method: 'POST', headers: llmHeaders(form) });
      setTest({ phase: 'ok', latencyMs: data.latencyMs, model: data.model });
    } catch (e) {
      setTest({
        phase: 'error',
        kind: e?.status === 400 ? 'config' : 'provider',
        detail: e?.detail || e?.message || 'Request failed',
      });
    }
  };

  const canTest = !!(form.baseUrl.trim() && form.apiKey.trim());
  const canSave = llmConfigComplete(form);

  return (
    <div className="card p-4 sm:p-6 mb-8 animate-fade">
      <div className="flex items-center gap-3 mb-4">
        <div className="p-2 bg-paper3 rounded-input text-brass">
          <Cpu size={18} />
        </div>
        <h2 className="font-display lowercase text-lg text-ink">AI Provider</h2>
      </div>

      {showForm ? (
        <>
          <p className="text-xs text-muted mb-4 leading-relaxed">
            Any OpenAI-compatible endpoint (Ollama, OpenRouter, vLLM, …) can power clip analysis, titles and
            descriptions — a Gemini key stays optional. Keys are only stored in your browser and sent per request,
            never stored server-side.
          </p>

          <div className="flex flex-wrap gap-1.5 mb-4" aria-label="quick fill">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => { setForm((f) => ({ ...f, baseUrl: p.baseUrl })); setSaved(false); }}
                className={`px-3 py-1.5 rounded-input text-xs border transition-colors ${
                  form.baseUrl === p.baseUrl ? 'border-brass text-ink bg-brass/10' : 'border-rule text-muted hover:text-ink'}`}
              >
                {p.label}
              </button>
            ))}
          </div>

          <div className="space-y-3">
            <div>
              <label className="block text-sm text-muted mb-1" htmlFor="llm-base-url">Endpoint URL</label>
              <input
                id="llm-base-url"
                type="text"
                value={form.baseUrl}
                onChange={setField('baseUrl')}
                placeholder="https://ollama.com/v1"
                className="input-field font-mono"
                autoComplete="off"
                spellCheck="false"
              />
            </div>
            <div>
              <label className="block text-sm text-muted mb-1" htmlFor="llm-api-key">API Key</label>
              <div className="relative">
                <input
                  id="llm-api-key"
                  type={isVisible ? 'text' : 'password'}
                  value={form.apiKey}
                  onChange={setField('apiKey')}
                  placeholder="sk-..."
                  className="input-field pr-12 font-mono"
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={() => setIsVisible(!isVisible)}
                  aria-label={isVisible ? 'hide key' : 'show key'}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-ink transition-colors"
                >
                  {isVisible ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>
            <div>
              <label className="block text-sm text-muted mb-1" htmlFor="llm-model">Model</label>
              <input
                id="llm-model"
                type="text"
                value={form.model}
                onChange={setField('model')}
                placeholder="gpt-oss:120b"
                className="input-field font-mono"
                autoComplete="off"
                spellCheck="false"
              />
              <p className="text-xs text-muted mt-1">
                Left empty, the model falls back to the server's LLM_MODEL setting — when it has one.
              </p>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row gap-2 mt-4">
            <button
              type="button"
              onClick={handleTest}
              disabled={!canTest || test?.phase === 'running'}
              className="btn-quiet py-2 px-4 text-sm disabled:opacity-50"
            >
              {test?.phase === 'running'
                ? <><Loader2 size={14} className="animate-spin" /> testing…</>
                : 'Test connection'}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={!canSave}
              className={saved ? 'badge-ok px-4 cursor-default' : 'btn-primary'}
            >
              {saved ? <><Check size={14} /> Saved</> : 'Save provider'}
            </button>
          </div>

          {test?.phase === 'ok' && (
            <p className="badge-ok mt-3 inline-flex items-center gap-1.5" role="status">
              <Check size={12} /> responded in {test.latencyMs}ms{test.model ? ` · ${test.model}` : ''}
            </p>
          )}
          {test?.phase === 'error' && (
            <div className="mt-3 px-3 py-2.5 rounded-input bg-paper3 border border-rule text-xs" role="alert">
              <p className="flex items-center gap-1.5 font-medium text-ink">
                <AlertTriangle size={12} className="text-warn shrink-0" />
                {test.kind === 'config'
                  ? 'This configuration is not usable:'
                  : 'The provider refused the request:'}
              </p>
              <p className="text-muted mt-1 break-words">{test.detail}</p>
            </div>
          )}
        </>
      ) : (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-start gap-2.5 text-sm">
            <span className="w-2 h-2 rounded-full bg-ok shrink-0 mt-1.5" aria-hidden="true" />
            <div>
              <p className="font-medium text-ink">Configured on the server</p>
              <p className="text-muted text-xs mt-0.5 font-mono break-all">{llmBaseUrl}</p>
              {llmModel && <p className="text-muted text-xs">model: <span className="font-mono">{llmModel}</span></p>}
            </div>
          </div>
          <button type="button" onClick={() => setOverrideRequested(true)} className="btn-quiet py-2 px-4 text-sm shrink-0">
            override with my own endpoint
          </button>
        </div>
      )}
    </div>
  );
}
```

#### 2. dashboard/src/App.jsx — MODIFY (card import + mount)
**File**: `dashboard/src/App.jsx`
**Changes**: Import `LlmProviderCard`; add the LLM fields to the useAuth destructure and the setter to the `llmConfig` state (both deferred from Phase 2 for lint isolation — the card mount is their first consumer); mount the card in the self-host Settings branch between `<KeyInput />` and the Social Integration card, inside the `!billingEnabled` fragment so cloud renders never see it.

```jsx
// --- component import: between KeyInput and MediaInput (alphabetical) ----------
import LlmProviderCard from './components/LlmProviderCard';

// --- useAuth destructure: Phase 3 adds the LLM fields (deferred from Phase 2) ------
// They are first consumed here as card props, so Phase 2 stayed lint-clean.
const { billingEnabled, isManaged, isSignedIn, me, plan, refreshMe, jobRetentionSeconds,
        llmConfigured, llmModel, llmBaseUrl } = useAuth();

// --- llmConfig state gains its setter (Phase 2 declared the value only) ------------
// Was (Phase 2): const [llmConfig] = useState(() => { ... });
const [llmConfig, setLlmConfig] = useState(() => { ... });

// --- Settings, self-host branch (the `: (` fallback of the isManaged/billingEnabled
// ternary): the card mounts between <KeyInput /> and the Social Integration card,
// INSIDE the !billingEnabled fragment, so cloud renders never see it --------------
<>
  <KeyInput onKeySet={setApiKey} savedKey={apiKey} />

  {/* Self-host only: this mount lives in the !billingEnabled branch, so the
      provider surface is structurally absent on cloud (Requirement). */}
  <LlmProviderCard
    savedConfig={llmConfig}
    onConfigSet={setLlmConfig}
    llmConfigured={llmConfigured}
    llmModel={llmModel}
    llmBaseUrl={llmBaseUrl}
  />

  <div className="card p-4 sm:p-6 mt-8">
    {/* ... Social Integration card unchanged ... */}
```

### Success Criteria:

#### Automated Verification:
- [x] `cd dashboard && npm run lint` passes with zero warnings
- [x] `grep -c "export default function LlmProviderCard" dashboard/src/components/LlmProviderCard.jsx` returns 1
- [x] `grep -c "LlmProviderCard" dashboard/src/App.jsx` returns 2 (import + mount)
- [x] `grep -c "setLlmConfig" dashboard/src/App.jsx` returns 2 (state declaration + `onConfigSet={setLlmConfig}`)
- [x] `grep -c "llmConfigured" dashboard/src/App.jsx` returns at least 2 (destructure + card prop)
- [x] `grep -c "/api/llm/test" dashboard/src/components/LlmProviderCard.jsx` returns 1
- [x] `grep -c "llmHeaders" dashboard/src/components/LlmProviderCard.jsx` returns 2 (import + test call)
- [x] `grep -c "llmConfigComplete" dashboard/src/components/LlmProviderCard.jsx` returns at least 3 (import + canSave + serverConfigured)
- [x] Phase committed: `git log -1 --oneline` names the phase; `git status --porcelain` clean

#### Manual Verification:
- [ ] Self-host Settings shows the AI Provider card between the Gemini key card and Social Integration; a BILLING_ENABLED deployment shows no provider surface at all
- [ ] Quick-fill chips fill the endpoint field (Ollama Cloud → `https://ollama.com/v1`)
- [ ] Save stays disabled until all three fields have content; after Save + reload the three fields are restored from `llmConfig_v1`
- [ ] Test connection against a live endpoint shows the latency line; a rejected key shows the provider's own message under "The provider refused the request:"; a malformed URL shows it under "This configuration is not usable:" (the 400 path)
- [ ] A server with `LLM_*` env and no local config shows the "Configured on the server" block with base URL and model plus the override button; clicking it reveals the form
- [ ] The key field is masked and the eye toggle works

---

## Phase 4: Unblock the app — headers, gates, copy

### Overview

This phase widens the app's front door from "has a Gemini key" to "has any AI backend" (`needsAiBackend = !apiKey && !llmActive && !managed`, where `llmActive = llmConfigComplete(providerCfg) || !!llmConfigured`), adds the `X-LLM-*` headers to `/api/process`, applies the cloud gate (`providerCfg` empties under billing), and updates the header badge, banner, and required-keys modal copy. It merges into the `App.jsx` fence after Phase 3.

> **Anchor note (Step 5 triage):** All numeric anchors below are HEAD-line numbers, stale after Phase 2/3 insertions (~+57 lines by Phase 4). Match content, not line numbers.

### Changes Required:

#### 1. dashboard/src/App.jsx — MODIFY (gate, headers, copy)
**File**: `dashboard/src/App.jsx`
**Changes**: Extend the llm lib import with `llmHeaders` (deferred from Phase 2 — `handleProcess` is its first consumer); the AI-backend gate (`providerCfg`, `llmActive`, `needsAiBackend`, `keysMissing`, `needsPlan`); `handleProcess` request headers; header badge copy; banner copy; `SaaShortsTab` and `ThumbnailStudio` mount props; required-keys modal title, intro, and Gemini block.

```jsx
// --- Slice 4: llm lib import gains llmHeaders (deferred from Phase 2; the gate and
// --- handleProcess below are its first consumers) ----------------------------------
import { llmConfigComplete, llmHeaders } from './lib/llm';

// --- Slice 4: the AI-backend gate — replaces lines 708-709 (the needsPlan line
// --- below is re-emitted once; the comment pair at 706-707 survives untouched) ---
  // The AI backend is any ONE of: a Gemini key, the saved provider triple, or a
  // server-side LLM_* setup reported by /api/config. The AI half of the banner
  // fires only when none of the three exists — a provider-only user runs the app.
  // Cloud gate (D3): a stale browser blob from a self-host era of this origin
  // must not reach cloud requests, gates or child components. The empty triple
  // makes every consumer inert under billing.
  const providerCfg = billingEnabled
    ? { baseUrl: '', apiKey: '', model: '' }
    : llmConfig;
  const llmActive = llmConfigComplete(providerCfg) || !!llmConfigured;
  const needsAiBackend = !apiKey && !llmActive;
  const keysMissing = !billingEnabled && (needsAiBackend || !uploadPostKey);
  const needsPlan = billingEnabled && !isManaged;   // hosted, signed-out or no active plan/trial

// --- Slice 4: handleProcess request headers — replaces lines 789-792 ------------
      let body;
      // BYOK sends the Gemini header plus the provider triple; managed users
      // rely on the bearer token that apiFetch attaches automatically. The
      // triple is the cloud-gated value, so a stale blob cannot leak (D3).
      const headers = {
        ...llmHeaders(providerCfg),
        ...(apiKey ? { 'X-Gemini-Key': apiKey } : {}),
      };

// --- Slice 4: header badge copy — replaces the ternary at 1170-1174 -------------
                {needsAiBackend && !uploadPostKey
                  ? 'AI & Upload-Post keys missing'
                  : needsAiBackend
                    ? 'AI Key Missing'
                    : 'Upload-Post API Key Missing'}

// --- Slice 4: banner copy — replaces the ternary at 1190-1194 -------------------
                  {needsAiBackend && !uploadPostKey
                    ? 'Set an AI key and your Upload-Post key to use OpenShorts.'
                    : needsAiBackend
                      ? 'Set a Gemini API key or an AI provider to use OpenShorts.'
                      : 'Set your Upload-Post API key to use OpenShorts.'}

// --- Slice 4: SaaShortsTab mount (line 1470) gains the Slice-5 props ------------
            <SaaShortsTab geminiApiKey={apiKey} elevenLabsKey={elevenLabsKey} falKey={falKey} uploadPostKey={uploadPostKey} uploadUserId={uploadUserId} managed={isManaged} llmConfig={providerCfg} llmActive={llmActive} />

// --- Slice 4: ThumbnailStudio mount (1608-1619) gains the same two props --------
            <ThumbnailStudio
              geminiApiKey={apiKey}
              llmConfig={providerCfg}
              llmActive={llmActive}
              uploadPostKey={uploadPostKey}
              uploadUserId={uploadUserId}
              managed={isManaged}
              onCreateClips={(sessionId) => {
                setActiveTab('dashboard');
                // The Studio source is the user's own upload, published to their
                // own channel; the handover carries that same attestation.
                handleProcess({ type: 'thumbnail_session', payload: sessionId, acknowledged: true });
              }}
            />

// --- Slice 4: required-keys modal — title ternary (1929-1933) -------------------
        title={needsAiBackend && !uploadPostKey
          ? 'Required API Keys Missing'
          : needsAiBackend
            ? 'AI Key Required'
            : 'Upload-Post API Key Required'}

// --- Slice 4: modal intro (1952-1954) --------------------------------------------
          <p className="text-sm text-muted">
            OpenShorts needs an <strong className="text-ink2">AI key</strong> — Gemini, or any
            OpenAI-compatible provider — and an <strong className="text-ink2">Upload-Post</strong> API key.
            Gemini and Upload-Post both have free tiers.
          </p>

// --- Slice 4: modal Gemini block — replaces the block at 1956-1982 --------------
          {/* AI block — a Gemini key or any OpenAI-compatible provider satisfies it */}
          <div className={`rounded-input p-4 space-y-2 border ${needsAiBackend ? 'border-rule2' : 'border-rule opacity-70'}`}>
            <p className="text-xs font-medium text-ink flex items-center gap-2">
              {needsAiBackend ? <AlertTriangle size={12} className="text-warn" /> : <Check size={12} className="text-ok" />}
              Gemini API Key
              {apiKey && <span className="text-ok">— set</span>}
              {!apiKey && llmActive && <span className="text-ok">— covered by your AI provider</span>}
            </p>
            {needsAiBackend && (
              <>
                <ol className="text-xs text-muted space-y-1 list-decimal list-inside">
                  <li>Go to <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" className="text-brass underline">aistudio.google.com/app/apikey</a></li>
                  <li>Sign in with your Google account</li>
                  <li>Click "Create API Key"</li>
                  <li>Copy the key and paste it below</li>
                </ol>
                <input
                  type="text"
                  placeholder="Paste your Gemini API key here..."
                  className="input-field"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && e.target.value.trim()) {
                      setApiKey(e.target.value.trim());
                    }
                  }}
                />
                <p className="text-xs text-muted">
                  No Google key? Any OpenAI-compatible endpoint works too — Ollama, OpenRouter, vLLM.
                  Close this dialog and set it up under Settings → AI Provider.
                </p>
              </>
            )}
          </div>
```

### Success Criteria:

#### Automated Verification:
- [x] `cd dashboard && npm run lint` passes with zero warnings
- [x] `grep -c "llmHeaders" dashboard/src/App.jsx` returns exactly 2 (import + handleProcess spread)
- [x] `grep -c "llmActive" dashboard/src/App.jsx` returns exactly 5 (definition + gate + modal suffix + two mount props)
- [x] `grep -c "needsAiBackend" dashboard/src/App.jsx` returns exactly 11 (gate ×2 + badge ×2 + banner ×2 + modal title ×2 + modal block ×3)
- [x] `grep -c "llmConfigComplete" dashboard/src/App.jsx` returns exactly 3 (import + Slice-2 persistence guard + gate)
- [x] `grep -c "providerCfg" dashboard/src/App.jsx` returns exactly 5 (definition + gate + spread + two mount props)
- [x] Phase committed: `git log -1 --oneline` names the phase; `git status --porcelain` clean

#### Manual Verification:
- [ ] Provider triple saved + Upload-Post key, no Gemini key: no keys banner; a submitted job's `/api/process` request carries the `X-LLM-*` triple (Network tab)
- [ ] No Gemini key, no provider, no Upload-Post key: banner names "AI key" (not "Gemini"); the modal shows the provider-alternative line under the Gemini block
- [ ] Provider saved, no Gemini key: the modal's Gemini block reads "— covered by your AI provider" and hides the paste input
- [ ] Server `LLM_*` env (`llmConfigured` true), no local keys: no AI demand anywhere; only the Upload-Post half of the banner can fire
- [ ] Cloud absence (D3): on a `BILLING_ENABLED` deployment holding a stale `llmConfig_v1` blob, `/api/process` sends no `X-LLM-*` headers
- [ ] Regression: with a Gemini key set, `/api/process` still carries `X-Gemini-Key`; SaaShortsTab and ThumbnailStudio still receive `geminiApiKey`

---

## Phase 5: Per-feature capability split

### Overview

This phase spreads the `X-LLM-*` triple across the LLM-routable request sites in `ThumbnailStudio.jsx` (analyze, confirm-title, refine, generate, describe) and `SaaShortsTab.jsx` (analyze), and re-points each surface's gate from Gemini-only to `needsAiBackend = !geminiApiKey && !llmActive && !managed`. The Generate step keeps a Gemini-specific inline note and disabled button, since image generation is Gemini-only by physics.

### Changes Required:

#### 1. dashboard/src/components/ThumbnailStudio.jsx — MODIFY
**File**: `dashboard/src/components/ThumbnailStudio.jsx`
**Changes**: Import `llmHeaders`; widen the signature to accept `llmConfig`/`llmActive`; replace `needsKey` with `needsAiBackend` + `needsGeminiImage`; add the triple to all five request sites; update the banner and step-0 gate; add the generate-step inline note with a disabled button.

```jsx
// --- imports: after the SegmentedControl import (line 6) ------------------------
import { llmHeaders } from '../lib/llm';

// --- component signature + gate definitions (replaces lines 73-77) ---------------
export default function ThumbnailStudio({ geminiApiKey, llmConfig, llmActive, uploadPostKey, uploadUserId, managed = false, onCreateClips = null }) {
  // Managed (hosted plan): Gemini runs server-side via the bearer token, no BYOK key.
  // Only send X-Gemini-Key for self-host BYOK. apiFetch attaches the bearer token.
  const keyHeader = geminiApiKey ? { 'X-Gemini-Key': geminiApiKey } : {};
  const needsAiBackend = !geminiApiKey && !llmActive && !managed;
  const needsGeminiImage = !geminiApiKey && !managed;

// --- handleAnalyze guard (replaces line 155) --------------------------------------
    if (needsAiBackend) return alert('Please set a Gemini API key or an AI provider in Settings first.');

// --- analyze request headers (replaces line 172) ----------------------------------
        headers: { ...keyHeader, ...llmHeaders(llmConfig) },

// --- handleConfirmTitle request headers (replaces line 218; the trailing comma lands on the replaced line) ---
          ...keyHeader,
          ...llmHeaders(llmConfig),

// --- handleRefine request headers (replaces line 241; the trailing comma lands on the replaced line) ---
          ...keyHeader,
          ...llmHeaders(llmConfig),

// --- handleGenerate guard (replaces line 268) -------------------------------------
    // Image generation is Gemini-only by physics (app.py:5003-5006 resolves the
    // Gemini key first and hard-fails without it); the concept-design text call
    // inside the same endpoint can still run on the provider.
    if (needsGeminiImage) return alert('AI image generation needs a Gemini API key. Set it in Settings first.');

// --- generate request headers (replaces line 288) ---------------------------------
        headers: { ...keyHeader, ...llmHeaders(llmConfig) },

// --- handleGenerateDescription guard (replaces line 347) --------------------------
    if (needsAiBackend) return alert('Please set a Gemini API key or an AI provider in Settings first.');

// --- describe request headers (replaces line 358; the trailing comma lands on the replaced line) ---
          ...keyHeader,
          ...llmHeaders(llmConfig),

// --- banner (replaces the warning block at 494-503) -------------------------------
        {/* AI backend warning (self-host BYOK only; managed uses server key) */}
        {needsAiBackend && (
          <div className="mb-6 p-5 bg-warn/10 rounded-card flex items-start gap-3">
            <AlertCircle size={18} className="text-warn shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-warn lowercase">AI Key Required</p>
              <p className="text-xs text-muted mt-1">YouTube Studio needs an AI backend for titles and descriptions — a Gemini API key or any OpenAI-compatible provider. Configure one in the <strong>Settings</strong> tab. Gemini's free tier includes 1,500 requests per day.</p>
            </div>
          </div>
        )}

// --- step-0 grid gate (replaces line 507) -----------------------------------------
          <div className={`grid md:grid-cols-2 gap-6 ${needsAiBackend ? 'opacity-50 pointer-events-none select-none' : ''}`}>

// --- generate button: inline note + disabled (replaces the button at 846-862; the blank line 863 and the column-closing </div> at 864 are unchanged) ---
              {needsGeminiImage && (
                <p className="text-xs text-warn flex items-start gap-1.5">
                  <AlertCircle size={12} className="shrink-0 mt-0.5" />
                  AI image generation needs a Gemini key — your provider handles text only.
                </p>
              )}

              <button
                onClick={handleGenerate}
                disabled={isGenerating || needsGeminiImage}
                className="w-full btn-primary"
              >
                {isGenerating ? (
                  <>
                    <Loader2 size={16} className="animate-spin" />
                    Generating thumbnails...
                  </>
                ) : (
                  <>
                    <Sparkles size={16} />
                    Generate Thumbnails
                  </>
                )}
              </button>
```

#### 2. dashboard/src/components/SaaShortsTab.jsx — MODIFY
**File**: `dashboard/src/components/SaaShortsTab.jsx`
**Changes**: Import `llmHeaders`; widen the signature to accept `llmConfig`/`llmActive`; replace `needsGeminiKey` with `needsAiBackend`; add the triple to the analyze request.

```jsx
// --- imports: after the StarBanner import (line 7) --------------------------------
import { llmHeaders } from '../lib/llm';

// --- component signature + gate definition (replaces lines 43-47) -----------------
export default function SaaShortsTab({ geminiApiKey, llmConfig, llmActive, elevenLabsKey, falKey, uploadPostKey, uploadUserId, managed = false }) {
  // Managed (hosted plan): Gemini (script) + Upload-Post run server-side via the
  // bearer token — no BYOK Gemini key needed. fal.ai + ElevenLabs stay BYOK.
  const geminiHeader = geminiApiKey ? { 'X-Gemini-Key': geminiApiKey } : {};
  const needsAiBackend = !geminiApiKey && !llmActive && !managed;

// --- handleAnalyze gate (replaces lines 203-206) ----------------------------------
    if (needsAiBackend) {
      setAnalyzeError('An AI key is required — set a Gemini key or an AI provider in Settings.');
      return;
    }

// --- analyze request headers (replaces lines 214-217) ------------------------------
        headers: {
          'Content-Type': 'application/json',
          ...geminiHeader,
          ...llmHeaders(llmConfig),
        },
```

### Success Criteria:

#### Automated Verification:
- [x] `cd dashboard && npm run lint` passes with zero warnings
- [x] `grep -c "llmHeaders" dashboard/src/components/ThumbnailStudio.jsx` returns exactly 6 (import + five request spreads)
- [x] `grep -c "llmHeaders" dashboard/src/components/SaaShortsTab.jsx` returns exactly 2 (import + analyze spread)
- [x] `grep -c "needsAiBackend" dashboard/src/components/ThumbnailStudio.jsx` returns exactly 5 (definition + analyze guard + describe guard + banner + step-0 gate)
- [x] `grep -c "needsGeminiImage" dashboard/src/components/ThumbnailStudio.jsx` returns exactly 4 (definition + guard + disabled + note)
- [x] `grep -c "needsAiBackend" dashboard/src/components/SaaShortsTab.jsx` returns exactly 2 (definition + gate)
- [x] `grep -c "needsKey" dashboard/src/components/ThumbnailStudio.jsx` returns 0 and `grep -c "needsGeminiKey" dashboard/src/components/SaaShortsTab.jsx` returns 0 (old flags fully replaced)
- [x] Terminal slice: `cd dashboard && npm run build` succeeds
- [x] Design regression set: `pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` passes
- [x] Phase committed: `git log -1 --oneline` names the phase; `git status --porcelain` clean

#### Manual Verification:
- [ ] LLM-only self-host (provider saved, no Gemini key): YouTube Studio opens with no key banner; analyze, title refine and describe all work; the Generate step shows the inline note and Generate stays disabled; nothing else is disabled
- [ ] Network tab: analyze / titles / describe / generate requests carry the `X-LLM-*` triple; upload, frames, publish and publish-status requests carry no `X-LLM-*` headers
- [ ] SaaShortsTab, LLM-only: analyze proceeds with no "Gemini API key required" error; the analyze request carries the `X-LLM-*` triple
- [ ] Regression, Gemini-only setup: banner and gates behave exactly as before this slice; thumbnail generate works
- [ ] Regression, managed cloud plan: no banner, generate enabled, no `X-LLM-*` headers on any request

---

## Testing Strategy

### Automated:
- Backend unit + contract tests: `pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` (Phase 1 adds them; Phase 5 re-runs as a regression set)
- Frontend lint (read-only, repo-wide, non-fixing): `cd dashboard && npm run lint`
- Frontend build (terminal slice, Phase 5): `cd dashboard && npm run build`
- Per-phase grep checks pin the structural counts (see each phase's Automated Verification)

### Manual Testing Steps:
1. pire-browser e2e: start `./dev.sh --no-renderer` + a Python `{choices:[{message:{content:'ok'},...}]}` stub on `localhost:11434`, open the dashboard, configure the provider (quick-fill Ollama Cloud, paste key, model `gpt-oss:120b`), Test connection, Save, submit a job, and inspect the outgoing `/api/process` request headers for the `X-LLM-*` triple. Accept the job fails at the LLM step (the stub body is minimal); the FRD bullet only asks to confirm the headers are present.
2. Storage migration: with a plaintext `gemini_key` in localStorage, reload and confirm the key migrates to `geminiKey_v1` and `gemini_key` is removed.
3. Rotation guard: set `geminiKey_v1` to a blob encrypted under a different `VITE_ENCRYPTION_KEY`, reload, and confirm the Gemini key starts empty with no garbage adopted.
4. Cloud absence (D3): on a `BILLING_ENABLED` deployment holding a stale `llmConfig_v1` blob, confirm `/api/process` sends no `X-LLM-*` headers.
5. Commit each phase at session end before starting the next — Phase 1 code was lost once before (see References: prior plan). `git log -1 --oneline` names the phase; `git status --porcelain` is clean.

## Performance Considerations

- `probe()` uses a one-off `_probe_client` with connect 5s / read 20s, not the shared 300s `_http_client` cache. A black-holed URL fails in 5s, not 5 minutes.
- The rate limiter (`_check_probe_rate`) reuses the existing `_probe_times` dict keyed by `request.client.host` on self-host. The metering probe is unreachable on self-host (`reserve_process_minutes` returns early at `app.py:285-286`), so no bucket starvation.
- AuthContext fetch retry adds at most 1-2 retries with 500ms–1s backoff. Only on failure; the happy path is unchanged.
- `llmConfig_v1` is a single encrypted JSON blob, not three separate keys. One localStorage read, one write.

## Migration Notes

- `gemini_key` (plaintext) to `geminiKey_v1` (encrypted JSON `{key}`): one-way, once per browser. The initializer reads `geminiKey_v1` first (JSON canary — see D12), else adopts the legacy `gemini_key`, removes it unconditionally, and the persistence effect writes the encrypted JSON form on mount. No data-preserving localStorage migration has ever shipped (precedent `d60c376` chose forced re-entry); this is untested territory.
- `llmConfig_v1` (new encrypted blob): no migration needed. The initializer wraps `JSON.parse(decrypt(stored))` in try/catch and returns the empty triple in the catch. Every corrupt path lands in the catch by design. Persisted only when the triple is complete (`llmConfigComplete` guard) — the same guard shape as the sibling keys, and what keeps the rollback claim below true: the effect never rewrites an empty value.
- `ResultCard.jsx:284` reads `gemini_key` directly — the dead fallback is removed in Phase 2, the same phase as the migration. After migration `removeItem`s `gemini_key`, that fallback reads `null`.
- Rollback: deleting `geminiKey_v1` and `llmConfig_v1` from localStorage restores the pre-feature state. The legacy `gemini_key` is already removed by the migration.

## Developer Context

Step 4 coverage review completed (1 blocker). Step 4 code review: agent cleaned up before final-table emission — 7 findings reconstructed from its verified working notes (all grounded in grep/read against HEAD); final formatted table not emitted.

## Plan Review (Step 4)

_Independent post-finalization review by artifact-code-reviewer and artifact-coverage-reviewer subagents. Findings triaged at Step 5._

| source   | plan-loc      | codebase-loc          | severity | dimension             | finding                                                                                                           | recommendation                                                                                              | resolution         |
| -------- | ------------- | --------------------- | -------- | --------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------ |
| coverage | VN §10        | <n/a>                 | blocker  | verification-coverage | "Commit each slice at session end" (top risk — Phase 1 code was lost) has no Success Criteria bullet and no Testing Strategy step. The same loss mode can repeat with zero criteria catching it. | Add a commit-check bullet to each phase's Automated Verification; add Testing Strategy step 5: "Commit each phase at session end before starting the next." | applied: commit-check bullets + Testing Strategy step 5 |
| code     | Phase 4 §1    | App.jsx:1169-1174     | concern  | codebase-fit          | Badge anchor "1169-1174" off-by-one at HEAD: 1169 is the `<span className="hidden md:inline">` opener; the ternary is 1170-1174. Range-literal replacement deletes the span opener → orphaned `</span>` → build fails. | Correct to 1170-1174 (or content-anchor). Design follow-up: same anchor in design Architecture.              | applied (plan-local; design follow-up: design Architecture) |
| code     | Phase 4 §1    | App.jsx:1189-1193     | concern  | codebase-fit          | Banner anchor "1189-1193" wrong at HEAD both ends: 1189 is the `<span className="text-muted">` opener; actual ternary is 1190-1194. Cited range excludes line 1194 → dangling `: 'Set your Upload-Post API key...'}` → syntax error. | Correct to 1190-1194. Design follow-up: same anchor in design Architecture.                                  | applied (plan-local; design follow-up: design Architecture) |
| code     | Phase 4 §1    | App.jsx:1929-1931     | concern  | codebase-fit          | Modal title anchor "1929-1931" short: actual ternary is 1929-1933 (5 lines). Cited range omits 1932-1933 → dangling `? 'Gemini API Key Required'` / `: 'Upload-Post API Key Required'}` after the new title's `}` → syntax error. | Correct to 1929-1933. Design follow-up: same anchor in design Architecture.                                  | applied (plan-local; design follow-up: design Architecture) |
| code     | Phase 4 §1    | App.jsx:1953-1956     | concern  | codebase-fit          | Modal intro anchor "1953-1956" wrong: actual paragraph is 1952-1954. Cited range starts one line late (1953 = text line) and ends two late (1955 blank, 1956 `{/* Gemini block */}`). Range-literal replacement leaves 1952 `<p>` opener unclosed and consumes the Gemini-block comment. | Correct to 1952-1954. Design follow-up: same anchor in design Architecture.                                  | applied (plan-local; design follow-up: design Architecture) |
| code     | Phase 4 §1    | App.jsx:1959-1990     | concern  | codebase-fit          | Gemini block anchor "1959-1990" materially wrong: actual block is 1956-1982. Cited range starts INSIDE the block (1959 = icon ternary, 3rd line) and ends INSIDE the Upload-Post block (1990). Range-literal replacement eats the Upload-Post block's opening lines and leaves the Gemini block's opening div/comment duplicated. Strongest anchor defect. | Correct to 1956-1982. Design follow-up: same anchor in design Architecture.                                  | applied (plan-local; design follow-up: design Architecture) |
| code     | Phase 4 §1    | App.jsx:1608-1621     | concern  | codebase-fit          | ThumbnailStudio mount anchor "1608-1621" end wrong: actual mount is 1608-1619 (`/>` at 1619). Cited range includes `)}` at 1620 + blank 1621. Replacement block ends at `/>` → drops `)}` → the `{activeTab === 'thumbnails' && (` expression never closes → syntax error. | Correct to 1608-1619. Design follow-up: same anchor in design Architecture.                                 | applied (plan-local; design follow-up: design Architecture) |
| code     | Phase 4 §1    | App.jsx (all anchors) | concern  | actionability         | All Phase 4 numeric anchors are bare-HEAD numbers, stale after Phase 2/3 insertions (~+57 lines by Phase 4). The plan acknowledges the merge ("merges into the App.jsx fence after Phase 3") but keeps HEAD numbers. An implementer navigating by number lands ~57 lines off; content/prose descriptions are the only reliable locators. | Add a note to Phase 4's preamble: "Re-locate all numeric anchors after Phase 2/3; match content, not line numbers." Or replace numbers with content anchors. | applied: re-locate note added to Phase 4 Overview |

## References

- Design: `.rpiv/artifacts/designs/2026-09-05_06-44-25_connect-llm-provider-frontend.md`
- Research: `.rpiv/artifacts/research/2026-09-04_20-47-42_connect-llm-provider-frontend.md`
- Parent design: `.rpiv/artifacts/designs/2026-08-30_16-58-07_connect-llm-provider-frontend.md`
- Prior plan (lost code): `.rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md`
- Phase-1 handoff (reports complete but uncommitted): `.rpiv/artifacts/handoffs/2026-08-31_05-52-02_connect-llm-provider-phase1.md`
- Discover (FRD): `.rpiv/artifacts/discover/2026-09-04_10-12-16_connect-llm-provider-frontend.md`

---

## Follow-up (2026-09-05T13:18:05+0700)

**Lint-green per-phase rework (Phase 2 ↔ Phases 3/4).**

Implementing Phase 2 in isolation failed its own `cd dashboard && npm run lint` check:
five bindings the plan declared in `App.jsx` have no consumer inside Phase 2, so
`no-unused-vars` flagged all five (import at App.jsx:29; useAuth destructure at :203,
three fields; state setter at :261):

| binding                                       | first consumed by                          |
| ---------------------------------------------- | ------------------------------------------ |
| `llmHeaders` (lib import)                     | Phase 4 — `handleProcess` headers spread   |
| `llmConfigured`, `llmModel`, `llmBaseUrl` (useAuth destructure) | Phase 3 — `LlmProviderCard` mount props |
| `setLlmConfig` (state setter)                 | Phase 3 — `onConfigSet={setLlmConfig}`     |

User decision: keep every phase a lint-green commit — each binding moves to the phase
that first consumes it. The merged end state is unchanged; every downstream grep count
already described it.

- **Phase 2 `App.jsx`**: imports `llmConfigComplete` only; `llmConfig` state declared
  value-only (`const [llmConfig]`); the useAuth destructure stays untouched. Two new
  grep checks pin the deferral (`llmHeaders` == 0, `setLlmConfig` == 0).
- **Phase 3 `App.jsx`**: adds `llmConfigured/llmModel/llmBaseUrl` to the useAuth
  destructure and upgrades the state declaration to `const [llmConfig, setLlmConfig]`.
  Its pre-existing checks (`setLlmConfig` == 2, `llmConfigured` ≥ 2) already described
  exactly this end state; only the "destructure" wording was corrected to "state
  declaration".
- **Phase 4 `App.jsx`**: extends the lib import to `{ llmConfigComplete, llmHeaders }`.
  Its pre-existing check (`llmHeaders` == 2: import + spread) already counted the import.

**Resume note:** Phase 2 was implemented against the pre-revision text (uncommitted
working-tree edits on top of Phase 1's `f83d555` carry all five bindings). Resuming
`/skill:implement .rpiv/artifacts/plans/2026-09-05_12-20-26_connect-llm-provider-frontend.md Phase 2`
must TRIM: `llmHeaders` from the import, the three LLM fields from the useAuth
destructure, and `setLlmConfig` from the state declaration. The two grep-zero checks
enforce it. `lib/llm.js`, `AuthContext.jsx` and `ResultCard.jsx` landed lint-clean and
need no change.
