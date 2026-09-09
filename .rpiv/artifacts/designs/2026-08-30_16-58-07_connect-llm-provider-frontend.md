---
date: 2026-08-30T16:58:07+0700
author: Yogiswara Utama
commit: 35e9d7e
branch: main
repository: openshorts
topic: "Connect the OpenAI-compatible LLM provider backend to the dashboard frontend"
tags: [design, llm-provider, dashboard, byok, openai-compatible, frontend, settings]
status: ready
parent: .rpiv/artifacts/research/2026-08-30_16-03-23_connect-llm-provider-frontend.md
last_updated: 2026-08-30T16:58:07+0700
last_updated_by: Yogiswara Utama
---

# Design: Connect the OpenAI-compatible LLM provider to the dashboard

## Summary

A new **AI Provider** card in self-host Settings collects an OpenAI-compatible endpoint URL,
API key and model, stores them as one encrypted blob in the browser, and sends them as
`X-LLM-Base-Url` / `X-LLM-Key` / `X-LLM-Model` headers built per component — the same shape
`X-Gemini-Key` already uses. `GET /api/config` gains three read-only fields so the dashboard
can see a server-side `LLM_*` setup and stop demanding a Gemini key. A new
`POST /api/llm/test` drives a "Test connection" button.

The app's front door widens from "has a Gemini key" to "has any AI backend". Features that
physically require Gemini (thumbnail image generation) stay available but carry an inline
note instead of a global block.

## Requirements

- Collect endpoint URL + API key + model in the dashboard and send them as the `X-LLM-*` header triple.
- Report a server-side `LLM_*` configuration to the UI so no key input is demanded when the server already has one.
- Unblock the app for a user who has only an OpenAI-compatible provider and no Gemini key.
- Label the genuinely Gemini-only action (thumbnail image generation) inline rather than blocking the whole tab.
- Refuse to save a half-configured provider — the backend goes silently inert otherwise.
- Provide quick-fill buttons for common providers (Ollama Cloud, local Ollama, OpenRouter).
- Provide a "Test connection" button that validates a provider without running a real job.
- Move the Gemini key from plaintext `localStorage` to the same obfuscated storage the other keys use.
- The entire provider surface must be absent under `BILLING_ENABLED`.

## Current State Analysis

The backend (commits `30ce67e` → `35e9d7e`) is complete: every reroutable call site already
runs through `llm_client.chat()`, and `resolve_llm` (`app.py:143-169`) accepts the header
triple. The frontend has **zero** LLM-provider code — a `grep` for `X-LLM`, `llmConfig`,
`baseUrl` and `base_url` across `dashboard/src/` returns nothing. This is greenfield UI work
against a finished contract.

### Key Discoveries

- **`config_from` requires a model, not just the pair** (`llm_client.py:150-178`). Base URL +
  key with no model anywhere resolves to `None`. The research artifact described this as pair
  validation; it is a **triple**. This drove the "all three required" form rule.
- **The 402 / `QuotaError` mis-render risk does not exist.** `LlmError` is a plain `Exception`
  (`llm_client.py:96-99`), and the endpoints catch it as
  `except Exception as e: raise HTTPException(status_code=500, detail=str(e))`
  (`app.py:4979-4981`). A provider's own 402 becomes an OpenShorts **500 with a string
  `detail`**. `apiFetch`'s 402 branch (`lib/api.js:32`) never fires, and `apiJson` populates
  `ApiError.detail` for string details. **No new error-handling code is needed.**
- **`/api/config` has two consumers, not one.** `AuthContext.jsx:104` and — missed by the
  research — `MediaInput.jsx:52`, which reads `youtubeUrlEnabled` directly. `MediaInput` is
  additive-safe and needs no change.
- **`ResultCard.jsx:284`'s `localStorage.getItem('gemini_key')` fallback is dead code.**
  `App.jsx` always passes `geminiApiKey={apiKey}`, and `apiKey` itself initialises from that
  same key, so the fallback can only fire when both are empty. The storage migration would
  otherwise strand it.
- **`llm_client._clients` is an unbounded per-base-URL `httpx.Client` cache with a 300s read
  timeout** (`llm_client.py:130-147`). Its own comment says "fine while base_urls are
  operator-set". A Test-connection button must not use it: a black-holed URL would hang the
  browser for five minutes, and every probed URL would be cached forever.
- **`ThumbnailStudio.jsx:507` hard-gates the entire step-0 grid** with
  `opacity-50 pointer-events-none select-none`, and the three handlers `alert()` at `:155`,
  `:268`, `:347`. This is the structure that has to become capability-aware.
- **`POST /api/thumbnail/generate` is the one inverted gate** (`app.py:5003-5006`):
  `resolve_gemini` first, hard fail, `resolve_llm` only afterwards. Image generation is
  Gemini-only by physics.
- **Storage convention**: `encrypt`/`decrypt` (`App.jsx:35-62`) are XOR + base64 with an `ENC:`
  prefix, used under versioned names `uploadPostKey_v3`, `elevenLabsKey_v1`, `falKey_v1`.
  `gemini_key` alone is plaintext (`App.jsx:211,574`).
- **Self-host has no authentication.** `get_current_user_optional` is a cloud-only import with
  a stub fallback (`app.py:106-121`). This ruled out a save-to-server config endpoint.

## Scope

### Building

- `llm_client.probe(config)` — one short-timeout, non-cached live call reusing `_post`'s error mapping.
- `GET /api/config` gains `llmConfigured`, `llmModel`, `llmBaseUrl`.
- `POST /api/llm/test` — self-host-only connection check.
- `AuthContext` exposes the three new config fields through `useAuth()`.
- `dashboard/src/components/LlmProviderCard.jsx` — the Settings card: three fields, quick-fill presets, triple validation, Test connection, server-status display.
- `App.jsx` — `llmConfig` state under `llmConfig_v1`, `gemini_key` → `geminiKey_v1` migration, `X-LLM-*` headers on `/api/process`, widened `keysMissing`, updated banner / header badge / required-keys modal copy.
- `ThumbnailStudio.jsx` and `SaaShortsTab.jsx` — `llmHeaders` alongside the existing Gemini header, `needsAiBackend` replacing the Gemini-only flag, and a generate-step-only Gemini note in the Studio.
- `tests/test_llm_client.py` — pin `probe`, the `/api/config` fields and `/api/llm/test`.

### Not Building

- **Server-side persistence of provider config.** Considered and explicitly cancelled by the
  developer: self-host has no auth, so a write endpoint would let anyone reachable redirect
  the install's AI traffic. Browser + headers only, accepting the same resume-loss
  `X-Gemini-Key` has.
- **Centralising BYOK headers in `apiFetch`.** Would send the provider key on uploads and
  social posting. Per-component construction is the house style and the developer's decision.
- **`ResultCard.jsx` beyond one dead line.** Its six endpoints are all Gemini-pinned class C.
- **Any cloud/billing provider surface.** `resolve_llm` returns `None` under `BILLING_ENABLED`
  (`app.py:158`) and a prefix sweep strips `LLM_*` from managed job envs (`app.py:2250`).
- **Fixing the unbounded `_clients` cache.** Pre-existing, already reachable via the six live
  endpoints, and orthogonal to this feature.
- **Reordering `cloud/alerts.py::_classify_failure`.** Corrected twice in production
  (`77905a9`, `730f7de`); the `"LLM provider"` prefix strings stay verbatim.
- **Rewording any `llm_client` error message.** `"LLM provider"` is a reserved,
  alert-classifying string (`llm_client.py:74`).

## Decisions

### D1: The provider surface is browser-stored and header-carried, not server-persisted

**Ambiguity**: The developer initially asked for the provider config to be settable as server
env from the frontend, which would survive restarts and mid-job resumes.

