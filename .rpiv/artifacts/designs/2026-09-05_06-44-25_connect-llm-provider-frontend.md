---
date: 2026-09-05T06:44:25+0700
author: Yogiswara Utama
commit: 35e9d7e
branch: main
repository: openshorts
topic: "Connect the OpenAI-compatible LLM provider to the dashboard frontend"
tags: [design, llm-provider, dashboard, byok, openai-compatible, frontend, settings, api-config, probe-endpoint, pire-browser]
status: ready
parent: .rpiv/artifacts/research/2026-09-04_20-47-42_connect-llm-provider-frontend.md
last_updated: 2026-09-05T06:44:25+0700
last_updated_by: Yogiswara Utama
---

# Design: Connect the OpenAI-compatible LLM provider to the dashboard frontend

## Summary

A new AI Provider card in self-host Settings collects an OpenAI-compatible endpoint URL, API key and model, stores them as one encrypted blob in the browser, and sends them as the `X-LLM-*` header triple built per component — the same shape `X-Gemini-Key` already uses. `GET /api/config` gains three read-only fields so the dashboard sees a server-side `LLM_*` setup. A new `POST /api/llm/test` drives a "Test connection" button with status-code discrimination and key redaction. The app's front door widens from "has a Gemini key" to "has any AI backend".

This design adopts the parent design (D1–D9, 5-phase structure, full architecture code) with four research corrections: status-code discrimination on `/api/llm/test` via `startswith("LLM provider")`, AuthContext config-fetch retry, hardened decrypt migration guard with a JSON-canary blob (round-trip re-encrypt-and-compare was proven an XOR no-op during Slice 2 verification), and a `_redact()` closure on the probe endpoint.

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

The backend LLM core is shipped: `resolve_llm` (`app.py:143`) plus six call sites, `llm_client.py` (`config_from`, `active_config`, `_post`, `chat`), and the `LLM_ENDPOINT_HINT` string. The frontend has zero provider code.

The Phase 1 deliverables — `POST /api/llm/test`, `probe()`, `_require_usable()`, `_env_llm_config`, the `/api/config` LLM fields, `lib/llm.js`, `LlmProviderCard.jsx`, `tests/test_llm_endpoints.py` — are NOT in the tree at HEAD `35e9d7e`, despite a 2026-08-31 handoff reporting them complete. The plan artifact's checked `[x]` boxes describe lost code. Phase 1 must be implemented fresh.

### Key Discoveries

- **`config_from` requires a model, not just the pair** (`llm_client.py:150-178`). Base URL + key with no model anywhere resolves to `None`. This drove the "all three required" form rule (D2).
- **The 402 / `QuotaError` mis-render risk does not exist.** `LlmError` reaches the client as `HTTPException(500, detail=str(e))` (`app.py:4979-4981`). `apiFetch`'s 402 branch (`lib/api.js:32`) never fires. No new error-handling code needed (D7).
- **`/api/config` has two consumers, not one.** `AuthContext.jsx:104` and `MediaInput.jsx:52`, which reads `youtubeUrlEnabled` directly. `MediaInput` is additive-safe and needs no change.
- **`ResultCard.jsx:284`'s `localStorage.getItem('gemini_key')` fallback is dead code.** `App.jsx` always passes `geminiApiKey={apiKey}`. The storage migration would strand it.
- **`llm_client._clients` is an unbounded per-base-URL `httpx.Client` cache with a 300s read timeout** (`llm_client.py:130-147`). A Test-connection button must not use it (D6).
- **`POST /api/thumbnail/generate` is the one inverted gate** (`app.py:5003-5006`): `resolve_gemini` first, hard fail, `resolve_llm` only afterwards. Image generation is Gemini-only by physics.
- **Storage convention**: `encrypt`/`decrypt` (`App.jsx:35-62`) are XOR + base64 with an `ENC:` prefix, used under versioned names `uploadPostKey_v3`, `elevenLabsKey_v1`, `falKey_v1`. `gemini_key` alone is plaintext (`App.jsx:211,574`).
- **Self-host has no authentication.** `get_current_user_optional` is a cloud-only import with a stub fallback (`app.py:106-121`). This ruled out a save-to-server config endpoint (D1).
- **AuthContext fetch is bare and one-shot.** `AuthContext.jsx:104` does a bare `fetch(getApiUrl('/api/config'))` with no `res.ok` check. On any failure the catch swallows it; `config` keeps its 2-key init and `loading=false` runs unconditionally. A failed fetch leaves `llmConfigured` false forever — the banner fires permanently on an env-configured server. Decision: retry the fetch.
- **`decrypt` garbage case.** `decrypt` (`App.jsx:48-67`) never throws, but a valid-base64-but-corrupt payload returns a non-empty garbage string (`:55-57`). A `VITE_ENCRYPTION_KEY` rotation turns every stored blob into the garbage case. Decision: harden the guard with a JSON canary — `geminiKey_v1` stores `encrypt(JSON.stringify({key}))` and the initializer `JSON.parse`s it; garbage never parses. (Round-trip re-encrypt-and-compare was proven an XOR no-op during Slice 2 verification and rejected — see D12.)
- **Status-code contract on `/api/llm/test`.** The endpoint must answer: 404 (billing), 429 (limiter), 400 (no config resolves, malformed URL, missing model), 502 (upstream rejection, transient, blocked). Never 402. The discriminator uses `startswith("LLM provider")`, not `in`, because every `_post`-family message starts with that literal prefix and provider error text is interpolated after it.
- **One-off probe client is required, not stylistic.** `_http_client` (`llm_client.py:137-147`) hardcodes `timeout=_TIMEOUT` (read=300s) and caches by `base_url` with no timeout parameter. A connect-5/read-20 probe cannot ride it without a signature + cache-key change.
- **`_redact()` closure.** The 2026-08-31 handoff records a `_redact()` closure at the endpoint boundary that strips the key from provider error text before echoing `detail`. Our code cannot format the key into detail (the key lives only in the request header), but a provider's error body could echo the `Authorization` header. Belt-and-suspenders measure to re-add.
- **`task=None` trap.** `config_from` reads `LLM_MODEL_<TASK>` only when a task is named. The `_env_llm_config` helper must loop `("thumbnail","saas")` so a server with only `LLM_MODEL_THUMBNAIL` resolves.
- **MCP `_FORWARD_HEADERS` already includes the X-LLM-* triple** (`mcp_server.py`): `"x-llm-base-url"`, `"x-llm-key"`, `"x-llm-model"`. The MCP path is a live reference implementation of the exact header triple.
- **SaaS generate is NOT Gemini-pinned.** `/api/saasshorts/generate` (`app.py:5770-5785`) takes only `X-Fal-Key` + `X-ElevenLabs-Key`. The feared SaaS generate seam does not exist.
- **ResultCard pins two endpoints, not six.** `/api/effects/generate` and `/api/edit` are Gemini-only. The backend 400 is unreachable from `handleAutoEdit` because the client-side gate at `ResultCard.jsx:288` throws first.

