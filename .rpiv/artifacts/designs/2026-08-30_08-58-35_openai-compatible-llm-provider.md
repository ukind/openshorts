---
date: 2026-08-30T08:58:35+0700
author: Yogiswara Utama
commit: 071c4c3
branch: main
repository: openshorts
topic: "OpenAI-compatible third-party LLM endpoint alongside Gemini (additive)"
tags: [design, llm-provider, llm_client, gemini, openai-compat, ollama, minimax, openrouter]
status: ready
parent: .rpiv/artifacts/solutions/2026-08-30_08-26-00_add-openai-compatible-llm-provider.md
last_updated: 2026-08-30T08:58:35+0700
last_updated_by: Yogiswara Utama
---

# Design: OpenAI-compatible third-party LLM endpoint alongside Gemini

## Summary

Add an opt-in OpenAI-compatible chat-completions backend (`llm_client.py`) alongside the existing Google Gemini calls. Gemini stays the default and byte-identical when no third-party endpoint is configured; every rerouted call site keeps its Gemini code verbatim and branches to `llm_client.chat()` only when an `LLM_*` config resolves. The backend is vendor-neutral: Ollama Cloud, local Ollama, MiniMax M3, OpenRouter, vLLM — any `/v1/chat/completions` endpoint.

## Requirements

