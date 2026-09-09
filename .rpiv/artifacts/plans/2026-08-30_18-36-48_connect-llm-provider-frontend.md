---
date: 2026-08-30T18:36:48+0700
author: Yogiswara Utama
commit: 35e9d7e
branch: main
repository: openshorts
topic: "Connect the OpenAI-compatible LLM provider to the dashboard frontend"
tags: [plan, llm-provider, dashboard, byok, openai-compatible, frontend, settings]
status: ready
parent: ".rpiv/artifacts/designs/2026-08-30_16-58-07_connect-llm-provider-frontend.md"
phase_count: 5
phases:
  - { n: 1, title: Backend — status channel + connection probe }
  - { n: 2, title: Browser state — storage, migration, server-status wiring }
  - { n: 3, title: The AI Provider settings card }
  - { n: 4, title: Unblock the app — headers, gates, copy }
  - { n: 5, title: Per-feature capability split }
last_updated: 2026-08-30T18:36:48+0700
last_updated_by: Yogiswara Utama
last_updated_note: "Step 5 triage complete — all 41 reviewer findings applied; Ordering Constraints section and a vitest suite added."
---

# Connect the OpenAI-compatible LLM provider to the dashboard Implementation Plan

## Overview

The backend LLM-provider work (`30ce67e` → `35e9d7e`) is complete: every reroutable call site runs
through `llm_client.chat()` and `resolve_llm` already accepts the `X-LLM-Base-Url` / `X-LLM-Key` /
`X-LLM-Model` header triple. The frontend has zero provider code. This plan implements the UI half:
a new **AI Provider** card in self-host Settings that collects endpoint URL, API key and model,
stores them as one encrypted blob in the browser, and sends them as headers built per component —
the same shape `X-Gemini-Key` already uses.

`GET /api/config` gains three read-only capability fields so the dashboard can see a server-side
`LLM_*` setup and stop demanding a Gemini key, and a new `POST /api/llm/test` drives a
"Test connection" button. The app's front door widens from "has a Gemini key" to "has any AI
backend"; features that physically require Gemini (thumbnail image generation) stay available but
carry an inline note instead of a global block.

Design artifact: `.rpiv/artifacts/designs/2026-08-30_16-58-07_connect-llm-provider-frontend.md`.

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

## What We're NOT Doing

- **Server-side persistence of provider config.** Considered and explicitly cancelled by the
  developer: self-host has no auth, so a write endpoint would let anyone reachable redirect the
  install's AI traffic. Browser + headers only, accepting the same resume-loss `X-Gemini-Key` has.
- **Centralising BYOK headers in `apiFetch`.** Would send the provider key on uploads and social
  posting. Per-component construction is the house style and the developer's decision.
- **`ResultCard.jsx` beyond one dead line.** Its six endpoints are all Gemini-pinned class C.
- **Any cloud/billing provider surface.** `resolve_llm` returns `None` under `BILLING_ENABLED`
  (`app.py:158`) and a prefix sweep strips `LLM_*` from managed job envs (`app.py:2250`).
- **Fixing the unbounded `_clients` cache.** Pre-existing, already reachable via the six live
  endpoints, and orthogonal to this feature.
- **Reordering `cloud/alerts.py::_classify_failure`.** Corrected twice in production (`77905a9`,
  `730f7de`); the `"LLM provider"` prefix strings stay verbatim.
- **Rewording any `llm_client` error message.** `"LLM provider"` is a reserved, alert-classifying
  string (`llm_client.py:74`). This now cuts twice: `POST /api/llm/test` uses the presence of that
  prefix to tell a caller-side rejection (400) from a real upstream failure (502), so adding the
  prefix to `_require_usable`'s two messages would break the split as well as the constraint.
- **Blocking private or loopback probe targets.** `POST /api/llm/test` will reach any URL the
  caller names, and echoes the provider's error body plus a `latencyMs` readout — a real SSRF and
  latency-oracle surface on an unauthenticated self-host endpoint. Rejecting RFC1918 and loopback
  would break the `http://localhost:11434/v1` local-Ollama preset the card ships, which is a
  primary use case. Self-host already trusts its own network, and every other BYOK endpoint has
  the same reach. The 15/hour limiter (`app.py:260`) caps volume, not reach — it makes the
  endpoint slow as an oracle, not unusable as one.

## Ordering Constraints

Carried from the design. **Phases 1-5 merge as one unit** — this repo's precedent
(`29681ba` fal.ai, `03477d4` ElevenLabs) is that a BYOK key ships backend and
frontend together, and Phase 1 alone is not a shippable state: it adds a
capability channel and a probe endpoint that nothing consumes.

- Phase 1 is the foundation: Phases 2-5 all read `/api/config` fields that do not exist until it lands.
- Phase 2 must precede Phase 3 — the card consumes `llmConfig` state and the `useAuth()` fields.
- Phase 3 must precede Phase 4 — Phase 4 mounts nothing new but its copy links to the card as the destination.
- Phase 5 depends on Phase 3 for `lib/llm.js` and on Phase 4 for `llmActive`'s precedent; it owns both components AND their `App.jsx` mounts, so no phase ever carries a prop without a reader.
- Phases 2, 3, 4 and 5 all touch `App.jsx`. Merge order is 2 → 3 → 4 → 5, with Phase 5 adding only prop pass-through.
- **Nothing runs in parallel.** Every phase after 1 builds on the previous phase's `App.jsx` state. This is also why the fence comments use prose anchors rather than line numbers: each App.jsx line number is stale the moment an earlier phase lands.

---

## Phase 1: Backend — status channel + connection probe

### Overview

Add the two things the frontend cannot exist without: a capability channel (`/api/config` reports
whether the server itself has an `LLM_*` setup, and which one) and a connection probe
(`POST /api/llm/test`) that validates a provider without running a real job. The probe deliberately
avoids `llm_client`'s shared client cache — its 300s read timeout and unbounded per-base-URL growth
make it unsuitable for an interactive button.

### Changes Required:

