"""
Main completion handler using LiteLLM
"""
import litellm
import json
import logging
import time
import random
from typing import Dict, Optional, Any
from .prompt import Prompt
import re
import os

logger = logging.getLogger(__name__)

try:
    from backend.services.infra import net_sentry  # runtime RPS dialer
except Exception:
    net_sentry = None

try:
    from backend.infra.ratelimit import acquire_slot
except Exception:
    acquire_slot = None

# simple per-key throttle so we don't spam the same message continuously
_last_notice_ts = {}

def _coerce_json(content: str) -> Optional[dict]:
    """Try hard to parse JSON from a model string:
       - strip ``` fences
       - extract the largest {...} or [...] block
       - remove trailing commas
    """
    if not isinstance(content, str):
        return None
    s = content.strip()
    # strip code fences
    if s.startswith("```"):
        # remove first fence
        s = s.split("\n", 1)[-1]
        # remove closing fence if present
        if "```" in s:
            s = s.rsplit("```", 1)[0]
        s = s.strip()
    # find outermost JSON-ish region
    import re as _re, json as _json
    m = _re.search(r'(\{.*\}|\[.*\])', s, flags=_re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    # kill trailing commas before } or ]
    block = _re.sub(r',\s*([}\]])', r'\1', block)
    # sanitize invalid backslash escapes (e.g., \$ -> $)
    block = _re.sub(r'\\(?!["\\/bfnrtu])', '', block)
    try:
        return _json.loads(block)
    except Exception:
        return None

def _sanitize_jsonish(s: str) -> str:
    """Remove invalid escape sequences and code fences without altering valid ones."""
    if not isinstance(s, str):
        return s
    t = s.strip()
    if t.startswith("```"):
        try:
            t = t.split("\n", 1)[-1]
            if "```" in t:
                t = t.rsplit("```", 1)[0]
        except Exception:
            pass
    # Remove invalid backslash escapes (\ not followed by a valid JSON escape)
    t = re.sub(r'\\(?!["\\/bfnrtu])', '', t)
    return t

def _throttled(key: str, throttle_secs: Optional[float] = None) -> bool:
    if throttle_secs is None:
        try:
            throttle_secs = float(os.getenv("CHATSTATS_LOG_THROTTLE_SECS", "3.0"))
        except Exception:
            throttle_secs = 3.0
    now = time.monotonic()
    last = _last_notice_ts.get(key, 0.0)
    if now - last < throttle_secs:
        return True
    _last_notice_ts[key] = now
    return False


def _provider_key(model: str) -> str:
    try:
        return f"llm:{model.split('/', 1)[0].lower()}"
    except Exception:
        return "llm:provider"


def _cap_hold(seconds: float) -> float:
    try:
        cap = float(os.getenv("CHATSTATS_PROVIDER_HOLD_CAP_S", "0.8"))
    except Exception:
        cap = 0.8
    try:
        val = float(seconds or 0.0)
    except Exception:
        val = 0.0
    return max(0.0, min(val, cap))

# HTTP/Provider transient error codes we consider retryable
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _get_current_rps_limit() -> int:
    """Read current RPS limit from Redis (set by net_sentry)."""
    try:
        from backend.infra.redis import get_redis
        r = get_redis()
        limit = int(r.get("llm:global_rps") or 450)
        return limit
    except Exception:
        return 450  # Default fallback


def _jitter_sleep_backoff(attempt_index: int, retry_after: Optional[float] = None) -> None:
    """Sleep with jitter using a capped exponential backoff.

    First few delays are sub-second to smooth bursts without escalating to Celery.
    """
    if retry_after is not None:
        try:
            time.sleep(min(float(retry_after), 2.0))
            return
        except Exception:
            pass
    delay = min(0.5, (0.05 * (2 ** attempt_index))) + random.random() * 0.05
    time.sleep(delay)


def _extract_retry_after_seconds(exc) -> Optional[float]:
    """
    Extract a provider-suggested delay from the exception. Supports:
    - HTTP Retry-After header
    - Google RPC RetryInfo: {"retryDelay": "12s"} inside the error payload/message
    """
    # 1) Standard header
    try:
        headers = getattr(exc, "headers", {}) or {}
        h = headers.get("retry-after") or headers.get("Retry-After")
        if h:
            return float(h)
    except Exception:
        pass
    # 2) Google-style payload embedded in the message string
    try:
        s = str(exc)
        m = re.search(r'"retryDelay"\s*:\s*"(\d+)s"', s)
        if m:
            return float(m.group(1))
        # Fallback: raw integer seconds
        m2 = re.search(r'"retryDelay"\s*:\s*"?(\d+)"?', s)
        if m2:
            return float(m2.group(1))
    except Exception:
        pass
    return None

def get_completion(
    compiled_prompt: Prompt,
    max_tokens: int = 65000,
    timeout: int = 1500,
    use_openrouter: bool = False  # Kept for compatibility
) -> Dict:
    """
    Gets a completion using LiteLLM's unified interface.
    
    Args:
        compiled_prompt: The compiled prompt object
        max_tokens: Maximum tokens for the response
        timeout: Timeout in seconds (passed to litellm)
        use_openrouter: Ignored - kept for backward compatibility
    
    Returns:
        A unified response with content, model, usage (including total_cost)
    """
    model = str(compiled_prompt.model).strip() if compiled_prompt.model else None
    _diag = False
    
    # Safety check - if model is None or "None", fail early with clear message
    if not model or model == "None":
        raise ValueError(f"Model not provided or invalid. Prompt object has model={compiled_prompt.model}")
    
    # Acquire rate limit slot before calling LLM
    if acquire_slot is not None:
        try:
            rps_limit = _get_current_rps_limit()
            max_wait_ms = int(os.getenv("CHATSTATS_LLM_MAX_WAIT_MS", "60000"))  # 60s default
            
            if not acquire_slot("llm:global", rps_limit, max_wait_ms):
                logger.warning("[LLM-GATE] Rate limited (current limit: %d RPS)", rps_limit)
                # Wait and retry once
                time.sleep(1.0)
                if not acquire_slot("llm:global", rps_limit, max_wait_ms):
                    raise Exception(f"Rate limited at {rps_limit} RPS, max wait exceeded")
        except Exception as e:
            # Log but continue anyway (fail open to avoid breaking existing behavior)
            logger.debug("[LLM-GATE] Rate limit check error: %s (continuing anyway)", e)
    
    # Build kwargs for litellm.completion
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": compiled_prompt.prompt_text}],
        "max_tokens": max_tokens,
        "timeout": timeout,
        "metadata": {
            "prompt_name": compiled_prompt.prompt_name,
            "prompt_version": compiled_prompt.prompt_version,
        }
    }
    
    # Add temperature if not a reasoning model
    low_model = model.lower()
    is_reasoning = ("o1" in low_model or "o3" in low_model or "gpt-5" in low_model)
    if not is_reasoning and compiled_prompt.temperature is not None:
        kwargs["temperature"] = compiled_prompt.temperature

    # Adjust token parameter for newer OpenAI models (gpt-5, o3) which expect max_completion_tokens
    try:
        if ("gpt-5" in low_model) or ("/o3" in low_model) or (low_model.startswith("o3")):
            # Move max_tokens → max_completion_tokens
            mt = kwargs.pop("max_tokens", None)
            if mt is not None:
                kwargs["max_completion_tokens"] = mt
    except Exception:
        pass
    
    # Handle response format
    if compiled_prompt.response_format:
        is_anthropic = ("anthropic" in model.lower() or "claude" in model.lower())
        is_gemini = ("gemini" in model.lower())
        # For Anthropic models, use tool format
        if is_anthropic:
            kwargs["tools"] = [{
                "type": "function",
                "function": {
                    "name": "respond_with_json",
                    "description": "Respond with structured JSON",
                    "parameters": compiled_prompt.response_format
                }
            }]
            kwargs["tool_choice"] = {"type": "function", "function": {"name": "respond_with_json"}}
        elif is_gemini:
            # Gemini JSON mode: use response_mime_type only (response_schema causes 400s on Vertex)
            kwargs["response_mime_type"] = "application/json"
            # Add a system rail to enforce strict JSON without inflating the user prompt
            try:
                sys_msg = "You MUST return only a valid JSON object that matches the requested schema. No markdown, no prose."
                # Insert at the beginning as a system role message
                kwargs.setdefault("messages", [])
                kwargs["messages"] = [{"role": "system", "content": sys_msg}] + kwargs["messages"]
            except Exception:
                pass
        else:
            # For other models, use response_format
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": compiled_prompt.response_format
                }
            }
    if _diag:
        try:
            logger.debug(f"[LLM-LITE] Built kwargs for model={model}: keys={list(kwargs.keys())}")
        except Exception:
            pass
    
    last_exc: Optional[BaseException] = None
    switched_to_fallback = False
    fallback_model_name = "xai/grok-4"
    for attempt in range(2):
        try:
            response = litellm.completion(**kwargs)

            # Extract content based on response type
            content = ""
            if compiled_prompt.response_format and ("anthropic" in model.lower() or "claude" in model.lower()):
                # For Anthropic with tools, extract from tool call
                if hasattr(response.choices[0].message, 'tool_calls') and response.choices[0].message.tool_calls:
                    tool_call = response.choices[0].message.tool_calls[0]
                    try:
                        content = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        # If it's already a dict, use it directly
                        content = tool_call.function.arguments
                else:
                    content = response.choices[0].message.content
            else:
                content = response.choices[0].message.content
                # If we requested structured JSON, try hard to coerce it
                if compiled_prompt.response_format:
                    parsed = None
                    if isinstance(content, (dict, list)):
                        parsed = content
                    elif isinstance(content, str):
                        try:
                            parsed = json.loads(content)
                        except Exception:
                            # attempt sanitization then parse again
                            try:
                                sanitized = _sanitize_jsonish(content)
                                parsed = json.loads(sanitized)
                            except Exception:
                                parsed = _coerce_json(sanitized if 'sanitized' in locals() else content)
                    if parsed is not None:
                        content = parsed
                    else:
                        # Log parse failure at DEBUG level (workflow will log at WARNING if recovery fails)
                        key = f"json-parse:{model}"
                        if not _throttled(key):
                            try:
                                _preview = content if isinstance(content, str) else json.dumps(content)
                                _preview = _preview[:500] + "..." if len(_preview) > 500 else _preview
                            except Exception:
                                _preview = str(content)[:500]
                            logger.debug(f"Failed to parse JSON response from {model}. Preview: {_preview}")
                        # Attach full raw content for downstream repair; truncate only for log preview
                        try:
                            raw_full = (content or "")
                            if isinstance(raw_full, (dict, list)):
                                raw_full = json.dumps(raw_full)
                            raw_full = str(raw_full)
                            raw_preview = raw_full[:1500]
                            # Attempt minimal sanitization on FULL text to aid trivial fixes
                            sanitized = raw_full.replace('\\$', '$')
                        except Exception:
                            # Fall back to a safe string representation, never empty if content exists
                            try:
                                sanitized = str(content) if content is not None else ""
                            except Exception:
                                sanitized = ""
                        return {
                            "error": "Failed to parse LLM JSON response",
                            "model": model,
                            "usage": {
                                "input_tokens": 0,
                                "output_tokens": 0,
                                "total_tokens": 0,
                                "total_cost": 0.0
                            },
                            "content": {},
                            "raw": sanitized
                        }

            # Build unified response format
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "total_cost": litellm.completion_cost(
                    model=model,
                    completion_response=response
                )
            }

            if _diag:
                try:
                    _ctype = type(content).__name__
                    _preview = content if isinstance(content, str) else json.dumps(content)
                    logger.debug(f"[LLM-LITE] Returning content_type={_ctype} content_preview={_preview[:600]}")
                except Exception:
                    pass
            
            # Tell sentry about success for recovery tracking
            try:
                if net_sentry:
                    net_sentry.note_result(
                        first_attempt=(attempt == 0),
                        ok=True,
                        status_code=200,
                        is_conn_error=False
                    )
            except Exception:
                pass
            
            return {
                "content": content,
                "model": model,
                "usage": usage
            }
        except Exception as e:  # noqa: BLE001 – litellm raises varied exceptions
            last_exc = e
            status_code = getattr(e, "status_code", None)
            headers = getattr(e, "headers", {}) or {}
            
            # Tell the runtime sentry about this attempt
            try:
                is_conn_err = False
                try:
                    from litellm.exceptions import APIConnectionError as _LLAPIConnectionError
                    is_conn_err = isinstance(e, _LLAPIConnectionError)
                except Exception:
                    pass
                if net_sentry:
                    net_sentry.note_result(
                        first_attempt=(attempt == 0),
                        ok=False,
                        status_code=status_code,
                        is_conn_error=is_conn_err
                    )
            except Exception:
                pass
            
            # Emit detailed error context once per throttle window
            # Skip logging for APIConnectionErrors (expected with bad internet, handled by retry)
            try:
                from litellm.exceptions import APIConnectionError as _LLAPIConnectionError
            except Exception:
                _LLAPIConnectionError = tuple()  # type: ignore
            
            is_api_conn_error = isinstance(e, _LLAPIConnectionError)
            
            if not is_api_conn_error:
                try:
                    key = f"llm-exc:{model}:{status_code or 'none'}"
                    if not _throttled(key):
                        err_type = type(e).__name__
                        # Avoid dumping potentially sensitive header values – show keys only
                        header_keys = []
                        try:
                            if isinstance(headers, dict):
                                header_keys = list(headers.keys())[:15]
                        except Exception:
                            header_keys = []
                        msg_preview = str(e)
                        try:
                            msg_preview = (msg_preview or "")[:1500]
                        except Exception:
                            pass
                        logger.error(
                            f"LiteLLM error for {model} (status={status_code}) {err_type}: {msg_preview} | header_keys={header_keys}",
                            exc_info=True  # Show full traceback
                        )
                except Exception:
                    # Never fail logging paths - if throttled logging failed, still log basic error
                    logger.error(f"LiteLLM error (fallback): {type(e).__name__}: {str(e)[:500]}", exc_info=True)
            
            retry_after_hdr = None
            if isinstance(headers, dict):
                retry_after_hdr = headers.get("retry-after") or headers.get("Retry-After")
            retry_after_val: Optional[float] = None
            try:
                if retry_after_hdr is not None:
                    retry_after_val = float(retry_after_hdr)
            except Exception:
                retry_after_val = None

            # Provider-specific hint (Google RPC RetryInfo or similar). If present, set a global hold
            hint = _extract_retry_after_seconds(e)
            if hint is not None:
                retry_after_val = hint
                try:
                    from backend.infra.ratelimit import set_hold as _set_hold  # type: ignore
                    _set_hold(_provider_key(model), _cap_hold(hint), reason="provider_429")
                except Exception:
                    # Best-effort only
                    pass

            # Provider-specific hint (Google RPC RetryInfo or similar). If present, set a global hold
            # (handled above)

            # Network-level failures: set a brief provider hold to avoid herd
            try:
                from litellm.exceptions import APIConnectionError as _LLAPIConnectionError
            except Exception:
                _LLAPIConnectionError = tuple()  # type: ignore
            try:
                from backend.infra.ratelimit import set_hold as _set_hold  # type: ignore
            except Exception:
                _set_hold = None  # type: ignore
            try:
                if isinstance(e, _LLAPIConnectionError) and _set_hold:
                    _set_hold(_provider_key(model), _cap_hold(5.0), reason="network_error")
            except Exception:
                pass

            # Special-case fallback: Gemini service unavailable → switch to xAI grok-4-fast-non-reasoning once
            try:
                msg_low = str(e).lower()
            except Exception:
                msg_low = ""
            if (
                ("gemini" in low_model)
                and not switched_to_fallback
                and (status_code == 503 or "unavailable" in msg_low)
            ):
                try:
                    logger.warning(
                        f"[LLM-FALLBACK] {model} unavailable (status={status_code}); switching to {fallback_model_name}"
                    )
                except Exception:
                    pass
                # Swap model and clean Gemini-specific kwargs
                model = fallback_model_name
                low_model = model.lower()
                kwargs["model"] = model
                # Remove Gemini-only parameter if present
                try:
                    if "response_mime_type" in kwargs:
                        kwargs.pop("response_mime_type", None)
                except Exception:
                    pass
                switched_to_fallback = True
                # Immediately retry with fallback on the next loop iteration
                _jitter_sleep_backoff(attempt, retry_after_val)
                continue

            # Retry fast for transient errors; otherwise, propagate
            if status_code in RETRYABLE_STATUS or status_code is None:
                _jitter_sleep_backoff(attempt, retry_after_val)
                continue
            # For non-retryable errors, keep noise low – rate-limit cases should already be WARNING via callbacks
            logger.error(f"LiteLLM non-retryable error (status={status_code}) for {model}: {e}")
            raise

    # Exhausted local retries – this will bubble to task-level retry. Keep it at WARNING if it's just rate-limits.
    try:
        low = str(last_exc).lower() if last_exc else ""
        if any(tok in low for tok in ("429", "rate limit", "too many requests", "resource_exhausted")):
            key = f"llm-exhausted:{model}"
            if not _throttled(key):
                logger.warning(f"LiteLLM call exhausted local retries for model {model}: {last_exc}")
        else:
            logger.error(f"LiteLLM call exhausted local retries for model {model}: {last_exc}")
    except Exception:
        logger.error(f"LiteLLM call exhausted local retries for model {model}: {last_exc}")
    # Escalate; upstream task base will apply Celery backoff
    raise last_exc if last_exc else RuntimeError("LLM request failed without exception detail")

def parse_completion_response(response: Dict) -> Any:
    """
    Parse the completion response - kept for compatibility
    """
    if isinstance(response, dict):
        if 'content' not in response:
            return response
        return response.get("content", "")
    return response 