## Scope

### Building

- `llm_client.probe(config)` — one short-timeout, non-cached live call reusing `_post`'s error mapping.
- `GET /api/config` gains `llmConfigured`, `llmModel`, `llmBaseUrl`.
- `POST /api/llm/test` — self-host-only connection check with status-code discrimination and `_redact()` closure.
- `AuthContext` exposes the three new config fields through `useAuth()`, with fetch retry.
- `dashboard/src/components/LlmProviderCard.jsx` — the Settings card: three fields, quick-fill presets, triple validation, Test connection, server-status display.
- `App.jsx` — `llmConfig` state under `llmConfig_v1`, `gemini_key` to `geminiKey_v1` migration with hardened decrypt guard, `X-LLM-*` headers on `/api/process`, widened `keysMissing`, updated banner / header badge / required-keys modal copy.
- `ThumbnailStudio.jsx` and `SaaShortsTab.jsx` — `llmHeaders` alongside the existing Gemini header, `needsAiBackend` replacing the Gemini-only flag, and a generate-step-only Gemini note in the Studio.
- `tests/test_llm_client.py` — pin `probe`, the `/api/config` fields and `/api/llm/test`.

### Not Building

- **Server-side persistence of provider config.** Self-host has no auth, so a write endpoint would let anyone reachable redirect the install's AI traffic. Browser + headers only.
- **Centralising BYOK headers in `apiFetch`.** Would send the provider key on uploads and social posting. Per-component construction is the house style.
- **`ResultCard.jsx` beyond one dead line.** Its endpoints are Gemini-pinned; the client-side gate already fails fast with a truthful message.
- **Any cloud/billing provider surface.** `resolve_llm` returns `None` under `BILLING_ENABLED`.
- **Fixing the unbounded `_clients` cache.** Pre-existing, orthogonal to this feature.
- **Reordering `cloud/alerts.py::_classify_failure`.** Corrected twice in production; the `"LLM provider"` prefix strings stay verbatim.
- **Rewording any `llm_client` error message.** `"LLM provider"` is a reserved, alert-classifying string.
- **Blocking private or loopback probe targets.** The probe reaches any URL the caller names — a reviewed SSRF row, rate-capped but not reach-capped.

## Decisions

### D1: The provider surface is browser-stored and header-carried, not server-persisted

Self-host has no auth (`app.py:106-121`), so a write endpoint would be world-writable. Browser + headers, matching `X-Gemini-Key`. Resume-loss is accepted precedent.

### D2: All three form fields are required; Save is disabled otherwise

`config_from` needs base + key + a resolved model. The silent-inert state is unreachable from the UI. Costs one retyped model on servers that already have one.