- Gemini remains the default; zero behavior change when no third-party endpoint is configured (structural guarantee: the Gemini code at each branch stays verbatim).
- Any OpenAI-compatible chat-completions endpoint can be selected via env (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) or per-request headers (`X-LLM-Base-Url` + `X-LLM-Key`, optional `X-LLM-Model`; self-host only).
- Structured output keeps working: `response_format: json_schema` → JSON-mode fallback → tolerant text parse (mirrors the repo's existing 3-strategy ladder).
- Blocked-content and transient-retry semantics survive: provider refusals raise the existing `GeminiBlockedError`; provider transients join the existing retry ladder as a typed exception.
- Self-host BYOK works for the third-party endpoint the way `X-Gemini-Key` works today (including the documented resume-loss caveat).
- Cost estimates mark unknown models `price_estimated=True` (existing fallback).
- Cloud/managed mode keeps working exactly as today: pinned to Gemini, no billing changes.
- The 4 pinned test suites (`test_gemini_retry`, `test_gemini_block_split`, `test_alert_classify`, `test_clip_selection`) stay green unmodified.
- **README.md must contain robust how-to-use guidance** for OpenAI-compatible endpoints (user requirement, 2026-08-30): configuration recipes for Ollama Cloud (primary), local Ollama, MiniMax, OpenRouter; a capability matrix (what reroutes, what stays Gemini); degradation and troubleshooting notes.

## Current State Analysis

All AI calls go through `google-genai` keyed by `GEMINI_API_KEY` / `X-Gemini-Key`. Capability classes (from the solutions artifact, corrected):

| Class | Calls | Sites | Phase-1 routing |
|---|---|---|---|
| A. Text + strict schema | clips score/detail, thumbnail titles/concepts/description, SaaS analyze/scripts | `main.py:1506-1639`, `thumbnail.py:117,166,234,406,717`, `saasshorts.py:357,539` | **Reroutes** via llm_client |
| B. Image-in (frames) | layout picker (12 frames), thumbnail titles (10 frames) | `layout_picker.py:139-167`, `thumbnail.py:47-50` | **Reroutes** via llm_client |
| C. Full-video upload | silent-video analysis, editor effects, **screencast wide-content detection** | `main.py:1650-1732`, `editor.py:36,131,140,221,386`, `screencast_layout.py:192` | Stays Gemini-pinned |
| D. Image generation | thumbnail image render | `thumbnail.py:543-591` | Stays Gemini-pinned |
| E. Google-Search grounding | SaaS online research | `saasshorts.py:108-124` | Stays Gemini-pinned |

### Key Discoveries

- **Research-table correction**: the solutions artifact lists `screencast_layout.py` under class B, but its `detect_content_ranges` uploads the whole video via the Files API (`screencast_layout.py:192`) — it is class C. Decision: keep it Gemini-pinned in phase 1 (user-confirmed).
- `transcribe_backends.py:16-30` (commit 719d444) is the repo's only second-backend precedent: ONE funnel function, written output contract, env switch, default = incumbent, automatic fallback. `llm_client.chat()` mirrors this shape.
- The blocked ladder is keyed entirely on `gemini_worker.GeminiBlockedError`: never-retry (`main.py:1466`), bisect (`main.py:1494-1510`), propagation (`main.py:1622-1625`), alert classifier message match `"blocked this video"` (`cloud/alerts.py:63`). Raising the same class from the new backend keeps all of it working.
- The transient ladder (`main.py:1442-1481`, `_run_gemini_stage`) uses a Google-shaped token list at `main.py:1472-1477`. Adding a typed `LlmTransientError` except-clause is safer than editing the token list (which could change Gemini retry behavior).
- `test_gemini_block_split.py` monkeypatches `main._run_gemini_stage` by name; `test_gemini_retry.py` fakes the genai client passed into it. Signatures must stay call-compatible (optional kwargs only).
- Git precedents: per-request keys die on resume (commit 900dc44 — accepted for `X-Gemini-Key`); body-key BYOK was a security hole closed in 6ec6935 (do not extend it); alert-class ordering was corrected twice in prod (`77905a9`, `730f7de`); redaction/provider strings must ship in the same commit (`29fed21`→`8160fc6`); fal.ai is the wrong key-plumbing precedent (never crosses the subprocess boundary — LLM config does).
- Ollama's OpenAI-compat layer supports `response_format`, base64 data-URL images, `temperature`, `max_tokens` (docs.ollama.com/api/openai-compatibility, accessed 2026-08-30). Ollama Cloud base URL: `https://ollama.com/v1`.
- Model choice is env-only today (no runtime model dropdown anywhere — verified by integration scan).
- Cost fallback already handles unknown models: `(0.50, 3.00)` + `price_estimated=True` (`gemini_worker.py:416-419`, `clip_selection.py:9-26`).

## Scope

### Building

- `llm_client.py`: `LlmConfig`, `config_from()`, `active_config(task)`, `chat()` funnel (json_schema ladder, blocked/transient mapping, cost builder).
- `tests/test_llm_client.py`: httpx MockTransport tests for every mapping + alert-class tests for the new failure class.
- Pipeline rerouting: `main.py` `get_viral_clips` (gate + model + branch inside `_run_gemini_stage`'s retry loop), `layout_picker.py` `pick()`.
- In-process rerouting: `thumbnail.py` (analyze_video_for_titles, refine_titles, plan_thumbnail_concepts, generate_youtube_description), `saasshorts.py` (analyze_saas, generate_scripts).
- `app.py`: `resolve_llm()`, `/api/process` gate (Gemini key OR LLM config) + subprocess env injection, endpoint wirings for thumbnail/titles/describe + saasshorts. (`_SENSITIVE_LOG_RE` intentionally NOT touched: the regex is dead at HEAD — `log_view.friendly_logs` is the live cloud filter — and cloud never sees third-party logs because BILLING strips `LLM_*` from job envs; see Verification Notes.)
- `cloud/alerts.py`: provider-neutral `"llm provider"` class below content-shaped classes, above `"gemini"`.
- `mcp_server.py`: forward `x-llm-base-url`, `x-llm-key`.
- Docs: README robust how-to section, `.env.example`, `skills/openshorts/reference.md`, `CLAUDE.md` env-chain note.

### Not Building

- editor.py effects (`/api/edit`, `/api/effects/generate`): class C video uploads, stay Gemini-pinned → no `LLM_MODEL_EDITOR` env (dead knob).
- `screencast_layout.py` rerouting: class C (research table mislabeled it B); its `WIDE_CONTENT_PROMPT` width numbers were measured on full-video input.
- `main.py get_visual_clips` (silent-video) rerouting: class C.
- Thumbnail image generation routing: class D, stays on `GEMINI_IMAGE_MODEL`.
- `research_saas_online` rerouting: class E (Google-Search grounding).
- Cloud/managed mode changes (managed keys, metering, entitlements): cloud stays Gemini-pinned; `resolve_llm` returns None under billing.
- Dashboard UI: no provider fields, no key inputs (header keys die on resume — the UI would advertise a flaky setting).
- LiteLLM, per-task provider matrix, token-based metering, marketing/SEO copy changes.
- Body-key BYOK for LLM (`/api/edit`-style): closed security surface (precedent 6ec6935); not extended.

## Decisions

### D1. Funnel seam: one `chat()` function with a written contract

Ambiguity: how should the new backend be shaped? Explored: (A) one funnel function modeled after `transcribe_backends.py:16-30` (commit 719d444 — the repo's only second-backend precedent, whose follow-up fixes were all capability gaps, not contract breaks); (B) finer-grained send/parse APIs composed at call sites.
**Decision: (A)** — `chat(prompt, schema=None, *, images=(), config, temperature=None, max_tokens=None) -> (parsed_dict | text, cost_analysis | None)`, with blocked-error and transient-error semantics in the contract. Confirmed by developer (directional confirm 1).

### D2. Blocked mapping: raise the existing `GeminiBlockedError`

Ambiguity: new exception hierarchy vs reuse. Explored: (A) import and raise `gemini_worker.GeminiBlockedError` directly — the never-retry ladder (`main.py:1466`), bisect (`main.py:1494`), propagation (`main.py:1622`), and the classifier's `"blocked this video"` message match (`cloud/alerts.py:63`) all work unchanged; (B) subclass; (C) own hierarchy + mapping at every catch site (larger diff, touches pinned paths).
**Decision: (A)**. Message text: `"The AI provider blocked this video's content (...)"` — contains the classifier substring. Confirmed by developer (directional confirm 2).

### D3. Transient mapping: typed `LlmTransientError` caught explicitly, token list untouched
Editing `main.py:1472-1477`'s Google-shaped token list (e.g. adding `'llm provider'`) would make ALL provider errors transient, including 401/404 (never retryable). A new `except llm_client.LlmTransientError` clause before the generic token check keeps Gemini behavior byte-identical and classifies provider errors exactly.
**Decision: typed exception; no token-list edits.**

### D4. Branch at call sites (seam placement)

Ambiguity: where does the third-party branch live? Explored: (A) purely additive `llm_client.py`; each of ~8 call sites keeps its Gemini code verbatim and gains `if llm: ... else: <existing code>`; the pinned suites are safe by construction and an llm_client bug cannot touch the default path; (B) unified funnel — move the Gemini path inside llm_client (single seam, but the byte-identical guarantee becomes behavioral, verified only by tests).
**Decision: (A)**. Confirmed by developer (Q1). Previewed shape: the branch sits inside `_run_gemini_stage`'s existing retry loop.

### D5. Screencast stays Gemini-pinned (class C correction)

The solutions artifact's capability table lists screencast under class B (frames), but the code uploads the whole video (`screencast_layout.py:192`). A frames variant of `WIDE_CONTENT_PROMPT` would be unmeasured — the repo's layout heuristics have a documented history of failing when re-measured on different input. Without a Gemini key, screencast is already a silent no-op (`screencast_layout.py:175-177`), so third-party-only users keep default routing with zero risk.
**Decision: keep Gemini-pinned in phase 1.** Confirmed by developer (Q2).

### D6. Model envs: `LLM_MODEL`, `LLM_MODEL_THUMBNAIL`, `LLM_MODEL_SAAS` (no EDITOR)

Idiom from commit e5d8899 (`GEMINI_MODEL_<TASK> or GEMINI_MODEL or default`). When the third-party backend is active, the chain is `LLM_MODEL_<TASK> or LLM_MODEL` — it never falls through to `GEMINI_MODEL*` (wrong vendor: a Gemini model name sent to an OpenAI-compat endpoint fails). `LLM_MODEL_EDITOR` is deferred until editor.py reroutes.
**Decision: 3 envs with live consumers.** Confirmed by developer (directional confirm 3).

### D7. Config resolution: headers → env, self-host only; cloud returns None

`resolve_llm(request, task=None)` mirrors `resolve_gemini` (`app.py:124`): under billing, return None (cloud is Gemini-pinned; a prefix sweep also strips `LLM_*` from the subprocess and resume envs so a stray server env cannot reroute managed jobs); self-host, the BYOK header triple `X-LLM-Base-Url` + `X-LLM-Key` (+ optional `X-LLM-Model`, completing the vendor triple) wins when base+key are BOTH present (never mix a header key with an env base_url — that would leak the caller's key to a third party's server), else `LLM_*` env with per-task `LLM_MODEL_<TASK>` resolution (`task`: "thumbnail"/"saas"). Header config injected into the `/api/process` subprocess env like `GEMINI_API_KEY` today; lost on resume exactly like `X-Gemini-Key` (documented, precedent 900dc44). Half-configured setups (no model) stay inert with a warning (llm_client handles it).
**Decision: header-triple-or-env, no body keys, cloud-pinned with env sweep.** Confirmed by developer (Q3: no dashboard UI). `X-LLM-Model` was added during slice-2 verification: a header pair without any server-side model could never activate, which silently defeated the whole BYOK path.

### D8. Documented vendor: Ollama Cloud primary

User: "i mostly use ollama cloud". README/.env.example examples target `LLM_BASE_URL=https://ollama.com/v1` + Ollama API key (docs.ollama.com, accessed 2026-08-30); local Ollama (`http://localhost:11434/v1`), MiniMax M3 and OpenRouter documented as alternates in the same section. Code stays vendor-neutral.
**Decision: Ollama Cloud primary example.** Confirmed by developer (Q4).

### D9. Alert class: `"llm provider"` at the TOP of `_classify_failure`

`_classify_failure` is strictly ordered, first-match-wins; its ordering was corrected twice in prod (`77905a9`, `730f7de`) and is pinned by `test_alert_classify.py`. Original placement (below content classes, above `"gemini"`) is WRONG for this backend: `_PROXY_HINTS` (`cloud/alerts.py:33-36`) matches `"insufficient balance"` and `"402 payment required"`, and llm_client's 402 message ECHOES the provider body — a third-party out-of-credit error would land in `"proxy"` before any lower class could run. The check goes FIRST: llm_client error messages are namespaced (`"LLM provider ..."` prefix) and can never be YouTube-proxy/transcription errors, while provider bodies can echo proxy phrases. Blocked-content messages say `"The AI provider blocked this video"` and never contain `"llm provider"`, so they still fall through to the existing blocked class untouched — its ordering is preserved. Two guardrails ship with this: the progress line says `"Third-party LLM call"` (never `"LLM provider"`) so log-tail fallbacks cannot poison the class, and new tests go in `tests/test_llm_client.py` (the pinned suite stays unmodified).
**Decision: FIRST position, namespaced-marker match, progress-line phrase reserved for errors.** (Amended during slice-1 verification — original below-content placement falsified by the proxy-hint echo.)

### D10. Cost: reuse `lookup_model_prices` + estimated fallback

`chat()` builds the same `cost_analysis` dict shape as `gemini_worker._calculate_cost_analysis` (`gemini_worker.py:412-440`) from OpenAI `usage.prompt_tokens`/`completion_tokens` (+ `completion_tokens_details.reasoning_tokens` as thinking when present), reusing `clip_selection.lookup_model_prices`; unknown models ride `(0.50, 3.00)` + `price_estimated=True`. No new price table.
**Decision: reuse the existing lookup and fallback.**

### D11. JSON ladder: json_schema → JSON-mode → bare-with-embedded-contract; parse failures are transient

Three rungs, tried until one answers: (1) `response_format={"type":"json_schema", "strict": false}` (repo schemas use optional fields, so strict is off and the pydantic model validates server-side-trusted output anyway); (2) `response_format={"type":"json_object"}`; (3) bare request with the JSON contract EMBEDDED in the prompt — necessary because `LAYOUT_CHOICE_PROMPT` never says "JSON" in-band (it was written for `response_schema`). A 400 naming response_format/json_schema/json_object (structured fields first, raw text fallback for THESE markers only) drops one rung. Parsing is `gemini_worker._parse_json_response_text` on every rung; a parse or validation failure raises `LlmTransientError` — mirroring the Gemini path, where an empty/unparseable 200 body is retried (prod 22-jul-2026). KNOWN NARROWNESS (accepted, documented in the module docstring): a plain-text 400 that names nothing recognizable raises `LlmError` (loud) instead of looping the ladder.
**Decision: 3-rung ladder, RF-markers may match raw text, blocked markers never do; parse failures transient.** (Amended during slice-1 verification — original 2-rung design missed the JSON-mode rung and the layout-prompt gap.)

## Architecture

### llm_client.py — NEW

One funnel `chat()` against any OpenAI-compatible `/v1/chat/completions` endpoint; GeminiBlockedError/LlmTransientError mapping; cost builder; env/header config resolution.

```python
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
    except ValueError as e:
        raise LlmTransientError(
            "LLM provider returned an unparseable body (retryable): %s" % e)
    try:
        obj = schema.model_validate(parsed)
    except Exception as e:
        raise LlmTransientError(
            "LLM provider response failed schema validation (retryable): %s" % e)
    return obj.model_dump(), _cost_from_usage(usage, config.model)
```

### tests/test_llm_client.py — NEW

httpx MockTransport tests for the funnel contract: schema happy path, response_format rejection fallback, parse-failure → transient, policy refusal → blocked (never retried upstream), 429/500/timeout → transient, 401/404 → non-transient, usage → cost with `price_estimated`, config resolution rules, alert-class tests for the new `"llm provider"` class.

```python
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


def test_half_configured_env_is_inert_with_warning(monkeypatch, capsys):
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


# --- _http_client construction (the seam tests monkeypatch over) --------------

def test_http_client_is_cached_per_base_url():
    a = llm_client._http_client("https://cache.test/v1/")
    b = llm_client._http_client("https://cache.test/v1/")
    assert a is b
    assert a.base_url == "https://cache.test/v1"  # trailing slash stripped
    assert a.follow_redirects is True


def test_http_client_rejects_invalid_url():
    with pytest.raises(llm_client.LlmError):
        llm_client._http_client("not a url")


# --- pipeline branch (slice 2): main._run_gemini_stage with an llm config ----
# main pulls cv2/torch/mediapipe at import; skip per-TEST (a module-level
# importorskip would void every slice-1 contract test above in minimal envs).


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
```

### main.py:1442-1520 — MODIFY

`_run_gemini_stage` gains the third-party branch inside the existing retry loop (Gemini code verbatim in the else), an `LlmTransientError` except clause, and an optional `llm` kwarg threaded through `_run_stage_split`; `get_viral_clips` gate becomes "Gemini key OR llm config", model selection per backend; `get_visual_clips` gets a clearer no-key message.

```python
# --- module top: after `import gemini_worker` (main.py:24) -------------------
import llm_client   # opt-in third-party backend; inert without LLM_* env


def _run_gemini_stage(client, model_name, prompt, schema, llm=None):
    """One schema-enforced Gemini call with transient-error backoff.
    Returns (parsed_dict, cost_analysis). With ``llm`` (an llm_client
    LlmConfig) the same one call goes to the OpenAI-compatible endpoint
    instead; the Gemini arm below is verbatim the pre-branch code."""
    config = genai_types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
    )
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            if llm is not None:
                # Third-party backend: the JSON ladder, blocked/transient
                # mapping and cost all live in llm_client. This loop stays
                # the single owner of RETRY policy for both backends.
                return llm_client.chat(prompt, schema, config=llm)
            response = client.models.generate_content(model=model_name, contents=prompt, config=config)
            # Policy blocks are deterministic — retrying only burns quota and
            # time, and the user deserves the real reason instead of a generic
            # "empty response" (prod 23-jul: PROHIBITED_CONTENT on every try).
            gemini_worker.raise_if_blocked(response)
            # Parsing lives inside the retry loop on purpose: Gemini sometimes
            # returns 200 with an empty body, which raises here rather than at
            # the call. Retrying that recovered every occurrence seen in prod
            # (22-jul-2026) — the same payload succeeds on the next attempt.
            parsed_obj = getattr(response, "parsed", None)
            if parsed_obj is not None:
                parsed = parsed_obj.model_dump() if hasattr(parsed_obj, "model_dump") else parsed_obj
            else:
                parsed = gemini_worker._parse_json_response_text(
                    gemini_worker._get_response_text(response))
            return parsed, gemini_worker._calculate_cost_analysis(response, model_name)
        except gemini_worker.GeminiBlockedError:
            raise  # deterministic policy block — never retry
        except llm_client.LlmTransientError:
            # Third-party outage/empty body: the same backoff the Gemini arm
            # gives its own blips. The message is deliberately NOT echoed
            # here — recovered blips must not put "LLM provider" into the
            # job log tail where the alert classifier's fallback reads it
            # (D9). The text surfaces only on the final raise.
            if attempt == max_attempts:
                raise
            wait = 5 * (2 ** (attempt - 1))
            print(f"⚠️ Third-party endpoint blip (attempt {attempt}/{max_attempts}), retrying in {wait}s")
            time.sleep(wait)
        except llm_client.LlmError:
            raise  # provider rejected the request (401/404/402/truncated): deterministic
        except Exception as e:
            msg = str(e)
            transient = any(tok in msg for tok in (
                '503', 'UNAVAILABLE', '429', 'RESOURCE_EXHAUSTED',
                '500', 'INTERNAL', 'overloaded', 'Deadline',
                'empty response body', 'did not contain a JSON object',
                'Failed to parse Gemini JSON response'))
            if attempt == max_attempts or not transient:
                raise
            wait = 5 * (2 ** (attempt - 1))
            print(f"⚠️ Gemini transient error (attempt {attempt}/{max_attempts}), retrying in {wait}s: {msg[:150]}")
            time.sleep(wait)


def _run_stage_split(client, model_name, items, build_prompt, schema, key, costs, label, llm=None):
    """Run a stage over ``items``; on a policy block, bisect. (Docstring
    unchanged from HEAD.) Thread ``llm`` only when set: tests and external
    monkeypatchers patch _run_gemini_stage with the historical 4-arg
    signature, and the pinned suites rely on that shape."""
    if not items:
        return []
    prompt = build_prompt(items)
    stage_kwargs = {"llm": llm} if llm is not None else {}
    try:
        parsed, cost = _run_gemini_stage(client, model_name, prompt, schema, **stage_kwargs)
        if cost:
            costs.append(cost)
        return list(parsed.get(key) or [])
    except gemini_worker.GeminiBlockedError as e:
        if len(items) == 1:
            print(f"   🚫 {label}: blocked window {items[0].get('id')} on its own; skipping it ({e})")
            return []
        mid = len(items) // 2
        print(f"   🚫 {label}: blocked a batch of {len(items)}; retrying as {mid} + {len(items) - mid}")
        return (_run_stage_split(client, model_name, items[:mid], build_prompt, schema, key, costs, label, llm=llm)
                + _run_stage_split(client, model_name, items[mid:], build_prompt, schema, key, costs, label, llm=llm))


# --- get_viral_clips head (main.py:1520-1529) — print order preserved --------
    llm = llm_client.active_config()
    if llm is not None:
        print("🤖 Analyzing with the third-party endpoint (2-pass: score → detail)...")
    else:
        print("\U0001f916  Analyzing with Gemini (2-pass: score → detail)...")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key and llm is None:
        print("❌ Error: GEMINI_API_KEY not found in environment variables.")
        return None
    # The llm arm never touches the client; None is fine when only the
    # third-party endpoint is configured.
    client = genai.Client(api_key=api_key) if api_key else None
    model_name = (llm.model if llm is not None
                  else os.environ.get("GEMINI_MODEL") or 'gemini-3.1-flash-lite')
    language = str(transcript_result.get('language') or 'unknown')
    print(f"\U0001f916  Model: {model_name} | language: {language}")
# (…windowing/prompts unchanged; BOTH _run_stage_split calls gain `llm=llm`.)

# --- cost aggregate (main.py:1606-1612, inside `if costs:`) -----------------
        cost_analysis = {
            "input_tokens": sum(c.get("input_tokens", 0) for c in costs),
            "output_tokens": sum(c.get("output_tokens", 0) for c in costs),
            "total_cost": sum(c.get("total_cost", 0) for c in costs),
            "model": model_name,
        }
        if llm is not None:
            # Third-party models are usually unknown to MODEL_PRICES: keep the
            # estimated flag so the UI marks the number as an estimate. Gemini
            # jobs keep their historical aggregate shape exactly.
            cost_analysis["price_estimated"] = any(
                c.get("price_estimated") for c in costs)

# --- get_viral_clips except chain (main.py:1622-1629) -----------------------
    except gemini_worker.GeminiBlockedError as e:
        # Content-policy rejection: propagate so the job fails with the real
        # reason instead of a generic "no clips found".
        print(f"🚫 {e}")
        raise
    except (llm_client.LlmError, llm_client.LlmTransientError) as e:
        # Deterministic provider rejection, or a transient that outlived the
        # retry ladder: propagate with the real reason instead of collapsing
        # into "no usable clips" (which the alert classifier would mislabel
        # as a user-content problem).
        print(f"❌ Third-party LLM error: {e}")
        raise
    except Exception as e:
        print(f"❌ Gemini Error: {e}")
        return None

# --- get_visual_clips no-key message (main.py:~1655) -------------------------
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ Error: GEMINI_API_KEY not found. Silent-video analysis "
              "watches the footage on Gemini; the third-party LLM endpoint "
              "cannot replace it.")
        return None
```

### layout_picker.py:143-190 — MODIFY

`pick()` gate becomes "GEMINI_API_KEY OR llm config"; third-party branch sends the same 12 frames through `llm_client.chat()` with `LayoutChoice` schema; every failure still degrades to `"none"`.

```python
def pick(video_path, video_duration):
    """The layout the model (Gemini, or the third-party endpoint when
    configured) picks for this video, or "none" on any failure.

    Never raises: a missing answer has to degrade to today's routing
    rather than break the job.
    """
    if not ENABLED:
        return "none"
    api_key = os.getenv("GEMINI_API_KEY")
    llm = None
    try:
        # Lazy like the genai import below: the disabled path (and CI) must
        # not pay for it. active_config() never raises; it may print a
        # one-line warning when the endpoint is half-configured.
        import llm_client
        llm = llm_client.active_config()
    except Exception:
        llm = None
    if llm is None and not api_key:
        return "none"

    model_name = os.environ.get("GEMINI_MODEL") or 'gemini-3.1-flash-lite'
    print("🎛️  Choosing a layout for this video…")
    try:
        # Inside the try on purpose: the contract above is that this never
        # raises, and an unimportable SDK is just one more reason to fall back.
        from google import genai
        from google.genai import types as genai_types
        import gemini_worker

        frames = sample_frames(video_path)
        if not frames:
            print("   ⚠️ No readable frames — keeping the default layout.")
            return "none"

        if llm is not None:
            # Third-party endpoint: same 12 frames, same closed-choice
            # schema. The third-party backend wins when configured, symmetric
            # with get_viral_clips — mixed routing would be worse than either.
            answer, _cost = llm_client.chat(
                gemini_worker.LAYOUT_CHOICE_PROMPT,
                gemini_worker.LayoutChoice,
                images=frames, config=llm)
        else:
            client = genai.Client(api_key=api_key)
            parts = [genai_types.Part.from_bytes(data=b, mime_type="image/jpeg")
                     for b in frames]
            response = client.models.generate_content(
                model=model_name,
                contents=parts + [gemini_worker.LAYOUT_CHOICE_PROMPT],
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=gemini_worker.LayoutChoice,
                ))
            gemini_worker.raise_if_blocked(response)
            answer = json.loads(response.text) or {}
    except Exception as e:
        print(f"   ⚠️ Layout choice failed ({e}) — keeping the default layout.")
        return "none"
    # (decision-validation tail below the try is unchanged from HEAD.)
```

### app.py — MODIFY

`resolve_llm(request, task=None)` next to `resolve_gemini` (`app.py:124`) with the BYOK header triple; `/api/process` gate (Gemini key OR LLM config, informative 400 on neither, self-host) + subprocess env injection next to `env["GEMINI_API_KEY"]` (`app.py:2197`); resume env sweep under BILLING (`app.py:876`); thumbnail analyze/titles/describe + saasshorts analyze endpoints resolve and thread `llm_config` (slice 3). `gemini_missing_error()` is NOT changed (class-C/D/E endpoints keep their accurate message); `_SENSITIVE_LOG_RE` is NOT changed (dead code — see Verification Notes).

```python
# --- hunk 1: resolve_llm next to resolve_gemini (app.py:124) -----------------
async def resolve_llm(request: Request, task: Optional[str] = None):
    """Resolve the third-party OpenAI-compatible LLM endpoint config.

    Cloud (hosted) stays Gemini-pinned: always None. Self-host: the BYOK
    header triple X-LLM-Base-Url + X-LLM-Key (+ optional X-LLM-Model) wins
    when base+key are both present — a header key must never travel to an
    env-configured base_url — else the LLM_* env with per-task
    LLM_MODEL_<TASK> resolution (``task``: "thumbnail" / "saas" today).
    None when no full config resolves (llm_client prints a warning on a
    half-configured env). NB: like X-Gemini-Key, header-provided config
    does NOT survive a redeploy resume — the manifest rebuilds env from
    os.environ only (app.py:876).
    """
    if BILLING_ENABLED:
        return None
    try:
        import llm_client
    except Exception:
        return None  # guarded like layout_picker's SDK import: gate, not 500
    cfg = llm_client.config_from(
        request.headers.get("X-LLM-Base-Url"),
        request.headers.get("X-LLM-Key"),
        task=task,
        model=request.headers.get("X-LLM-Model"))
    if cfg is not None:
        return cfg
    return llm_client.active_config(task)


# --- hunk 2: /api/process gate (app.py:2073-2075) -----------------------------
    api_key = await resolve_gemini(request)
    llm_cfg = await resolve_llm(request)
    if not api_key and llm_cfg is None:
        if not BILLING_ENABLED:
            raise HTTPException(status_code=400, detail=(
                "Missing X-Gemini-Key header. Set a Gemini key, or use an "
                "OpenAI-compatible endpoint: server env LLM_BASE_URL + "
                "LLM_API_KEY + LLM_MODEL, or X-LLM-Base-Url + X-LLM-Key "
                "(+ X-LLM-Model) headers."))
        raise gemini_missing_error()


# --- hunk 3: subprocess env (app.py:2197-2198) --------------------------------
    env = os.environ.copy()
    if api_key:
        env["GEMINI_API_KEY"] = api_key  # Override with key from request
    if llm_cfg is not None:
        # Per-request provider override travels to the subprocess as env —
        # the same road GEMINI_API_KEY takes (and the same resume caveat).
        env["LLM_BASE_URL"] = llm_cfg.base_url
        env["LLM_API_KEY"] = llm_cfg.api_key
        env["LLM_MODEL"] = llm_cfg.model
    elif BILLING_ENABLED:
        # Cloud is Gemini-pinned: a stray LLM_* in the server env must not
        # reroute managed jobs to a third-party endpoint (resolve_llm is
        # already None under billing; this closes the env-copy hole).
        # Prefix sweep, not a fixed list: future LLM_* knobs inherit it.
        for _k in [k for k in env if k.startswith("LLM_")]:
            env.pop(_k, None)


# --- hunk 4: resume env rebuild (app.py:~876, inside _resume_interrupted_jobs)
        env = os.environ.copy()
        if BILLING_ENABLED and user_id is not None:
            try:
                env["GEMINI_API_KEY"] = managed_keys.gemini_key()
            except Exception:
                pass
        if BILLING_ENABLED:
            # Cloud stays Gemini-pinned on resume too (same sweep as spawn).
            for _k in [k for k in env if k.startswith("LLM_")]:
                env.pop(_k, None)

# --- hunk 5: LLM_ENDPOINT_HINT module constant (self-host 400 detail) ----------
LLM_ENDPOINT_HINT = ("Missing X-Gemini-Key header. Set a Gemini key, or use "
    "an OpenAI-compatible endpoint: server env LLM_BASE_URL + LLM_API_KEY + "
    "LLM_MODEL, or X-LLM-Base-Url + X-LLM-Key (+ X-LLM-Model) headers.")


# --- hunk 6: /api/thumbnail/analyze gate + wiring (app.py:4762) -----------------
    api_key = await resolve_gemini(request)
    llm_cfg = await resolve_llm(request, task="thumbnail")
    if not api_key and llm_cfg is None:
        if not BILLING_ENABLED:
            raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
        raise gemini_missing_error()
    # ...(analyze_video_for_titles executor call: append llm_cfg positionally)


# --- hunk 7: /api/thumbnail/titles gate + wiring (app.py:4864) -------------------
    api_key = await resolve_gemini(request)
    llm_cfg = await resolve_llm(request, task="thumbnail")
    if not api_key and llm_cfg is None:
        if not BILLING_ENABLED:
            raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
        raise gemini_missing_error()
    # ...(refine_titles executor call: append llm_cfg positionally)


# --- hunk 8: /api/thumbnail/generate — gate UNCHANGED; concepts threaded only ----
    # Gate unchanged (images need Gemini). Only adds llm_cfg resolution
    # and threads it into generate_thumbnail.
    llm_cfg = await resolve_llm(request, task="thumbnail")
    # ...(generate_thumbnail call gains llm_config=llm_cfg)


# --- hunk 9: /api/thumbnail/describe gate + wiring (app.py:5071) -----------------
    api_key = await resolve_gemini(request)
    llm_cfg = await resolve_llm(request, task="thumbnail")
    if not api_key and llm_cfg is None:
        if not BILLING_ENABLED:
            raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
        raise gemini_missing_error()
    # ...(generate_youtube_description executor call: append llm_cfg positionally)


# --- hunk 10: /api/saasshorts/analyze gate + wiring (app.py:5271) ---------------
    gemini_key = await resolve_gemini(request)
    llm = await resolve_llm(request, task="saas")
    if not gemini_key and llm is None:
        if not BILLING_ENABLED:
            raise HTTPException(status_code=400, detail=LLM_ENDPOINT_HINT)
        raise gemini_missing_error()
    ...
        def run_analysis():
            web_research = None
            if req.url and req.url.strip():
                scraped = scrape_website(req.url)
                if gemini_key:
                    web_research = research_saas_online(req.url, gemini_key)
                else:
                    # Grounded research is class E (Gemini-only): with a
                    # third-party endpoint and no Gemini key, skip it rather
                    # than crash — the analysis runs on the scrape alone.
                    print("[SaaSShorts] No Gemini key — skipping grounded web "
                          "research (third-party endpoint in use).")
                analysis = analyze_saas(scraped, gemini_key,
                                        web_research=web_research, llm_config=llm)
            else:
                ...(manual-description branch unchanged)...
            scripts = generate_scripts(analysis, gemini_key, req.num_scripts,
                                       req.style, req.language, req.actor_gender,
                                       llm_config=llm)
```

### thumbnail.py — MODIFY

Four text functions gain `llm_config=None` and branch to `llm_client.chat(json_mode=True)` (these prompts carry their JSON contract in-band; the branch stays inside HEAD's try/except so the exception surface and fallback prints are verbatim). `generate_thumbnail` gains `llm_config=None` threaded into `plan_thumbnail_concepts` ONLY (its image calls stay Gemini — class D). Frames stay 10@1024 (`TITLE_FRAMES`/`TITLE_FRAME_WIDTH`) with []-degradation; `genai.Client` is built only when `llm_config is None`.

> NOTE (from slice-3 verification): these in-process sites have no retry ladder (neither does the Gemini arm), so a provider `LlmTransientError`/`LlmError` propagates to a 500 with the real provider reason — the same surface a Gemini 500 gives today. Consistent by design.

> The round-1 verifier caught five real bugs, all fixed: (1) `response.text` hoisted out of HEAD's try changed the exception surface — now the branch sits inside the try with HEAD's `(json.JSONDecodeError, AttributeError)` tuple; (2) frames defaulted to 12 (layout picker) instead of 10 — now explicit `TITLE_FRAMES`/`TITLE_FRAME_WIDTH`; (3) `plan_thumbnail_concepts` was orphaned (generate endpoint unchanged) — now `/api/thumbnail/generate` threads `llm_cfg` to it (gate untouched); (4) `schema=None` sent no JSON enforcement while the Gemini arm pins `response_mime_type=json` — `chat(json_mode=True)` sends the JSON-mode rung; (5) `run_in_executor` takes positional args only — `llm_config` is the last positional param, appended positionally at each site.

```python
# Four text functions gain an optional ``llm_config=None`` param and branch
# to llm_client.chat() with ``json_mode=True`` (these prompts carry their JSON
# contract in-band; no pydantic schema exists). The genai client is built only
# when llm_config is None (the Gemini arm); image generation (_generate_one /
# generate_thumbnail render flow) is untouched (class D). The branch sits
# INSIDE the same try/except as the Gemini arm so HEAD's exception surface
# (json.JSONDecodeError, AttributeError) and fallback prints stay verbatim.

def analyze_video_for_titles(api_key, video_path, transcript=None, llm_config=None):
    """(docstring unchanged)"""
    ...(transcript handling unchanged)...
    # Third-party endpoint: the SAME 10 frames @1024px the Gemini arm sends
    # (_frame_parts defaults via TITLE_FRAMES/TITLE_FRAME_WIDTH), degraded to
    # [] on any failure — mirrors _frame_parts. Gemini arm keeps _frame_parts.
    if llm_config is not None:
        import llm_client
        from layout_picker import sample_frames
        try:
            frames = sample_frames(video_path, n=TITLE_FRAMES,
                                   width=TITLE_FRAME_WIDTH)
        except Exception as e:
            print(f"⚠️ [Thumbnail] Could not sample frames: {e}")
            frames = []
    else:
        frames = _frame_parts(video_path)
    client = genai.Client(api_key=api_key) if (api_key and llm_config is None) else None
    ...(language/segments/transcript_text unchanged)...
    print("🤖 [Thumbnail] Brainstorming titles...")
    try:
        if llm_config is not None:
            text, _cost = llm_client.chat(brainstorm_prompt, config=llm_config,
                                          images=frames, json_mode=True)
        else:
            response = client.models.generate_content(
                model=TEXT_MODEL,
                contents=frames + [brainstorm_prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            text = response.text
        draft = _parse_json(text)
    except (json.JSONDecodeError, AttributeError):
        print(f"❌ [Thumbnail] Failed to parse brainstorm JSON: "
              f"{text if llm_config is not None else getattr(response, 'text', '')}")
        return {  # fallback dict — verbatim from HEAD
            "titles": ["Could not generate titles - please try again"],
            "thumbnail_texts": [], "transcript_summary": transcript.get("text", "")[:500],
            "language": language, "segments": segments,
            "video_duration": video_duration, "recommended": [],
        }
    ...(summary/candidates unchanged)...
    print("🧐 [Thumbnail] Scoring titles...")
    try:
        if llm_config is not None:
            text, _cost = llm_client.chat(critic_prompt, config=llm_config,
                                          json_mode=True)
        else:
            response = client.models.generate_content(
                model=TEXT_MODEL, contents=[critic_prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            text = response.text
        picked = _parse_json(text)
        titles = [t for t in picked.get("titles", []) if isinstance(t, str) and t.strip()]
        if not titles:
            raise ValueError("no titles")
    except (json.JSONDecodeError, AttributeError, ValueError):
        print(f"⚠️ [Thumbnail] Critic failed, falling back to the brainstorm: "
              f"{text if llm_config is not None else getattr(response, 'text', '')[:300]}")
        titles = candidates[:10]
        picked = {"thumbnail_texts": [], "recommended": []}
    ...(tail unchanged)...


def refine_titles(api_key, context, user_message, conversation_history=None, llm_config=None):
    """(docstring unchanged)"""
    client = genai.Client(api_key=api_key) if (api_key and llm_config is None) else None
    ...(history_text/prompt unchanged)...
    try:
        if llm_config is not None:
            import llm_client
            text, _cost = llm_client.chat(prompt, config=llm_config, json_mode=True)
        else:
            response = client.models.generate_content(
                model=TEXT_MODEL, contents=[prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json"))
            text = response.text
        result = _parse_json(text)
        titles = [t for t in result.get("titles", []) if isinstance(t, str) and t.strip()]
        texts = [str(t) for t in result.get("thumbnail_texts", [])][:len(titles)]
        texts += [""] * (len(titles) - len(texts))
        return {"titles": titles, "thumbnail_texts": texts,
                "language": str(result.get("language") or "")[:5]}
    except (json.JSONDecodeError, AttributeError):
        print(f"❌ [Thumbnail] Failed to parse refined titles: "
              f"{text if llm_config is not None else getattr(response, 'text', '')}")
        return {"titles": ["Could not refine titles - please try again"],
                "thumbnail_texts": [], "language": ""}


# plan_thumbnail_concepts gains llm_config=None (last positional). Its caller
# generate_thumbnail gains llm_config=None and threads it through here ONLY.
# Image calls, reference images, the genai client, the ThreadPoolExecutor
# render flow: untouched (class D).
def plan_thumbnail_concepts(client, title, count, video_context="", extra_prompt="",
                            thumbnail_text_hint="", has_person=False, language="en",
                            llm_config=None):
    ...(prompt construction unchanged)...
    try:
        if llm_config is not None:
            import llm_client
            text, _cost = llm_client.chat(prompt, config=llm_config, json_mode=True)
        else:
            response = client.models.generate_content(
                model=TEXT_MODEL, contents=[prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json"))
            text = response.text
        concepts = _parse_json(text).get("concepts", [])
    except (json.JSONDecodeError, AttributeError):
        print(f"⚠️ [Thumbnail] Concept JSON unreadable, using a generic concept: "
              f"{text if llm_config is not None else getattr(response, 'text', '')[:200]}")
        concepts = []
    return normalise_concepts(concepts, count, title, thumbnail_text_hint)


def generate_thumbnail(api_key, title, session_id, ..., llm_config=None):
    # The genai client is still built here (image gen needs it); llm_config is
    # threaded ONLY into plan_thumbnail_concepts.
    client = genai.Client(api_key=api_key)  # unchanged — images need Gemini
    ...(rest unchanged until the plan_thumbnail_concepts call)...
    concepts = plan_thumbnail_concepts(
        client, title, count, video_context=video_context, extra_prompt=extra_prompt,
        thumbnail_text_hint=thumbnail_text_hint, has_person=bool(reference_images),
        language=language, llm_config=llm_config)
    ...(render flow unchanged)...


def generate_youtube_description(api_key, title, transcript_segments, language,
                                 video_duration, llm_config=None):
    client = genai.Client(api_key=api_key) if (api_key and llm_config is None) else None
    ...(prompt construction unchanged)...
    print("🤖 [Thumbnail] Generating YouTube description with chapters...")
    if llm_config is not None:
        import llm_client
        description = llm_client.chat(prompt, config=llm_config, json_mode=True)[0].strip()
    else:
        response = client.models.generate_content(model=TEXT_MODEL, contents=[prompt])
        description = response.text.strip()
    ...(markdown cleanup + return unchanged)...
```

### saasshorts.py — MODIFY

`analyze_saas` and `generate_scripts` gain `llm_config=None` and branch to `llm_client.chat(json_mode=True)`. `research_saas_online` (Google-Search grounding, class E) is untouched; the URL flow skips it with a log line when only the third-party endpoint is configured (no Gemini key to ground with). `generate_scripts` parses a top-level JSON ARRAY — it stays on the text path (chat's schema path is object-shaped) with `max_tokens=8192` parity. The genai client is built only when `llm_config is None` (at HEAD's position, before the call).

> NOTE (from slice-3 verification): these in-process sites have no retry ladder (neither does the Gemini arm); a provider `LlmTransientError`/`LlmError` propagates to a 500 with the real provider reason — the same surface a Gemini 500 gives today. Consistent by design.

```python
def analyze_saas(scraped_data: dict, gemini_key: str, web_research: dict = None,
                 llm_config=None) -> dict:
    """(docstring unchanged)"""
    from google import genai
    from google.genai import types

    print(f"[SaaSShorts] 🧠 Analyzing {scraped_data['url']} (with web research)...")

    # The client stays at HEAD's position (before prompt build); only built
    # when the Gemini arm runs.
    if llm_config is None:
        client = genai.Client(api_key=gemini_key)
    ...(research_context/prompt construction unchanged)...

    if llm_config is not None:
        import llm_client
        raw, _cost = llm_client.chat(prompt, config=llm_config, json_mode=True)
    else:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        raw = response.text
    ...(HEAD's `raw` empty-check + find("{") object parse + return unchanged)...


def generate_scripts(analysis, gemini_key, num_scripts=3, style="ugc", language="en",
                     actor_gender="female", llm_config=None) -> list:
    """(docstring unchanged)"""
    from google import genai
    from google.genai import types
    ...(prompt construction unchanged)...
    if llm_config is None:
        client = genai.Client(api_key=gemini_key)
    ...
    if llm_config is not None:
        import llm_client
        # ARRAY response: stays on the text path (chat's schema path is
        # object-shaped); 8192 mirrors the Gemini arm's max_output_tokens.
        raw, _cost = llm_client.chat(prompt, config=llm_config, max_tokens=8192,
                                     json_mode=True)
    else:
        response = client.models.generate_content(
            model=GEMINI_MODEL, contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=8192,
            ),
        )
        raw = response.text
    ...(HEAD's empty-check + find("[")/rfind("]") array parse unchanged)...

# research_saas_online: UNTOUCHED (class E, Google-Search grounding).
# The /api/saasshorts/analyze URL flow skips it when there is no Gemini key
# and a third-party endpoint is in use (see the app.py endpoint wiring).
```

### cloud/alerts.py:58-70 — MODIFY

`_classify_failure` gains a provider-neutral `"llm provider"` class at the TOP — namespaced `"LLM provider ..."` messages can never be YouTube-proxy/transcription errors, while provider bodies CAN echo `_PROXY_HINTS` phrases ("insufficient balance"). Blocked-content messages say "The AI provider blocked this video" and never contain "llm provider", so the existing blocked ordering is untouched (precedent 77905a9/730f7de). The progress line in llm_client says "Third-party LLM call" (never "LLM provider") so log-tail fallbacks cannot poison the class (D9 guardrail).

> The original below-content placement was falsified by the proxy-hint echo: a 402 "insufficient balance" from a provider would land in "proxy" before any lower class. The check goes FIRST.

```python
def _classify_failure(err: str) -> str:
    """One-word category for the last error, so the alert points the right way."""
    e = (err or "").lower()
    # Third-party LLM provider error: checked FIRST. Namespaced
    # "LLM provider ..." messages can never be proxy/transcription/ffmpeg
    # errors, but provider bodies can echo _PROXY_HINTS phrases
    # ("insufficient balance", "402 payment required"). A blocked-content
    # message says "The AI provider blocked this video" — it never contains
    # "llm provider", so it falls through to the existing blocked class.
    if "llm provider" in e:
        return "llm provider"
    if _looks_like_proxy_error(e):
        return "proxy"
    ...(rest of the function unchanged from HEAD)...
```

### mcp_server.py:53 — MODIFY

`_FORWARD_HEADERS` gains `x-llm-base-url`, `x-llm-key` and `x-llm-model` so MCP callers can bring their own endpoint (the full BYOK triple — a pair without a model can never activate).

```python
_FORWARD_HEADERS = ("authorization", "x-api-key", "x-gemini-key",
                    "x-upload-post-key", "x-llm-base-url", "x-llm-key",
                    "x-llm-model")
```

### README.md — MODIFY

Robust "Using an OpenAI-compatible endpoint" section: env/header reference table, Ollama Cloud / local Ollama / MiniMax / OpenRouter recipes, capability matrix (what reroutes vs stays Gemini), degradation notes (layout picker falls back, screencast/editor/visual need Gemini), resume caveat for header keys, troubleshooting (401/404/model-not-found/timeout).

```markdown
## Using an OpenAI-compatible endpoint (instead of, or alongside, Gemini)

OpenShorts uses Google Gemini by default for all AI work. Every TEXT stage can
instead run on ANY OpenAI-compatible chat-completions endpoint — Ollama Cloud,
a local Ollama, MiniMax, OpenRouter, vLLM, llama.cpp server, an OpenAI-compatible
proxy — selected with three environment variables. Gemini stays the default:
with the variables unset, nothing changes.

### Configuration (server env)

| Variable | Required | Meaning |
|---|---|---|
| `LLM_BASE_URL` | yes | Base URL of the OpenAI-compatible API, e.g. `https://ollama.com/v1` |
| `LLM_API_KEY` | yes | API key (Ollama Cloud key, MiniMax key, OpenRouter key, ...). Any value works for a local Ollama (`ollama`). |
| `LLM_MODEL` | yes | Default model for all rerouted stages, e.g. `gpt-oss:120b` |
| `LLM_MODEL_THUMBNAIL` | no | Model for thumbnail title/concept text (defaults to `LLM_MODEL`) |
| `LLM_MODEL_SAAS` | no | Model for SaaS analyze/script text (defaults to `LLM_MODEL`) |

All three of `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` are required: a
partially-set endpoint stays INERT (Gemini keeps running) with a one-line
warning naming what is missing. There is no `LLM_MODEL_EDITOR` yet — the
editor effects stage and the other Gemini-only stages below do not reroute.

### What reroutes, what stays on Gemini

| Stage | Reroutes to the endpoint? | Notes |
|---|---|---|
| Clip scoring + detail (the 2-pass analysis) | yes | including the blocked-content bisect ladder |
| Layout picker (12 frames) | yes | degrades to the default layout on any failure, exactly as with Gemini |
| Thumbnail titles / concepts / description (TEXT) | yes | image GENERATION stays on Gemini |
| SaaS analyze + script generation | yes | |
| SaaS web research (Google-Search grounding) | no — Gemini-only | skipped with a log line when only the endpoint is configured |
| Thumbnail image generation | no — Gemini-only | needs a Gemini key as before |
| Silent-video clip detection (vision) | no — Gemini-only | the endpoint cannot watch video |
| Editor effects (/api/edit, /api/effects) | no — Gemini-only | video upload stages |
| Cloud/managed mode | no — Gemini-pinned | `LLM_*` env vars are stripped from managed jobs |

Structured output: requests ask for `response_format=json_schema` first, fall
back to JSON mode, then to a plain request (every prompt embeds its JSON shape
in-band and responses are parsed tolerantly — the same ladder the Gemini path
uses). Provider policy refusals raise the SAME blocked-content error the Gemini
path raises (never retried; alerts classify it as "blocked content (user video)").
Provider outages (429/5xx/timeout) retry 3x with backoff and then fail with the
real reason; bad keys and unknown models fail immediately with the provider's
message.

### Recipes

Ollama Cloud (hosted; get an API key at ollama.com):

```bash
LLM_BASE_URL=https://ollama.com/v1
LLM_API_KEY=sk-...your-ollama-key...
LLM_MODEL=gpt-oss:120b
```

Local Ollama (same machine; any non-empty key value works):

```bash
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen3:32b
```

MiniMax M3 (OpenAI-compatible endpoint):

```bash
LLM_BASE_URL=https://api.minimax.io/v1
LLM_API_KEY=eyJ...your-minimax-key...
LLM_MODEL=MiniMax-M3
```

OpenRouter (one key, many models):

```bash
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=sk-or-v1-...
LLM_MODEL=anthropic/claude-sonnet-4
```

vLLM, llama.cpp and anything else speaking `/v1/chat/completions` works the
same way. Vision stages (layout picking, thumbnail title brainstorm) need a
model that accepts base64 images; they are sent as data URLs.

### Per-request BYOK (API / MCP callers)

Self-host callers can override the endpoint per request with headers (base-url
AND key must be sent together — a header key is never sent to an env-configured
base URL):

```bash
curl -X POST http://localhost:8000/api/thumbnail/analyze \
  -H "X-LLM-Base-Url: https://ollama.com/v1" \
  -H "X-LLM-Key: $OLLAMA_KEY" \
  -H "X-LLM-Model: gpt-oss:120b" \
  -F "file=@video.mp4"
```

Caveat: like `X-Gemini-Key`, header-provided config does NOT survive a
redeploy-resume — an interrupted job falls back to the server's env (and, with
no env config, fails with the normal missing-key message). Env-configured
endpoints survive restarts and resumes.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Warning "...no model is set... backend stays inactive" | `LLM_MODEL` (or the per-task var) is missing; Gemini keeps running |
| `LLM provider rejected the request (HTTP 401)` | Wrong API key |
| `HTTP 404` naming the model | Model does not exist on that endpoint (`ollama pull` it, or fix the name) |
| `LLM provider response was truncated (finish_reason=length)` | Model context too small for transcript + frames — pick a larger-context model |
| `LLM provider timeout` / `transient error (HTTP 429/5xx)` | Retried 3x automatically; if it still ends the job, the endpoint was down — try again later |
| `The AI provider blocked this video's content (...)` | The endpoint's policies refuse this material — same meaning as Gemini's blocked error, never retried |
| Jobs use Gemini despite LLM_* being set | The three required vars are not all set (check the startup warning), or you are on cloud/managed mode (pinned to Gemini) |

Rollback: unset the `LLM_*` variables and restart — the pipeline returns to
Gemini with no code or data changes.
### .env.example — MODIFY

New LLM_* block with Ollama Cloud example and per-task model envs.

```
# --- OpenAI-compatible third-party LLM endpoint (optional) ------------------
# Reroutes the TEXT stages (clip scoring/detail, layout picking, thumbnail
# titles/concepts/description, SaaS analyze/scripts) to any OpenAI-compatible
# chat-completions endpoint. Gemini stays the default when these are unset.
# All three are required; a partially-set endpoint stays inert.

LLM_BASE_URL=https://ollama.com/v1
LLM_API_KEY=sk-...your-ollama-key...
LLM_MODEL=gpt-oss:120b
# LLM_MODEL_THUMBNAIL=qwen3-coder:480b  # optional per-task models
# LLM_MODEL_SAAS=anthropic/claude-sonnet-4
```
### skills/openshorts/reference.md — MODIFY

BYOK contract: `X-LLM-Base-Url` + `X-LLM-Key` headers alongside `X-Gemini-Key`.

```markdown
Self-hosted instances can route the AI text stages to any OpenAI-compatible
endpoint with three env vars (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) or
the header triple `X-LLM-Base-Url` + `X-LLM-Key` (+ optional `X-LLM-Model`).
See the server README for the full capability matrix and recipes. The Gemini
key is still needed for image generation, silent-video analysis, and editor
effects (class C/D/E stages stay Gemini-pinned in phase 1).
```
### CLAUDE.md — MODIFY

Short note in the model/env documentation: the LLM_* chain and what stays Gemini-pinned.

```markdown
# Third-party LLM endpoint (optional, phase 1):
#   LLM_BASE_URL + LLM_API_KEY + LLM_MODEL — any OpenAI-compatible /v1/chat/completions
#   Per-task: LLM_MODEL_THUMBNAIL, LLM_MODEL_SAAS (chain: LLM_MODEL_<TASK> or LLM_MODEL)
#   Reroutes: clips score/detail, layout picker, thumbnail text, SaaS analyze/scripts
#   Stays Gemini: image gen, silent-video, editor effects, SaaS grounded research, cloud/managed
#   Half-configured (no model) → inert + warning; default path byte-identical when unset.
```

## Slices

### Slice 1: llm_client.py — the OpenAI-compat backend + unit tests

**Files**: `llm_client.py`, `tests/test_llm_client.py`

#### Automated Verification:

- [ ] `python -m pytest tests/test_llm_client.py -q` passes (40 contract tests)
- [ ] `grep -n "blocked this video" llm_client.py` finds the `_blocked()` message (classifier substring)
- [ ] 401/404/402/truncation/malformed-URL raise `LlmError` with the mock handler called exactly once
- [ ] 429/408/500/503/529/timeout/empty-body/schema-failure raise `LlmTransientError`
- [ ] Blocked takes precedence over transient; HTTP-200 error-body refusals are blocked; HTML pages are never classified blocked
- [ ] Reasoning tokens reported but billed once (`output_cost == 5/1M*3.00`, never 8/1M)
- [ ] Ladder: json_schema → json_object → bare-with-embedded-contract; a genuine 400 never loops it
- [ ] `config_from` requires BOTH values; missing model → None (inert, warned once); chain `LLM_MODEL_<TASK>` → `LLM_MODEL`; no `LLM_MODEL_CLIPS`; never `GEMINI_MODEL*`
- [ ] Half-configured env does not hijack Gemini (`active_config() is None`)

#### Manual Verification:

- [ ] Pinned suites green, unmodified, no LLM_* env: `python -m pytest tests/test_gemini_retry.py tests/test_gemini_block_split.py tests/test_alert_classify.py tests/test_clip_selection.py tests/test_layout_picker.py -q`
- [ ] Module imports cleanly with no LLM_* env: `python -c "import llm_client; assert llm_client.active_config() is None"`

### Slice 2: Subprocess pipeline vertical — /api/process path

**Files**: `main.py`, `layout_picker.py`, `app.py` (cloud pinning ships here as the BILLING `LLM_*` env sweep — the redaction-precedent replacement, see Verification Notes)

#### Automated Verification:

- [ ] `python -m pytest tests/test_llm_client.py -q` passes — slice-1 contract tests still collect (per-test skips, no module-level skip) + 11 pipeline tests
- [ ] Pinned suites green UNMODIFIED, no LLM_* in shell env AND no LLM_* rows in .env: `python -m pytest tests/test_gemini_retry.py tests/test_gemini_block_split.py tests/test_layout_picker.py -q`
- [ ] `_run_stage_split` keeps the historical 4-arg call shape when llm is None; bisect halves forward `llm=` (both pinned by tests)
- [ ] The Google transient token tuple in `_run_gemini_stage` is byte-identical: `git diff main.py | grep "^[+-].*RESOURCE_EXHAUSTED"` prints nothing
- [ ] Recovered llm blips never print "LLM provider" (capsys-pinned)
- [ ] `price_estimated` present on the llm-path aggregate, absent from Gemini-path aggregates (test drives a full llm run with shorts)
- [ ] BILLING strips LLM_* from subprocess and resume envs: `grep -c 'startswith("LLM_")' app.py` returns 2
- [ ] get_viral_clips with llm-only env calls the backend, never constructs genai.Client(None), and propagates LlmError with "Third-party LLM error"

#### Manual Verification:

- [ ] Self-host with LLM_BASE_URL/LLM_API_KEY/LLM_MODEL (no GEMINI_API_KEY): a YouTube job produces clips via the endpoint; logs show "Analyzing with the third-party endpoint"
- [ ] Same instance, env unset and no .env LLM_* rows: pipeline logs identical to pre-change
- [ ] Header-only job (X-LLM-* headers, no env) fails after a mid-job redeploy with the clear missing-key message — the documented per-request caveat

### Slice 3: In-process endpoints vertical — thumbnail text + SaaS text

**Files**: `thumbnail.py`, `saasshorts.py`, `app.py`

#### Automated Verification:

- [ ] `python -m pytest tests/test_llm_client.py -q` passes — slice-1/2 tests collect; +6 slice-3 tests (per-test importorskips)
- [ ] No `genai.Client` construction with a None key on rerouted paths; unused-client construction skipped when llm_config active (`if (api_key and llm_config is None)`)
- [ ] `generate_scripts` llm request carries `max_tokens=8192`; `json_mode=True` sends the json_object rung first (both test-pinned)
- [ ] `research_saas_online` and `_generate_one`/image flow untouched in the diff
- [ ] `/api/thumbnail/generate` gate unchanged; only `llm_cfg` resolution + threading added
- [ ] The 4 dual-gated endpoints self-host 400 carries `LLM_ENDPOINT_HINT`; cloud keeps `gemini_missing_error()` (402)

#### Manual Verification:

- [ ] Self-host with LLM_* env: /api/thumbnail/analyze returns titles via the endpoint; /api/thumbnail/generate still needs the Gemini key for images (concepts via the endpoint when both configured)
- [ ] /api/saasshorts/analyze with a URL and llm-only config completes with the "skipping grounded web research" log line
- [ ] Cloud mode: all endpoints behave exactly as before (resolve_llm returns None; gates reduce to the original)

### Slice 4: Observability + MCP + docs

**Files**: `cloud/alerts.py`, `tests/test_llm_client.py` (alert-class tests, appended), `mcp_server.py`, `README.md`, `.env.example`, `skills/openshorts/reference.md`, `CLAUDE.md`

#### Automated Verification:

- [ ] `python -m pytest tests/test_llm_client.py -q` passes — including the 4 alert-class tests
- [ ] `grep -n '"llm provider"' cloud/alerts.py` — the check is the FIRST branch of `_classify_failure`, before `_looks_like_proxy_error`
- [ ] Existing alert tests green unmodified: `python -m pytest tests/test_alert_classify.py -q`
- [ ] `grep -n "x-llm" mcp_server.py` → 3 headers in `_FORWARD_HEADERS`
- [ ] `grep -c 'startswith("LLM_")' app.py` returns 2 (spawn + resume sweeps — slice 2's cloud pinning)

#### Manual Verification:

- [ ] Simulated job log with "LLM provider rejected the request (HTTP 402): insufficient balance" classifies as "llm provider", not "proxy" (Telegram alert reads sensibly)
- [ ] MCP caller with the X-LLM-* header triple reaches the third-party endpoint end-to-end
- [ ] README section reviewed: env table, capability matrix, 4 recipes, BYOK caveat, troubleshooting table all present

## Desired End State

Self-host operator sets env and everything text-shaped routes to their endpoint:

```bash
# Ollama Cloud (primary documented example)
LLM_BASE_URL=https://ollama.com/v1
LLM_API_KEY=<ollama api key>
LLM_MODEL=gpt-oss:120b
# optional per-task models (chain: LLM_MODEL_<TASK> or LLM_MODEL)
LLM_MODEL_THUMBNAIL=qwen3-coder:480b

curl -X POST http://localhost:8000/api/process \
  -H "Content-Type: application/json" \
  -d '{"youtube_url": "https://youtu.be/..."}'
# → clips generated by the third-party model; cost_analysis shows
#   "model": "gpt-oss:120b", "price_estimated": true
```

Per-request BYOK (API/MCP callers, self-host):

```bash
curl -X POST http://localhost:8000/api/thumbnail/analyze \
  -H "X-LLM-Base-Url: https://ollama.com/v1" \
  -H "X-LLM-Key: $OLLAMA_KEY" \
  -H "X-LLM-Model: gpt-oss:120b" \
  -F "file=@video.mp4"
# → titles generated by the third-party model (still needs a Gemini key
#   for thumbnail IMAGE rendering; title/TEXT calls alone do not)
```

With the env unset, every path behaves exactly as before: same logs, same calls, same costs, pinned suites green.

A provider outage retried then failed maps to the alert class `"llm provider"` (not "gemini", not "mixed"); a provider policy refusal maps to `"blocked content (user video)"` exactly like Gemini's does.

## File Map

```
llm_client.py                     # NEW — OpenAI-compat backend: chat() funnel, config, mappings
tests/test_llm_client.py          # NEW — MockTransport tests + alert-class tests
main.py                           # MODIFY — _run_gemini_stage branch, get_viral_clips gate/model, _run_stage_split threading, visual message
layout_picker.py                  # MODIFY — pick() gate + third-party frames branch
app.py                            # MODIFY — resolve_llm (header triple), /api/process gate+env, resume env sweep, endpoint wirings
thumbnail.py                      # MODIFY — 4 text fns llm_config param + branch
saasshorts.py                     # MODIFY — analyze_saas, generate_scripts llm_config param + branch
cloud/alerts.py                   # MODIFY — "llm provider" failure class
mcp_server.py                     # MODIFY — forward x-llm-base-url / x-llm-key
README.md                         # MODIFY — robust OpenAI-compat how-to section
.env.example                      # MODIFY — LLM_* block (Ollama Cloud example)
skills/openshorts/reference.md    # MODIFY — BYOK header contract
CLAUDE.md                         # MODIFY — LLM_* env-chain note
```

## Ordering Constraints

- Slice 1 before all others (defines the funnel every call site branches to).
- Slice 2 before Slice 3 (`resolve_llm` lives in app.py and is introduced in Slice 2).
- Slice 4 last (alert-class tests assert the error-message shapes produced by Slice 1; docs describe behavior landed in 2+3).
- Within Slice 2: `main.py` and `layout_picker.py` changes are independent of each other but both need Slice 1.
- No slice may modify any of the 4 pinned test suites.

## Verification Notes

- Pinned default-path invariance: `python -m pytest tests/test_gemini_retry.py tests/test_gemini_block_split.py tests/test_alert_classify.py tests/test_clip_selection.py tests/test_layout_picker.py` green with zero modifications, with NO `LLM_*` env set.
- Blocked mapping: `grep -n "blocked this video" llm_client.py` → message substring present (classifier match).
- Transient classification: `LlmTransientError` caught in `main.py` retry loop; a 401 from the endpoint must NOT be retried (assert `calls == 1`).
- Alert ordering (AMENDED, see D9): the `"llm provider"` check goes FIRST in `_classify_failure` — namespaced `"LLM provider ..."` messages can never be YouTube-proxy errors, while provider bodies can echo `_PROXY_HINTS` phrases ("insufficient balance"). Blocked-content messages never contain "llm provider", so the existing blocked ordering is untouched (precedent 77905a9/730f7de).
- Redaction (AMENDED during slice-2 verification): `_SENSITIVE_LOG_RE` is DEAD code at HEAD — zero references; the live cloud filter is the `log_view.friendly_logs` whitelist (`app.py:1652-1655`). The provider-strings/redaction precedent (29fed21→8160fc6) is therefore satisfied differently and more strongly: BILLING strips every `LLM_*` var from job envs, so third-party requests (and their error strings) can never run in cloud mode at all, and cloud users only ever see whitelist lines. No regex edit ships.
- Subprocess: `/api/process` with only `LLM_*` env (no GEMINI_API_KEY) must reach `get_viral_clips` and NOT 400.
- Resume: env-configured `LLM_*` survives (rebuild from `os.environ`, `app.py:876`); header-configured does not (documented in README).
- MiniMax M3 json_schema support is UNVERIFIED (research blocker) — the ladder covers both outcomes; manual spike before finalizing vendor docs is optional.
- Capability probe risk (research): provider image-input support varies; layout picker already degrades to "none" on any chat failure (pinned by `test_layout_picker.py`).

## Performance Considerations

- No new dependencies: httpx is already pinned (`requirements.txt:18`).
- One module-level `httpx.Client` per base_url, reused across calls (connect pooling, follow_redirects on); timeout 300s read (long generations on reasoning models), 10s connect.
- Frames travel as base64 data URLs (~4MB payload for 12 frames at 1024px q80) — within chat-completions norms; only on class-B calls (layout picker, thumbnail brainstorm).
- No streaming; single request per call, retry left to the caller's ladder (unchanged semantics).
- Cost/latency parity: 12 frames ≈ 3k tokens regardless of source duration (existing measurement, `CLAUDE.md` layout-picker notes).

## Migration Notes

No persisted-schema changes; no data migration. Rollback = unset `LLM_*` env (code path reverts to Gemini by construction). Existing jobs are unaffected. Backwards compatibility: all new params are optional kwargs; all new envs unread when absent.

## Pattern References

- `transcribe_backends.py:16-30` — the funnel + written-contract + env-switch-defaulting-to-incumbent shape (commit 719d444).
- `gemini_worker.py:356-391` — `GeminiBlockedError` + `raise_if_blocked` (the class and message shapes we reuse).
- `gemini_worker.py:412-440` — cost_analysis dict shape and price-estimated fallback.
- `main.py:1462-1510` — the retry ladder and bisect ladder the mappings must join.
- `layout_picker.py:143-190` — silent no-op degradation on optional AI paths.
- `saasshorts.py:575-649` — `_fal_run`: second-provider HTTP plumbing precedent (httpx against a third-party API).
- `app.py:124-140` — `resolve_gemini` resolution chain `resolve_llm` mirrors.
- `editor.py:31-34` — env-chain model selection idiom.

## Developer Context

- Directional confirm 1 (funnel shape): Follow the transcribe_backends funnel pattern — approved.
- Directional confirm 2 (blocked mapping): Reuse `GeminiBlockedError` directly — approved.
- Directional confirm 3 (model envs): 3 envs, drop EDITOR — approved.
- Q1 seam placement (evidence: pinned suites fake/monkeypatch `_run_gemini_stage`; commits 05578c5/719d444 shipped flag-off-byte-identical): **Branch at call sites** — approved.
- Q2 screencast scope (evidence: `screencast_layout.py:192` Files API upload = class C; research table mislabeled it B): **Keep Gemini-pinned** — approved.
- Q3 dashboard UI (evidence: header keys die on resume, `app.py:876-879`, commit 900dc44): **No UI, env-config** — approved.
- Q4 documented vendor (research open question): developer answered "i mostly use ollama cloud" → **Ollama Cloud primary example** (`https://ollama.com/v1`, docs.ollama.com/api/openai-compatibility), local Ollama/MiniMax/OpenRouter as alternates.
- Decomposition approval came with the requirement: **README guidance must be robust** (config recipes, capability matrix, degradation + troubleshooting) — folded into Slice 4 as a hard requirement.

## Design History

- Slice 1: llm_client.py — approved as generated (3 slice-verifier rounds drove: reasoning-token billing fix, blocked-before-transient precedence, half-configured-env inertness, structured-field-only blocked markers, 3-rung ladder with embedded JSON contract, follow_redirects, malformed-URL guards)
- Slice 2: Subprocess pipeline vertical — approved as generated (2 verifier rounds drove: price_estimated kept on the llm aggregate, BILLING `LLM_*` env sweep at spawn+resume, `gemini_missing_error` untouched with a process-local 400 instead, silent retry prints (D9 guardrail), LlmError/exhausted-transient propagate loudly, X-LLM-Model third header + `config_from(model=...)` amendment, bisect-with-llm + aggregate tests, dead `_SENSITIVE_LOG_RE` hunk dropped)
- Slice 3: In-process endpoints vertical — approved as generated (verifier round 1 caught 5 bugs — exception-surface drift, frame-count, orphaned concepts param, missing JSON enforcement, executor arg threading — all fixed via the `chat(json_mode=)` slice-1 amendment; round 2 re-dispatch returned no result)
- Slice 4: Observability + MCP + docs — approved as generated (no dedicated verifier round: 3 lines in alerts.py + 1 in mcp_server.py + 4 alert tests + docs; the alert-class placement (FIRST, D9-amended) and the blocked-content ordering were verified structurally during slice 1-3 verification rounds)

## References

- Parent solutions artifact: `.rpiv/artifacts/solutions/2026-08-30_08-26-00_add-openai-compatible-llm-provider.md` (option 1 selected there; this design implements it with the seam/corrections above).
- Ollama OpenAI compatibility (base URLs, response_format, data-URL images): https://docs.ollama.com/api/openai-compatibility — accessed 2026-08-30.
- MiniMax OpenAI-compatible Chat Completions: https://platform.minimax.io/docs/api-reference/text-chat-openai — accessed 2026-08-30.
- OpenRouter structured outputs: https://openrouter.ai/docs/guides/features/structured-outputs — accessed 2026-08-30.
- Git precedents: 719d444 (transcribe_backends), 900dc44 (resume env rebuild), 6ec6935 (body-key bypass closed), 77905a9/730f7de (alert-class ordering), 29fed21→8160fc6 (redaction), e5d8899 (model-env chains).
