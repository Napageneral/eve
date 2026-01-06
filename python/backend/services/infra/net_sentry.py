# app/backend/services/infra/net_sentry.py
from __future__ import annotations
import os, logging, time
from typing import Optional

logger = logging.getLogger(__name__)

_redis = None
def _r():
    global _redis
    if _redis is not None:
        return _redis
    try:
        import redis  # type: ignore
        url = os.getenv("CHATSTATS_LIMITER_REDIS_URL") or os.getenv("CHATSTATS_METRICS_REDIS_URL") or os.getenv("REDIS_URL")
        if not url:
            return None
        _redis = redis.Redis.from_url(url, decode_responses=True)
        return _redis
    except Exception:
        return None

def note_result(*, first_attempt: bool, ok: bool, status_code: Optional[int], is_conn_error: bool):
    """
    Adjust RPS exponentially based on connection errors.
    - On error: Halve RPS immediately (200 → 100 → 50 → 25...)
    - On success: Double RPS after 5s clean window (5 → 10 → 20 → 40...)
    """
    if os.getenv("NETSENTRY_DISABLE", "0") in ("1", "true", "True"):
        return

    if not first_attempt:
        return

    r = _r()
    if not r:
        return

    RPS_KEY = "llm:global_rps"
    LAST_ERROR_KEY = "llm:last_error_ts"
    FLOOR = 5
    CEILING = 450
    DEFAULT_START = int(os.getenv("CHATSTATS_LLM_GLOBAL_RPS", "450"))

    try:
        current_rps = int(r.get(RPS_KEY) or DEFAULT_START)
    except Exception:
        current_rps = DEFAULT_START

    # On connection error: halve immediately
    if not ok and (is_conn_error or status_code is None):
        new_rps = max(FLOOR, current_rps // 2)
        r.setex(RPS_KEY, 90, new_rps)
        r.setex(LAST_ERROR_KEY, 90, int(time.time()))
        # Only log if RPS changed (reduces spam when at floor)
        if new_rps != current_rps:
            logger.warning("[NETSENTRY] Connection error → halve RPS: %d → %d", current_rps, new_rps)
        return

    # On success: check if we should step up
    if ok:
        try:
            last_error = float(r.get(LAST_ERROR_KEY) or 0)
        except Exception:
            last_error = 0
        now = time.time()

        # If 5 seconds clean, double RPS
        if now - last_error >= 5.0:
            new_rps = min(CEILING, current_rps * 2)
            if new_rps != current_rps:
                r.setex(RPS_KEY, 90, new_rps)
                logger.info("[NETSENTRY] 5s clean → double RPS: %d → %d", current_rps, new_rps)
        return

    # On any other error, update last error timestamp
    if not ok:
        r.setex(LAST_ERROR_KEY, 90, int(time.time()))