### D3: `/api/config` reports configured-state, model and base URL — never the key

The endpoint is served pre-auth. Report `llmConfigured`, `llmModel` and `llmBaseUrl`. The key is never included. Under `BILLING_ENABLED` all three are forced off.

### D4: YouTube Studio stays usable on an LLM-only setup; only image generation is labelled

`ThumbnailStudio.jsx:507`'s whole-step gate is re-pointed at `needsAiBackend = !geminiApiKey && !llmActive && !managed`. `handleGenerate` keeps a Gemini-specific check, and the generate button carries an inline note.

### D5: The Settings card is its own component file

`App.jsx` is 2093 lines. `KeyInput.jsx` and `McpConnectCard.jsx` are the established precedent for a self-contained Settings card.

### D6: `probe()` uses a one-off short-timeout client, not `_http_client`

`_clients` is an unbounded cache and `_TIMEOUT` reads for 300s. `probe()` constructs its own client with connect 5s / read 20s and closes it, while reusing `_post` so the status-to-error mapping stays identical.

### D7: No new error-handling code

`LlmError` reaches the client as `HTTPException(500, detail=str(e))`. `apiJson` carries string details. Existing callers already read `detail`.

### D8: State lives in `App.jsx` and travels as props

`llmConfig` sits next to `apiKey`, `elevenLabsKey` and `falKey`, persisted by the same effect shape, and is passed to `ThumbnailStudio` and `SaaShortsTab` as props.

### D9: `X-LLM-Model` is omitted, never sent empty

A whitespace-only model falls back to the env chain (`tests/test_llm_client.py:707`). The header is only added when the model is non-empty.

### D10: Status-code discrimination on `/api/llm/test` via `startswith("LLM provider")`

The endpoint maps `LlmError` whose message `startswith("LLM provider")` to 502 and lifts the two prefix-free validation messages (malformed URL `llm_client.py:365-368`, missing model `:370-373`) to 400. `LlmTransientError` and `GeminiBlockedError` reach the unconditional 502. Never 402 — `apiFetch`'s 402 branch fires before `apiJson` sees the body.

### D11: AuthContext config-fetch retry

Add 1-2 retries with backoff to the `AuthContext.jsx:104` config fetch. Recovers transient failures; keeps single-shot semantics minimal. The fetch runs once per mount.

### D12: Hardened decrypt migration guard

`geminiKey_v1` stores `encrypt(JSON.stringify({key}))`, and the initializer `JSON.parse`s it with a shape check before adopting. The JSON shape is the rotation guard: `decrypt` never throws, and round-trip re-encrypt-and-compare (`encrypt(decrypt(stored)) === stored`) is a mathematical no-op because encrypt/decrypt XOR with the same keystream per index (any well-formed `ENC:` blob round-trips under any key) — proven during Slice 2 verification and rejected. Rotation garbage never survives `JSON.parse`: every bad path lands in the catch, falls through to the legacy plaintext migration, and starts empty when that is absent too. Catches `VITE_ENCRYPTION_KEY` rotation garbage.

### D13: `_redact()` closure on the probe endpoint

Strip `cfg.api_key` from the provider error `detail` before echoing it. Belt-and-suspenders: our code cannot format the key into detail, but a provider's error body could echo the `Authorization` header.

### D14: E2e validation via headers-only Python stub

Keep the simple `{choices:[{message:{content:'ok'},...}]}` stub on `localhost:11434`. Inspect the outgoing `/api/process` request headers via pire-browser. Accept the job fails at the LLM step (the FRD bullet only asks to confirm headers are present).

## Architecture

### llm_client.py — MODIFY

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

### app.py — MODIFY

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

### tests/test_llm_client.py — MODIFY

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

### tests/test_llm_endpoints.py — NEW

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

### dashboard/src/lib/llm.js — NEW

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

### dashboard/src/App.jsx — MODIFY

