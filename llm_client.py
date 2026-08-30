"""OpenAI-compatible LLM backend: one chat() funnel alongside Gemini.

Every AI call in this repo defaults to Google Gemini (google-genai). This
module adds an OPT-IN second backend for any OpenAI-compatible
chat-completions endpoint (Ollama Cloud, local Ollama, MiniMax, OpenRouter,
vLLM, ...). It is inert unless LLM_BASE_URL + LLM_API_KEY + a model resolve;
no existing module imports it on the default path. A half-configured env
(base+key but no model) is inert too, with a one-line warning — mirroring
transcribe_backends at CONFIG time: a gap degrades to the incumbent rather
than hijacking a working Gemini setup. At RUNTIME the contract is the
opposite: a fully-configured endpoint that rejects the job fails LOUDLY
(LlmError with the actionable reason) rather than silently billing Gemini.

Contract of chat() — the reason call sites can branch to it blindly:

    chat(prompt, schema=None, *, config, images=(), temperature=None,
         max_tokens=None) -> (parsed_or_text, cost_or_None)

  - schema given (a pydantic model) -> parsed is a dict validated against
    that model. A non-pydantic schema degrades to text mode (parsed is str);
    every schema the pipeline passes is a pydantic model.
  - schema absent -> parsed is the raw assistant text (str). Object-shaped
    JSON only on the schema path; array-shaped responses (SaaS scripts)
    stay on the text path where callers parse them as they do today.
  - Provider POLICY refusal raises gemini_worker.GeminiBlockedError with a
    message containing "blocked this video" so the existing never-retry,
    bisect and alert-classification ladders apply unchanged. Checked BEFORE
    any transient classification and only against STRUCTURED error fields
    (error.message/code/type) — never raw body text, so a gateway HTML page
    that happens to say "usage policy" cannot read as a block.
  - Provider TRANSIENT failure (429, 408, 5xx, timeout, transport error,
    empty/unparseable body, schema-validation failure) raises
    LlmTransientError — callers retry per their existing ladders.
  - Any other provider rejection (401, 403, 404, 402, bad request, response
    truncated at max_tokens, malformed endpoint URL) raises LlmError: not
    transient, not blocked; the job fails with the real, actionable reason.

JSON ladder (mirrors gemini_worker's strict-json / json-text-recovery /
structured-schema semantics), tried in order until one answers:
  1. response_format={"type": "json_schema", "strict": false}
  2. response_format={"type": "json_object"}          (JSON mode)
  3. bare request with the JSON contract EMBEDDED in the prompt — some
     prompts (LAYOUT_CHOICE_PROMPT) never say "JSON" in-band because they
     were written for response_schema, so the bare rung must carry it.
A 400 naming response_format/json_schema/json_object signals a capability
gap: the next rung runs. KNOWN NARROWNESS (accepted): the capability-gap
400 is recognized from structured JSON error fields first, and from raw
body text only as a fallback for THESE markers (never for blocked markers)
— a plain-text 400 that says nothing recognizable raises LlmError, loud
and actionable, instead of looping the ladder. Parsing is gemini_worker's
tolerant text parser on every rung.

Config resolution:
  - active_config(task=None) reads env: LLM_BASE_URL + LLM_API_KEY + a model
    all required; model = LLM_MODEL_<TASK> or LLM_MODEL (NEVER GEMINI_MODEL*
    — a Gemini model name sent to an OpenAI-compat endpoint is a 404).
    Tasks with a per-model env today: "thumbnail", "saas". task=None (clips
    and everything else) reads plain LLM_MODEL.
  - config_from(base_url, api_key, task=None, model=None) builds one from
    explicit values (request headers: the X-LLM-Base-Url + X-LLM-Key pair,
    plus optional X-LLM-Model which wins over the env chain) and returns
    None unless BOTH base and key are present, so a header key can never be
    sent to an env-configured third-party server.

Cost: usage.prompt_tokens / completion_tokens through
clip_selection.lookup_model_prices; unknown models estimate at (0.50, 3.00)
USD/1M with price_estimated=True, exactly like the Gemini path. NB: on
OpenAI-compat APIs completion_tokens ALREADY INCLUDES reasoning tokens
(usage.completion_tokens_details.reasoning_tokens is a subset), unlike
Gemini's candidates_token_count which excludes thoughts — reasoning tokens
are reported but never billed twice.

Log hygiene: the progress line says "Third-party LLM call", deliberately NOT
"LLM provider" — that phrase is reserved for error messages and is what
cloud/alerts._classify_failure keys the "llm provider" failure class on.
"""
import base64
import json
import os
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

