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