```jsx
// --- imports: after the other lib imports near the top of the file -------------
import { llmConfigComplete, llmHeaders } from './lib/llm';

// --- component import: between KeyInput and MediaInput (alphabetical) ----------
import LlmProviderCard from './components/LlmProviderCard';

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
// The config setter's only writer is the card's Save.
const [llmConfig, setLlmConfig] = useState(() => {
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

// --- useAuth destructure (line 201) — Slice 2's AuthContext fields land here ----
const { billingEnabled, isManaged, isSignedIn, me, plan, refreshMe, jobRetentionSeconds,
        llmConfigured, llmModel, llmBaseUrl } = useAuth();

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

// --- Slice 4: header badge copy — replaces the ternary at 1169-1174 -------------
                {needsAiBackend && !uploadPostKey
                  ? 'AI & Upload-Post keys missing'
                  : needsAiBackend
                    ? 'AI Key Missing'
                    : 'Upload-Post API Key Missing'}

// --- Slice 4: banner copy — replaces the ternary at 1189-1193 -------------------
                  {needsAiBackend && !uploadPostKey
                    ? 'Set an AI key and your Upload-Post key to use OpenShorts.'
                    : needsAiBackend
                      ? 'Set a Gemini API key or an AI provider to use OpenShorts.'
                      : 'Set your Upload-Post API key to use OpenShorts.'}

// --- Slice 4: SaaShortsTab mount (line 1470) gains the Slice-5 props ------------
            <SaaShortsTab geminiApiKey={apiKey} elevenLabsKey={elevenLabsKey} falKey={falKey} uploadPostKey={uploadPostKey} uploadUserId={uploadUserId} managed={isManaged} llmConfig={providerCfg} llmActive={llmActive} />

// --- Slice 4: ThumbnailStudio mount (1608-1621) gains the same two props --------
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

// --- Slice 4: required-keys modal — title ternary (1929-1931) -------------------
        title={needsAiBackend && !uploadPostKey
          ? 'Required API Keys Missing'
          : needsAiBackend
            ? 'AI Key Required'
            : 'Upload-Post API Key Required'}

// --- Slice 4: modal intro (1953-1956) --------------------------------------------
          <p className="text-sm text-muted">
            OpenShorts needs an <strong className="text-ink2">AI key</strong> — Gemini, or any
            OpenAI-compatible provider — and an <strong className="text-ink2">Upload-Post</strong> API key.
            Gemini and Upload-Post both have free tiers.
          </p>

// --- Slice 4: modal Gemini block — replaces the block at 1959-1990 --------------
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

### dashboard/src/contexts/AuthContext.jsx — MODIFY

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

### dashboard/src/components/ResultCard.jsx — MODIFY

```jsx
// --- handleAutoEdit (line 284): dead fallback removed ---------------------------
// Was: const apiKey = geminiApiKey || a dead localStorage fallback (the
// legacy plaintext key). App.jsx always passes geminiApiKey, and the
// geminiKey_v1 migration removes the key that fallback used to find.
const apiKey = geminiApiKey;
```

### dashboard/src/components/LlmProviderCard.jsx — NEW

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

### dashboard/src/components/ThumbnailStudio.jsx — MODIFY

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

### dashboard/src/components/SaaShortsTab.jsx — MODIFY

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

## Slices

### Slice 1: Backend — status channel + connection probe

**Files**: `llm_client.py`, `app.py`, `tests/test_llm_client.py`, `tests/test_llm_endpoints.py`

#### Automated Verification:
- [ ] `pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` passes (both in one process)
- [ ] `grep -c "def probe" llm_client.py` returns 1
- [ ] `grep -c "_probe_client" llm_client.py` returns at least 2 (definition + call in probe)
- [ ] `grep -c "_require_usable" llm_client.py` returns at least 3 (definition + call in chat + call in probe)
- [ ] `grep -c "/api/llm/test" app.py` returns 1
- [ ] `grep -c "llmConfigured" app.py` returns at least 1

#### Manual Verification:
- [ ] `POST /api/llm/test` with a BYOK triple probes the endpoint and returns `{ok: true, model, latencyMs}`
- [ ] `POST /api/llm/test` with no config returns 400 with `LLM_ENDPOINT_HINT`
- [ ] `POST /api/llm/test` under `BILLING_ENABLED` returns 404
- [ ] `/api/config` includes `llmConfigured`, `llmModel`, `llmBaseUrl` fields
- [ ] The API key never appears in any `/api/config` or `/api/llm/test` response body
- [ ] A server with only `LLM_MODEL_THUMBNAIL` resolves on the probe (task loop)

### Slice 2: Browser state — storage, migration, server-status wiring

**Files**: `dashboard/src/lib/llm.js`, `dashboard/src/App.jsx`, `dashboard/src/contexts/AuthContext.jsx`, `dashboard/src/components/ResultCard.jsx`

#### Automated Verification:
- [ ] `cd dashboard && npm run lint` passes with zero warnings
- [ ] `grep -c "llmConfigured" dashboard/src/contexts/AuthContext.jsx` returns at least 2
- [ ] `grep -rn "gemini_key" dashboard/src/` returns exactly 2 matches, both in App.jsx's initializer (one `getItem` + one `removeItem`)
- [ ] `grep -c "export const" dashboard/src/lib/llm.js` returns 2
- [ ] `grep -c "llmConfig_v1" dashboard/src/App.jsx` returns 2 (initializer read + persistence write)

#### Manual Verification:
- [ ] With a plaintext `gemini_key` in localStorage, reload → Settings shows the key, `gemini_key` removed, `geminiKey_v1` holds an `ENC:`-prefixed blob
- [ ] With `llmConfig_v1` set to garbage (`ENC:AAAA`), reload → app boots to the empty provider state, no console crash
- [ ] Rotation guard: with `geminiKey_v1` holding a blob encrypted under a different `VITE_ENCRYPTION_KEY`, reload → the Gemini key starts empty (no garbage adopted), app healthy
- [ ] "auto edit" on a result clip still sends `X-Gemini-Key` when a Gemini key is set (Network tab)
- [ ] With the API server stopped, reload → dashboard renders after ~1.5s of retries, no permanent spinner

### Slice 3: The AI Provider settings card

**Files**: `dashboard/src/components/LlmProviderCard.jsx`, `dashboard/src/App.jsx`

#### Automated Verification:
- [ ] `cd dashboard && npm run lint` passes with zero warnings
- [ ] `grep -c "export default function LlmProviderCard" dashboard/src/components/LlmProviderCard.jsx` returns 1
- [ ] `grep -c "LlmProviderCard" dashboard/src/App.jsx` returns 2 (import + mount)
- [ ] `grep -c "setLlmConfig" dashboard/src/App.jsx` returns 2 (destructure + `onConfigSet={setLlmConfig}`)
- [ ] `grep -c "llmConfigured" dashboard/src/App.jsx` returns at least 2 (destructure + card prop)
- [ ] `grep -c "/api/llm/test" dashboard/src/components/LlmProviderCard.jsx` returns 1
- [ ] `grep -c "llmHeaders" dashboard/src/components/LlmProviderCard.jsx` returns 2 (import + test call)
- [ ] `grep -c "llmConfigComplete" dashboard/src/components/LlmProviderCard.jsx` returns at least 3 (import + canSave + serverConfigured)

#### Manual Verification:
- [ ] Self-host Settings shows the AI Provider card between the Gemini key card and Social Integration; a BILLING_ENABLED deployment shows no provider surface at all
- [ ] Quick-fill chips fill the endpoint field (Ollama Cloud → `https://ollama.com/v1`)
- [ ] Save stays disabled until all three fields have content; after Save + reload the three fields are restored from `llmConfig_v1`
- [ ] Test connection against a live endpoint shows the latency line; a rejected key shows the provider's own message under "The provider refused the request:"; a malformed URL shows it under "This configuration is not usable:" (the 400 path)
- [ ] A server with `LLM_*` env and no local config shows the "Configured on the server" block with base URL and model plus the override button; clicking it reveals the form
- [ ] The key field is masked and the eye toggle works

