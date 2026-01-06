"""
LiteLLM configuration module
"""
import litellm
import os
import logging
import httpx
import atexit
import time

logger = logging.getLogger(__name__)

# simple per-key throttle so we don't spam same warning continuously
_last_notice_ts = {}

def _throttled(key: str, throttle_secs: float = None) -> bool:
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


def _build_shared_httpx_clients():
    """Create shared httpx Client and AsyncClient with large keep-alive pools.
    Tunable via env; safe defaults chosen for high RPS.
    """
    max_conns = int(os.getenv("LITELLM_POOL_MAX_CONNECTIONS", "1000"))
    max_keepalive = int(os.getenv("LITELLM_POOL_MAX_KEEPALIVE", "500"))
    keepalive_secs = float(os.getenv("LITELLM_POOL_KEEPALIVE_SECS", "30"))
    force_h1 = os.getenv("FORCE_HTTP1", "0").lower() in ("1", "true", "yes")
    http2_enabled = False if force_h1 else (os.getenv("LITELLM_HTTP2", "1").lower() not in ("0", "false", "no"))

    connect_to = float(os.getenv("LITELLM_CONNECT_TIMEOUT", "3.0"))
    read_to = float(os.getenv("LITELLM_READ_TIMEOUT", "60.0"))
    write_to = float(os.getenv("LITELLM_WRITE_TIMEOUT", "60.0"))
    pool_to = float(os.getenv("LITELLM_POOL_TIMEOUT", "5.0"))

    limits = httpx.Limits(
        max_connections=max_conns,
        max_keepalive_connections=max_keepalive,
        keepalive_expiry=keepalive_secs,
    )
    timeout = httpx.Timeout(connect=connect_to, read=read_to, write=write_to, pool=pool_to)

    common_headers = {"Connection": "keep-alive", "Accept-Encoding": "gzip"}

    client = httpx.Client(http2=http2_enabled, limits=limits, timeout=timeout, headers=common_headers)
    aclient = httpx.AsyncClient(http2=http2_enabled, limits=limits, timeout=timeout, headers=common_headers)

    atexit.register(lambda: client.close())
    atexit.register(lambda: (aclient.aclose()))
    return client, aclient


def configure_litellm():
    """Configure LiteLLM with API keys and settings"""
    # Set API keys from environment or existing constants
    litellm.openai_key = os.getenv("OPENAI_API_KEY", "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")
    litellm.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "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")
    litellm.xai_key = os.getenv("XAI_API_KEY", "xai-bGDJ6Vcouj300wzHK1vE867jjbd4htx0dgiMOk7H9dVwVFVYy6QsL9Y7f9l6lQ4bn432tcBIm3E6VgzF")
    os.environ["XAI_API_KEY"] = "xai-bGDJ6Vcouj300wzHK1vE867jjbd4htx0dgiMOk7H9dVwVFVYy6QsL9Y7f9l6lQ4bn432tcBIm3E6VgzF"
    # LiteLLM expects GEMINI_API_KEY environment variable
    gemini_key = os.getenv("GEMINI_API_KEY", "AIzaSyAghRtaqr6kSMwXzlJmv3vAgGqlMvFlQ6s")
    os.environ["GEMINI_API_KEY"] = gemini_key
    

    # Wire shared HTTPX clients
    try:
        client, aclient = _build_shared_httpx_clients()
        litellm.client_session = client
        litellm.aclient_session = aclient
        force_h1 = os.getenv("FORCE_HTTP1", "0").lower() in ("1", "true", "yes")
        logger.info("LiteLLM HTTPX pools ready (http2=%s)", "0" if force_h1 else os.getenv("LITELLM_HTTP2", "1"))
    except Exception as e:
        logger.warning("Failed to build shared httpx clients; falling back to defaults: %s", e)

    # Set default behavior
    litellm.drop_params = True  # Drop unsupported params instead of failing
    litellm.set_verbose = False  # Reduce logging in production
    # Default global RPS for our limiter nudged slightly; overridable via env
    os.environ.setdefault("CHATSTATS_LLM_GLOBAL_RPS", "500")
    # Extra safety: turn down any internal print-y noise if supported/env-read by litellm
    os.environ.setdefault("LITELLM_LOG", "0")
    os.environ.setdefault("LITELLM_DEBUG", "0")
    os.environ.setdefault("LITELLM_LOGGING", "ERROR")
    
    # Suppress LiteLLM footers without touching celery.* loggers
    import logging
    from backend.config.logging import LiteLLMSpamFilter  # module-scope, importable by workers
    for logger_name in ('litellm', 'LiteLLM'):
        logging.getLogger(logger_name).addFilter(LiteLLMSpamFilter())

    # Optional: enable verbose LiteLLM debugging via env (captured by Celery redirected stdout)
    if os.getenv("CHATSTATS_LITELLM_DEBUG", "0").lower() in ("1", "true", "yes", "on"):  # pragma: no cover
        try:
            litellm.set_verbose = True
        except Exception:
            pass
        # Ensure LiteLLM respects debug envs even if defaults above were set
        os.environ["LITELLM_LOG"] = "1"
        os.environ["LITELLM_DEBUG"] = "1"
        os.environ["LITELLM_LOGGING"] = "DEBUG"
        try:
            if hasattr(litellm, "_turn_on_debug"):
                litellm._turn_on_debug()  # type: ignore[attr-defined]
        except Exception:
            # Best effort – different versions expose different toggles
            pass
        logger.warning("LiteLLM debug enabled via CHATSTATS_LITELLM_DEBUG=1")

    # Set callbacks
    litellm.success_callback = [log_success]
    litellm.failure_callback = [log_failure]

    logger.info("LiteLLM configured successfully")


def log_success(kwargs, completion_response, start_time, end_time):
    """Log successful completions for monitoring"""
    model = kwargs.get('model', 'unknown')
    duration = end_time - start_time
    logger.debug(f"LLM Success: {model} - {duration:.2f}s")


def log_failure(kwargs, completion_response, start_time, end_time):
    """Log failed completions with de-noising: 429s become warnings and are throttled."""
    model = kwargs.get('model', 'unknown')
    error = completion_response
    # If LiteLLM didn't pass an error payload, avoid spamming
    if error is None:
        key = f"llm-failure-none:{model}"
        if not _throttled(key):
            logger.warning(f"LLM failure (no detail): {model}")
        return

    # Detect rate-limit style errors and downgrade to WARNING (throttled)
    try:
        text = str(error).lower()
        if any(tok in text for tok in ("429", "rate limit", "too many requests", "resource_exhausted")):
            key = f"llm-ratelimited:{model}"
            if not _throttled(key):
                logger.warning(f"LLM RateLimited: {model}")
            return
    except Exception:
        # Fall through to error log
        pass

    # Non-rate-limit failures: log at error level but still throttle
    key = f"llm-failure:{model}"
    if not _throttled(key):
        logger.error(f"LLM Failure: {model} - {error}")


# Auto-configure when module is imported
configure_litellm() 