import httpx

from clip_selection import lookup_model_prices
from gemini_worker import GeminiBlockedError, _parse_json_response_text


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str = field(repr=False)   # never lands in logs/tracebacks via repr
    model: str = ""


class LlmError(Exception):
    """Provider rejected the request (401/403/404/402/bad request/truncated/
    misconfigured endpoint). Not transient, not blocked: the job fails with
    the real reason."""


class LlmTransientError(Exception):
    """Provider failed transiently (429/408/5xx/timeout/empty body). Retried
    by callers through their existing retry ladders."""


# Policy-refusal markers, matched ONLY against structured error fields
# (error.message / error.code / error.type) — never raw body text, so an
# HTML gateway page mentioning "usage policy" cannot read as a block.
# Deliberately explicit: "invalid request"-style 400s stay LlmError.
_BLOCKED_MARKERS = (
    "content_filter",
    "content policy",
    "content_policy",
    "moderation system",
    "moderation_blocked",
    "safety system",
    "responsible ai",
)

_BLOCKED_FINISH = {"content_filter", "sensitive"}

# A 400 naming one of these means "I cannot do response_format", not a
# refusal and not our payload's fault: drop a rung and retry. Unlike the
# blocked markers, these MAY be matched against raw body text as a
# fallback — the worst case is one extra rung attempt, never a misclass.
_RF_REJECT_MARKERS = ("response_format", "response format", "json_schema",
                      "json mode", "json_object")

_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)

_clients = {}  # base_url -> httpx.Client; one per deployment in practice
               # ponytail: unbounded dict, fine while base_urls are operator-set
_warned_no_model = False


def _http_client(base_url: str) -> httpx.Client:
    client = _clients.get(base_url)
    if client is None:
        try:
            client = httpx.Client(base_url=base_url.rstrip("/"),
                                  timeout=_TIMEOUT, follow_redirects=True)
        except httpx.InvalidURL as e:
            raise LlmError("LLM provider endpoint URL is malformed (%s): %s"
                           % (base_url, e))
        _clients[base_url] = client
    return client


def config_from(base_url, api_key, task: Optional[str] = None,
                model: Optional[str] = None) -> Optional[LlmConfig]:
    """LlmConfig from explicit values (e.g. request headers), or None.

    Both values must be present: a caller that sends only a key must NOT have
    it forwarded to whatever base_url the server env happens to name. An
    explicit ``model`` (the X-LLM-Model header) completes the BYOK triple
    and wins over the env chain; with no model anywhere the config is None
    (backend inert): a half-configured endpoint must not hijack a working
    Gemini setup."""
    base_url = str(base_url or "").strip()
    api_key = str(api_key or "").strip()
    if not base_url or not api_key:
        return None
    resolved = str(model or "").strip()
    if not resolved and task:
        resolved = (os.environ.get("LLM_MODEL_" + task.upper()) or "").strip()
    resolved = resolved or (os.environ.get("LLM_MODEL") or "").strip()
    if not resolved:
        global _warned_no_model
        if not _warned_no_model:
            _warned_no_model = True
            print("⚠️ An OpenAI-compatible endpoint is configured "
                  "(LLM_BASE_URL/LLM_API_KEY) but no model is set "
                  "(LLM_MODEL, or LLM_MODEL_THUMBNAIL / LLM_MODEL_SAAS) — "
                  "the third-party backend stays inactive; Gemini remains "
                  "in use.")
        return None
    return LlmConfig(base_url=base_url, api_key=api_key, model=resolved)


def active_config(task: Optional[str] = None) -> Optional[LlmConfig]:
    """LlmConfig from env (LLM_BASE_URL + LLM_API_KEY + model), else None.

    None means: backend inert, every call site runs its Gemini code verbatim."""
    return config_from(os.environ.get("LLM_BASE_URL"),
                       os.environ.get("LLM_API_KEY"), task=task)


def _err_fields(resp) -> str:
    """Structured error fields only (message | code | type). Empty string for
    a non-JSON body: blocked-marker matching must never see raw body text."""
    try:
        data = resp.json()
    except ValueError:
        return ""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            parts = [str(err.get(k)) for k in ("message", "code", "type")
                     if err.get(k) is not None]
            return " | ".join(parts)
        if err is not None:
            return str(err)
    return ""