### Slice 4: Unblock the app — headers, gates, copy

**Files**: `dashboard/src/App.jsx`

#### Automated Verification:
- [ ] `cd dashboard && npm run lint` passes with zero warnings
- [ ] `grep -c "llmHeaders" dashboard/src/App.jsx` returns exactly 2 (import + handleProcess spread)
- [ ] `grep -c "llmActive" dashboard/src/App.jsx` returns exactly 5 (definition + gate + modal suffix + two mount props)
- [ ] `grep -c "needsAiBackend" dashboard/src/App.jsx` returns exactly 11 (gate ×2 + badge ×2 + banner ×2 + modal title ×2 + modal block ×3)
- [ ] `grep -c "llmConfigComplete" dashboard/src/App.jsx` returns exactly 3 (import + Slice-2 persistence guard + gate)
- [ ] `grep -c "providerCfg" dashboard/src/App.jsx` returns exactly 5 (definition + gate + spread + two mount props)

#### Manual Verification:
- [ ] Provider triple saved + Upload-Post key, no Gemini key: no keys banner; a submitted job's `/api/process` request carries the `X-LLM-*` triple (Network tab)
- [ ] No Gemini key, no provider, no Upload-Post key: banner names "AI key" (not "Gemini"); the modal shows the provider-alternative line under the Gemini block
- [ ] Provider saved, no Gemini key: the modal's Gemini block reads "— covered by your AI provider" and hides the paste input
- [ ] Server `LLM_*` env (`llmConfigured` true), no local keys: no AI demand anywhere; only the Upload-Post half of the banner can fire
- [ ] Cloud absence (D3): on a `BILLING_ENABLED` deployment holding a stale `llmConfig_v1` blob, `/api/process` sends no `X-LLM-*` headers
- [ ] Regression: with a Gemini key set, `/api/process` still carries `X-Gemini-Key`; SaaShortsTab and ThumbnailStudio still receive `geminiApiKey`

### Slice 5: Per-feature capability split

**Files**: `dashboard/src/components/ThumbnailStudio.jsx`, `dashboard/src/components/SaaShortsTab.jsx`

