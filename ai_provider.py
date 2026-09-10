"""
AI Provider Abstraction for OpenShorts.

This module provides a common interface for different AI providers,
allowing switching between Gemini and other LLM providers without
changing the core application logic.
"""

import os
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union
from pydantic import BaseModel

# Import Pydantic models from gemini_worker for consistency
from gemini_worker import (
    ScoredWindowModel,
    ScoreResponse,
    DetailClipModel,
    DetailResponse,
    VisualClipModel,
    VisualResponse
)



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


class AIProviderError(Exception):
    """Base exception for AI provider errors."""
    def __init__(self, message: str, provider: str = "unknown", model: str = "unknown"):
        self.message = message
        self.provider = provider
        self.model = model
        super().__init__(self.message)


class AIProvider(ABC):
    """Abstract base class for AI providers."""

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

    @abstractmethod
    def generate_content(
        self,
        prompt: str,
        schema: Optional[BaseModel] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Generate content from the AI provider.

        Args:
            prompt: The prompt to send to the AI
            schema: Optional Pydantic model for structured output validation
            messages: Optional multimodal message content (for vision models)
            **kwargs: Additional parameters for the request

        Returns:
            Dictionary containing the response data and cost analysis
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the name of this provider."""
        pass


class GeminiProvider(AIProvider):
    """Gemini-specific AI provider implementation."""

    def __init__(self, model_name: str = "gemini-2.5-flash",
                 api_key: Optional[str] = None,
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 timeout: Optional[float] = None):
        super().__init__(model_name, api_key,
                         temperature=temperature, max_tokens=max_tokens,
                         timeout=timeout)

        # Import here to avoid circular dependencies
        from google import genai
        from google.genai import types as genai_types

        self.genai = genai
        self.genai_types = genai_types

        # Configure the client with API key if provided
        try:
            if self.api_key:
                self.client = self.genai.Client(api_key=self.api_key)
            else:
                # Initialize without API key for cases where it might not be needed
                self.client = self.genai.Client()
        except Exception as e:
            # If configuration fails, we still want to allow the provider to exist
            # but it will fail when used
            self.client = None
            print(f"WARNING: Gemini client configuration failed: {e}")

    def generate_content(
        self,
        prompt: str,
        schema: Optional[BaseModel] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate content using Google Gemini API."""
        # Import here to avoid circular dependencies
        from gemini_worker import (
            _parse_json_response_text,
            _get_response_text,
            raise_if_blocked,
            _calculate_cost_analysis
        )

        # Convert the canonical OpenAI-style multimodal message format into
        # Gemini contents. Callers always send: [{"role":"user",
        # "content":[{"type":"text",...},{"type":"image_url",...}]}].
        # Also accept the older flat list for backward compatibility.
        if messages is not None:
            contents = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                parts = msg.get("content")
                if isinstance(parts, list):
                    for part_msg in parts:
                        if not isinstance(part_msg, dict):
                            continue
                        kind = part_msg.get("type")
                        if kind == "text":
                            text = part_msg.get("text") or ""
                            if text:
                                contents.append(text)
                        elif kind == "image_url":
                            image_url = part_msg.get("image_url") or {}
                            data_url = image_url.get("url") if isinstance(image_url, dict) else image_url
                            if not data_url:
                                continue
                            if isinstance(data_url, str) and data_url.startswith("data:"):
                                import base64
                                header, encoded = data_url.split(",", 1)
                                mime_type = header[5:].split(";", 1)[0] or "image/jpeg"
                                try:
                                    raw = base64.b64decode(encoded)
                                    contents.append(self.genai_types.Part.from_bytes(data=raw, mime_type=mime_type))
                                except Exception as exc:
                                    raise AIProviderError(
                                        f"Invalid Gemini image data: {exc}",
                                        provider="gemini",
                                        model=self.model_name,
                                    ) from exc
                            else:
                                # Gemini can ingest a remote URI only through a
                                # file/URI part supported by the SDK. Keep the
                                # failure explicit rather than silently dropping it.
                                raise AIProviderError(
                                    "GeminiProvider only accepts data: image URLs for multimodal messages",
                                    provider="gemini",
                                    model=self.model_name,
                                )
                else:
                    # Legacy flat message: {type,text/image_url}.
                    kind = msg.get("type")
                    if kind == "text":
                        text = msg.get("text") or ""
                        if text:
                            contents.append(text)
                    elif kind == "image_url":
                        image_url = msg.get("image_url") or {}
                        data_url = image_url.get("url") if isinstance(image_url, dict) else image_url
                        if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:"):
                            continue
                        import base64
                        header, encoded = data_url.split(",", 1)
                        mime_type = header[5:].split(";", 1)[0] or "image/jpeg"
                        raw = base64.b64decode(encoded)
                        contents.append(self.genai_types.Part.from_bytes(data=raw, mime_type=mime_type))

            # The canonical callers already include the full prompt as their
            # first text part. Avoid duplicating it; prepend only when missing.
            if not any(isinstance(item, str) and item.strip() == prompt.strip() for item in contents):
                contents.insert(0, prompt)
            if not contents:
                contents = [prompt]
        else:
            contents = [prompt]

        # For Gemini, we need to handle the structured output differently
        # since it's built into the API via response_schema parameter

        # Create a config that matches the original behavior
        config = self.genai_types.GenerateContentConfig(
            response_mime_type="application/json",
        )

        if schema is not None:
            # For structured responses, use the schema if available
            config.response_schema = schema

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=config
                )

                # Check for blocked content (deterministic policy block)
                raise_if_blocked(response)

                # Try to parse using the structured approach first
                parsed_obj = getattr(response, "parsed", None)
                if parsed_obj is not None:
                    parsed = parsed_obj.model_dump() if hasattr(parsed_obj, "model_dump") else parsed_obj
                else:
                    # Fallback to text parsing
                    parsed = _parse_json_response_text(_get_response_text(response))

                cost_analysis = _calculate_cost_analysis(response, self.model_name)

                return {
                    "response": parsed,
                    "cost_analysis": cost_analysis
                }
            except Exception as e:
                msg = str(e)
                # Check if this is a transient error that should be retried
                transient = any(tok in msg for tok in (
                    '503', 'UNAVAILABLE', '429', 'RESOURCE_EXHAUSTED',
                    '500', 'INTERNAL', 'overloaded', 'Deadline',
                    'empty response body', 'did not contain a JSON object',
                    'Failed to parse Gemini JSON response'
                ))

                if attempt == max_attempts or not transient:
                    # Re-raise the original exception with provider context
                    raise AIProviderError(
                        f"AI request failed after {max_attempts} attempts: {msg}",
                        provider="gemini",
                        model=self.model_name
                    ) from e

                wait = 5 * (2 ** (attempt - 1))
                print(f"âš ï¸ Gemini transient error (attempt {attempt}/{max_attempts}), retrying in {wait}s: {msg[:150]}")
                time.sleep(wait)

        # This should never be reached, but just in case
        raise AIProviderError(
            "AI request failed after all retries",
            provider="gemini",
            model=self.model_name
        )

    def get_provider_name(self) -> str:
        """Return the name of this provider."""
        return "gemini"