#### 1. Probe helper and short timeout
**File**: `llm_client.py`
**Changes**: Add `_PROBE_TIMEOUT`, `_probe_client()`, `_require_usable()` (lifted verbatim out of
`chat()`'s prologue so the probe cannot drift from real-call validation), and `probe()`. `chat()`'s
inline guard at `:363-374` is replaced by a `_require_usable(config)` call.

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


# --- inside chat(), replacing the inline guard (llm_client.py:362-373) ----------
# The range starts at the `base_url = (config.base_url or "").strip()`
# assignment and ends at the closing paren of the `if not config.model:` raise.
# Confirm that assignment is the first line of the range before replacing.

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

#### 2. Capability fields and the test endpoint
**File**: `app.py`
**Changes**: New `_env_llm_config()` helper; `/api/config` gains `llmConfigured`, `llmModel`,
`llmBaseUrl` (never the key); new self-host-only `POST /api/llm/test` sharing the metadata-probe
rate limiter.

```python
def _env_llm_config():
    """The server's own LLM_* config, or None — what /api/config reports.

    Asks by TASK, never with task=None: config_from resolves
    LLM_MODEL_<TASK> or LLM_MODEL, so "thumbnail" alone already covers a
    plain LLM_MODEL server, while a task=None probe on a server configured
    only with LLM_MODEL_THUMBNAIL would trip llm_client's once-only
    "the third-party backend stays inactive" warning on a perfectly healthy
    setup. Reporting such a server as unconfigured would also make the
    dashboard demand a key it does not need.

    NB: the loop order narrows the warning, it does not eliminate it. A
    server carrying only LLM_MODEL_SAAS still trips it on the "thumbnail"
    pass before the "saas" pass succeeds — harmless (once per process, and
    the config still resolves) but the text is misleading there."""
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
    # Mirrors _env_llm_config's task loop rather than asking with task=None:
    # config_from only consults LLM_MODEL_<TASK> when a task is NAMED
    # (llm_client.py:150-178), so a task=None ask on a server carrying only
    # LLM_MODEL_THUMBNAIL resolves to None — /api/config would report that
    # server configured while this button answered 400.
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
    except llm_client.LlmError as e:
        # _require_usable's two rejections ("The third-party LLM endpoint
        # ...") are the CALLER's problem — a malformed URL or a missing model
        # — so they answer 400. Everything _post raises carries the reserved
        # "LLM provider" prefix and is a real upstream failure, so it answers
        # 502. Using the prefix as the discriminator is deliberate: it means
        # neither message has to be reworded, and the prefix is load-bearing
        # for cloud/alerts.py::_classify_failure (llm_client.py:74).
        raise HTTPException(
            status_code=502 if str(e).startswith("LLM provider") else 400,
            detail=str(e))
    except Exception as e:
        # LlmTransientError (a sibling of LlmError, not a subclass) and
        # anything else: a real provider failure. Same shape every other
        # LLM-aware endpoint uses — a non-2xx with the provider's own text as
        # a string detail, which apiJson surfaces verbatim. Never a 402 —
        # that is apiFetch's QuotaError branch and would render as an
        # OpenShorts top-up prompt.
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True, "model": cfg.model,
            "latencyMs": int((time.monotonic() - started) * 1000)}
```

#### 3. Probe unit tests
**File**: `tests/test_llm_client.py`
**Changes**: Pin `probe()` — success shape, 401→`LlmError`, 5xx→`LlmTransientError`, the
shared-cache guarantee, and malformed-URL / missing-model rejection. Monkeypatches `_probe_client`,
mirroring how this file already stands in for `_http_client`. No server, no network.

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

#### 4. HTTP-level endpoint contract
**File**: `tests/test_llm_endpoints.py` (NEW)
**Changes**: The `/api/config` capability fields and the `POST /api/llm/test` contract. Split from
`test_llm_client.py`, which is deliberately app-free (`httpx.MockTransport` only) — these need the
real `app` object.

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

    def test_a_task_only_model_server_can_be_tested(self, client, monkeypatch):
        # The /api/config twin reports this server as configured, so the
        # button must not answer 400. Pins the task loop against a task=None
        # regression: config_from reads LLM_MODEL_<TASK> only when a task is
        # named, so task=None would resolve None here on a healthy server.
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL_THUMBNAIL", "thumb-model")
        monkeypatch.setattr(llm_client, "probe", lambda cfg: None)
        res = client.post("/api/llm/test")
        assert res.status_code == 200
        assert res.json()["model"] == "thumb-model"

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

    def test_the_key_is_not_echoed_on_the_failure_path_either(
            self, client, monkeypatch):
        # The 200 path above proves little: the risk is the 502 branch, where
        # _post interpolates the provider's own error body (_err_detail[:300])
        # into a message this endpoint passes through as `detail`.
        def boom(cfg):
            raise llm_client.LlmError(
                "LLM provider rejected the request (HTTP 401): "
                "invalid key byok-secret-key")
        monkeypatch.setattr(llm_client, "probe", boom)
        res = client.post("/api/llm/test", headers=BYOK)
        assert res.status_code == 502
        assert "byok-secret-key" not in res.text

    def test_a_provider_402_is_never_forwarded_as_402(self, client, monkeypatch):
        # D7: apiFetch treats 402 as OpenShorts' own QuotaError and renders a
        # top-up prompt (lib/api.js:29). A third-party provider's "out of
        # credit" must therefore never reach the browser as 402.
        def out_of_credit(cfg):
            raise llm_client.LlmError(
                "LLM provider rejected the request (HTTP 402): "
                "insufficient credit")
        monkeypatch.setattr(llm_client, "probe", out_of_credit)
        res = client.post("/api/llm/test", headers=BYOK)
        assert res.status_code == 502
        assert "LLM provider" in res.json()["detail"]

    def test_a_caller_side_rejection_is_a_400_not_a_502(self, client, monkeypatch):
        # _require_usable's messages do NOT carry the "LLM provider" prefix,
        # which is exactly how the endpoint tells a malformed-URL/missing-model
        # rejection (the caller's fault) from a real upstream failure.
        def bad_input(cfg):
            raise llm_client.LlmError(
                "The third-party LLM endpoint URL is missing or malformed")
        monkeypatch.setattr(llm_client, "probe", bad_input)
        assert client.post(
            "/api/llm/test", headers=BYOK).status_code == 400

    def test_a_saas_only_model_server_can_be_tested(self, client, monkeypatch):
        # The second pass of the task loop. Also the case whose llm_client
        # "stays inactive" warning the _env_llm_config docstring flags as
        # unavoidable.
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.test/v1")
        monkeypatch.setenv("LLM_API_KEY", "k")
        monkeypatch.setenv("LLM_MODEL_SAAS", "saas-model")
        monkeypatch.setattr(llm_client, "probe", lambda cfg: None)
        assert client.post("/api/llm/test").json()["model"] == "saas-model"


class TestBillingDisablesTheWholeSurface:
    """D3 + the design's Verification Note 5. conftest.py pins
    BILLING_ENABLED=0 process-wide, so this cannot be monkeypatched in-process
    — app.py reads it at import. A subprocess is the only honest check."""

    SCRIPT = (
        "import os, json;"
        "os.environ['BILLING_ENABLED']='1';"
        "os.environ['LLM_BASE_URL']='https://ollama.test/v1';"
        "os.environ['LLM_API_KEY']='secret-key';"
        "os.environ['LLM_MODEL']='gpt-oss:120b';"
        "import app as a;"
        "from fastapi.testclient import TestClient;"
        "c=TestClient(a.app, raise_server_exceptions=False);"
        "cfg=c.get('/api/config').json();"
        "print(json.dumps({'cfg':cfg,"
        "'test_status':c.post('/api/llm/test').status_code}))")

    def test_billing_forces_the_config_fields_off_and_404s_the_probe(self):
        import subprocess, sys, json as _json
        out = subprocess.run([sys.executable, "-c", self.SCRIPT],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        data = _json.loads(out.stdout.strip().splitlines()[-1])
        assert data["cfg"]["llmConfigured"] is False
        assert data["cfg"]["llmModel"] is None
        assert data["cfg"]["llmBaseUrl"] is None
        assert data["test_status"] == 404
```

### Success Criteria:

#### Automated Verification:
- [x] `./.venv/Scripts/python.exe -m pytest tests/test_llm_client.py -q` passes
- [x] `./.venv/Scripts/python.exe -m pytest tests/test_llm_endpoints.py -q` passes
- [x] `./.venv/Scripts/python.exe -m pytest tests/ -q` shows no new failures against the pre-slice baseline
- [x] `grep -c "_require_usable" llm_client.py` returns 3 — the definition plus its two call sites in `chat()` and `probe()`
- [x] `./.venv/Scripts/python.exe -c "import inspect, llm_client; assert '_http_client' not in inspect.getsource(llm_client.probe)"` exits 0 — D6: the probe must never touch the shared client cache. (A `sed '/^def probe/,/^$/p'` range cannot be used: it terminates on the blank line inside `probe()`'s own docstring and would pass vacuously.)
- [x] `tests/test_llm_endpoints.py::TestConfigFields::test_existing_fields_are_unchanged` passes — the four pre-existing fields survive, since `MediaInput.jsx:52` reads them independently (offline equivalent of the `curl` check now listed under Manual)
- [x] `tests/test_llm_endpoints.py::TestConfigFields::test_the_api_key_never_appears_in_the_payload` passes — D3: the key never reaches the payload. The enumerated env states this must hold under are the three the file covers: full `LLM_*`, task-only model, and half-configured. (The old wording cited `test_llm_client.py:485-488`, which pins `repr(LlmConfig)` — a different invariant.)
- [x] `tests/test_llm_endpoints.py::TestBillingDisablesTheWholeSurface` passes — D3 under billing: `/api/config` reports all three fields off and `POST /api/llm/test` 404s, even with full `LLM_*` env. Runs in a subprocess because `conftest.py` pins `BILLING_ENABLED=0` process-wide and `app.py` reads it at import.

#### Manual Verification:
- [ ] `./dev.sh`, then `curl localhost:8000/api/config` with no `LLM_*` env → `llmConfigured:false`, `llmModel:null`, `llmBaseUrl:null`
- [ ] With `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL` set → model and base URL reported; grep the response for the key value and find nothing
- [ ] With `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL_THUMBNAIL` only → still `llmConfigured:true`, and the backend log does NOT print "the third-party backend stays inactive"
- [ ] `curl -s localhost:8000/api/config | python -c "import json,sys; d=json.load(sys.stdin); assert {'youtubeUrlEnabled','billingEnabled','googleAuthEnabled','jobRetentionSeconds'} <= set(d)"` — the four pre-existing fields survive against a running server
- [ ] With `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL_SAAS` only → `llmConfigured:true`, and the backend log DOES print "the third-party backend stays inactive" once (expected: the loop's `"thumbnail"` pass trips it before `"saas"` succeeds — see the `_env_llm_config` docstring NB)
- [ ] `curl -X POST localhost:8000/api/llm/test -H 'X-LLM-Base-Url: https://ollama.com/v1' -H 'X-LLM-Key: <real>' -H 'X-LLM-Model: gpt-oss:120b'` → `{"ok":true,...}` with a plausible `latencyMs`. **No provider account?** Substitute a local stub — `python -c "from http.server import*;import json;h=type('H',(BaseHTTPRequestHandler,),{'do_POST':lambda s:(s.send_response(200),s.send_header('Content-Type','application/json'),s.end_headers(),s.wfile.write(json.dumps({'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]}).encode()))});HTTPServer(('',11434),h).serve_forever()"` — then probe `http://localhost:11434` with any key and model
- [ ] Same call against `http://10.255.255.1:9/v1` → returns within ~10s (not the 300s `_TIMEOUT`), `detail` begins `"LLM provider"`
- [ ] Same call with a deliberately wrong key → 502, `detail` names the provider's own rejection
- [ ] `POST /api/llm/test` with no headers and no env → 400 carrying `LLM_ENDPOINT_HINT` verbatim
- [ ] Restart with `BILLING_ENABLED=1` → `POST /api/llm/test` returns 404 and `/api/config` reports `llmConfigured:false` even with `LLM_*` env set (D3, `app.py:158`)
- [ ] Fire `POST /api/llm/test` 16 times in a minute → the 16th returns 429 from the shared probe limiter (`app.py:260`, `PROBES_PER_HOUR = 15`)

---

## Phase 2: Browser state — storage, migration, server-status wiring

### Overview

The browser-side foundation with no UI yet: `llmConfig` state under an encrypted `llmConfig_v1`
key, the one-way `gemini_key` → `geminiKey_v1` migration onto the `ENC:` convention its three
siblings already use, and the three new `/api/config` fields surfaced through `useAuth()`.
`llmHeaders` and the gate changes are deliberately held back to Phase 4 so this phase contains no
code without a consumer.

### Changes Required:

#### 1. Capability fields on the auth context
**File**: `dashboard/src/contexts/AuthContext.jsx`
**Changes**: Expose `llmConfigured`, `llmModel`, `llmBaseUrl` through `useAuth()`, following the
`jobRetentionSeconds` precedent (`d0e1f5a`).

```jsx
// --- in the `value` object, on the line after `jobRetentionSeconds` -------------

    jobRetentionSeconds: config.jobRetentionSeconds || null,
    // Self-host only: the server's own LLM_* setup, so Settings can say
    // "configured on the server" instead of demanding a key. Never carries the
    // API key — /api/config reports state and identity only. Under billing the
    // backend forces these off, since resolve_llm returns None there.
    llmConfigured: !!config.llmConfigured,
    llmModel: config.llmModel || null,
    llmBaseUrl: config.llmBaseUrl || null,
```

#### 2. State, migration and persistence
**File**: `dashboard/src/App.jsx`
**Changes**: Widen the `useAuth()` destructure; replace the plaintext `gemini_key` state with the
migrating encrypted read; add `llmConfig` state beside `falKey`; replace the `gemini_key`
persistence effect and add the `llmConfig_v1` effect — which, unlike its three siblings, also
clears, so emptying the form really removes the stored provider.

```jsx
// --- the useAuth() destructure at the top of the App component -------------------

  const { billingEnabled, isManaged, isSignedIn, me, plan, refreshMe,
    jobRetentionSeconds, llmConfigured, llmModel, llmBaseUrl,
    loading: authLoading } = useAuth();


// --- replaces the plaintext gemini_key state (the `apiKey` useState) ------------

  // Migrated to the ENC: convention its three siblings already use. One-way and
  // once per browser: read the encrypted form, else adopt the legacy plaintext
  // value and rewrite it. ResultCard's direct localStorage read of the old key
  // is removed in the same slice.
  const [apiKey, setApiKey] = useState(() => {
    const stored = localStorage.getItem('geminiKey_v1');
    // decrypt() returns '' for a corrupt ENC: payload rather than throwing, so
    // an empty result must fall through to the legacy value instead of being
    // adopted as "the key is blank" — otherwise a corrupted blob silently
    // loses a key the plaintext copy could still have restored.
    if (stored) {
      const decrypted = decrypt(stored);
      if (decrypted) return decrypted;
    }
    const legacy = localStorage.getItem('gemini_key');
    if (legacy) {
      localStorage.setItem('geminiKey_v1', encrypt(legacy));
      localStorage.removeItem('gemini_key');
      return legacy;
    }
    return '';
  });


// --- new, immediately after the `falKey` useState --------------------------------

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


// --- replaces the gemini_key persistence effect (the useEffect writing
//     `gemini_key`, beside the elevenLabsKey / falKey effects) --------------------

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
```

#### 3. Drop the stranded legacy read
**File**: `dashboard/src/components/ResultCard.jsx`
**Changes**: Remove the dead `localStorage.getItem('gemini_key')` fallback at `:284`. `App.jsx`
always passes `geminiApiKey={apiKey}`, so it could only fire when both were empty — and the storage
migration would otherwise strand it.

```jsx
// --- inside handleAutoEdit (ResultCard.jsx:284) ----------------------------------

            const apiKey = geminiApiKey;
```

### Success Criteria:

#### Automated Verification:
- [ ] `cd dashboard && npm run build` succeeds
- [ ] `cd dashboard && npx eslint src/App.jsx src/contexts/AuthContext.jsx src/components/ResultCard.jsx` reports no new errors
- [ ] `grep -rn "gemini_key" dashboard/src/` returns exactly two hits, both inside the `useState` migration initializer in `App.jsx` — the `getItem('gemini_key')` read and the `removeItem('gemini_key')` cleanup; `ResultCard.jsx` must no longer appear
- [ ] `grep -c "geminiKey_v1" dashboard/src/App.jsx` returns 3 (migration read, migration write, persistence effect)
- [ ] `grep -n "localStorage.removeItem('llmConfig_v1')" dashboard/src/App.jsx` matches — the clear branch the three sibling key effects lack

#### Manual Verification:
- [ ] Load the dashboard with an existing plaintext `gemini_key` in `localStorage` → after one refresh, `geminiKey_v1` exists with an `ENC:` prefix, `gemini_key` is gone, and the key still shows in Settings
- [ ] The Gemini key still works end to end after migration: run a clip job and confirm it is not rejected for a missing key
- [ ] Auto-edit on a result card (`ResultCard` → `/api/effects/generate`) still sends `X-Gemini-Key` after the migration — this is the caller whose `localStorage` fallback was removed
- [ ] In DevTools, set `llmConfig_v1` by hand, reload → the three values are read back intact; corrupt it → the app still loads with empty fields and no console error
- [ ] `useAuth().llmConfigured` reflects the server: false with no `LLM_*` env, true with a full one (check via React DevTools or a temporary log)
- [ ] No API key value is visible in the `/api/config` network response in DevTools
- [ ] With `BILLING_ENABLED=1` on the server and full `LLM_*` env set, `useAuth().llmConfigured` is still false — the backend forces the triple off, which is what this phase's own comment claims
- [ ] Rollback: after migrating, revert to the pre-change bundle and reload → the app loads with no console error and the Gemini key box is empty but re-enterable. (The migration is destructive — it `removeItem`s `gemini_key` — so this confirms the documented downgrade cost is exactly "re-enter the key once", nothing worse.)

---

## Phase 3: The AI Provider settings card

### Overview

The visible surface: a shared header-rule module and the Settings card itself — quick-fill presets,
three required fields, triple validation, Test connection, and the server-configured status block.
Markup follows the ElevenLabs / fal.ai cards in `App.jsx` so it reads as native.

### Changes Required:

#### 1. The shared header rule
**File**: `dashboard/src/lib/llm.js` (NEW)
**Changes**: `llmHeaders()` and `llmConfigComplete()`. Four call sites need this and it encodes two
pinned backend constraints that are easy to get subtly wrong. Not a centralisation of BYOK into
`apiFetch` — each component still attaches its own headers.

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

#### 2. The Settings card
**File**: `dashboard/src/components/LlmProviderCard.jsx` (NEW)
**Changes**: The full card component — presets, three inputs with visibility toggle, Save gated on
the complete triple, Clear, Test connection against the *draft* config, and the
"Configured on the server" status block with an override affordance.

```jsx
import React, { useEffect, useRef, useState } from 'react';
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
  const [expanded, setExpanded] = useState(false);
  // A server-configured install needs no input, so the form stays collapsed
  // behind the status block — unless this browser already overrides it, or the
  // user asks to. DERIVED, never frozen in useState: serverConfigured arrives
  // asynchronously from /api/config, so a mount-time snapshot reads false on
  // first paint and the collapsed state would never appear for a session that
  // restores straight onto the Settings tab.
  const showForm = expanded || !serverConfigured || llmConfigComplete(config);

  const complete = llmConfigComplete(draft);
  const dirty = draft.baseUrl !== config.baseUrl
    || draft.apiKey !== config.apiKey
    || draft.model !== config.model;

  const set = (field) => (e) => {
    setDraft((d) => ({ ...d, [field]: e.target.value }));
    setTest(null);
    setSaved(false);
  };

  // The Settings tab is conditionally rendered, so switching tabs inside the
  // 2s window unmounts the card while the timer still holds setSaved.
  const savedTimer = useRef(null);
  useEffect(() => () => clearTimeout(savedTimer.current), []);

  const handleSave = () => {
    onSave(draft);
    setSaved(true);
    clearTimeout(savedTimer.current);
    savedTimer.current = setTimeout(() => setSaved(false), 2000);
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
            <button type="button" onClick={() => setExpanded(true)} className="btn-quiet py-1.5 px-3 text-xs mt-3">
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
                type="button"
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
                type="button"
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
              type="button"
              onClick={handleSave}
              disabled={!complete || !dirty}
              className={saved ? 'badge-ok px-4' : 'btn-primary py-2 px-4 text-sm'}
            >
              {saved ? <><Check size={12} /> saved</> : 'Save'}
            </button>
            <button
              type="button"
              onClick={runTest}
              disabled={!complete || !!test?.busy}
              className="btn-quiet py-2 px-4 text-sm"
            >
              {test?.busy
                ? <><Loader2 size={14} className="animate-spin" /> testing…</>
                : 'Test connection'}
            </button>
            {(config.baseUrl || config.apiKey || config.model) && (
              <button type="button" onClick={handleClear} className="btn-ghost py-2 px-4 text-sm sm:ml-auto">
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

#### 3. Mount the card
**File**: `dashboard/src/App.jsx`
**Changes**: Import `LlmProviderCard` and mount it in the self-host Settings branch immediately
after `<KeyInput>` (`App.jsx:1294`), wired to the `llmConfig` state and the three `useAuth()`
capability fields from Phase 2.

```jsx
// --- new import, beside the other component imports -----------------------------

import LlmProviderCard from './components/LlmProviderCard';


// --- in the self-host Settings branch, right after <KeyInput> -------------------

              <KeyInput onKeySet={setApiKey} savedKey={apiKey} />

              <LlmProviderCard
                config={llmConfig}
                onSave={setLlmConfig}
                serverConfigured={llmConfigured}
                serverModel={llmModel}
                serverBaseUrl={llmBaseUrl}
              />
```

#### 4. Automated coverage for the header rule
**File**: `dashboard/package.json`, `dashboard/src/lib/llm.spec.js` (NEW)
**Changes**: Add vitest and a `test` script, then pin `lib/llm.js`'s two pinned
backend constraints. This is the only pure-logic module in the change set and the
only place D9 (omit a blank model) and D2 (all three required) live in code —
without a runner they are checked solely by eyeballing DevTools.

```json
// --- dashboard/package.json: one script, one devDependency --------------------

  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "lint": "eslint . --report-unused-disable-directives --max-warnings 0",
    "preview": "vite preview",
    "test": "vitest run"
  },

  "devDependencies": {
    "vitest": "^2.1.9"
  }
```

```js
// dashboard/src/lib/llm.spec.js — NEW
//
// The header rule is the one place a silent, backend-visible bug can hide: a
// blank X-LLM-Model falls back to the server's env chain and a base-without-key
// pair is dropped by config_from, so both failures look like "it just used
// Gemini" rather than an error.
import { describe, it, expect } from 'vitest';
import { llmHeaders, llmConfigComplete } from './llm';

const FULL = { baseUrl: 'https://ollama.com/v1', apiKey: 'k', model: 'm' };

describe('llmHeaders', () => {
  it('sends the full triple when all three are present', () => {
    expect(llmHeaders(FULL)).toEqual({
      'X-LLM-Base-Url': 'https://ollama.com/v1',
      'X-LLM-Key': 'k',
      'X-LLM-Model': 'm',
    });
  });

  it('OMITS a blank model rather than sending it empty', () => {
    // D9: a whitespace-only X-LLM-Model falls back to the server's env chain
    // (tests/test_llm_client.py:707), which is a silent wrong-model bug.
    const h = llmHeaders({ ...FULL, model: '   ' });
    expect(h).not.toHaveProperty('X-LLM-Model');
    expect(h['X-LLM-Base-Url']).toBe('https://ollama.com/v1');
  });

  it('sends nothing for a base-only or key-only config', () => {
    // config_from returns None for either alone, and a header key must never
    // reach an env-configured base_url (app.py:145-147).
    expect(llmHeaders({ ...FULL, apiKey: '' })).toEqual({});
    expect(llmHeaders({ ...FULL, baseUrl: '' })).toEqual({});
    expect(llmHeaders({ ...FULL, apiKey: '  ' })).toEqual({});
  });

  it('tolerates null/undefined without throwing', () => {
    expect(llmHeaders(null)).toEqual({});
    expect(llmHeaders(undefined)).toEqual({});
  });

  it('trims the values it does send', () => {
    expect(llmHeaders({ baseUrl: ' https://x/v1 ', apiKey: ' k ', model: ' m ' }))
      .toEqual({ 'X-LLM-Base-Url': 'https://x/v1', 'X-LLM-Key': 'k', 'X-LLM-Model': 'm' });
  });
});

describe('llmConfigComplete', () => {
  it('requires all three to be non-blank', () => {
    // D2: the Save gate is the only thing keeping the backend's silent-inert
    // state (tests/test_llm_client.py:442) unreachable from the UI.
    expect(llmConfigComplete(FULL)).toBe(true);
    expect(llmConfigComplete({ ...FULL, model: '' })).toBe(false);
    expect(llmConfigComplete({ ...FULL, model: '   ' })).toBe(false);
    expect(llmConfigComplete({ ...FULL, apiKey: '' })).toBe(false);
    expect(llmConfigComplete({ ...FULL, baseUrl: '' })).toBe(false);
    expect(llmConfigComplete(null)).toBe(false);
  });
});
```

### Success Criteria:

#### Automated Verification:
- [ ] `cd dashboard && npm run build` succeeds
- [ ] `cd dashboard && npx eslint src/components/LlmProviderCard.jsx src/lib/llm.js src/App.jsx` reports no new errors
- [ ] `grep -rn "X-LLM" dashboard/src/` shows the triple ONLY in `src/lib/llm.js` — no call site hand-builds the headers. (`-r` is required: a non-recursive `grep -n` on a directory prints "Is a directory" and matches nothing, passing vacuously.)
- [ ] The three literals match the backend exactly: `grep -o "X-LLM-[A-Za-z-]*" dashboard/src/lib/llm.js | sort -u` equals `grep -o "X-LLM-[A-Za-z-]*" app.py | sort -u` — a typo like `X-LLM-BaseUrl` would otherwise pass build, eslint and every other grep here and fail only at runtime
- [ ] `cd dashboard && npm test` passes — `src/lib/llm.spec.js` covers the blank-model omission (D9), base-only and key-only returning `{}`, and whitespace-only fields counting as blank (D2)
- [ ] `grep -nE "apiFetch\(|from '\.\./lib/api'" dashboard/src/lib/llm.js` returns nothing — D-scope: the helper builds headers, it does not send requests. (The bare token `apiFetch` cannot be the pattern: the module's own header comment says "Deliberately NOT attached inside apiFetch" and would match.)
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

---

## Phase 4: Unblock the app — headers, gates, copy

### Overview

The front door widens from "has a Gemini key" to "has any AI backend": `keysMissing` is re-pointed
at `aiBackendMissing`, `/api/process` carries the `X-LLM-*` triple alongside any Gemini header, and
the badge, banner and required-keys modal copy stop naming Gemini as the only option. The
`!billingEnabled` guard `05578c5` added is preserved verbatim.

### Changes Required:

#### 1. Gate, headers and copy
**File**: `dashboard/src/App.jsx`
**Changes**: Import `llmHeaders` / `llmConfigComplete`; derive `llmActive` and `aiBackendMissing`
and rebuild `keysMissing` on them; merge the LLM triple into `handleProcess`'s headers; update the
header warning badge, the standing banner, the required-keys modal title, and the modal's intro
plus its AI-backend block (which now shows a green tick for either backend and links to
Settings → AI Provider).

```jsx
// --- second new import, beside the lib/api import --------------------------------

import { llmHeaders, llmConfigComplete } from './lib/llm';


// --- replaces the keysMissing gate -----------------------------------------------

  // "has some AI backend": a Gemini key, a browser-configured OpenAI-compatible
  // provider, or one in the server's LLM_* env. Upload-Post stays separately
  // required. The !billingEnabled guard is preserved verbatim — 05578c5 made this
  // gate self-host-only and widening it must not reintroduce it in cloud.
  const llmActive = llmConfigComplete(llmConfig) || llmConfigured;
  const aiBackendMissing = !apiKey && !llmActive;
  // authLoading suppresses the gate until /api/config has answered.
  // llmConfigured starts false, so an env-only-configured server would
  // otherwise flash the banner and the header badge on first paint, and a
  // Generate click inside that window would open the required-keys modal.
  const keysMissing = !billingEnabled && !authLoading
    && (aiBackendMissing || !uploadPostKey);


// --- header construction in handleProcess ----------------------------------------

      // BYOK sends the Gemini header and/or the OpenAI-compatible triple; managed
      // users rely on the bearer token apiFetch attaches automatically. Both may
      // travel together: the backend prefers the LLM path per capability class and
      // falls back to Gemini for video understanding.
      const headers = {
        ...(apiKey ? { 'X-Gemini-Key': apiKey } : {}),
        ...llmHeaders(llmConfig),
      };


// --- header warning badge copy (the `hidden md:inline` span in the header) ------

                <span className="hidden md:inline">
                  {aiBackendMissing && !uploadPostKey
                    ? 'AI backend & Upload-Post key missing'
                    : aiBackendMissing
                      ? 'AI backend missing'
                      : 'Upload-Post API Key Missing'}
                </span>


// --- standing banner copy (the `text-muted` span in the keysMissing banner) -----

                <span className="text-muted">
                  {aiBackendMissing && !uploadPostKey
                    ? 'Set a Gemini key or an OpenAI-compatible provider, plus your Upload-Post API key, to use OpenShorts.'
                    : aiBackendMissing
                      ? 'Set a Gemini key or an OpenAI-compatible provider to use OpenShorts.'
                      : 'Set your Upload-Post API key to use OpenShorts.'}
                </span>


// --- required-keys modal title (the `title=` prop on the setup Modal) -----------

        title={aiBackendMissing && !uploadPostKey
          ? 'Required API Keys Missing'
          : aiBackendMissing
            ? 'AI Backend Required'
            : 'Upload-Post API Key Required'}


// --- required-keys modal intro + its first block ---------------------------------

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

### Success Criteria:

#### Automated Verification:
- [ ] `cd dashboard && npm run build` succeeds
- [ ] `cd dashboard && npx eslint src/App.jsx` reports no new errors
- [ ] `grep -n "const keysMissing = !billingEnabled" dashboard/src/App.jsx` matches — the guard `05578c5` added must survive the widening. (The invariant is folded into the pattern so the check fails mechanically if the guard is dropped, rather than relying on the reader to eyeball a printed line.)
- [ ] `grep -c "aiBackendMissing" dashboard/src/App.jsx` returns 12 — definition, `keysMissing`, 2 badge branches, 2 banner branches, 2 modal-title branches, the block `className`, the icon ternary, the `!aiBackendMissing` green-tick guard, and the block's own `aiBackendMissing && (` guard
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
- [ ] The card's resume claim holds: start a provider-backed job, restart the backend mid-run, and confirm the resumed job behaves as the copy says — it falls back to Gemini rather than failing (`900dc44`'s accepted `X-Gemini-Key` precedent). If no Gemini key is set, the resumed job fails with a missing-backend error rather than silently producing nothing
- [ ] No first-paint flash: with `LLM_*` set in the server env and an empty browser config, hard-reload the dashboard and confirm the banner and header badge never appear, not even for one frame (this is what the `authLoading` guard buys)