#### Automated Verification:
- [ ] `cd dashboard && npm run lint` passes with zero warnings
- [ ] `grep -c "llmHeaders" dashboard/src/components/ThumbnailStudio.jsx` returns exactly 6 (import + five request spreads)
- [ ] `grep -c "llmHeaders" dashboard/src/components/SaaShortsTab.jsx` returns exactly 2 (import + analyze spread)
- [ ] `grep -c "needsAiBackend" dashboard/src/components/ThumbnailStudio.jsx` returns exactly 5 (definition + analyze guard + describe guard + banner + step-0 gate)
- [ ] `grep -c "needsGeminiImage" dashboard/src/components/ThumbnailStudio.jsx` returns exactly 4 (definition + guard + disabled + note)
- [ ] `grep -c "needsAiBackend" dashboard/src/components/SaaShortsTab.jsx` returns exactly 2 (definition + gate)
- [ ] `grep -c "needsKey" dashboard/src/components/ThumbnailStudio.jsx` returns 0 and `grep -c "needsGeminiKey" dashboard/src/components/SaaShortsTab.jsx` returns 0 (old flags fully replaced)
- [ ] Terminal slice: `cd dashboard && npm run build` succeeds
- [ ] Design regression set: `pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` passes

#### Manual Verification:
- [ ] LLM-only self-host (provider saved, no Gemini key): YouTube Studio opens with no key banner; analyze, title refine and describe all work; the Generate step shows the inline note and Generate stays disabled; nothing else is disabled
- [ ] Network tab: analyze / titles / describe / generate requests carry the `X-LLM-*` triple; upload, frames, publish and publish-status requests carry no `X-LLM-*` headers
- [ ] SaaShortsTab, LLM-only: analyze proceeds with no "Gemini API key required" error; the analyze request carries the `X-LLM-*` triple
- [ ] Regression, Gemini-only setup: banner and gates behave exactly as before this slice; thumbnail generate works
- [ ] Regression, managed cloud plan: no banner, generate enabled, no `X-LLM-*` headers on any request

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

## File Map

```path/to/file.ext  # NEW/MODIFY — purpose
```
llm_client.py  # MODIFY — probe(), _require_usable(), _probe_client(), _PROBE_TIMEOUT
app.py  # MODIFY — _env_llm_config(), /api/config LLM fields, POST /api/llm/test with _redact()
tests/test_llm_client.py  # MODIFY — probe() tests
tests/test_llm_endpoints.py  # NEW — HTTP-level contract tests for /api/config and /api/llm/test
dashboard/src/lib/llm.js  # NEW — llmHeaders(), llmConfigComplete()
dashboard/src/App.jsx  # MODIFY — llmConfig state, gemini_key migration, gate, headers, card mount, copy
dashboard/src/contexts/AuthContext.jsx  # MODIFY — config fetch retry, llmConfigured/llmModel/llmBaseUrl passthrough
dashboard/src/components/ResultCard.jsx  # MODIFY — remove dead gemini_key fallback
dashboard/src/components/LlmProviderCard.jsx  # NEW — settings card with presets, validation, Test connection
dashboard/src/components/ThumbnailStudio.jsx  # MODIFY — llmHeaders, needsAiBackend, generate-step Gemini note
dashboard/src/components/SaaShortsTab.jsx  # MODIFY — llmHeaders, needsAiBackend

## Ordering Constraints

- Slice 1 (backend) must complete before Slice 2 (frontend needs the `/api/config` fields).
- Slice 2 (browser state + lib helpers) must complete before Slices 3, 4, 5 (they depend on `llmConfig`, `llmHeaders`, `llmConfigComplete`).
- Slice 4 (app gate) should complete before Slice 5 (per-feature split follows the gate pattern).
- Slices 3 and 4 both modify `App.jsx` but in different sections; Slice 4 merges into the `App.jsx` code fence after Slice 3.
- `tests/test_llm_endpoints.py` and `tests/test_llm_client.py` additions run in Slice 1 only.

## Verification Notes

- `grep -c "/api/llm/test" app.py` returns 1 (the endpoint exists).
- `grep -c "llmConfigured" dashboard/src/contexts/AuthContext.jsx` returns at least 2 (destructure + passthrough).
- `grep -c "llmHeaders" dashboard/src/components/ThumbnailStudio.jsx` returns 6 (definition + five spreads).
- `grep -c "llmHeaders" dashboard/src/components/SaaShortsTab.jsx` returns at least 2 (definition + spread).
- `grep -rn "gemini_key" dashboard/src/` returns exactly 2 matches (one `getItem` + one `removeItem` in the initializer) after migration.
- `pytest tests/test_llm_client.py tests/test_llm_endpoints.py -q` passes.
- `cd dashboard && npm run lint` passes with zero warnings.
- `cd dashboard && npm run build` succeeds.
- pire-browser e2e: start `./dev.sh --no-renderer` + Python stub on `localhost:11434`, open the dashboard, configure the provider, submit a job, inspect outgoing `/api/process` headers for the `X-LLM-*` triple.
- Unverified artifact state is the top risk: the Phase 1 implementation was never committed and is absent at HEAD. Commit each slice at session end.

## Performance Considerations

