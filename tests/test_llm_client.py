"""llm_client contract tests against a mock OpenAI-compatible endpoint.

httpx.MockTransport stands in for the provider (installed by monkeypatching
llm_client._http_client, the same seam every real call goes through): no
server, no network. Every error mapping (blocked, transient, rejected) and
the json_schema fallback ladder are exercised here so call sites can branch
to chat() blindly.

The "llm provider" alert-class tests are appended to this file when
cloud/alerts.py gains the class (slice 4); this file owns them because the
pinned test_alert_classify.py must stay unmodified.
"""
import httpx
import pytest
import json
from pydantic import BaseModel

import llm_client
from gemini_worker import GeminiBlockedError

CFG = llm_client.LlmConfig(base_url="https://provider.test/v1",
                           api_key="k", model="test-model")
GEMINI_CFG = llm_client.LlmConfig(base_url="https://provider.test/v1",
                                  api_key="k", model="gemini-2.5-flash")


class _Win(BaseModel):
    id: str
    score: int


def _ok(text='{"id": "w0", "score": 90}', usage=None, finish=None):
    body = {"choices": [{"message": {"role": "assistant", "content": text},
                         "finish_reason": finish or "stop"}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def _err(status, message, code=None, err_type=None):
    err = {"message": message}
    if code:
        err["code"] = code
    if err_type:
        err["type"] = err_type
    return httpx.Response(status, json={"error": err})


def _body(request):
    import json as _json
    return _json.loads(request.read())


@pytest.fixture
def mock_llm(monkeypatch):
    """Install a mock provider; every chat() in this session hits it."""
    def install(handler):
        client = httpx.Client(base_url=CFG.base_url,
                              transport=httpx.MockTransport(handler))
        monkeypatch.setattr(llm_client, "_http_client",
                            lambda base_url: client)
    return install


# --- happy paths -----------------------------------------------------------

def test_schema_happy_path_requests_json_schema_and_validates(mock_llm):
    seen = []

    def handler(request):
        # httpx merges base_url + path: /v1 stays, /chat/completions appends.
        assert request.url.path == "/v1/chat/completions"
        seen.append(_body(request))
        return _ok(usage={"prompt_tokens": 10, "completion_tokens": 5})

    mock_llm(handler)
    parsed, cost = llm_client.chat("score this", _Win, config=CFG)
    assert parsed == {"id": "w0", "score": 90}
    sent = seen[0]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is False
    assert sent["model"] == "test-model"
    assert cost["input_tokens"] == 10 and cost["price_estimated"] is True


def test_no_schema_returns_raw_text(mock_llm):
    mock_llm(lambda request: _ok(text="plain words"))
    text, cost = llm_client.chat("hi", config=CFG)
    assert text == "plain words"
    assert cost is None  # no usage in body


def test_text_only_call_sends_plain_string_content(mock_llm):
    seen = []

    def handler(request):
        seen.append(_body(request))
        return _ok()

    mock_llm(handler)
    llm_client.chat("hi", _Win, config=CFG)
    assert seen[0]["messages"][0]["content"] == "hi"  # str, not a parts array


def test_known_gemini_model_prices_not_estimated(mock_llm):
    mock_llm(lambda request: _ok(usage={"prompt_tokens": 1_000_000,
                                        "completion_tokens": 0}))
    _, cost = llm_client.chat("hi", _Win, config=GEMINI_CFG)
    assert cost["price_estimated"] is False
    assert cost["input_cost"] == pytest.approx(0.30)


def test_reasoning_tokens_reported_but_not_double_billed(mock_llm):
    # completion_tokens already INCLUDES reasoning on OpenAI-compat APIs:
    # output must be billed on 5, never on 5+3.
    mock_llm(lambda request: _ok(usage={
        "prompt_tokens": 0, "completion_tokens": 5,
        "completion_tokens_details": {"reasoning_tokens": 3}}))
    _, cost = llm_client.chat("hi", _Win, config=CFG)
    assert cost["thinking_tokens"] == 3
    assert cost["output_tokens"] == 5
    assert cost["output_cost"] == pytest.approx(5 / 1_000_000 * 3.00)


def test_garbage_usage_does_not_fail_the_call(mock_llm):
    mock_llm(lambda request: _ok(usage="not-a-dict"))
    parsed, cost = llm_client.chat("hi", _Win, config=CFG)
    assert parsed["id"] == "w0"
    assert cost is None


def test_images_travel_as_data_url_parts(mock_llm):
    seen = []

    def handler(request):
        seen.append(_body(request))
        return _ok()

    mock_llm(handler)
    llm_client.chat("look", _Win, config=CFG, images=(b"\xff\xd8fake",))
    parts = seen[0]["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


# --- the json_schema fallback ladder ----------------------------------------

def test_response_format_rejection_falls_back_rung_by_rung(mock_llm):
    calls = []

    def handler(request):
        body = _body(request)
        calls.append(body)
        if "response_format" in body:
            return _err(400, "response_format is not supported on this model")
        return _ok()

    mock_llm(handler)
    parsed, _ = llm_client.chat("score", _Win, config=CFG)
    assert parsed["id"] == "w0"
    assert len(calls) == 3  # json_schema -> json_object -> bare
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[2]


def test_ladder_stops_when_rung_one_works(mock_llm):
    calls = []

    def handler(request):
        body = _body(request)
        calls.append(body)
        return _ok(text='{"id": "w1", "score": 1}')

    mock_llm(handler)
    parsed, _ = llm_client.chat("score", _Win, config=CFG)
    assert parsed["id"] == "w1"
    assert len(calls) == 1


def test_rung2_only_rejection_reaches_the_bare_rung(mock_llm):
    # json_schema accepted is NOT assumed: rung 1 rejects with a generic
    # marker, rung 2 rejects naming json_object — the bare rung must still run.
    calls = []

    def handler(request):
        body = _body(request)
        calls.append(body)
        rf = body.get("response_format") or {}
        if rf.get("type") == "json_schema":
            return _err(400, "response_format is not supported")
        if rf.get("type") == "json_object":
            return _err(400, "'json_object' is not supported on this model")
        return _ok()

    mock_llm(handler)
    parsed, _ = llm_client.chat("score", _Win, config=CFG)
    assert parsed["id"] == "w0"
    assert len(calls) == 3


def test_plain_text_400_naming_response_format_still_drops_a_rung(mock_llm):
    # Raw-text fallback applies to RF markers only (never blocked markers).
    calls = []

    def handler(request):
        body = _body(request)
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(400, text="response_format unsupported")
        return _ok()

    mock_llm(handler)
    parsed, _ = llm_client.chat("score", _Win, config=CFG)
    assert parsed["id"] == "w0"
    assert len(calls) == 3


def test_bare_rung_embeds_the_json_contract_in_band(mock_llm):
    calls = []

    def handler(request):
        body = _body(request)
        calls.append(body)
        if "response_format" in body:
            return _err(400, "response format not supported")
        return _ok()

    mock_llm(handler)
    llm_client.chat("pick a layout", _Win, config=CFG)  # prompt never says JSON
    bare = calls[2]["messages"][0]["content"]
    assert "JSON object matching this schema" in bare
    assert "score" in bare  # the schema itself is embedded


def test_genuine_bad_request_is_not_swallowed_by_the_ladder(mock_llm):
    calls = []

    def handler(request):
        calls.append(1)
        return _err(400, "invalid request: unknown field 'foo'")

    mock_llm(handler)
    with pytest.raises(llm_client.LlmError):
        llm_client.chat("score", _Win, config=CFG)
    assert len(calls) == 1  # no response_format marker -> no ladder retry


# --- blocked mapping ---------------------------------------------------------

def test_policy_refusal_raises_blocked_with_classifier_substring(mock_llm):
    mock_llm(lambda request: _err(400, "your request violates our content policy"))
    with pytest.raises(GeminiBlockedError) as exc:
        llm_client.chat("score", _Win, config=CFG)
    assert "blocked this video" in str(exc.value)


def test_refusal_marker_in_error_code_is_detected(mock_llm):
    mock_llm(lambda request: _err(400, "the request cannot be completed",
                                  code="content_filter"))
    with pytest.raises(GeminiBlockedError):
        llm_client.chat("score", _Win, config=CFG)


def test_blocked_takes_precedence_over_transient(mock_llm):
    # A 429 whose body mentions a policy refusal must read as BLOCKED
    # (deterministic), never enter the retry ladder.
    mock_llm(lambda request: _err(429, "moderation system rejected the request"))
    with pytest.raises(GeminiBlockedError):
        llm_client.chat("score", _Win, config=CFG)


def test_content_filter_finish_reason_raises_blocked(mock_llm):
    mock_llm(lambda request: _ok(finish="content_filter", text=""))
    with pytest.raises(GeminiBlockedError):
        llm_client.chat("score", _Win, config=CFG)


def test_http200_error_body_refusal_is_blocked(mock_llm):
    # Some providers answer 200 with an error body instead of choices.
    mock_llm(lambda request: httpx.Response(
        200, json={"error": {"code": "content_filter",
                             "message": "flagged by the safety system"}}))
    with pytest.raises(GeminiBlockedError):
        llm_client.chat("score", _Win, config=CFG)


def test_http200_error_body_non_refusal_is_llm_error(mock_llm):
    mock_llm(lambda request: httpx.Response(
        200, json={"error": {"message": "model is warming up, retry later"}}))
    with pytest.raises(llm_client.LlmError):
        llm_client.chat("score", _Win, config=CFG)


def test_http200_empty_error_alongside_choices_is_ignored(mock_llm):
    mock_llm(lambda request: httpx.Response(200, json={
        "error": {}, "choices": [{"message": {"content": '{"id": "w0", "score": 9}'},
                                    "finish_reason": "stop"}]}))
    parsed, _ = llm_client.chat("score", _Win, config=CFG)
    assert parsed["id"] == "w0"


def test_html_error_page_is_never_classified_blocked(mock_llm):
    # Marker matching must never see raw body text: an HTML block page that
    # happens to say "usage policy" must not tell the user their video was
    # blocked by policy.
    mock_llm(lambda request: httpx.Response(
        400, text="<html>403 forbidden — see our usage policy page</html>"))
    with pytest.raises(llm_client.LlmError):
        llm_client.chat("score", _Win, config=CFG)


# --- transient mapping --------------------------------------------------------

@pytest.mark.parametrize("resp", [
    _err(429, "rate limit exceeded"),
    _err(500, "internal error"),
    _err(503, "overloaded"),
    _err(408, "request timeout"),
    _err(529, "cloudflare is having problems"),
])
def test_provider_outages_are_transient(resp, mock_llm):
    mock_llm(lambda request: resp)
    with pytest.raises(llm_client.LlmTransientError):
        llm_client.chat("score", _Win, config=CFG)


def test_timeout_is_transient(mock_llm):
    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    mock_llm(handler)
    with pytest.raises(llm_client.LlmTransientError):
        llm_client.chat("score", _Win, config=CFG)


def test_html_200_body_is_transient(mock_llm):
    mock_llm(lambda request: httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(llm_client.LlmTransientError):
        llm_client.chat("score", _Win, config=CFG)


def test_empty_choices_is_transient(mock_llm):
    mock_llm(lambda request: httpx.Response(200, json={"choices": []}))
    with pytest.raises(llm_client.LlmTransientError):
        llm_client.chat("score", _Win, config=CFG)


def test_schema_validation_failure_is_transient(mock_llm):
    mock_llm(lambda request: _ok(text='{"id": "w0"}'))  # missing "score"
    with pytest.raises(llm_client.LlmTransientError):
        llm_client.chat("score", _Win, config=CFG)


# --- non-transient rejections -------------------------------------------------

def test_bad_key_is_llm_error_never_retried(mock_llm):
    calls = []

    def handler(request):
        calls.append(1)
        return _err(401, "invalid api key")

    mock_llm(handler)
    with pytest.raises(llm_client.LlmError):
        llm_client.chat("score", _Win, config=CFG)
    assert len(calls) == 1


def test_unknown_model_is_llm_error_never_retried(mock_llm):
    calls = []

    def handler(request):
        calls.append(1)
        return _err(404, "model 'nope' not found")

    mock_llm(handler)
    with pytest.raises(llm_client.LlmError):
        llm_client.chat("score", _Win, config=CFG)
    assert len(calls) == 1


def test_out_of_credit_is_llm_error_never_retried(mock_llm):
    calls = []

    def handler(request):
        calls.append(1)
        return _err(402, "insufficient balance")

    mock_llm(handler)
    with pytest.raises(llm_client.LlmError):
        llm_client.chat("score", _Win, config=CFG)
    assert len(calls) == 1


def test_truncated_response_fails_loudly_not_transient(mock_llm):
    calls = []

    def handler(request):
        calls.append(1)
        return _ok(text='{"id": "w0", "sc', finish="length")

    mock_llm(handler)
    with pytest.raises(llm_client.LlmError) as exc:
        llm_client.chat("score", _Win, config=CFG)
    assert "truncated" in str(exc.value).lower()
    assert len(calls) == 1


def test_malformed_base_url_scheme_is_llm_error():
    for bad in ("localhost:11434/v1", "http://", "ftp://x/v1"):
        cfg = llm_client.LlmConfig(base_url=bad, api_key="k", model="m")
        with pytest.raises(llm_client.LlmError):
            llm_client.chat("score", _Win, config=cfg)


def test_missing_model_config_fails_loudly():
    cfg = llm_client.LlmConfig(base_url="https://x", api_key="k", model="")
    with pytest.raises(llm_client.LlmError) as exc:
        llm_client.chat("score", _Win, config=cfg)
    assert "LLM_MODEL" in str(exc.value)


# --- config resolution ----------------------------------------------------------

def test_config_from_requires_both_values(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "m")  # a valid model so None means the PAIR
    assert llm_client.config_from("https://x", "") is None
    assert llm_client.config_from("", "k") is None
    assert llm_client.config_from("https://x", "k").base_url == "https://x"


@pytest.fixture
def fresh_warn_flag():
    # The once-only half-configured warning lives in a module global; reset
    # it so this test is order-independent under -p randomly / -k / any other
    # module that imported llm_client first.
    llm_client._warned_no_model = False
    yield
    llm_client._warned_no_model = False


def test_half_configured_env_is_inert_with_warning(fresh_warn_flag, monkeypatch, capsys):
    # No model: the backend must NOT go active and hijack a working Gemini
    # setup — it stays inert and says why, once.
    monkeypatch.setenv("LLM_BASE_URL", "https://x")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL_THUMBNAIL", raising=False)
    monkeypatch.delenv("LLM_MODEL_SAAS", raising=False)
    assert llm_client.active_config() is None
    assert llm_client.active_config() is None
    out = capsys.readouterr().out
    assert "LLM_MODEL" in out and out.count("⚠️") == 1  # warned exactly once


def test_active_config_reads_env_and_task_chain(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://x")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "base-model")
    monkeypatch.setenv("LLM_MODEL_THUMBNAIL", "thumb-model")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    assert llm_client.active_config().model == "base-model"
    assert llm_client.active_config("thumbnail").model == "thumb-model"
    # never falls through to a Gemini model name, and no invented knobs
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.delenv("LLM_MODEL_THUMBNAIL")
    assert llm_client.active_config() is None  # no model anywhere -> inert
    monkeypatch.setenv("LLM_MODEL_CLIPS", "invented")
    assert llm_client.active_config() is None  # LLM_MODEL_CLIPS does not exist


def test_inactive_without_env(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert llm_client.active_config() is None


def test_non_pydantic_schema_is_ignored_gracefully(mock_llm):
    mock_llm(lambda request: _ok(text="words"))
    text, _ = llm_client.chat("hi", object, config=CFG)
    assert text == "words"


def test_api_key_never_appears_in_repr():
    cfg = llm_client.LlmConfig(base_url="https://x", api_key="sk-SUPERSECRET",
                               model="m")
    assert "sk-SUPERSECRET" not in repr(cfg)


# --- _http_client construction (the seam tests monkeypatch over) ---------------

def test_http_client_is_cached_per_base_url():
    a = llm_client._http_client("https://cache.test/v1/")
    b = llm_client._http_client("https://cache.test/v1/")
    assert a is b
    assert a.base_url == "https://cache.test/v1/"  # httpx enforces the trailing slash
    assert a.follow_redirects is True


def test_http_client_rejects_invalid_url():
    # httpx 0.28 parses scheme-less strings ("not a url") as relative URLs — the
    # URL httpx actually refuses at Client construction carries a control character.
    with pytest.raises(llm_client.LlmError):
        llm_client._http_client("http://exa\tmple.com")

# --- pipeline branch (slice 2): main._run_gemini_stage with an llm config ----
# main pulls cv2/torch/mediapipe at import; skip per-TEST (a module-level
# importorskip would void the slice-1 contract tests). NB google-genai is a
# hard collection-time dependency regardless: module-level `import llm_client`
# pulls gemini_worker, which imports google.genai — minimal envs are not a
# supported run mode; the per-test guard still avoids cv2/torch/mediapipe.


def _main():
    return pytest.importorskip("main")


def _llm_cfg():
    return llm_client.LlmConfig(base_url="https://provider.test/v1",
                                api_key="k", model="test-model")


def test_stage_with_llm_config_skips_the_genai_client(monkeypatch):
    main = _main()
    calls = []

    def fake_chat(prompt, schema, *, config, **kw):
        calls.append(config.model)
        return {"windows": [{"id": "w0", "score": 90}]}, {"total_cost": 0.001}

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    parsed, cost = main._run_gemini_stage(None, "any-model", "p", object,
                                          llm=_llm_cfg())
    assert parsed["windows"][0]["score"] == 90
    assert calls == ["test-model"]  # the config's model, not the genai one


def test_stage_retries_llm_transients_up_to_three_attempts(monkeypatch, capsys):
    main = _main()
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    boom = {"n": 0}

    def flaky(prompt, schema, *, config, **kw):
        boom["n"] += 1
        if boom["n"] <= 2:
            raise llm_client.LlmTransientError(
                "LLM provider transient error (HTTP 500, retryable)")
        return {"windows": []}, None

    monkeypatch.setattr(llm_client, "chat", flaky)
    parsed, _ = main._run_gemini_stage(None, "m", "p", object, llm=_llm_cfg())
    assert boom["n"] == 3 and parsed == {"windows": []}
    # Recovered blips must NOT echo "LLM provider" into the log (D9 guardrail:
    # that phrase is reserved for terminal errors the classifier reads).
    assert "LLM provider" not in capsys.readouterr().out


def test_stage_never_retries_llm_hard_errors(monkeypatch):
    main = _main()
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    boom = {"n": 0}

    def reject(prompt, schema, *, config, **kw):
        boom["n"] += 1
        raise llm_client.LlmError(
            "LLM provider rejected the request (HTTP 404): model not found")

    monkeypatch.setattr(llm_client, "chat", reject)
    with pytest.raises(llm_client.LlmError):
        main._run_gemini_stage(None, "m", "p", object, llm=_llm_cfg())
    assert boom["n"] == 1


def test_stage_blocked_never_retries(monkeypatch):
    main = _main()

    def refuse(prompt, schema, *, config, **kw):
        raise GeminiBlockedError(
            "The AI provider blocked this video's content (content_filter)")

    monkeypatch.setattr(llm_client, "chat", refuse)
    with pytest.raises(GeminiBlockedError):
        main._run_gemini_stage(None, "m", "p", object, llm=_llm_cfg())


def test_stage_split_keeps_the_historical_call_shape_without_llm(monkeypatch):
    main = _main()
    import json as _json
    seen = []

    def fake_stage(client, model, prompt, schema, **kw):
        seen.append(kw)
        return {"windows": [{"id": "w0", "score": 1}]}, None

    monkeypatch.setattr(main, "_run_gemini_stage", fake_stage)
    items = [{"id": "w0", "start": 0, "end": 10, "text": "t"}]
    main._run_stage_split(None, "m", items, lambda ws: _json.dumps(ws),
                          None, "windows", [], "score")
    assert seen == [{}]  # 4-arg shape preserved — pinned fakes keep working

    main._run_stage_split(None, "m", items, lambda ws: _json.dumps(ws),
                          None, "windows", [], "score", llm=_llm_cfg())
    assert seen[-1] == {"llm": _llm_cfg()}


def test_stage_split_bisects_blocked_llm_batches(monkeypatch):
    main = _main()
    import json as _json
    seen = []

    def stage(client, model, prompt, schema, **kw):
        ids = [w["id"] for w in _json.loads(prompt)]
        seen.append((tuple(ids), kw.get("llm") is not None))
        if "w0" in ids and "w1" in ids:
            raise GeminiBlockedError(
                "The AI provider blocked this video's content (content_filter)")
        return {"windows": [{"id": i, "score": 50} for i in ids]}, None

    monkeypatch.setattr(main, "_run_gemini_stage", stage)
    items = [{"id": f"w{i}", "start": i * 10, "end": i * 10 + 10, "text": "t"}
             for i in range(4)]
    out = main._run_stage_split(None, "m", items, lambda ws: _json.dumps(ws),
                                None, "windows", [], "score", llm=_llm_cfg())
    assert sorted(w["id"] for w in out) == ["w0", "w1", "w2", "w3"]
    assert all(flag for _, flag in seen)  # every bisect half kept the llm config


def test_get_viral_clips_gate_accepts_llm_only(monkeypatch, capsys):
    main = _main()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    calls = []

    def fake_chat(prompt, schema, *, config, **kw):
        calls.append(1)
        return {"windows": []}, None

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    result = main.get_viral_clips(
        {"language": "en", "text": "hello world",
         "segments": [{"start": 0, "end": 20, "text": "hello world", "words": []}]},
        20.0)
    assert calls, "the llm backend must actually be called"
    assert result is None  # empty transcript yields no clips — not a crash
    out = capsys.readouterr().out
    assert "GEMINI_API_KEY not found" not in out
    assert "third-party endpoint" in out


def test_get_viral_clips_propagates_llm_hard_errors(monkeypatch, capsys):
    main = _main()
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    def reject(prompt, schema, *, config, **kw):
        raise llm_client.LlmError(
            "LLM provider rejected the request (HTTP 401): invalid api key")

    monkeypatch.setattr(llm_client, "chat", reject)
    with pytest.raises(llm_client.LlmError):
        main.get_viral_clips(
            {"language": "en", "text": "hi",
             "segments": [{"start": 0, "end": 20, "text": "hi", "words": []}]},
            20.0)
    assert "Third-party LLM error" in capsys.readouterr().out


def test_get_viral_clips_llm_aggregate_marks_estimated(monkeypatch):
    main = _main()
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    def fake_chat(prompt, schema, *, config, **kw):
        name = getattr(schema, "__name__", "")
        if name == "ScoreResponse":
            return {"windows": [{"id": "window_001", "start": 0, "end": 10,
                                 "score": 90, "reason": "hook"}]}, \
                   {"total_cost": 0.001, "price_estimated": True,
                    "input_tokens": 1, "output_tokens": 1}
        return {"shorts": [{"start": 1.0, "end": 16.0,
                            "source_window_id": "window_001",
                            "predicted_score": 90,
                            "video_description_for_tiktok": "d",
                            "video_description_for_instagram": "d",
                            "video_title_for_youtube_short": "t",
                            "viral_hook_text": "h"}]}, \
               {"total_cost": 0.002, "price_estimated": True,
                "input_tokens": 1, "output_tokens": 1}

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    result = main.get_viral_clips(
        {"language": "en", "text": "hello world hello world",
         "segments": [{"start": 0, "end": 20, "text": "hello world hello world", "words": []}]},
        20.0)
    assert result and result["shorts"]
    assert result["cost_analysis"]["model"] == "test-model"
    assert result["cost_analysis"]["price_estimated"] is True


def test_explicit_model_header_wins_over_env_chain(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "env-model")
    cfg = llm_client.config_from("https://x", "k", model="header-model")
    assert cfg.model == "header-model"
    assert llm_client.config_from("https://x", "k", model="  ").model == "env-model"

# --- in-process endpoints (slice 3) ---------------------------------------------
def _thumb():
    return pytest.importorskip("thumbnail")


def _saas():
    return pytest.importorskip("saasshorts")


def _no_genai(monkeypatch):
    import google.genai as _g

    def boom(*a, **k):
        raise AssertionError("genai.Client must not be constructed on the llm path")

    monkeypatch.setattr(_g, "Client", boom)


def test_thumbnail_titles_via_llm_without_genai(monkeypatch):
    thumb = _thumb()
    _no_genai(monkeypatch)

    def fake_chat(prompt, schema=None, *, config, **kw):
        if "Brainstorm" in prompt:
            return json.dumps({"transcript_summary": "s",
                               "candidates": ["T1", "T2"]}), None
        return json.dumps({"titles": ["Best T"], "thumbnail_texts": ["WOW"],
                           "recommended": []}), None

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    import layout_picker
    monkeypatch.setattr(layout_picker, "sample_frames",
                        lambda *a, **k: [b"\xff\xd8fake"])

    out = thumb.analyze_video_for_titles(
        None, "/nonexistent.mp4",
        transcript={"language": "en", "text": "hi",
                    "segments": [{"start": 0, "end": 5, "text": "hi", "words": []}]},
        llm_config=_llm_cfg())
    assert out["titles"] == ["Best T"]
    assert out["thumbnail_texts"] == ["WOW"]


def test_saas_analysis_via_llm_without_genai(monkeypatch):
    saas = _saas()
    _no_genai(monkeypatch)
    monkeypatch.setattr(llm_client, "chat",
                        lambda prompt, schema=None, *, config, **kw:
                        (json.dumps({"product_name": "P", "pain_points": []}), None))
    out = saas.analyze_saas({"url": "https://x.test", "title": "T",
                             "meta_description": "", "headings": [],
                             "main_content": "c", "additional_pages": []},
                            None, llm_config=_llm_cfg())
    assert out["product_name"] == "P"


def test_saas_scripts_via_llm_parse_array(monkeypatch):
    saas = _saas()
    _no_genai(monkeypatch)
    scripts_json = [{"title": "s1", "style": "ugc", "duration_seconds": 23,
                     "target_platform": "tiktok", "hook_text": "h",
                     "segments": []}]
    monkeypatch.setattr(llm_client, "chat",
                        lambda prompt, schema=None, *, config, **kw:
                        (json.dumps(scripts_json), None))
    out = saas.generate_scripts({"product_name": "P"}, None, llm_config=_llm_cfg())
    assert isinstance(out, list) and out[0]["hook_text"] == "h"


def test_generate_scripts_llm_request_carries_max_tokens(monkeypatch):
    saas = _saas()
    seen = {}

    def fake_chat(prompt, schema=None, *, config, max_tokens=None, **kw):
        seen["max_tokens"] = max_tokens
        return "[]", None

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    saas.generate_scripts({"product_name": "P"}, None, llm_config=_llm_cfg())
    assert seen["max_tokens"] == 8192


def test_chat_json_mode_requests_json_object_rung(mock_llm):
    seen = []

    def handler(request):
        seen.append(_body(request))
        if "response_format" in seen[-1]:
            return _err(400, "json mode not supported")
        return _ok(text='{"a": 1}')

    mock_llm(handler)
    text, _ = llm_client.chat("hi", config=CFG, json_mode=True)
    assert text == '{"a": 1}'
    assert len(seen) == 2  # json_object -> bare
    assert seen[0]["response_format"] == {"type": "json_object"}

# --- alert-class tests (slice 4): cloud/alerts._classify_failure ---------------
def _alerts():
    return pytest.importorskip("cloud.alerts", reason="cloud deps not installed")


def test_llm_provider_error_classifies_as_llm_provider():
    alerts = _alerts()
    assert alerts._classify_failure(
        "LLM provider rejected the request (HTTP 401): invalid api key"
    ) == "llm provider"


def test_llm_provider_outage_classifies_as_llm_provider():
    alerts = _alerts()
    assert alerts._classify_failure(
        "LLM provider transient error (HTTP 500, retryable): overloaded"
    ) == "llm provider"


def test_provider_out_of_credit_not_classified_as_proxy():
    # The provider body echoes "insufficient balance" (a _PROXY_HINTS phrase):
    # the namespaced "llm provider" check must win FIRST or the alert reads
    # as a YouTube-proxy outage.
    alerts = _alerts()
    assert alerts._classify_failure(
        "❌ Third-party LLM error: LLM provider rejected the request "
        "(HTTP 402): insufficient balance"
    ) == "llm provider"


def test_blocked_content_still_classifies_correctly():
    # The blocked message says "The AI provider blocked..." — never
    # "llm provider" — so it falls through to the existing content class:
    # the pinned ordering in test_alert_classify.py is untouched.
    alerts = _alerts()
    assert alerts._classify_failure(
        "🚫 The AI provider blocked this video's content (content_filter)."
    ) == "blocked content (user video)"