---

## Phase 5: Per-feature capability split

### Overview

YouTube Studio and AI Shorts stop being Gemini-gated. Both components send `aiHeaders` (the Gemini
header and/or the LLM triple, whichever exist) and gate on `needsAiBackend`. The one action a
chat-completions endpoint physically cannot serve — thumbnail image generation — keeps a
Gemini-specific check and an inline note on the generate step rather than blocking the tab. This
phase owns both the components and their `App.jsx` mounts, so no phase ever carries a prop without
a reader.

### Changes Required:

#### 1. Capability-aware YouTube Studio
**File**: `dashboard/src/components/ThumbnailStudio.jsx`
**Changes**: New `llmConfig` / `llmConfigured` props; `keyHeader` → `aiHeaders` at all five call
sites; `needsKey` → `needsAiBackend` on the standing warning card and the step-0 grid gate; new
`needsGeminiForImages` guarding `handleGenerate`, the generate-step note, and both the Generate and
Regenerate buttons.

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


// --- the five header call sites: keyHeader -> aiHeaders -------------------------
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

#### 2. Capability-aware AI Shorts
**File**: `dashboard/src/components/SaaShortsTab.jsx`
**Changes**: New `llmConfig` / `llmConfigured` props; `geminiHeader` → `aiHeaders`;
`needsGeminiKey` → `needsAiBackend` on the analyze guard, whose message no longer names Gemini
alone.

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