- `probe()` uses a one-off `_probe_client` with connect 5s / read 20s, not the shared 300s `_http_client` cache. A black-holed URL fails in 5s, not 5 minutes.
- The rate limiter (`_check_probe_rate`) reuses the existing `_probe_times` dict keyed by `request.client.host` on self-host. The metering probe is unreachable on self-host (`reserve_process_minutes` returns early at `app.py:285-286`), so no bucket starvation.
- AuthContext fetch retry adds at most 1-2 retries with 500ms–1s backoff. Only on failure; the happy path is unchanged.
- `llmConfig_v1` is a single encrypted JSON blob, not three separate keys. One localStorage read, one write.

## Migration Notes

- `gemini_key` (plaintext) to `geminiKey_v1` (encrypted JSON `{key}`): one-way, once per browser. The initializer reads `geminiKey_v1` first (JSON canary — see D12), else adopts the legacy `gemini_key`, removes it unconditionally, and the persistence effect writes the encrypted JSON form on mount. No data-preserving localStorage migration has ever shipped (precedent `d60c376` chose forced re-entry); this is untested territory.
- `llmConfig_v1` (new encrypted blob): no migration needed. The initializer wraps `JSON.parse(decrypt(stored))` in try/catch and returns the empty triple in the catch. Every corrupt path lands in the catch by design. Persisted only when the triple is complete (`llmConfigComplete` guard) — the same guard shape as the sibling keys, and what keeps the rollback claim below true: the effect never rewrites an empty value.
- `ResultCard.jsx:284` reads `gemini_key` directly — the dead fallback is removed in Slice 2, the same slice as the migration. After migration `removeItem`s `gemini_key`, that fallback reads `null`.
- Rollback: deleting `geminiKey_v1` and `llmConfig_v1` from localStorage restores the pre-feature state. The legacy `gemini_key` is already removed by the migration.

## Pattern References

- `dashboard/src/components/KeyInput.jsx` — the self-contained Settings card pattern (icon + title + input + save button).
- `d0e1f5a` (2026-08-20) — the one-commit end-to-end path for adding a `/api/config` field: endpoint to AuthContext to `useAuth()` destructure.
- `03477d4` (2026-01-23) — ElevenLabs BYOK card: backend module + endpoint + App.jsx card + `_v1` encrypted key + header in one commit. Origin of the `_v1` convention.
- `29681ba` (2026-03-15) — fal.ai BYOK card: same pattern, but do not copy its plumbing (the `X-LLM-*` triple crosses the job subprocess boundary and dies on resume).
- `e96407b` — every new BYOK header must land in `mcp_server._FORWARD_HEADERS`. The `X-LLM-*` triple is already there.
- `tests/test_llm_client.py:1-60` — the mock-transport test pattern (`httpx.MockTransport` installed by monkeypatching `llm_client._http_client`).

## Developer Context

**Q (discover: Adopt existing plan with pire-browser delta)**: The probe found a complete, triaged plan for this FE work from 2026-08-30. None of it shipped. How should this FRD treat it?
A: Adopt plan with deltas — the plan's five phases and decisions D1–D9 are the baseline; the one delta is pire-browser e2e validation.

**Q (discover: Provider config storage: browser plus headers)**: Keep browser-stored encrypted `localStorage` `llmConfig_v1` traveling per-request as the `X-LLM-*` triple, or change it?
A: Keep browser plus headers — `app.py:106-121` (self-host has no auth); matches `X-Gemini-Key` precedent (`App.jsx:211,792`).

**Q (discover: FE scope: full plan scope)**: Keep the full plan scope (settings card + app-wide gate change)?
A: Full plan scope — gate widening is necessary; an LLM-only user sees a block despite a working provider otherwise.

**Q (discover: E2E validation: full pire-browser journey)**: What should pire-browser e2e cover?
A: Full browser journey — the real Settings card flow, Test connection, and a generation request.

**Q (discover: E2E stub: local Python stub)**: What serves as the OpenAI-compatible endpoint?
A: Python stub — a 10-line `http.server` stub on `localhost:11434`. Zero deps, zero network.

**Q (`llm_client.py:443-447`): How should the e2e stub serve the `/api/process` bullet (a real job fails the LLM step on the simple stub body)?**
A: Headers-only stub — keep the simple `{choices:[{message:{content:'ok'},...}]}` stub, inspect the outgoing `/api/process` request headers via pire-browser, and accept the job fails at the LLM step. The FRD bullet only asks to confirm the headers are present.

**Q (`AuthContext.jsx:104,110-111`): How should Phase 4 handle a `/api/config` fetch failure (leaves `loading=false` + empty config to permanent banner)?**
A: Retry the fetch — add 1-2 retries with backoff to the AuthContext config fetch; recovers transient failures, keeps single-shot semantics minimal.