def _schema_required_array(schema):
    """The schema's single required array field name (e.g. "shorts"), or None."""
    import json as _json
    try:
        js = schema.model_json_schema()
        req = js.get("required") or []
        props = js.get("properties") or {}
        arrays = [k for k in req
                  if isinstance(props.get(k), dict) and props[k].get("type") == "array"]
        return arrays[0] if len(arrays) == 1 else None
    except Exception:
        return None


def _json_candidates(content):
    """Loose-JSON candidates: every fenced block first, then the outermost
    {...} and [...]. Models that ignore response_format answer in prose with
    embedded fragments — these are the places a payload can hide."""
    cands = []
    pos = 0
    while True:
        i = content.find("```", pos)
        if i == -1:
            break
        j = content.find("```", i + 3)
        if j == -1:
            break
        block = content[i + 3:j]
        block = block.split("\n", 1)[-1] if block.lstrip().lower().startswith("json") else block
        cands.append(block.strip())
        pos = j + 3
    for opener, closer in (("{", "}"), ("[", "]")):
        s, e2 = content.find(opener), content.rfind(closer)
        if s != -1 and e2 > s:
            cands.append(content[s:e2 + 1])
    return cands


def _salvage_schema_json(content, schema):
    """Best-effort schema validation over loose JSON. A bare list of items is
    wrapped under the schema's single required array field. Returns the
    model_dump()ed payload, or None when nothing in the content fits."""
    field = _schema_required_array(schema)
    for cand in _json_candidates(content):
        try:
            data = json.loads(cand)
        except Exception:
            continue
        if isinstance(data, list) and field:
            data = {field: data}
        if not isinstance(data, dict):
            continue
        try:
            validated = schema(**data)
            return validated.model_dump() if hasattr(validated, "model_dump") else validated
        except Exception:
            continue
    return None