def _err_detail(resp) -> str:
    """Everything useful for a MESSAGE (not marker matching): structured
    fields, falling back to raw text for context."""
    structured = _err_fields(resp)
    if structured:
        return structured
    return (resp.text or "")[:400]


def _blocked(reason: str) -> GeminiBlockedError:
    return GeminiBlockedError(
        "The AI provider blocked this video's content (%s). The provider's "
        "usage policies reject this material, so it can't be analyzed." % reason)


def _rf_rejected(resp) -> bool:
    """True when a 400 means 'response_format not supported here'. Structured
    fields first; raw text fallback is safe for THESE markers only (worst
    case: one extra rung attempt) — blocked markers never get this fallback."""
    low = _err_fields(resp).lower()
    if not low:
        low = (resp.text or "").lower()
    return any(s in low for s in _RF_REJECT_MARKERS)


def _post(client: httpx.Client, headers: dict, payload: dict) -> Optional[dict]:
    """One POST /chat/completions. Returns the decoded body, or None when the
    endpoint rejected response_format itself (caller drops one rung).
    Precedence: blocked (deterministic) BEFORE transient, mirroring
    gemini_worker.raise_if_blocked's position before the retry ladder."""
    try:
        resp = client.post("/chat/completions", headers=headers, json=payload)
    except httpx.TimeoutException as e:
        raise LlmTransientError("LLM provider timeout (retryable): %s" % e)
    except httpx.TransportError as e:
        raise LlmTransientError(
            "LLM provider connection error (retryable): %s" % e)

    if resp.status_code >= 400:
        fields = _err_fields(resp)
        low = fields.lower()
        if any(m in low for m in _BLOCKED_MARKERS):
            raise _blocked("HTTP %d: %s" % (resp.status_code, fields[:120]))
        if resp.status_code == 400 and "response_format" in payload \
                and _rf_rejected(resp):
            return None  # capability gap: drop one rung
        if resp.status_code == 429 or resp.status_code == 408 \
                or resp.status_code >= 500 \
                or "rate_limit" in low or "overloaded" in low:
            raise LlmTransientError(
                "LLM provider transient error (HTTP %d, retryable): %s"
                % (resp.status_code, _err_detail(resp)[:300]))
        raise LlmError(
            "LLM provider rejected the request (HTTP %d): %s"
            % (resp.status_code, _err_detail(resp)[:300]))
    try:
        return resp.json()
    except ValueError as e:
        raise LlmTransientError(
            "LLM provider returned a non-JSON body (retryable): %s" % e)


def _assistant_text(body: dict) -> str:
    body = body if isinstance(body, dict) else {}

    # Some providers answer HTTP 200 with an error body instead of choices.
    # The same precedence applies: a policy refusal is deterministic. An
    # EMPTY error ({}/"") alongside valid choices is ignored (truthiness),
    # so a successful completion is never discarded.
    err = body.get("error")
    if err:
        fields = err if isinstance(err, dict) else {"message": str(err)}
        parts = [str(fields.get(k)) for k in ("message", "code", "type")
                 if fields.get(k) is not None]
        joined = " | ".join(parts) or str(err)
        low = joined.lower()
        if any(m in low for m in _BLOCKED_MARKERS):
            raise _blocked("200 error body: %s" % joined[:120])
        raise LlmError(
            "LLM provider returned an error body (HTTP 200): %s"
            % joined[:300])

    choices = body.get("choices") or []
    if not isinstance(choices, list) or not choices:
        raise LlmTransientError(
            "LLM provider returned an empty response body (retryable).")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    finish = str(choice.get("finish_reason") or "").lower()
    if finish in _BLOCKED_FINISH:
        raise _blocked("finish_reason=%s" % finish)
    if finish == "length":
        # Truncation is deterministic: retrying just burns the ladder. The
        # fix is a bigger context/max_tokens, and the message must say so.
        raise LlmError(
            "LLM provider response was truncated (finish_reason=length). "
            "Use a model with a larger context window, or lower the input "
            "size (fewer frames / shorter transcript).")
    message = choice.get("message") or {}
    text = message.get("content") if isinstance(message, dict) else None
    if isinstance(text, list):  # some providers return content parts
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    if not (text or "").strip():
        raise LlmTransientError(
            "LLM provider returned an empty response body (retryable).")
    return text