**Q (`App.jsx:55-57`): Harden the `decrypt` migration guard against key-rotation garbage?**
A: Harden the guard — validate the decrypted value (round-trip re-encrypt-and-compare) before adopting; fall through to the legacy `gemini_key` migration on mismatch.

**Q (`llm_client.py:130`): Probe one-off client timeout policy (connect 5s/read 20s vs the 10s NFR)?**
A: Keep connect 5s / read 20s; clarify the NFR text to name the silent-host 20s case.

**Q (Slice 2/5 checkpoint, 2026-09-05: D12 mechanism change)**: The recorded round-trip guard (`encrypt(decrypt(stored)) === stored`) is a mathematical no-op — `encrypt`/`decrypt` (`App.jsx:36-67`) XOR with the same keystream per index, so the round trip reproduces any well-formed `ENC:` blob under any key (XOR involution; proven by slice-verifier pass 1). Ratify the replacement: `geminiKey_v1` stores `encrypt(JSON.stringify({key}))` and the initializer `JSON.parse`s it — rotation garbage never parses and lands in the catch.
A: Approved — JSON canary adopted. D12, Summary, Key Discoveries and Migration Notes amended to match the code. Also fixed across 3 verifier passes: `llmConfig` persistence guarded by `llmConfigComplete` (restores the rollback claim), AuthContext mount-effect range corrected to lines 101-113, comment text kept token-free so the `gemini_key` grep count holds.

**Q (Slice 3 checkpoint, 2026-09-05: card approved as presented)**: Two surfaced items — a 429 from the shared probe limiter renders under "The provider refused the request:" in `LlmProviderCard.jsx` `handleTest` (copy-level; the server's verbatim detail still shows), and `npm run build` stays on Slice 5 per the terminal-slice rule (verifier confirmed no gap: all imports and utility classes resolve).
A: Approved — no changes. The card locked as generated after one revision pass (verifier pass 1: unused `Server` import removed; comment tokens de-tokenized so `grep -c` counts hold; `App.jsx` useAuth anchor corrected to line 201).

**Q (Slice 5 checkpoint, 2026-09-05: capability split approved as presented)**: Three verifier passes; six accepted findings (five anchors: `ThumbnailStudio.jsx:73-77` and `846-862`, `SaaShortsTab.jsx:43-47`, import lines 6/7; one insert-vs-replace wording on the comma-less `...keyHeader` lines 218/241/358) and one rejected with awk evidence (`app.py:5003-5006` — `resolve_gemini` 5003, `resolve_llm` 5006). Surfaced: the Regenerate button (`ThumbnailStudio.jsx:936`) keeps its original disabled state — a mid-session key clear is covered by the `handleGenerate` guard.

## Design History

- Slice 1: Backend — status channel + connection probe — approved as generated
- Slice 2: Browser state — storage, migration, server-status wiring — revised: D12 round-trip guard (proven XOR no-op) replaced with a JSON-canary blob; llmConfig persistence guarded by llmConfigComplete; approved
- Slice 3: The AI Provider settings card — revised: unused `Server` icon import dropped and comment tokens de-tokenized (verifier pass 1, keeps the grep criteria counts stable); approved
- Slice 4: Unblock the app — headers, gates, copy — revised: D3 cloud gate added (providerCfg) after verifier pass 1 caught a stale `llmConfig_v1` blob leaking `X-LLM-*` under `BILLING_ENABLED`; section anchors corrected to 708-709 and 789-792; Section G carries the verbatim `onCreateClips` body (verifier pass 2: Decisions/Cross-slice/Research OK); approved
- Slice 5: Per-feature capability split — revised: five line-anchor corrections and insert-vs-replace wording fixed over three verifier passes (one verifier finding — app.py:5002-5005 — rejected with direct awk evidence: 5003-5006 is correct); approved; post-approval authoring fix (advisor review): the generate-button block was completed to a true full 846-862 replacement — the unchanged inner body (disk `ThumbnailStudio.jsx:851-862`) appended verbatim so literal application no longer deletes the ternary

## References

- `.rpiv/artifacts/research/2026-09-04_20-47-42_connect-llm-provider-frontend.md` — the research artifact (parent).
- `.rpiv/artifacts/designs/2026-08-30_16-58-07_connect-llm-provider-frontend.md` — the parent design, decisions D1–D9.
- `.rpiv/artifacts/plans/2026-08-30_18-36-48_connect-llm-provider-frontend.md` — the 5-phase implementation plan (the implementation baseline; its checked boxes describe lost code).
- `.rpiv/artifacts/handoffs/2026-08-31_05-52-02_connect-llm-provider-phase1.md` — reports Phase 1 complete but uncommitted; records the `_redact()` closure detail.
- `.rpiv/artifacts/discover/2026-09-04_10-12-16_connect-llm-provider-frontend.md` — the FRD: requirements, acceptance criteria, the pire-browser delta.