class OpenAICompatibleProvider(AIProvider):
    """OpenAI-compatible AI provider implementation."""

    def __init__(self, model_name: str = "gpt-4", api_key: Optional[str] = None,
                 base_url: str = "https://api.openai.com/v1",
                 temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 timeout: Optional[float] = None):
        super().__init__(model_name, api_key,
                         temperature=temperature, max_tokens=max_tokens,
                         timeout=timeout)
        self.base_url = base_url

        # Import here to avoid circular dependencies
        import openai
        self.openai = openai

        # Configure the client with base URL and API key (API key is optional)
        # For OpenAI-compatible providers, API key is optional (LM Studio doesn't require it)
        # However, the OpenAI SDK requires a non-empty credential for initialization
        # so we use an internal placeholder when no real API key is provided
        if self.api_key:
            api_key_to_use = self.api_key
        else:
            # Use internal placeholder to satisfy OpenAI SDK requirements
            # but never expose this as user credential
            api_key_to_use = "lm-studio-placeholder-key-please-ignore"

        self.client = self.openai.OpenAI(
            api_key=api_key_to_use,
            base_url=self.base_url
        )

    def generate_content(
        self,
        prompt: str,
        schema: Optional[BaseModel] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Generate content using OpenAI-compatible API."""
        import json

        # Prepare the messages for chat completion
        if messages is not None:
            # Use provided multimodal messages
            final_messages = messages
        else:
            # Fallback to original behavior
            final_messages = [
                {"role": "user", "content": prompt}
            ]

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

        # Add structured output format if schema is provided
        # LM Studio and some local servers don't support json_schema — we'll retry without it on 400
        use_schema = schema is not None
        if use_schema:
            # Generate OpenAI-compatible JSON Schema format
            json_schema = schema.model_json_schema()
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__ if hasattr(schema, "__name__") else "response_schema",
                    "strict": True,
                    "schema": json_schema
                }
            }

        # Timeout appears in the request only when a value exists (today's
        # wire shape). The stored constructor value applies when no per-call
        # kwarg is given.
        timeout = kwargs.get("timeout")
        if timeout is None:
            timeout = self.timeout
        if timeout is not None:
            params["timeout"] = timeout

        max_attempts = 3
        last_finish = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.client.chat.completions.create(**params)
                last_finish = getattr(response.choices[0], "finish_reason", None)

                # Extract the content from the response
                content = response.choices[0].message.content

                if not content:
                    raise AIProviderError(
                        "Empty response from OpenAI API",
                        provider="openai",
                        model=self.model_name
                    )

                # Try to parse as JSON, tolerating prose-wrapped payloads
                # (fenced blocks, outermost object/array) from servers that
                # skip response_format enforcement.
                parsed = None
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    for cand in _json_candidates(content):
                        try:
                            parsed = json.loads(cand)
                            break
                        except Exception:
                            continue
                if parsed is None:
                    raise AIProviderError(
                        f"Failed to parse JSON response from OpenAI: {content[:200]}...",
                        provider="openai",
                        model=self.model_name
                    )

                # If schema is provided, validate the parsed data against it
                if schema:
                    try:
                        validated = schema(**parsed)
                        parsed = validated.model_dump() if hasattr(validated, "model_dump") else validated
                    except Exception as e:
                        salvaged = _salvage_schema_json(content, schema)
                        if salvaged is None:
                            raise AIProviderError(
                                f"Failed to validate JSON response against schema: {str(e)}",
                                provider="openai",
                                model=self.model_name
                            )
                        parsed = salvaged

                # For now, we don't have cost analysis in this implementation
                # but we can return empty dict for compatibility
                return {
                    "response": parsed,
                    "cost_analysis": None
                }
            except Exception as e:
                msg = str(e)
                # Reasoning models can burn the whole completion budget on
                # thinking and answer with an empty or truncated content
                # (finish_reason="length"). Escalate the budget instead of
                # retrying into the same wall.
                if last_finish == "length":
                    # 8192 ceiling: some servers answer 200-with-empty-content
                    # when max_tokens exceeds the model's output cap, and an
                    # over-limit request must not win the retry.
                    _bumped = min(int(params.get("max_tokens") or 4096) * 4, 8192)
                    if _bumped > int(params.get("max_tokens") or 0):
                        print(f"[ai_provider] max_tokens={params.get('max_tokens')} exhausted "
                              f"(finish_reason=length) — retrying with {_bumped}")
                        params["max_tokens"] = _bumped
                # LM Studio local compat: json_schema/json_object not supported → retry once without response_format
                if use_schema and 'response_format' in msg.lower() and ('400' in msg or 'unsupported' in msg.lower() or 'json_schema' in msg.lower() or 'json_object' in msg.lower()):
                    print(f"[ai_provider] response_format not supported ({msg[:120]}), retrying without it")
                    params.pop("response_format", None)
                    use_schema = False
                    try:
                        response = self.client.chat.completions.create(**params)
                        content2 = response.choices[0].message.content
                        if not content2:
                            raise AIProviderError("Empty response from OpenAI API", provider="openai", model=self.model_name)
                        import json as _json2
                        txt = content2.strip()
                        if txt.startswith("```"):
                            txt = txt.split("\n", 1)[-1] if "\n" in txt else txt[3:]
                            if txt.endswith("```"):
                                txt = txt[:-3]
                            txt = txt.strip()
                        s, e2 = txt.find("{"), txt.rfind("}")
                        if s != -1 and e2 != -1:
                            txt = txt[s:e2+1]
                        parsed = _json2.loads(txt)
                        if schema:
                            try:
                                validated = schema(**parsed)
                                parsed = validated.model_dump() if hasattr(validated, "model_dump") else validated
                            except Exception as ve:
                                raise AIProviderError(f"Failed to validate JSON response against schema: {ve}", provider="openai", model=self.model_name)
                        return {"response": parsed, "cost_analysis": None}
                    except Exception as re2:
                        raise AIProviderError(f"AI request failed after retry without response_format: {re2}", provider="openai", model=self.model_name) from re2
                # Check if this is a transient error that should be retried
                # Don't retry 400 errors caused by invalid response_format
                is_bad_request = False

                # Check for specific OpenAI BadRequestError with invalid response_format
                from openai import BadRequestError
                if isinstance(e, BadRequestError):
                    error_msg = str(e)
                    # Check if this is specifically about response_format being unsupported
                    if 'response_format' in error_msg.lower() and ('json_object' in error_msg.lower() or 'unsupported' in error_msg.lower()):
                        is_bad_request = True

                # Also check for general transient errors
                transient = any(tok in msg for tok in (
                    '503', 'UNAVAILABLE', '429', 'RESOURCE_EXHAUSTED',
                    '500', 'INTERNAL', 'overloaded', 'Deadline',
                    'timeout', 'connection', 'retry'
                ))

                if attempt == max_attempts or not transient or is_bad_request:
                    # Re-raise the original exception with provider context
                    raise AIProviderError(
                        f"AI request failed after {max_attempts} attempts: {msg}",
                        provider="openai",
                        model=self.model_name
                    ) from e

                wait = 5 * (2 ** (attempt - 1))
                print(f"âš ï¸ OpenAI transient error (attempt {attempt}/{max_attempts}), retrying in {wait}s: {msg[:150]}")
                time.sleep(wait)

        # This should never be reached, but just in case
        raise AIProviderError(
            "AI request failed after all retries",
            provider="openai",
            model=self.model_name
        )

    def get_provider_name(self) -> str:
        """Return the name of this provider."""
        return "openai"


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


def create_ai_provider(provider_type: str, model_name: str = None, api_key: str = None, **kwargs) -> AIProvider:
    """
    Factory function to create an AI provider instance.

    Args:
        provider_type: Type of provider ("gemini" or "openai")
        model_name: Name of the model to use
        api_key: API key for the provider
        **kwargs: Additional parameters for the provider

    Returns:
        AIProvider instance

    Raises:
        ValueError: If provider type is not supported
    """
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
    else:
        raise ValueError(f"Unsupported AI provider type: {provider_type}")