**Explored**:
- **Option A — save to server behind an opt-in env flag.** Fixes resume-loss. But nothing in
  the codebase writes `.env` or mutates `os.environ` at runtime (`load_dotenv()` runs once at
  `app.py:32`), there is no settings-write endpoint precedent, and self-host has no auth
  (`app.py:106-121`), so the endpoint would be world-writable on any reachable install.
- **Option B — browser + headers**, matching `X-Gemini-Key`.

**Decision**: **Option B.** The developer cancelled the server-save direction once the
no-authentication consequence was stated. Resume-loss is accepted precedent (`900dc44`).

### D2: All three form fields are required; Save is disabled otherwise

**Ambiguity**: `config_from` needs base + key + a resolved model. The model can come from the
server's `LLM_MODEL` env, so the UI *could* make the field conditionally optional.

**Explored**:
- **Option A — always require all three.** The silent-inert state (`tests/test_llm_client.py:442`)
  is unreachable from the UI. Costs one retyped model on servers that already have one.
- **Option B — require the model only when `/api/config` reports the server has none.** Fewer
  fields, but the rule changes with server state and a stale `/api/config` read produces exactly
  the silent failure the validation exists to prevent.

**Decision**: **Option A.** Chosen by the developer.

### D3: `/api/config` reports configured-state, model and base URL — never the key

**Ambiguity**: The endpoint is served pre-auth. `LlmConfig` marks `api_key` as `repr=False`
(`llm_client.py:90-93`, pinned by `tests/test_llm_client.py:485-488`), but model and base URL
carry no such protection.

**Decision**: Report `llmConfigured`, `llmModel` and `llmBaseUrl`. Chosen by the developer for
debuggability — seeing the live endpoint and model at a glance catches a wrong-env setup. The
key is never included. Under `BILLING_ENABLED` all three are forced to `False`/`None`, since
`resolve_llm` returns `None` there regardless of env.

### D4: YouTube Studio stays usable on an LLM-only setup; only image generation is labelled

**Decision**: `ThumbnailStudio.jsx:507`'s whole-step gate is re-pointed at
`needsAiBackend = !geminiApiKey && !llmActive && !managed`. `handleGenerate` (`:268`) keeps a
Gemini-specific check, and the generate button carries an inline note. Chosen by the developer
from a rendered comparison. Matches research Developer Context Q2: "let them in, and label what
needs Gemini".

### D5: The Settings card is its own component file

`App.jsx` is 2093 lines and the three existing BYOK cards are inline. This card is larger than
any of them (three inputs, validation, a probe call, a status block), and `KeyInput.jsx` and
`McpConnectCard.jsx` are the established precedent for a self-contained Settings card. Decided
directly — the developer confirmed this is an implementation choice, not a product one.

### D6: `probe()` uses a one-off short-timeout client, not `_http_client`

`_clients` is an unbounded cache and `_TIMEOUT` reads for 300s (`llm_client.py:130-147`). A
test button on those settings would hang the browser for five minutes on a black-holed URL and
grow the cache per probed URL. `probe()` constructs its own client with a short timeout and
closes it, while reusing `_post` so the status→error mapping stays identical to a real call.

### D7: No new error-handling code

Verified against runtime behaviour rather than the research's inference: `LlmError` reaches the
client as `HTTPException(500, detail=str(e))` (`app.py:4979-4981`), `apiJson` carries string
details (`lib/api.js:56-58`), and `ThumbnailStudio.jsx:293` / `SaaShortsTab.jsx:229` already
read `detail`. Self-host job errors reach the log pane raw. The `"LLM provider ..."` text
already arrives intact.

### D8: State lives in `App.jsx` and travels as props

`llmConfig` sits next to `apiKey`, `elevenLabsKey` and `falKey`, persisted by the same effect
shape, and is passed to `ThumbnailStudio` and `SaaShortsTab` as props — identical to every
other key in this codebase.

### D9: `X-LLM-Model` is omitted, never sent empty

`tests/test_llm_client.py:707` pins that a whitespace-only model falls back to the env chain.
The header is only added when the model is non-empty.

## Architecture

### llm_client.py — MODIFY

Add a `probe()` helper and its short timeout. Reuses `_post` so the error mapping is identical to a real call.

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


# --- inside chat(), replacing the inline guard at llm_client.py:363-374 ---------

    base_url = _require_usable(config)


# --- appended after chat() ------------------------------------------------------

def probe(config: LlmConfig) -> None:
    """One minimal live call, to verify an endpoint before a real job runs.

    Raises exactly what chat() would (LlmError / LlmTransientError /
    GeminiBlockedError), so a green result means the next job resolves the
    same way. _post carries the whole status->error mapping, so the message a
    user sees here is byte-identical to the one a failing job would produce."""
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

### app.py — MODIFY

`/api/config` gains the three capability fields; a new self-host-only `POST /api/llm/test` drives the Test connection button.

```python
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
        return None          # cloud is Gemini-pinned, exactly like resolve_llm
    try:
        import llm_client
    except Exception:
        return None          # guarded like resolve_llm's import: gate, not 500
    for task in ("thumbnail", "saas"):
        cfg = llm_client.active_config(task)
        if cfg is not None:
            return cfg
    return None


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


@app.post("/api/llm/test")
async def llm_test(request: Request):
    """Self-host only: one minimal live call against the resolved provider.

    Resolves exactly like a real request — the BYOK header triple when both
    base and key are present, else the server's LLM_* env — so a green result
    means the next job talks to that same endpoint. Shares the metadata-probe
    limiter, keyed by client host because self-host has no user model."""
    if BILLING_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    _check_probe_rate(request.client.host if request.client else "anon")
    cfg = await resolve_llm(request)
    if cfg is None:
        raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
    import llm_client
    started = time.monotonic()
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, llm_client.probe, cfg)
    except Exception as e:
        # Same shape every other LLM-aware endpoint uses: a non-2xx with the
        # provider's own "LLM provider ..." text as a string detail, which
        # apiJson surfaces verbatim. Never a 402 — that is apiFetch's
        # QuotaError branch and would render as an OpenShorts top-up prompt.
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True, "model": cfg.model,
            "latencyMs": int((time.monotonic() - started) * 1000)}
```

### tests/test_llm_client.py — MODIFY

Pin `probe()`, the `/api/config` fields and the `/api/llm/test` contract.

```python
# --- probe() (connection test) --------------------------------------------------
# Monkeypatches _probe_client, mirroring how this file already stands in for
# _http_client. No server, no network.

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
```

### tests/test_llm_endpoints.py — NEW