def _cost_from_usage(usage, model_name: str) -> Optional[dict]:
    """Same dict shape as gemini_worker._calculate_cost_analysis.

    completion_tokens already includes reasoning tokens on OpenAI-compat
    APIs, so output is billed on completion_tokens alone; thinking_tokens is
    reported for parity with the Gemini shape, never added again."""
    if not isinstance(usage, dict) or not usage:
        return None
    prices = lookup_model_prices(model_name)
    price_estimated = prices is None
    if prices is None:
        # Unknown model: conservative estimate so the UI shows something sane.
        prices = (0.50, 3.00)
    input_price, output_price = prices
    try:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        details = usage.get("completion_tokens_details") or {}
        thinking = int(details.get("reasoning_tokens") or 0) \
            if isinstance(details, dict) else 0
    except (TypeError, ValueError):
        return None  # garbage usage must not fail a successful generation
    input_cost = (prompt_tokens / 1_000_000) * input_price
    output_cost = (completion_tokens / 1_000_000) * output_price
    return {
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "thinking_tokens": thinking,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
        "model": model_name,
        "price_estimated": price_estimated,
    }


def _json_contract(schema) -> str:
    schema_json = json.dumps(schema.model_json_schema())
    return ("Return ONLY a valid JSON object matching this schema — no "
            "markdown fences, no commentary:\n%s" % schema_json)


def chat(prompt, schema=None, *, config: LlmConfig, images: Sequence = (),
         temperature=None, max_tokens=None,
         json_mode: bool = False) -> Tuple[object, Optional[dict]]:
    """One chat-completions call against the configured endpoint. See the
    module docstring for the full contract. Never retries on its own except
    the response_format ladder; transient retries belong to callers."""
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

    # A non-pydantic schema (main.py's pinned tests pass `object`) degrades
    # to text mode; every schema real callers pass is a pydantic model.
    if schema is not None and not hasattr(schema, "model_json_schema"):
        schema = None

    # Plain string content for text-only calls (universally accepted);
    # array content parts only when images ride along.
    if images:
        content = [{"type": "text", "text": prompt}]
        for img in images:
            if isinstance(img, (bytes, bytearray)):
                b64 = base64.b64encode(bytes(img)).decode("ascii")
                content.append({"type": "image_url",
                                "image_url": {"url": "data:image/jpeg;base64," + b64}})
            else:
                content.append({"type": "image_url", "image_url": {"url": str(img)}})
    else:
        content = prompt

    rungs = []
    if schema is not None:
        rungs.append({"type": "json_schema",
                      "json_schema": {"name": getattr(schema, "__name__", "response"),
                                      "schema": schema.model_json_schema(),
                                      "strict": False}})
        rungs.append({"type": "json_object"})
    elif json_mode:
        # No pydantic schema exists for these shapes (thumbnail/saas prompts
        # carry their JSON contract in-band). Ask for JSON mode anyway; the
        # bare rung still works where the endpoint cannot do it.
        rungs.append({"type": "json_object"})
    rungs.append(None)  # bare: contract embedded when schema is given

    client = _http_client(base_url)
    headers = {"Authorization": "Bearer " + config.api_key,
               "Content-Type": "application/json"}

    print("🤖 Third-party LLM call: model=%s images=%d schema=%s"
          % (config.model, len(images), "yes" if schema else "no"))

    body = None
    for rung in rungs:
        payload = {"model": config.model,
                   "messages": [{"role": "user", "content": content}]}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if rung is not None:
            payload["response_format"] = rung
        elif schema is not None:
            # The bare rung must carry the JSON contract in-band: prompts
            # written for response_schema (LAYOUT_CHOICE_PROMPT) never ask
            # for JSON in the text itself.
            bare = content + [{"type": "text", "text": _json_contract(schema)}] \
                if isinstance(content, list) \
                else content + "\n\n" + _json_contract(schema)
            payload["messages"] = [{"role": "user", "content": bare}]
        body = _post(client, headers, payload)
        if body is not None:
            break

    text = _assistant_text(body)
    usage = body.get("usage") if isinstance(body, dict) else None

    if schema is None:
        return text, _cost_from_usage(usage, config.model)
    try:
        parsed = _parse_json_response_text(text)
    except ValueError:
        # Neutral wording: the shared parser's text names Gemini — wrong for a
        # third-party provider. Callers only need the retryable class.
        raise LlmTransientError(
            "LLM provider returned an unparseable response (retryable).")
    try:
        obj = schema.model_validate(parsed)
    except Exception as e:
        raise LlmTransientError(
            "LLM provider response failed schema validation (retryable): %s" % e)
    return obj.model_dump(), _cost_from_usage(usage, config.model)