#### 3. Prop wiring
**File**: `dashboard/src/App.jsx`
**Changes**: Pass `llmConfig` and `llmConfigured` to the `SaaShortsTab` and `ThumbnailStudio`
mounts. Moved here from Phase 4 so the props and their readers land together.

```jsx
// --- the SaaShortsTab mount (currently a single line; reformatted to three) ------

            <SaaShortsTab geminiApiKey={apiKey} llmConfig={llmConfig} llmConfigured={llmConfigured}
              elevenLabsKey={elevenLabsKey} falKey={falKey} uploadPostKey={uploadPostKey}
              uploadUserId={uploadUserId} managed={isManaged} />


// --- the ThumbnailStudio mount ---------------------------------------------------

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

### Success Criteria:

#### Automated Verification:
- [ ] `cd dashboard && npm run build` succeeds
- [ ] `cd dashboard && npx eslint src/components/ThumbnailStudio.jsx src/components/SaaShortsTab.jsx src/App.jsx` reports no new errors
- [ ] `grep -rnE "\b(keyHeader|geminiHeader|needsGeminiKey|needsKey)\b" dashboard/src/components/ThumbnailStudio.jsx dashboard/src/components/SaaShortsTab.jsx | grep -v "//"` returns nothing — every old identifier is gone, so no call site can still send Gemini-only headers. Word boundaries are required (`ResultCard.jsx`'s untouched `geminiHeaders` contains `geminiHeader` as a substring) and the comment filter is required (both files carry a "renamed from …" comment this plan itself emits).
- [ ] `grep -c "aiHeaders" dashboard/src/components/ThumbnailStudio.jsx` returns 6 (definition + 5 call sites)
- [ ] `grep -c "needsGeminiForImages" dashboard/src/components/ThumbnailStudio.jsx` returns 5 (definition, handleGenerate guard, the note, and both buttons)
- [ ] `cd dashboard && npm test` passes — the vitest suite Phase 3 lands must stay green (the earlier "if a suite exists, otherwise record there is none" wording made this criterion unfailable)

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

---

## Testing Strategy

### Automated:
- `./.venv/Scripts/python.exe -m pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` — the probe unit tests and the HTTP-level endpoint contract
- `./.venv/Scripts/python.exe -m pytest tests/ -q` — full backend suite against the pre-change baseline
- `cd dashboard && npm run build` after every frontend phase
- `cd dashboard && npx eslint <phase files>` after every frontend phase
- `cd dashboard && npm test` — the vitest suite Phase 3 lands over `src/lib/llm.js`, the only
  pure-logic module in the change set (blank-model omission, base-only / key-only, whitespace)
- The `grep` invariants in each phase's Automated Verification — they encode the pinned constraints
  (`X-LLM` only in `lib/llm.js`, no BYOK in `apiFetch`, `!billingEnabled` preserved, old
  Gemini-only header names gone)

### Manual Testing Steps:

Reference only — the per-phase Success Criteria above are the load-bearing checks. These restate the
design's Verification Notes as things to keep an eye on while testing:

1. **Never send `X-LLM-Model: ""`** — a whitespace-only model silently falls back to the env chain
   (`tests/test_llm_client.py:707`).
2. **Never send base URL without key or key without base URL** — `config_from` returns `None` for
   either (`tests/test_llm_client.py:425`).
3. **A base+key pair with no model is inert, not an error** (`tests/test_llm_client.py:442`). D2's
   Save gate is the only thing preventing it from the UI.
4. **`/api/config` must never echo the API key.** `api_key` is `repr=False`
   (`tests/test_llm_client.py:485-488`); the endpoint must read individual fields, never `repr(cfg)`
   or `asdict`.
5. **The whole surface must vanish under `BILLING_ENABLED`** — `resolve_llm` returns `None` there
   (`app.py:158`) and `LLM_*` is swept from managed job envs (`app.py:2250`).
6. **Do not reword any `"LLM provider ..."` message** — the prefix is load-bearing for
   `cloud/alerts.py::_classify_failure` (`llm_client.py:74`, tests at
   `tests/test_llm_client.py:805-847`).
7. **`POST /api/llm/test` must not use `_http_client`** — the 300s read timeout and unbounded cache
   make it unsuitable for an interactive button (`llm_client.py:130-147`).
8. **The Gemini key migration must not strand `ResultCard.jsx:284`** — that read is removed in the
   same phase as the migration.
9. **`MediaInput.jsx:52` independently reads `/api/config`** — adding fields is additive and safe,
   but the endpoint's shape must stay backward-compatible.
10. Precedent lesson: BYOK keys ship backend + frontend together in this repo (`29681ba`,
    `03477d4`) — Phase 1 alone is not a shippable state.

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

## Developer Context

**Inherited verification limitation (from the design artifact, carried forward deliberately)**:
every subagent dispatched during design — two research agents and the mandatory `slice-verifier` —
terminated with an empty final message. No independent review of any slice occurred at design time.
All design-time verification claims came from direct execution by the author (`llm_client._post`'s
error mapping against a mock provider, `_env_llm_config` across five server-env states,
`resolve_llm` against the BYOK header triple, `encrypt`/`decrypt` over eight inputs). The React
components in Phases 3-5 were never executed — they were checked by reading against the live
codebase, with icon and CSS-class existence verified mechanically. **The Step 4
`artifact-code-reviewer` / `artifact-coverage-reviewer` pair is therefore load-bearing on this plan
in a way it normally is not.**

**Step 4 dispatch failure (this session, same mode as the design's)**: the
`artifact-code-reviewer` and `artifact-coverage-reviewer` subagent types both terminated with an
empty final message after 2 tool calls — identical to the `slice-verifier` failure recorded above.
Rather than proceeding on the Step 4.4 fallback with no findings, both reviews were re-dispatched
as `general-purpose` agents carrying the same review contracts; those completed normally (12 and 9
tool calls) and produced the 41 findings in `## Plan Review (Step 4)`. The gate was therefore
exercised, but by a substituted agent type — worth knowing if the custom reviewer types are fixed
later and results differ. All 41 findings were triaged and applied. Two resolutions narrow the
reviewer's literal recommendation, each with the reasoning inline: the `"LLM provider"` prefix is
deliberately still absent from `_require_usable`'s messages (adding it would break the design's
verbatim-lift invariant, and the absence is now the 400/502 discriminator), and the SSRF surface is
documented in Scope rather than blocked (blocking would break the shipped local-Ollama preset).