The HTTP-level contract. Split from `test_llm_client.py`, which is deliberately app-free (`httpx.MockTransport` only) — these need the real `app` object.

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
    # The probe limiter is a module global shared with the YouTube metadata
    # probe; 15/hour would otherwise leak across tests in one process.
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
        # A server with only LLM_MODEL_THUMBNAIL is a working setup; reporting
        # it as unconfigured would make the dashboard demand a key it does not
        # need. Also pins the task order that keeps llm_client's
        # "stays inactive" warning from firing on a healthy server.
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL_THUMBNAIL", "thumb-model")
        cfg = client.get("/api/config").json()
        assert cfg["llmConfigured"] is True
        assert cfg["llmModel"] == "thumb-model"

    def test_half_configured_env_reports_unconfigured(self, client, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")   # no model anywhere
        assert client.get("/api/config").json()["llmConfigured"] is False

    def test_existing_fields_are_unchanged(self, client):
        # MediaInput.jsx:52 reads this endpoint independently of AuthContext.
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
```

### dashboard/src/contexts/AuthContext.jsx — MODIFY

Expose the three new `/api/config` fields through `useAuth()`, following the `jobRetentionSeconds` precedent (`d0e1f5a`).

```jsx
// --- in the `value` object (AuthContext.jsx:140-152), after jobRetentionSeconds ---

    jobRetentionSeconds: config.jobRetentionSeconds || null,
    // Self-host only: the server's own LLM_* setup, so Settings can say
    // "configured on the server" instead of demanding a key. Never carries the
    // API key — /api/config reports state and identity only. Under billing the
    // backend forces these off, since resolve_llm returns None there.
    llmConfigured: !!config.llmConfigured,
    llmModel: config.llmModel || null,
    llmBaseUrl: config.llmBaseUrl || null,
```

### dashboard/src/App.jsx — MODIFY

`llmConfig` state + persistence, the Gemini key migration, `X-LLM-*` headers on `/api/process`, the widened front-door gate, updated copy, and the new card mount + child props.

```jsx
// --- the useAuth destructure (App.jsx:201) ---------------------------------------

  const { billingEnabled, isManaged, isSignedIn, me, plan, refreshMe,
    jobRetentionSeconds, llmConfigured, llmModel, llmBaseUrl } = useAuth();


// --- replaces the plaintext gemini_key state (App.jsx:211) -----------------------

  // Migrated to the ENC: convention its three siblings already use. One-way and
  // once per browser: read the encrypted form, else adopt the legacy plaintext
  // value and rewrite it. ResultCard's direct localStorage read of the old key
  // is removed in the same slice.
  const [apiKey, setApiKey] = useState(() => {
    const stored = localStorage.getItem('geminiKey_v1');
    if (stored) return decrypt(stored);
    const legacy = localStorage.getItem('gemini_key');
    if (legacy) {
      localStorage.setItem('geminiKey_v1', encrypt(legacy));
      localStorage.removeItem('gemini_key');
      return legacy;
    }
    return '';
  });


// --- new, beside the falKey state (App.jsx:222-226) ------------------------------

  // OpenAI-compatible provider, self-host BYOK. One blob keeps the triple
  // atomic: llm_client ignores base+key unless a model resolves too
  // (llm_client.py:150-178), so the three values are only ever meaningful
  // together.
  const [llmConfig, setLlmConfig] = useState(() => {
    const stored = localStorage.getItem('llmConfig_v1');
    if (!stored) return { baseUrl: '', apiKey: '', model: '' };
    try {
      const { baseUrl = '', apiKey = '', model = '' } = JSON.parse(decrypt(stored));
      return { baseUrl, apiKey, model };
    } catch (e) {
      return { baseUrl: '', apiKey: '', model: '' };
    }
  });


// --- replaces the gemini_key persistence effect (App.jsx:572-575) ----------------

  useEffect(() => {
    if (apiKey) localStorage.setItem('geminiKey_v1', encrypt(apiKey));
  }, [apiKey]);

  // Unlike its three siblings this one also CLEARS: emptying the fields in
  // Settings has to actually remove the stored provider, or "clear" would
  // silently leave the old endpoint in place until the next reload.
  useEffect(() => {
    if (llmConfig.baseUrl || llmConfig.apiKey || llmConfig.model) {
      localStorage.setItem('llmConfig_v1', encrypt(JSON.stringify(llmConfig)));
    } else {
      localStorage.removeItem('llmConfig_v1');
    }
  }, [llmConfig]);


// --- new import, beside the other component imports (App.jsx:3-27) --------------

import LlmProviderCard from './components/LlmProviderCard';


// --- in the self-host Settings branch, right after <KeyInput> (App.jsx:1294) -----

              <KeyInput onKeySet={setApiKey} savedKey={apiKey} />

              <LlmProviderCard
                config={llmConfig}
                onSave={setLlmConfig}
                serverConfigured={llmConfigured}
                serverModel={llmModel}
                serverBaseUrl={llmBaseUrl}
              />


// --- second new import, beside the lib/api import (App.jsx:29) ------------------

import { llmHeaders, llmConfigComplete } from './lib/llm';


// --- replaces the keysMissing gate (App.jsx:708) ---------------------------------

  // "has some AI backend": a Gemini key, a browser-configured OpenAI-compatible
  // provider, or one in the server's LLM_* env. Upload-Post stays separately
  // required. The !billingEnabled guard is preserved verbatim — 05578c5 made this
  // gate self-host-only and widening it must not reintroduce it in cloud.
  const llmActive = llmConfigComplete(llmConfig) || llmConfigured;
  const aiBackendMissing = !apiKey && !llmActive;
  const keysMissing = !billingEnabled && (aiBackendMissing || !uploadPostKey);


// --- header construction in handleProcess (App.jsx:792) --------------------------

      // BYOK sends the Gemini header and/or the OpenAI-compatible triple; managed
      // users rely on the bearer token apiFetch attaches automatically. Both may
      // travel together: the backend prefers the LLM path per capability class and
      // falls back to Gemini for video understanding.
      const headers = {
        ...(apiKey ? { 'X-Gemini-Key': apiKey } : {}),
        ...llmHeaders(llmConfig),
      };


// --- header warning badge copy (App.jsx:~1084-1090) ------------------------------

                <span className="hidden md:inline">
                  {aiBackendMissing && !uploadPostKey
                    ? 'AI backend & Upload-Post key missing'
                    : aiBackendMissing
                      ? 'AI backend missing'
                      : 'Upload-Post API Key Missing'}
                </span>


// --- standing banner copy (App.jsx:~1104-1110) -----------------------------------

                <span className="text-muted">
                  {aiBackendMissing && !uploadPostKey
                    ? 'Set a Gemini key or an OpenAI-compatible provider, plus your Upload-Post API key, to use OpenShorts.'
                    : aiBackendMissing
                      ? 'Set a Gemini key or an OpenAI-compatible provider to use OpenShorts.'
                      : 'Set your Upload-Post API key to use OpenShorts.'}
                </span>


// --- required-keys modal title (App.jsx:~1932-1936) ------------------------------

        title={aiBackendMissing && !uploadPostKey
          ? 'Required API Keys Missing'
          : aiBackendMissing
            ? 'AI Backend Required'
            : 'Upload-Post API Key Required'}


// --- required-keys modal intro + first block (App.jsx:~1955-1990) ----------------

          <p className="text-sm text-muted">
            OpenShorts needs an <strong className="text-ink2">AI backend</strong> — a Gemini API key or
            an OpenAI-compatible endpoint — and an <strong className="text-ink2">Upload-Post</strong> API
            key for publishing. Both have free options.
          </p>

          {/* AI backend block: satisfied by a Gemini key OR a configured provider */}
          <div className={`rounded-input p-4 space-y-2 border ${aiBackendMissing ? 'border-rule2' : 'border-rule opacity-70'}`}>
            <p className="text-xs font-medium text-ink flex items-center gap-2">
              {aiBackendMissing
                ? <AlertTriangle size={12} className="text-warn" />
                : <Check size={12} className="text-ok" />}
              AI backend
              {!aiBackendMissing && (
                <span className="text-ok">— {apiKey ? 'Gemini key set' : 'provider set'}</span>
              )}
            </p>
            {aiBackendMissing && (
              <>
                <ol className="text-xs text-muted space-y-1 list-decimal list-inside">
                  <li>Go to <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" className="text-brass underline">aistudio.google.com/app/apikey</a></li>
                  <li>Sign in with your Google account</li>
                  <li>Click &quot;Create API Key&quot;</li>
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
                  Or use any OpenAI-compatible endpoint instead — set it up under{' '}
                  <button
                    onClick={() => { setShowKeyModal(false); setActiveTab('settings'); }}
                    className="text-brass underline"
                  >
                    Settings → AI Provider
                  </button>.
                </p>
              </>
            )}
          </div>
```

### dashboard/src/components/ResultCard.jsx:284 — MODIFY

Drop the dead `localStorage.getItem('gemini_key')` fallback stranded by the storage migration.

```jsx
// --- inside handleAutoEdit (ResultCard.jsx:284) ----------------------------------

            const apiKey = geminiApiKey;
```

### dashboard/src/lib/llm.js — NEW

The `X-LLM-*` header rule, in one place. Four call sites need it (`LlmProviderCard`, `/api/process`, `ThumbnailStudio`, `SaaShortsTab`) and it encodes two pinned backend constraints that are easy to get subtly wrong. Not a centralisation of BYOK into `apiFetch` — each component still attaches its own headers.

```js
// The X-LLM-* header triple, built from the browser-stored provider config.
//
// Deliberately NOT attached inside apiFetch: that would send the provider key
// on uploads and social posting too. Each component builds its own headers next
// to its existing X-Gemini-Key and shares only this rule, so the two backend
// constraints below cannot drift across the four call sites:
//   - base URL and key travel together or not at all — llm_client.config_from
//     returns None for either alone (tests/test_llm_client.py:425), and a header
//     key must never reach an env-configured base_url (app.py:145-147).
//   - a blank model is OMITTED, never sent empty: a whitespace-only X-LLM-Model
//     falls back to the server's env chain (tests/test_llm_client.py:707).
export function llmHeaders(config) {
  const baseUrl = (config?.baseUrl || '').trim();
  const apiKey = (config?.apiKey || '').trim();
  if (!baseUrl || !apiKey) return {};
  const model = (config?.model || '').trim();
  return {
    'X-LLM-Base-Url': baseUrl,
    'X-LLM-Key': apiKey,
    ...(model ? { 'X-LLM-Model': model } : {}),
  };
}

// The browser-side twin of llm_client.config_from: all three values must be
// present for the backend to actually switch. base+key with no model anywhere
// is inert rather than an error (tests/test_llm_client.py:442), which is the
// silent failure the Save gate exists to prevent.
export const llmConfigComplete = (config) =>
  !!(config?.baseUrl?.trim() && config?.apiKey?.trim() && config?.model?.trim());
```

### dashboard/src/components/LlmProviderCard.jsx — NEW

The Settings card: quick-fill presets, three required fields, triple validation, Test connection, and the server-configured status block. Markup follows the ElevenLabs / fal.ai cards in `App.jsx` so it reads as native.

```jsx
import React, { useState } from 'react';
import { Bot, Check, Eye, EyeOff, Loader2, AlertTriangle, CheckCircle2, XCircle } from 'lucide-react';
import { apiFetch } from '../lib/api';
import { llmHeaders, llmConfigComplete } from '../lib/llm';

const EMPTY = { baseUrl: '', apiKey: '', model: '' };

// The endpoints people actually use. Only the base URL is filled — a model name
// is provider- and account-specific, so guessing one would produce exactly the
// half-configured state the Save gate exists to prevent.
const PRESETS = [
  { label: 'ollama cloud', baseUrl: 'https://ollama.com/v1' },
  { label: 'local ollama', baseUrl: 'http://localhost:11434/v1' },
  { label: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1' },
];

export default function LlmProviderCard({ config, onSave, serverConfigured = false,
                                          serverModel = null, serverBaseUrl = null }) {
  const [draft, setDraft] = useState(config);
  const [visible, setVisible] = useState(false);
  const [saved, setSaved] = useState(false);
  const [test, setTest] = useState(null);   // null | {busy} | {ok,...} | {error}
  // A server-configured install needs no input, so the form starts collapsed
  // behind the status block — unless this browser already overrides it.
  const [showForm, setShowForm] = useState(!serverConfigured || llmConfigComplete(config));

  const complete = llmConfigComplete(draft);
  const dirty = draft.baseUrl !== config.baseUrl
    || draft.apiKey !== config.apiKey
    || draft.model !== config.model;

  const set = (field) => (e) => {
    setDraft((d) => ({ ...d, [field]: e.target.value }));
    setTest(null);
    setSaved(false);
  };

  const handleSave = () => {
    onSave(draft);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleClear = () => {
    setDraft(EMPTY);
    onSave(EMPTY);
    setTest(null);
  };

  // Tests the DRAFT, not the saved config, so an endpoint can be checked before
  // it is committed. The backend resolves it exactly as a real job would.
  const runTest = async () => {
    setTest({ busy: true });
    try {
      const res = await apiFetch('/api/llm/test', {
        method: 'POST',
        headers: llmHeaders(draft),
      });
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).detail; } catch (e) { /* non-JSON body */ }
        throw new Error(detail || `Server error ${res.status}`);
      }
      const data = await res.json();
      setTest({ ok: true, model: data.model, latencyMs: data.latencyMs });
    } catch (e) {
      setTest({ error: e.message });
    }
  };

  return (
    <div className="card p-4 sm:p-6 mt-8">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-input bg-paper3 flex items-center justify-center shrink-0">
            <Bot size={16} className="text-brass" />
          </div>
          <h2 className="text-base font-medium text-ink lowercase">AI Provider</h2>
        </div>
        <span className="readout">BYOK</span>
      </div>
      <p className="text-xs text-muted mb-6 leading-relaxed">
        Use any <strong>OpenAI-compatible</strong> endpoint (Ollama, OpenRouter, vLLM, …) instead of
        Gemini for clip scoring, titles, descriptions and AI Shorts. Video understanding and AI
        thumbnail images stay on Gemini — those still need a Gemini key.
      </p>

      {serverConfigured && (
        <div className="px-3.5 py-3 mb-6 rounded-input bg-paper3 border border-rule text-sm">
          <p className="flex items-center gap-2 text-ink">
            <CheckCircle2 size={14} className="text-ok shrink-0" /> Configured on the server
          </p>
          <p className="mt-1 font-mono text-xs text-muted break-all">{serverBaseUrl}</p>
          <p className="font-mono text-xs text-muted">model: {serverModel}</p>
          <p className="mt-2 text-xs text-muted">
            Your server&apos;s environment provides the connection — nothing to enter here.
          </p>
          {!showForm && (
            <button onClick={() => setShowForm(true)} className="btn-quiet py-1.5 px-3 text-xs mt-3">
              override with my own endpoint
            </button>
          )}
        </div>
      )}

      {showForm && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="readout">quick fill</span>
            {PRESETS.map((p) => (
              <button
                key={p.baseUrl}
                onClick={() => {
                  setDraft((d) => ({ ...d, baseUrl: p.baseUrl }));
                  setTest(null);
                  setSaved(false);
                }}
                className="btn-ghost py-1 px-2.5 text-xs lowercase"
              >
                {p.label}
              </button>
            ))}
          </div>

          <div>
            <label className="block text-sm text-muted mb-2">Endpoint URL</label>
            <input
              type="text"
              value={draft.baseUrl}
              onChange={set('baseUrl')}
              className="input-field font-mono"
              placeholder="https://ollama.com/v1"
            />
          </div>

          <div>
            <label className="block text-sm text-muted mb-2">API Key</label>
            <div className="relative">
              <input
                type={visible ? 'text' : 'password'}
                value={draft.apiKey}
                onChange={set('apiKey')}
                className="input-field pr-12 font-mono"
                placeholder="sk-…"
              />
              <button
                onClick={() => setVisible(!visible)}
                aria-label={visible ? 'hide key' : 'show key'}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-ink transition-colors"
              >
                {visible ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </div>

          <div>
            <label className="block text-sm text-muted mb-2">Model</label>
            <input
              type="text"
              value={draft.model}
              onChange={set('model')}
              className="input-field font-mono"
              placeholder="gpt-oss:120b"
            />
            {!draft.model.trim() && (draft.baseUrl.trim() || draft.apiKey.trim()) && (
              <p className="mt-2 text-xs text-warn flex items-start gap-1.5">
                <AlertTriangle size={12} className="shrink-0 mt-0.5" />
                Required — without a model the provider stays inactive and Gemini keeps being used.
              </p>
            )}
          </div>

          <div className="flex flex-col sm:flex-row gap-2">
            <button
              onClick={handleSave}
              disabled={!complete || !dirty}
              className={saved ? 'badge-ok px-4' : 'btn-primary py-2 px-4 text-sm'}
            >
              {saved ? <><Check size={12} /> saved</> : 'Save'}
            </button>
            <button
              onClick={runTest}
              disabled={!complete || !!test?.busy}
              className="btn-quiet py-2 px-4 text-sm"
            >
              {test?.busy
                ? <><Loader2 size={14} className="animate-spin" /> testing…</>
                : 'Test connection'}
            </button>
            {(config.baseUrl || config.apiKey || config.model) && (
              <button onClick={handleClear} className="btn-ghost py-2 px-4 text-sm sm:ml-auto">
                Clear
              </button>
            )}
          </div>

          {test?.ok && (
            <p className="text-xs text-ok flex items-start gap-1.5">
              <CheckCircle2 size={12} className="shrink-0 mt-0.5" />
              Responded in {test.latencyMs}ms using {test.model}.
            </p>
          )}
          {test?.error && (
            <p className="text-xs text-danger flex items-start gap-1.5">
              <XCircle size={12} className="shrink-0 mt-0.5" />
              <span className="break-words">{test.error}</span>
            </p>
          )}

          <div className="text-xs text-muted leading-relaxed">
            The endpoint must accept <span className="font-mono">POST /chat/completions</span> in the
            OpenAI format. Test connection sends one tiny request so you can check it before running
            a job.
            <br /><br />
            <span className="text-muted">
              Keys are only stored in your browser. They are sent to the backend only to process your
              request, never stored server-side — so a job that resumes after a backend restart falls
              back to Gemini.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
```

### dashboard/src/components/ThumbnailStudio.jsx — MODIFY

`llmHeaders` alongside `keyHeader`; `needsAiBackend` replaces `needsKey` on the whole-step gate; a Gemini-only note on the generate step.

```jsx
// --- new import, beside the lib/api import (ThumbnailStudio.jsx:4) ---------------

import { llmHeaders, llmConfigComplete } from '../lib/llm';


// --- replaces the prop signature and flags (ThumbnailStudio.jsx:73-77) -----------

export default function ThumbnailStudio({ geminiApiKey, llmConfig = null,
                                          llmConfigured = false, uploadPostKey,
                                          uploadUserId, managed = false,
                                          onCreateClips = null }) {
  // Managed (hosted plan): Gemini runs server-side via the bearer token, no BYOK
  // key. Only self-host sends BYOK headers. Both backends may travel together —
  // the server picks per capability class. Renamed from keyHeader: it is no
  // longer only the Gemini key.
  const aiHeaders = {
    ...(geminiApiKey ? { 'X-Gemini-Key': geminiApiKey } : {}),
    ...llmHeaders(llmConfig),
  };
  const llmActive = llmConfigComplete(llmConfig) || llmConfigured;
  // Titles, analysis and describe accept either backend (app.py:4819, 4924, 5136).
  const needsAiBackend = !geminiApiKey && !llmActive && !managed;
  // Image generation is Gemini-only by physics: a chat-completions endpoint
  // cannot render an image, so /api/thumbnail/generate hard-requires a Gemini key
  // before it even looks at the provider (app.py:5003-5006).
  const needsGeminiForImages = !geminiApiKey && !managed;


// --- the four header call sites: keyHeader -> aiHeaders --------------------------
// :172  headers: aiHeaders                       (/api/thumbnail/analyze)
// :218  ...aiHeaders                             (/api/thumbnail/titles, refine)
// :241  ...aiHeaders                             (/api/thumbnail/titles)
// :288  headers: aiHeaders                       (/api/thumbnail/generate)
// :358  ...aiHeaders                             (/api/thumbnail/describe)


// --- handler guards (ThumbnailStudio.jsx:155, :268, :347) ------------------------

  // handleAnalyze (:155)
    if (needsAiBackend) return alert('Set a Gemini API key or an AI provider in Settings first.');

  // handleGenerate (:268) — the one action a provider cannot serve
    if (needsGeminiForImages) return alert('AI thumbnail generation needs a Gemini API key. Add one in Settings.');

  // handleGenerateDescription (:347)
    if (needsAiBackend) return alert('Set a Gemini API key or an AI provider in Settings first.');


// --- the standing warning card (ThumbnailStudio.jsx:494-503) ---------------------

        {/* Self-host BYOK only; managed uses the server key. Satisfied by either
            a Gemini key or an OpenAI-compatible provider. */}
        {needsAiBackend && (
          <div className="mb-6 p-5 bg-warn/10 rounded-card flex items-start gap-3">
            <AlertCircle size={18} className="text-warn shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-warn lowercase">AI backend required</p>
              <p className="text-xs text-muted mt-1">YouTube Studio needs either a Google Gemini API key or an OpenAI-compatible provider. Configure one in the <strong>Settings</strong> tab. Gemini&apos;s free tier includes 1,500 requests per day.</p>
            </div>
          </div>
        )}


// --- the step-0 gate (ThumbnailStudio.jsx:507) -----------------------------------

          <div className={`grid md:grid-cols-2 gap-6 ${needsAiBackend ? 'opacity-50 pointer-events-none select-none' : ''}`}>


// --- the generate step: note + both buttons (ThumbnailStudio.jsx:845-852, :935-940) ---

              {needsGeminiForImages && (
                <div className="mb-3 px-3.5 py-3 rounded-input bg-warn/10 flex items-start gap-2.5 text-sm">
                  <AlertCircle size={16} className="text-warn shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <p className="text-ink">AI image generation needs a Gemini API key.</p>
                    <p className="text-xs text-muted mt-0.5">
                      Your OpenAI-compatible provider handles text only — titles, analysis and
                      descriptions on this page already work. Add a Gemini key in{' '}
                      <strong>Settings</strong> to generate thumbnails.
                    </p>
                  </div>
                </div>
              )}

              <button
                onClick={handleGenerate}
                disabled={isGenerating || needsGeminiForImages}
                className="w-full btn-primary"
              >

  // and the Regenerate button at :935-940
                  <button
                    onClick={handleGenerate}
                    disabled={isGenerating || needsGeminiForImages}
                    className="w-full btn-ghost"
                  >
```

### dashboard/src/components/SaaShortsTab.jsx — MODIFY

`llmHeaders` alongside `geminiHeader`; `needsAiBackend` replaces `needsGeminiKey`.

```jsx
// --- new import, beside the lib/api import (SaaShortsTab.jsx:4) ------------------

import { llmHeaders, llmConfigComplete } from '../lib/llm';


// --- replaces the prop signature and flags (SaaShortsTab.jsx:43-47) --------------

export default function SaaShortsTab({ geminiApiKey, llmConfig = null,
                                       llmConfigured = false, elevenLabsKey, falKey,
                                       uploadPostKey, uploadUserId, managed = false }) {
  // Managed (hosted plan): Gemini (script) + Upload-Post run server-side via the
  // bearer token — no BYOK Gemini key needed. fal.ai + ElevenLabs stay BYOK.
  // /api/saasshorts/analyze accepts either backend (app.py:5340), so the whole
  // gate widens; renamed from geminiHeader for the same reason.
  const aiHeaders = {
    ...(geminiApiKey ? { 'X-Gemini-Key': geminiApiKey } : {}),
    ...llmHeaders(llmConfig),
  };
  const needsAiBackend = !geminiApiKey && !llmConfigComplete(llmConfig)
    && !llmConfigured && !managed;


// --- the analyze guard (SaaShortsTab.jsx:203-206) --------------------------------

    if (needsAiBackend) {
      setAnalyzeError('Set a Gemini API key or an AI provider in Settings.');
      return;
    }


// --- the analyze call site (SaaShortsTab.jsx:212-217) ----------------------------

      const res = await apiFetch('/api/saasshorts/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...aiHeaders,
        },
```

### dashboard/src/App.jsx (Slice 5 contribution) — MODIFY

Prop wiring for the two components. Moved here from Slice 4 so the props and their readers land together.

```jsx
// --- the SaaShortsTab mount (App.jsx:~1409) --------------------------------------

            <SaaShortsTab geminiApiKey={apiKey} llmConfig={llmConfig} llmConfigured={llmConfigured}
              elevenLabsKey={elevenLabsKey} falKey={falKey} uploadPostKey={uploadPostKey}
              uploadUserId={uploadUserId} managed={isManaged} />


// --- the ThumbnailStudio mount (App.jsx:~1560) -----------------------------------

            <ThumbnailStudio
              geminiApiKey={apiKey}
              llmConfig={llmConfig}
              llmConfigured={llmConfigured}
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
```

## Slices

### Slice 1: Backend — status channel + connection probe

**Files**: `llm_client.py`, `app.py`, `tests/test_llm_client.py`, `tests/test_llm_endpoints.py`

#### Automated Verification:
- [ ] `./.venv/Scripts/python.exe -m pytest tests/test_llm_client.py -q` passes
- [ ] `./.venv/Scripts/python.exe -m pytest tests/test_llm_endpoints.py -q` passes
- [ ] `./.venv/Scripts/python.exe -m pytest tests/ -q` shows no new failures against the pre-slice baseline
- [ ] `grep -c "_require_usable" llm_client.py` returns 3 — the definition plus its two call sites in `chat()` and `probe()`
- [ ] `sed -n '/^def probe/,/^$/p' llm_client.py | grep -c "_http_client"` returns 0 — D6: the probe must never touch the shared client cache
- [ ] `curl -s localhost:8000/api/config | python -c "import json,sys; d=json.load(sys.stdin); assert {'youtubeUrlEnabled','billingEnabled','googleAuthEnabled','jobRetentionSeconds'} <= set(d)"` — the four pre-existing fields survive, since `MediaInput.jsx:52` reads them independently
- [ ] The `/api/config` response body contains no `api_key`/`LLM_API_KEY` value under any env combination (D3, `tests/test_llm_client.py:485-488`)

#### Manual Verification:
- [ ] `./dev.sh`, then `curl localhost:8000/api/config` with no `LLM_*` env → `llmConfigured:false`, `llmModel:null`, `llmBaseUrl:null`
- [ ] With `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL` set → model and base URL reported; grep the response for the key value and find nothing
- [ ] With `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL_THUMBNAIL` only → still `llmConfigured:true`, and the backend log does NOT print "the third-party backend stays inactive"
- [ ] `curl -X POST localhost:8000/api/llm/test -H 'X-LLM-Base-Url: https://ollama.com/v1' -H 'X-LLM-Key: <real>' -H 'X-LLM-Model: gpt-oss:120b'` → `{"ok":true,...}` with a plausible `latencyMs`
- [ ] Same call against `http://10.255.255.1:9/v1` → returns within ~10s (not the 300s `_TIMEOUT`), `detail` begins `"LLM provider"`
- [ ] Same call with a deliberately wrong key → 502, `detail` names the provider's own rejection
- [ ] `POST /api/llm/test` with no headers and no env → 400 carrying `LLM_ENDPOINT_HINT` verbatim
- [ ] Restart with `BILLING_ENABLED=1` → `POST /api/llm/test` returns 404 and `/api/config` reports `llmConfigured:false` even with `LLM_*` env set (D3, `app.py:158`)
- [ ] Fire `POST /api/llm/test` 16 times in a minute → the 16th returns 429 from the shared probe limiter (`app.py:260`, `PROBES_PER_HOUR = 15`)

### Slice 2: Browser state — storage, migration, server-status wiring

**Files**: `dashboard/src/contexts/AuthContext.jsx`, `dashboard/src/App.jsx`, `dashboard/src/components/ResultCard.jsx`

#### Automated Verification:
- [ ] `cd dashboard && npm run build` succeeds
- [ ] `cd dashboard && npx eslint src/App.jsx src/contexts/AuthContext.jsx src/components/ResultCard.jsx` reports no new errors
- [ ] `grep -rn "gemini_key" dashboard/src/` returns exactly one hit — the legacy read inside the migration in `App.jsx`; `ResultCard.jsx` must no longer appear
- [ ] `grep -c "geminiKey_v1" dashboard/src/App.jsx` returns 3 (migration read, migration write, persistence effect)
- [ ] `grep -n "localStorage.removeItem('llmConfig_v1')" dashboard/src/App.jsx` matches — the clear branch the three sibling key effects lack

#### Manual Verification:
- [ ] Load the dashboard with an existing plaintext `gemini_key` in `localStorage` → after one refresh, `geminiKey_v1` exists with an `ENC:` prefix, `gemini_key` is gone, and the key still shows in Settings
- [ ] The Gemini key still works end to end after migration: run a clip job and confirm it is not rejected for a missing key
- [ ] Auto-edit on a result card (`ResultCard` → `/api/effects/generate`) still sends `X-Gemini-Key` after the migration — this is the caller whose `localStorage` fallback was removed
- [ ] In DevTools, set `llmConfig_v1` by hand, reload → the three values are read back intact; corrupt it → the app still loads with empty fields and no console error
- [ ] `useAuth().llmConfigured` reflects the server: false with no `LLM_*` env, true with a full one (check via React DevTools or a temporary log)
- [ ] No API key value is visible in the `/api/config` network response in DevTools

### Slice 3: The AI Provider settings card

**Files**: `dashboard/src/lib/llm.js`, `dashboard/src/components/LlmProviderCard.jsx`, `dashboard/src/App.jsx`

#### Automated Verification:
- [ ] `cd dashboard && npm run build` succeeds
- [ ] `cd dashboard && npx eslint src/components/LlmProviderCard.jsx src/lib/llm.js src/App.jsx` reports no new errors
- [ ] `grep -n "X-LLM" dashboard/src/` shows the triple ONLY in `src/lib/llm.js` — no call site hand-builds the headers
- [ ] `grep -n "apiFetch" dashboard/src/lib/llm.js` returns nothing — D-scope: the helper builds headers, it does not send requests
- [ ] `grep -n "llmHeaders\|llmConfigComplete" dashboard/src/lib/api.js` returns nothing — BYOK must never enter `apiFetch`

#### Manual Verification:
- [ ] Settings shows the AI Provider card directly under the Gemini key box, visually indistinguishable in style from the ElevenLabs and fal.ai cards
- [ ] Save is disabled with 0, 1 or 2 of the three fields filled; enabled only when all three are non-blank
- [ ] Typing a URL and key but leaving Model blank shows the amber "Required — without a model the provider stays inactive" note
- [ ] Each quick-fill button sets only the endpoint URL and leaves key and model untouched
- [ ] Test connection against a real endpoint shows latency and the model that answered; against a wrong key shows the provider's own message, NOT a top-up prompt
- [ ] Test connection uses the current form values, not the last saved ones — edit the model, test, and confirm the request carries the edited value in DevTools
- [ ] Save, reload the page, reopen Settings → all three values are still there; the eye toggle reveals the key
- [ ] Clear empties all three fields and removes `llmConfig_v1` from `localStorage`
- [ ] With `LLM_*` env set on the server, the card opens showing "Configured on the server" with the endpoint and model, and no inputs until "override with my own endpoint" is clicked
- [ ] With `BILLING_ENABLED=1` the card does not render at all (it sits inside the self-host-only branch)
- [ ] The card renders correctly at 360px width — buttons stack, the endpoint URL wraps rather than overflowing

### Slice 4: Unblock the app — headers, gates, copy

**Files**: `dashboard/src/App.jsx`

#### Automated Verification:
- [ ] `cd dashboard && npm run build` succeeds
- [ ] `cd dashboard && npx eslint src/App.jsx` reports no new errors
- [ ] `grep -n "const keysMissing" dashboard/src/App.jsx` still contains `!billingEnabled &&` — the guard `05578c5` added must survive the widening
- [ ] `grep -c "aiBackendMissing" dashboard/src/App.jsx` returns 8 (definition, `keysMissing`, 2 badge branches, 2 banner branches, 2 modal branches) plus the block conditions
- [ ] `grep -n "'X-Gemini-Key'" dashboard/src/App.jsx` still matches — the Gemini header must not be dropped when a provider is also present

#### Manual Verification:
- [ ] Self-host, no Gemini key, no provider, no Upload-Post key → banner reads "AI backend & Upload-Post key missing"; the modal offers both the Gemini paste-box and the Settings → AI Provider link
- [ ] Configure a provider only → the AI-backend half of the banner and the header badge disappear; the Upload-Post half remains
- [ ] With a provider set and Upload-Post set, the header warning badge is gone entirely and the Clip Generator accepts a job
- [ ] Start a job with a provider configured and confirm in DevTools that `POST /api/process` carries `X-LLM-Base-Url`, `X-LLM-Key` and `X-LLM-Model`
- [ ] Start a job with BOTH a Gemini key and a provider → the request carries `X-Gemini-Key` **and** the LLM triple
- [ ] Clear the model field only → `X-LLM-Model` is absent from the request and the gate closes again (`llmConfigComplete` is false)
- [ ] With `LLM_*` set in the server env and nothing in the browser → the gate opens with no headers sent at all, and the job still runs on the provider
- [ ] The setup modal's AI-backend block shows a green tick reading "provider set" when only a provider is configured, and "Gemini key set" when only Gemini is
- [ ] `BILLING_ENABLED=1` → `keysMissing` never fires and no banner appears, exactly as before this change

### Slice 5: Per-feature capability split

**Files**: `dashboard/src/components/ThumbnailStudio.jsx`, `dashboard/src/components/SaaShortsTab.jsx`, `dashboard/src/App.jsx`

#### Automated Verification:
- [ ] `cd dashboard && npm run build` succeeds
- [ ] `cd dashboard && npx eslint src/components/ThumbnailStudio.jsx src/components/SaaShortsTab.jsx src/App.jsx` reports no new errors
- [ ] `grep -rn "keyHeader\|geminiHeader\|needsGeminiKey\|needsKey" dashboard/src/` returns nothing — every old name is gone, so no call site can still send Gemini-only headers
- [ ] `grep -c "aiHeaders" dashboard/src/components/ThumbnailStudio.jsx` returns 6 (definition + 5 call sites)
- [ ] `grep -c "needsGeminiForImages" dashboard/src/components/ThumbnailStudio.jsx` returns 5 (definition, handleGenerate guard, the note, and both buttons)
- [ ] `cd dashboard && npm test` — if a frontend suite exists; otherwise record that there is none

#### Manual Verification:
- [ ] Provider configured, no Gemini key: YouTube Studio step 0 is fully interactive — no grey overlay, no "AI backend required" card
- [ ] Analyze a video, then refine titles, then generate a description — all three succeed on the provider; DevTools shows `X-LLM-*` on each and no `X-Gemini-Key`
- [ ] Step 3 shows the amber "AI image generation needs a Gemini API key" note, and BOTH the Generate and Regenerate buttons are disabled
- [ ] Add a Gemini key on top of the provider → the note disappears, both buttons enable, and thumbnail generation works
- [ ] AI Shorts: analyze succeeds with only a provider configured; the error message no longer names Gemini alone
- [ ] With neither backend, both tabs behave exactly as before this change (gate closed, warning card shown)
- [ ] Managed cloud user (`BILLING_ENABLED=1`, entitled): both tabs behave exactly as before — no gate, no notes, no `X-LLM-*` headers on any request
- [ ] Point the provider at an unreachable URL and run a Studio action → the error surfaced in the UI begins `"LLM provider"` and is NOT a top-up prompt (D7)
- [ ] The step-3 note renders correctly at 360px width without overflowing

## Desired End State

A self-hoster with no Gemini key and an Ollama Cloud account:

```
1. Settings → AI Provider
     quick fill: [ollama cloud]  → endpoint pre-filled
     API key:    paste
     model:      gpt-oss:120b
     [Test connection] → "✓ responded in 840ms"
     [Save]

2. The "Required API keys missing" banner drops the Gemini half.
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

## File Map

```
llm_client.py                                   # MODIFY — probe() + short probe timeout
app.py                                          # MODIFY — /api/config fields, POST /api/llm/test
tests/test_llm_client.py                        # MODIFY — probe() unit tests (app-free)
tests/test_llm_endpoints.py                     # NEW    — /api/config fields + POST /api/llm/test
dashboard/src/contexts/AuthContext.jsx          # MODIFY — expose llmConfigured/llmModel/llmBaseUrl
dashboard/src/App.jsx                           # MODIFY — state, migration, headers, gates, copy, mount
dashboard/src/components/ResultCard.jsx         # MODIFY — drop dead gemini_key fallback (1 line)
dashboard/src/lib/llm.js                        # NEW    — llmHeaders() + llmConfigComplete()
dashboard/src/components/LlmProviderCard.jsx    # NEW    — the AI Provider settings card
dashboard/src/components/ThumbnailStudio.jsx    # MODIFY — llmHeaders, needsAiBackend, generate note
dashboard/src/components/SaaShortsTab.jsx       # MODIFY — llmHeaders, needsAiBackend
```

## Ordering Constraints

- Slice 1 is the foundation: Slices 2-5 all read `/api/config` fields that do not exist until it lands.
- Slice 2 must precede Slice 3 — the card consumes `llmConfig` state and `useAuth()` fields.
- Slice 3 must precede Slice 4 — Slice 4 mounts nothing new but its copy references the card as the destination.
- Slice 5 depends on Slice 3 for `lib/llm.js` and on Slice 4 for `llmActive`'s precedent; it owns both the two components AND their `App.jsx` mounts, so no slice ever carries a prop without a reader.
- Slices 2 and 5 both touch `App.jsx`; Slice 3 and 4 do too. Merge order is 2 → 3 → 4, with Slice 5 adding only prop pass-through.
- Nothing here runs in parallel: every slice after 1 builds on the previous slice's `App.jsx` state.

## Verification Notes

- **Never send `X-LLM-Model: ""`** — a whitespace-only model silently falls back to the env chain (`tests/test_llm_client.py:707`).
- **Never send base URL without key or key without base URL** — `config_from` returns `None` for either (`tests/test_llm_client.py:425`).
- **A base+key pair with no model is inert, not an error** (`tests/test_llm_client.py:442`). D2's Save gate is the only thing preventing it from the UI.
- **`/api/config` must never echo the API key.** `api_key` is `repr=False` (`tests/test_llm_client.py:485-488`); the endpoint must read individual fields, never `repr(cfg)` or `asdict`.
- **The whole surface must vanish under `BILLING_ENABLED`** — `resolve_llm` returns `None` there (`app.py:158`) and `LLM_*` is swept from managed job envs (`app.py:2250`).
- **Do not reword any `"LLM provider ..."` message** — the prefix is load-bearing for `cloud/alerts.py::_classify_failure` (`llm_client.py:74`, tests at `tests/test_llm_client.py:805-847`).
- **`POST /api/llm/test` must not use `_http_client`** — the 300s read timeout and unbounded cache make it unsuitable for an interactive button (`llm_client.py:130-147`).
- **The Gemini key migration must not strand `ResultCard.jsx:284`** — that read is removed in the same slice as the migration.
- **`MediaInput.jsx:52` independently reads `/api/config`** — adding fields is additive and safe, but the endpoint's shape must stay backward-compatible.
- Precedent lesson: BYOK keys ship backend + frontend together in this repo (`29681ba`, `03477d4`) — Slice 1 alone is not a shippable state.

## Performance Considerations

- `/api/config` is fetched twice per page load (`AuthContext.jsx:104`, `MediaInput.jsx:52`). The
  three new fields come from `llm_client.active_config()`, which reads `os.environ` and does no
  I/O — negligible.
- `POST /api/llm/test` makes one live outbound call with `max_tokens=1`. Rate-limited and
  short-timeout so it cannot be used as a cheap latency oracle or hang a browser tab.
- The encrypted-blob read/write is one `localStorage` round trip on mount and on save, matching
  the three existing versioned keys.

## Migration Notes

- **`gemini_key` → `geminiKey_v1`**: read `geminiKey_v1` and `decrypt()` it; if absent, fall
  back to the plaintext `gemini_key`, then write the encrypted form and remove the plaintext.
  One-way, runs once per browser, no server involvement. A user who downgrades loses the key
  from the UI but it is re-enterable — acceptable, and the same exposure any of the three
  existing `_v1`/`_v3` keys already carries.
- No server-side schema change. `llmConfig_v1` is a new key with no predecessor.
- Rollback: reverting the frontend leaves `geminiKey_v1` and `llmConfig_v1` orphaned in
  `localStorage` and the user re-enters their Gemini key once. No data loss beyond that.

## Pattern References

- `dashboard/src/components/ThumbnailStudio.jsx:73-77` — the canonical per-component BYOK header + capability flag pair. The template for `llmHeaders` / `needsAiBackend`.
- `dashboard/src/components/KeyInput.jsx:1-70` — self-contained Settings card component; the shape `LlmProviderCard.jsx` follows.
- `dashboard/src/App.jsx` (ElevenLabs and fal.ai cards) — the visual card template: `card p-4 sm:p-6 mt-8`, icon block, `readout` BYOK badge, label + input + Save with a 2s transient `saved` state.
- `dashboard/src/App.jsx:35-62` + the `falKey`/`elevenLabsKey` state and persistence effects — the encrypted versioned-key convention.
- `dashboard/src/contexts/AuthContext.jsx:104,140-152` — the `/api/config` → `useAuth()` path established by `d0e1f5a`.
- `mcp_server.py:53-55` — `_FORWARD_HEADERS`, a live reference implementation of the exact header triple the dashboard must speak.
- `llm_client.py:232-266` — `_post`'s status→error mapping, reused verbatim by `probe()`.

## Developer Context

**Q (`ThumbnailStudio.jsx:507`, `:155,:268,:347` vs research Q2): Today the whole YouTube Studio tab is greyed out without a Gemini key. With an OpenAI-compatible provider, titles/analysis/descriptions work but image generation cannot. What should the tab look like?**
A: **Usable, with a note on the thumbnail step.** Nothing greyed out; a note on step 3 only.

**Q (`app.py:1868-1875`, served pre-auth, `tests/test_llm_client.py:485-488`): How much should `/api/config` report about a server-side provider setup?**
A: **Model name and base URL**, plus the boolean. Never the key.

**Q (`llm_client.py:150-178`, `tests/test_llm_client.py:442`): The backend goes silently inert on a half-configured provider. Which form fields should the dashboard insist on?**
A: **All three required, Save blocked otherwise.**

**Q (optional extras: presets, test-connection endpoint, Gemini key encryption):**
A: **All three included.**

**Q (developer redirect, then reversal): "I want to set env to be also add via front end" — server-persisted provider config.**
A: Raised, then **cancelled** by the developer once the no-authentication consequence of a
self-host write endpoint was made explicit (`app.py:106-121`). Browser + headers stands.

**Q (directional, card placement): inline card in `App.jsx` vs extracted component?**
A: Developer indicated this is an implementation detail rather than a product choice. Decided
directly in favour of extraction (D5), on the `KeyInput.jsx` precedent and `App.jsx`'s size.

**Note on question style**: the developer asked for questions phrased in terms of observable
behaviour rather than code structure. Micro-checkpoint summaries follow that convention.

**Verification limitation (confirmed post-hoc)**: every subagent dispatched this session — two
research agents and the mandatory `slice-verifier` — terminated with an empty final message after
2-4 tool calls. Their on-disk transcripts confirm this is not a reporting failure: the verifier read
this artifact and listed the repo root, but never opened `llm_client.py` or `app.py`, the files it
was asked to check the code against. **No independent review of any slice occurred, and no silent
pass should be inferred.** All verification claims in this document come from direct execution by
the author: `llm_client._post`'s error mapping against a mock provider, `_env_llm_config` across
five server-env states, `resolve_llm` against the BYOK header triple, and `encrypt`/`decrypt` over
eight inputs. The React components in Slices 3-5 were never executed — they were checked by reading
against the live codebase (icon and CSS-class existence were verified mechanically). The
`artifact-code-reviewer` / `artifact-coverage-reviewer` pair in `/skill:plan` Step 4 is therefore
load-bearing here in a way it normally is not.

## Design History

- Slice 1: Backend — status channel + connection probe — approved as generated, with two in-flight corrections: `_env_llm_config` reordered to try `"thumbnail"`/`"saas"` (the original `None`-first order made llm_client emit its "backend stays inactive" warning on a correctly task-configured server), and the HTTP-level tests split into a new `tests/test_llm_endpoints.py` because `test_llm_client.py` is deliberately app-free. Verified by executing the code against a mock provider — the `slice-verifier` agent returned empty output on three dispatches and was unavailable this session.
- Slice 2: Browser state — storage, migration, server-status wiring — approved as generated. `llmHeaders` and the gate changes were deliberately held back to Slice 4 so this slice contains no code without a consumer. The `llmConfig_v1` persistence effect gained a clear branch its three siblings lack, so emptying the form really removes the stored provider. Verified by executing the real `encrypt`/`decrypt` over four realistic provider configs and four corrupted/legacy values: exact round-trips, graceful degradation to empty fields.
- Slice 3: The AI Provider settings card — approved as generated, with one addition to the file list: `dashboard/src/lib/llm.js`, a two-function module holding the header rule. Four call sites need it and it encodes two pinned backend constraints (omit a blank `X-LLM-Model`, never send base without key) whose duplication would invite drift. Developer confirmed this does not contradict the per-component decision, since headers are still attached per component and never inside `apiFetch`. Verified: all 8 lucide icons and all 7 shared CSS classes exist in this codebase.
- Slice 4: Unblock the app — headers, gates, copy — approved as generated, with one boundary change from the approved decomposition: the `llmConfig` / `llmConfigured` props for `ThumbnailStudio` and `SaaShortsTab` moved OUT of this slice into Slice 5, so that neither slice contains a prop without a reader. Slice 5's file list gains `dashboard/src/App.jsx` as a result.
- Slice 5: Per-feature capability split — approved as generated. `keyHeader` / `geminiHeader` renamed to `aiHeaders` and `needsKey` / `needsGeminiKey` to `needsAiBackend`: the old names became actively wrong once the objects carried both backends. Gained `dashboard/src/App.jsx` (prop wiring moved from Slice 4). The generate-step note and the two disabled buttons implement the developer's chosen "usable, with a note on the thumbnail step" layout verbatim.

## References

- `.rpiv/artifacts/research/2026-08-30_16-03-23_connect-llm-provider-frontend.md` — parent research
- `.rpiv/artifacts/designs/2026-08-30_08-58-35_openai-compatible-llm-provider.md` — the backend design; decision Q3 (no UI) is reversed here
- `.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md` — backend phased plan
- `.rpiv/artifacts/validation/2026-08-30_15-13-19_openai-compatible-third-party-llm-endpoint-alongside-gemini-additive.md` — backend validation report
- Precedents: `29681ba` (fal.ai BYOK end-to-end), `03477d4` (ElevenLabs BYOK), `d0e1f5a` (`/api/config` field), `05578c5` (`keysMissing` gate), `900dc44` (`X-Gemini-Key` resume-loss accepted)