## Plan Review (Step 4)

_Independent post-finalization review against the live codebase at 35e9d7e. Findings triaged at Step 5._

_Dispatch note: the `artifact-code-reviewer` and `artifact-coverage-reviewer` subagent types both terminated with empty output after 2 tool calls — the identical failure mode the design artifact recorded for `slice-verifier`. Both reviews were re-run as `general-purpose` agents with the same review contracts, which completed normally (12 and 9 tool calls). The findings below are those runs._

**Tally: 9 blockers, 23 concerns, 9 suggestions.**

| source | plan-loc | codebase-loc | severity | dimension | finding | recommendation | resolution |
| --- | --- | --- | --- | --- | --- | --- | --- |
| code | Phase 5 §Automated, `grep -rn "keyHeader\|geminiHeader\|needsGeminiKey\|needsKey"` | `dashboard/src/components/ResultCard.jsx:291` | blocker | actionability | `geminiHeader` is a substring of ResultCard's `geminiHeaders` (lines 291, 298, 330), which the plan explicitly leaves untouched, so this gate can never return nothing. | Anchor the pattern (`\bgeminiHeader\b`) or scope it to `ThumbnailStudio.jsx` and `SaaShortsTab.jsx`. | applied: Phase 5 criterion now uses `grep -rnE "\b(...)\b"` scoped to the two changed components, so ResultCard's untouched `geminiHeaders` cannot match. |
| code | Phase 4 §Automated, `grep -c "aiBackendMissing"` returns 8 | `dashboard/src/App.jsx` | blocker | actionability | The Phase 4 fences emit 12 lines containing `aiBackendMissing`, not 8. | Change the criterion to 12 and drop the self-contradicting "plus the block conditions" clause. | applied: criterion now asserts 12 and the open-ended "plus the block conditions" clause is deleted. |
| code | Phase 2 §Automated, `grep -rn "gemini_key"` returns exactly one hit | `dashboard/src/App.jsx:211` | blocker | actionability | The migration fence itself contains two `gemini_key` lines — `getItem` and `removeItem` — so the post-change count is 2. | State 2 hits, both inside the `useState` migration initializer in `App.jsx`. | applied: criterion now asserts two hits, both named as the `getItem` read and the `removeItem` cleanup inside the migration initializer. |
| code | Phase 3 §Automated, `grep -n "apiFetch" dashboard/src/lib/llm.js` returns nothing | `dashboard/src/lib/llm.js` (new) | blocker | actionability | The fence's own header comment reads "Deliberately NOT attached inside apiFetch", so the grep matches that comment line. | Assert on `import .*lib/api` or `apiFetch(` instead of the bare token. | applied: pattern changed to `apiFetch\(` OR-ed with `from '\.\./lib/api'`, with the reason the bare token cannot be used recorded inline. |
| code | Phase 1 §2, `cfg = await resolve_llm(request)` | `app.py:143` | blocker | code-quality | `resolve_llm` is called with `task=None` while `_env_llm_config` deliberately loops `("thumbnail","saas")`, so an `LLM_MODEL_THUMBNAIL`-only server reports `llmConfigured:true` but `POST /api/llm/test` returns 400. | Mirror `_env_llm_config`: try `resolve_llm(request, task="thumbnail")` then `"saas"` before the 400, and add a test. | applied (plan-local; design follow-up: `.rpiv/artifacts/designs/2026-08-30_16-58-07_connect-llm-provider-frontend.md` §Architecture → app.py): the fence now loops `("thumbnail","saas")` mirroring `_env_llm_config`, and Phase 1 §4 gains `test_a_task_only_model_server_can_be_tested`. Verified against the live `config_from` (llm_client.py) — the `LLM_MODEL_<TASK>` branch is genuinely skipped when `task` is None. |
| coverage | Phase 5 → Success Criteria (banned-token grep) | `dashboard/src/components/ThumbnailStudio.jsx:76` | blocker | verification-coverage | The plan's own emitted comments contain the banned tokens ("Renamed from keyHeader", "renamed from geminiHeader"), so this criterion is guaranteed to fail on correct code. | Scope the pattern to identifiers and exclude comment lines, or drop the words from the comments. | applied: the same Phase 5 criterion edit adds a `grep -v "//"` stage, so the two "renamed from …" comments this plan emits are excluded. |
| coverage | Phase 1 → Success Criteria (`sed -n '/^def probe/,/^$/p' \| grep -c "_http_client"` returns 0) | `llm_client.py:137` | blocker | verification-coverage | `sed` stops at the first blank line — line 2 of `probe()`'s own docstring — so the range never reaches the body and the count is 0 unconditionally; this is the only automated criterion asserting D6. | Replace with `python -c "import inspect,llm_client; assert '_http_client' not in inspect.getsource(llm_client.probe)"`. | applied: replaced with `./.venv/Scripts/python.exe -c "import inspect, llm_client; assert '_http_client' not in inspect.getsource(llm_client.probe)"`, and the reason `sed` cannot be used is recorded inline. |
| coverage | Phase 4 → Success Criteria (`aiBackendMissing` = 8) | `dashboard/src/App.jsx:708` | blocker | verification-coverage | The Phase 4 fence emits 12 matching lines, and the trailing "plus the block conditions" makes an exact integer simultaneously open-ended. | Change the asserted count to 12 and delete the trailing clause so the criterion is a single falsifiable number. | applied: same edit as the paired `code` row — asserts 12, trailing clause deleted. |
| coverage | Phase 2 → Success Criteria (`gemini_key` = one hit) | `dashboard/src/App.jsx:211` | blocker | verification-coverage | The migration block emits two `gemini_key` lines, so the criterion fails against correct code. | Assert two hits inside the `App.jsx` migration, keeping the separate assertion that `ResultCard.jsx` no longer appears. | applied: same edit as the paired `code` row — asserts two hits, `ResultCard.jsx` assertion retained. |
| code | Phase 4 §1, `const llmActive = llmConfigComplete(llmConfig) \|\| llmConfigured` | `dashboard/src/contexts/AuthContext.jsx:18,104` | concern | code-quality | `/api/config` resolves asynchronously, so on an env-only-configured self-host `keysMissing` is true on first paint — banner/badge flash, and a Generate click in that window hits `setShowKeyModal(true)` (`App.jsx:774`). | Suppress the gate while `useAuth().loading` is true, or seed `llmConfigured` from a cached value. | applied (plan-local; design follow-up): `keysMissing` now also requires `!authLoading`, sourced from `useAuth().loading` which AuthContext already exposes at :139 — no new context field needed. Phase 4 gains a no-flash manual bullet. |
| code | Phase 3 §2, `useState(!serverConfigured \|\| llmConfigComplete(config))` | `dashboard/src/App.jsx:530,1235` | concern | code-quality | `showForm` is frozen at mount from an async prop; when a persisted session restores `activeTab==='settings'` the card mounts before `/api/config` lands, so the collapsed status-block state and the override button never appear. | Derive it (`const showForm = expanded \|\| !serverConfigured \|\| llmConfigComplete(config)`) instead of freezing it in `useState`. | applied (plan-local; design follow-up): `showForm` is now derived — `expanded \|\| !serverConfigured \|\| llmConfigComplete(config)` — with a new `expanded` useState driving only the override button. |
| code | Phase 4 §1, badge `(App.jsx:~1084-1090)` and banner `(App.jsx:~1104-1110)` | `dashboard/src/App.jsx:1169-1175, 1189-1195` | concern | actionability | Both anchors are ~85 lines off; 1084-1110 is the mobile tab bar and top-header shell, not the badge or banner. | Repoint to 1169-1175 (badge) and 1189-1195 (banner). | applied: both comments now use prose anchors ("the `hidden md:inline` span in the header", "the `text-muted` span in the keysMissing banner") rather than line numbers, per the triage decision to swap the whole class. |
| code | Phase 5 §3, mounts `(App.jsx:~1409)` and `(App.jsx:~1560)` | `dashboard/src/App.jsx:1470, 1608` | concern | actionability | Both mount anchors are 48-61 lines off, and the `SaaShortsTab` mount is currently a single line, not the 3-line form the fence implies. | Repoint to 1470 and 1607-1620, and note the single-line → multi-line reformat. | applied: prose anchors, and the SaaShortsTab comment now states the mount is currently a single line being reformatted to three. |
| code | Phase 1 §1, "chat()'s inline guard at `:363-374`" | `llm_client.py:362-373` | concern | actionability | The guard actually spans 362-373; a literal 363-374 replacement leaves the stale `base_url = ...strip()` on 362 and consumes the blank separator on 374. | Cite 362-373 as the replaced range. | applied: range corrected to 362-373 and the comment now names the first and last statements of the range so the boundary is checkable without trusting the digits. |
| code | Phase 1 §2, `_env_llm_config` docstring | `llm_client.py:150-178` | concern | code-quality | The task loop only avoids the "stays inactive" warning for `LLM_MODEL_THUMBNAIL`; an `LLM_MODEL_SAAS`-only server still prints it on the first `/api/config`, and the manual check only covers the thumbnail case. | Reorder to read `LLM_MODEL` first, or add an `LLM_MODEL_SAAS`-only case and soften the docstring claim. | applied: the docstring gains an NB stating the loop narrows the warning rather than eliminating it; Phase 1 gains `test_a_saas_only_model_server_can_be_tested` and a manual bullet that expects the warning on an LLM_MODEL_SAAS-only server. |
| code | Phase 1 §2, `POST /api/llm/test` | `app.py:230-266` | concern | code-quality | Unauthenticated on self-host, it turns an arbitrary `X-LLM-Base-Url` into a server-side POST whose provider error body is echoed in the 502 detail plus a `latencyMs` readout; "cannot be used as a cheap latency oracle" overstates 15/hour. | Note the SSRF/oracle exposure in "What We're NOT Doing", or reject loopback/link-local/private targets before probing. | applied: documented rather than blocked — a new "What We're NOT Doing" entry records the SSRF/oracle reach, because rejecting RFC1918 and loopback would break the `http://localhost:11434/v1` local-Ollama preset the card ships. The overstated "cannot be used as a cheap latency oracle" claim is corrected there. |
| code | Phase 1 §4, `test_the_key_is_never_echoed_back` | `tests/test_llm_endpoints.py` (new) | concern | code-quality | It monkeypatches `probe` to a no-op, so it only exercises the 200 path and never the 502 branch where `_post`'s `_err_detail(resp)[:300]` could carry a provider echo of the key. | Add a failing-probe variant whose `LlmError` message embeds the key and assert it is absent from the response. | applied: added `test_the_key_is_not_echoed_on_the_failure_path_either`, whose LlmError embeds the BYOK key and asserts it is absent from the 502 body. |
| code | Phase 5 §1, "the four header call sites" | `ThumbnailStudio.jsx:172,218,241,288,358` | concern | actionability | The heading says four while the block lists five, and the `grep -c aiHeaders` = 6 criterion assumes five. | Change the heading to "the five header call sites". | applied: heading corrected to "the five header call sites"; the `grep -c aiHeaders` = 6 criterion was already right. |
| code | Phase 2 §2 `(App.jsx:222-226)`, `(App.jsx:572-575)`; §1 `(AuthContext.jsx:140-152)` | `App.jsx:225-230, 571-575`; `AuthContext.jsx:135-153` | concern | actionability | `falKey` is at 225-230 not 222-226, the persistence effect starts at 571, and the `value` object spans 135-153 with `jobRetentionSeconds` at 138 — outside the cited range. | Correct all three anchors; the "after jobRetentionSeconds" prose anchor is the only unambiguous one today. | applied: all three anchors replaced with prose ("immediately after the `falKey` useState", "the useEffect writing `gemini_key`", "on the line after `jobRetentionSeconds`"). |
| coverage | `## Verification Notes` §2 / Manual Testing Steps §2 | `dashboard/src/lib/llm.js` (new) | concern | verification-coverage | "Never send base URL without key or key without base URL" appears in no phase's Success Criteria — nothing exercises `llmHeaders` returning `{}` for a base-only or key-only config. | Add a Phase 3 criterion asserting a base-only (and a key-only) config produces zero `X-LLM-*` headers. | applied: `llm.spec.js` (new, Phase 3 §4) asserts base-only, key-only and whitespace-only configs all yield `{}`, and a Phase 3 criterion runs it. |
| coverage | Phase 3 → Success Criteria (whole block) | `dashboard/package.json:6-11` | concern | verification-coverage | `dashboard/package.json` has no `test` script and no vitest/jest dependency, so `lib/llm.js` — the only pure-logic module in the change set, encoding D9 and D2 — has zero automated coverage. | Add vitest plus a `test` script and four assertions over `llmHeaders`/`llmConfigComplete`. | applied: Phase 3 §4 adds a `test": "vitest run"` script, vitest as a devDependency, and `src/lib/llm.spec.js` covering D9 and D2. |
| coverage | Phase 5 → Success Criteria (`npm test` — "if a frontend suite exists") | `dashboard/package.json:6` | concern | verification-coverage | `npm test` exits with "Missing script: test", and the escape clause converts that failure into a recorded note, so this criterion can never fail. | Either land the vitest suite and make it a hard pass, or delete the bullet rather than leaving an unfailable check. | applied: the escape clause is gone — Phase 5 now requires `npm test` to pass against the suite Phase 3 lands. |
| coverage | Phase 1 → Automated bullet 7 ("no `api_key` under any env combination") | `tests/test_llm_client.py:483-488` | concern | verification-coverage | The bullet carries no command, "under any env combination" is unbounded and so unfalsifiable, and the cited test pins `repr(LlmConfig)` rather than the endpoint payload. | Re-point it at `test_the_api_key_never_appears_in_the_payload` and enumerate the exact env combinations. | applied: re-pointed at `test_the_api_key_never_appears_in_the_payload` by name, with the three env states it covers enumerated, and the misleading `test_llm_client.py:485-488` citation removed. |
| coverage | Phase 1 → Success Criteria (D3 billing case) | `tests/conftest.py:11` | concern | verification-coverage | `conftest.py` sets `BILLING_ENABLED=0` at import, so the `/api/llm/test` 404 and the forced-off `/api/config` triple have no automated coverage — only one manual restart bullet covers both surfaces. | Add a subprocess/reimport test booting `app` with `BILLING_ENABLED=1` asserting the 404 plus `llmConfigured:false` with full `LLM_*` env. | applied: added `TestBillingDisablesTheWholeSurface`, which boots `app` in a subprocess with `BILLING_ENABLED=1` plus full `LLM_*` env and asserts both the forced-off `/api/config` triple and the 404 on the probe. |
| coverage | Phase 2 → Success Criteria | `dashboard/src/contexts/AuthContext.jsx:138` | concern | verification-coverage | Phase 2 has no `BILLING_ENABLED=1` bullet at all, even though it is the phase surfacing the three fields and its own comment claims "under billing the backend forces these off"; Phases 1, 3, 4, 5 each carry one. | Add a Phase 2 manual bullet asserting `useAuth().llmConfigured` is false under `BILLING_ENABLED=1` with full `LLM_*` env. | applied: Phase 2 gains a manual bullet asserting `useAuth().llmConfigured` stays false under billing with full `LLM_*` env. |
| coverage | `## Migration Notes` (rollback bullet) | `dashboard/src/App.jsx:211` | concern | verification-coverage | The rollback path is documented but checkable nowhere, and the migration is destructive (`removeItem('gemini_key')`), so a downgrade silently empties the Gemini key box. | Add a Phase 2 manual bullet: migrate, revert to the pre-change bundle, confirm the app loads and the key is re-enterable with no console error. | applied: Phase 2 gains a rollback manual bullet — migrate, revert the bundle, confirm the app loads and the key is re-enterable — which pins the documented downgrade cost at exactly "re-enter once". |
| coverage | `## Decisions` D7 (Phase 1 tests; Phase 5 manual) | `dashboard/src/lib/api.js:29` | concern | verification-coverage | D7's specific claim — a provider's own 402 must never render as an OpenShorts top-up prompt — is never exercised: the failure tests use 401 and the Phase 5 manual bullet uses an unreachable URL. | Add an endpoint test where the provider returns 402 and assert the response is 502 with the provider's own `LLM provider ...` detail. | applied: added `test_a_provider_402_is_never_forwarded_as_402`, which makes the provider raise on HTTP 402 and asserts the response is 502 with the provider's own detail, never 402. |
| coverage | `## Pattern References` (`mcp_server.py:53-55`) | `app.py:163-166` | concern | verification-coverage | No criterion cross-checks the three header names `llmHeaders` emits against the names `resolve_llm` reads, so a typo such as `X-LLM-BaseUrl` would pass build, eslint and every `grep "X-LLM"` bullet. | Add a Phase 3 criterion asserting the exact literals appear in `lib/llm.js` and match `app.py:163-166`. | applied: Phase 3 gains a criterion diffing `grep -o "X-LLM-[A-Za-z-]*"` over `lib/llm.js` against the same over `app.py`, so a casing typo fails mechanically. |
| coverage | Phase 3 → Success Criteria (`grep -n "X-LLM" dashboard/src/`) | `<n/a>` | concern | verification-coverage | Non-recursive `grep -n` against a directory emits "Is a directory" and no matches, so the criterion vacuously passes; Phases 2 and 5 correctly use `grep -rn`. | Change to `grep -rn` and state the expected result as "hits in `src/lib/llm.js` only". | applied: changed to `grep -rn`, with the reason the non-recursive form passes vacuously recorded inline. |
| coverage | Phase 1 → Manual bullets 4 and 6; Phase 3 → Manual bullet 5 | `<n/a>` | concern | verification-coverage | Three bullets require a live third-party provider account with no offline substitute, so a reviewer without an Ollama Cloud account cannot complete them. | Offer a local stub (`/chat/completions` responder on `localhost`) as the documented substitute, keeping the real-provider run optional. | applied: the Phase 1 bullet now carries a one-line local `/chat/completions` stub as the documented offline substitute, keeping the real-provider run optional. |
| coverage | `## Verification Notes` §5 / Manual Testing Steps §5 | `app.py:2250` | concern | verification-coverage | The note cites the `LLM_*` prefix sweep from managed job envs as part of "the whole surface must vanish under `BILLING_ENABLED`", but no phase criterion exercises that sweep. | Either assert a managed job env contains no `LLM_*` key, or mark the sweep as pre-existing backend coverage out of scope. | applied: scoped out explicitly — the sweep at `app.py:2250` is pre-existing backend behaviour with its own coverage, and the new `TestBillingDisablesTheWholeSurface` covers what this plan actually adds under billing. |
| coverage | `## Developer Context` (inherited slice-verifier gap) | `<n/a>` | concern | verification-coverage | The documented gap is real and load-bearing exactly where predicted: of the five count-based criteria, `aiBackendMissing`=8 and `gemini_key`=1 are arithmetically wrong and the `probe`/`_http_client` sed range is vacuous. | Re-derive every count mechanically from each phase's own code fence before implementation starts. | applied: every count-based criterion was re-derived from its own phase fence during this triage — `aiBackendMissing` 8 to 12, `gemini_key` 1 to 2, the vacuous sed range replaced, and `_require_usable`/`geminiKey_v1`/`aiHeaders`/`needsGeminiForImages` confirmed correct at 3/3/6/5. |
| code | Phase 1 §1, `_require_usable` raise sites vs `HTTPException(502, str(e))` | `llm_client.py:364-373` | suggestion | code-quality | The two lifted messages do not carry the load-bearing `"LLM provider"` prefix, and a caller-side malformed-URL/missing-model rejection surfaces as a 502 rather than a 400. | Catch `LlmError` from `_require_usable` separately and return 400, leaving 502 for real provider failures. | applied in part: the 400-vs-502 split is implemented, using the presence of the `"LLM provider"` prefix as the discriminator. The prefix was deliberately NOT added to the two `_require_usable` messages — doing so would reword them and break both the design's verbatim-lift invariant (a green probe must mean chat() is callable) and its "do not reword" constraint. The absence of the prefix is now load-bearing in a second way. |
| code | Phase 3 §2, `setTimeout(() => setSaved(false), 2000)` | `dashboard/src/App.jsx:1235` | suggestion | code-quality | The Settings tab is conditionally rendered, so switching tabs within 2s unmounts the card while the timer still holds a `setSaved` reference. | Store the id and clear it in a `useEffect` cleanup. | applied: the timer id is held in a `useRef` and cleared both on re-save and in a `useEffect` unmount cleanup; the card now imports `useEffect` and `useRef`. |
| code | Phase 3 §2, all eight `<button>` elements | `LlmProviderCard.jsx` (new) | suggestion | code-quality | None carry `type="button"`; harmless today because `App.jsx` contains no `<form>`, but the default `submit` type becomes a live bug if the card is ever nested in one. | Add `type="button"` to every button in the card. | applied: `type="button"` added to all six button elements in the card. |
| code | Phase 2 §2, the `apiKey` migration initializer | `dashboard/src/App.jsx:44-62` | suggestion | code-quality | `decrypt` returns `''` for a corrupt `ENC:` payload rather than throwing, so a corrupted `geminiKey_v1` returns empty and the legacy `gemini_key` fallback is skipped — the key is silently lost. | Fall through to the legacy branch when `decrypt(stored)` yields an empty string. | applied: the migration now falls through to the legacy plaintext value when `decrypt` yields an empty string, so a corrupted blob cannot silently discard a key the plaintext copy could still restore. |
| coverage | Phase 4 → Success Criteria (`grep -n "const keysMissing"`) | `dashboard/src/App.jsx:708` | suggestion | verification-coverage | The command only prints the line; the `!billingEnabled &&` assertion is left to the reader's eye, so it cannot mechanically fail if the guard is dropped. | Fold the invariant into the pattern: `grep -n "const keysMissing = !billingEnabled"` must match. | applied: pattern tightened to `grep -n "const keysMissing = !billingEnabled"`, so the invariant fails mechanically rather than by eye. |
| coverage | Phase 1 → Automated bullet 6 (`curl \| python -c ...`) | `tests/test_llm_endpoints.py` (new) | suggestion | verification-coverage | Filed as Automated but needs a running server and invokes a bare `python` while every sibling uses `./.venv/Scripts/python.exe`; `test_existing_fields_are_unchanged` already covers the invariant offline. | Move it to Manual Verification and cite the test as the automated equivalent. | applied: moved to Manual Verification, and the Automated slot now cites `test_existing_fields_are_unchanged` as the offline equivalent. |
| coverage | Phase 5 → Changes Required §1 vs `aiHeaders` = 6 | `ThumbnailStudio.jsx:172,218,241,288,358` | suggestion | verification-coverage | The prose says "four header call sites" while listing five and the criterion asserts 6 = definition + 5; the live file confirms five, so 6 is correct and only the heading is wrong. | Correct the heading to "the five header call sites". | applied: same edit as the paired `code` row — heading corrected to five. |
| coverage | `## Testing Strategy` → Manual Testing Steps §10 | `<n/a>` | suggestion | verification-coverage | The precedent that Phase 1 alone is not shippable is stated but enforced by no criterion and no ordering constraint — the plan, unlike the design, has no `## Ordering Constraints` section. | Carry the design's ordering constraints into the plan and add a release gate stating Phases 1-5 merge as one unit. | applied: the plan gains an `## Ordering Constraints` section carrying the design's constraints verbatim plus an explicit "Phases 1-5 merge as one unit" release gate. |
| coverage | Phase 3 §2 card copy / `900dc44` precedent | `<n/a>` | suggestion | verification-coverage | The card ships the user-visible claim "a job that resumes after a backend restart falls back to Gemini", which no criterion verifies. | Add a Phase 4 manual bullet: start a provider-backed job, restart the backend mid-run, confirm the resumed job behaves as the copy states. | applied: Phase 4 gains a manual bullet that restarts the backend mid-job and confirms the resumed job behaves as the copy states. |

## References

- Design: `.rpiv/artifacts/designs/2026-08-30_16-58-07_connect-llm-provider-frontend.md`
- Research: `.rpiv/artifacts/research/2026-08-30_16-03-23_connect-llm-provider-frontend.md`
- Backend design (its decision Q3 "no UI" is reversed here): `.rpiv/artifacts/designs/2026-08-30_08-58-35_openai-compatible-llm-provider.md`
- Backend phased plan: `.rpiv/artifacts/plans/2026-08-30_12-08-05_openai-compatible-llm-provider.md`
- Backend validation report: `.rpiv/artifacts/validation/2026-08-30_15-13-19_openai-compatible-third-party-llm-endpoint-alongside-gemini-additive.md`
- Precedents: `29681ba` (fal.ai BYOK end-to-end), `03477d4` (ElevenLabs BYOK), `d0e1f5a` (`/api/config` field), `05578c5` (`keysMissing` gate), `900dc44` (`X-Gemini-Key` resume-loss accepted)
