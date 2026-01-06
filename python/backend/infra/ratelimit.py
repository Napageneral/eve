"""
Simple Redis-backed rate limiter utilities.

Provides a lightweight token acquisition helper suitable for per-second
limits across multiple worker processes. Uses a fixed 1s window and
cooperative sleeps to smooth bursts at high RPS with minimal Redis load.
"""

from __future__ import annotations

import os
import time
import random
from typing import Optional

try:
    # In Celery workers we monkey-patch gevent; time.sleep is cooperative,
    # but use gevent.sleep if available to be explicit.
    import gevent  # type: ignore
except Exception:  # pragma: no cover – gevent not required in all contexts
    gevent = None

import redis  # type: ignore
from functools import lru_cache

from backend.infra.redis import get_redis


_LUA_TRY_TAKE = """
local key   = KEYS[1]
local now_s = tonumber(ARGV[1])
local cap   = tonumber(ARGV[2])
local k     = key .. ":" .. tostring(now_s)

local n = redis.call('GET', k)
if not n then
  redis.call('SET', k, 1, 'EX', 2)
  return {1, 0}
end
n = tonumber(n)
if n < cap then
  redis.call('INCR', k)
  return {1, 0}
end

local t = redis.call('TIME')
local ms = math.floor(1000 - (tonumber(t[2]) / 1000))
if ms < 0 then ms = 1 end
return {0, ms}
"""

def _cooperative_sleep(seconds: float) -> None:
    if seconds <= 0:
        return
    try:
        if gevent is not None:
            gevent.sleep(seconds)
            return
    except Exception:
        pass
    time.sleep(seconds)


@lru_cache(maxsize=1)
def _get_limiter_redis() -> redis.Redis:
    """Return a Redis client for the limiter role.

    If CHATSTATS_LIMITER_REDIS_URL is set, use a dedicated pool; otherwise
    fall back to the default shared client from backend.infra.redis.
    """
    url = os.getenv("CHATSTATS_LIMITER_REDIS_URL")
    if not url:
        # Fallback to default pool (usually broker Redis in dev)
        return get_redis()
    pool = redis.ConnectionPool.from_url(
        url,
        max_connections=int(os.getenv("LIMITER_REDIS_MAX_CONNECTIONS", "2000")),
        socket_keepalive=True,
        health_check_interval=30,
    )
    return redis.Redis(connection_pool=pool)


def acquire_slot(key: str, limit_per_sec: int, max_wait_ms: int = 250) -> bool:
    """Try to acquire a token for the given key under a per-second cap.

    The limiter performs a single EVAL. If no slot is available, sleep up to
    the next boundary (bounded by max_wait_ms), then retry once. Returns True
    if a slot was acquired; False otherwise.
    """
    # Use hybrid implementation if enabled
    if os.getenv("ENABLE_HYBRID_RATE_LIMITER", "true").lower() == "true":
        try:
            from backend.infra.ratelimit_hybrid import acquire_slot_optimized
            return acquire_slot_optimized(key, limit_per_sec, max_wait_ms)
        except ImportError:
            pass
    
    r = _get_limiter_redis()
    took, wait_ms = r.eval(
        _LUA_TRY_TAKE,
        1,
        f"rl:{key}",
        int(time.time()),
        int(limit_per_sec),
    )
    if int(took) == 1:
        return True
    sleep_ms = min(int(wait_ms or 1), int(max_wait_ms))
    _cooperative_sleep(sleep_ms / 1000.0)
    took2, _ = r.eval(
        _LUA_TRY_TAKE,
        1,
        f"rl:{key}",
        int(time.time()),
        int(limit_per_sec),
    )
    return int(took2) == 1



# ------------- Provider "hold" (circuit breaker) ----------------------
def set_hold(key: str, seconds: float, reason: str = "") -> None:
    """
    Set a temporary provider hold for `key` – callers should pause for this TTL.
    Stored under rl:{key}:hold with millisecond precision.
    """
    try:
        ms = max(0, int(float(seconds) * 1000))
    except Exception:
        ms = 0
    if ms <= 0:
        return
    r = _get_limiter_redis()
    k = f"rl:{key}:hold"
    # Only extend if we'd substantially lengthen the TTL (avoid churn)
    try:
        ttl = r.pttl(k)
    except Exception:
        ttl = None
    if ttl is None or (isinstance(ttl, int) and ttl < ms - 100):
        try:
            r.psetex(k, ms, reason or "1")
        except Exception:
            # Best-effort; ignore if Redis hiccups
            pass


def hold_remaining_ms(key: str) -> int:
    """Return remaining hold time in ms for `key` (0 if none)."""
    try:
        r = _get_limiter_redis()
        t = r.pttl(f"rl:{key}:hold")
        return int(t) if isinstance(t, int) and t > 0 else 0
    except Exception:
        return 0


def wait_for_slot(key: str, limit_per_sec: int, max_block_ms: int = 5000) -> bool:
    """
    Cooperatively block up to `max_block_ms` to acquire one token.
    Respects any provider hold set for `key`.
    Returns True if a slot was acquired; False if we timed out.
    """
    # Use hybrid implementation if enabled
    if os.getenv("ENABLE_HYBRID_RATE_LIMITER", "true").lower() == "true":
        try:
            from backend.infra.ratelimit_hybrid import wait_for_slot_optimized
            return wait_for_slot_optimized(key, limit_per_sec, max_block_ms)
        except ImportError:
            pass
    
    deadline = time.monotonic() + (max_block_ms / 1000.0)
    # Try in small slices so we can respond to holds and avoid long sleeps
    while True:
        # Honor provider hold in small slices
        rem = hold_remaining_ms(key)
        if rem > 0:
            _cooperative_sleep(min(rem, 250) / 1000.0)

        slice_left_ms = int(max(0, (deadline - time.monotonic()) * 1000))
        if slice_left_ms <= 0:
            return False
        if acquire_slot(key, limit_per_sec, max_wait_ms=min(250, slice_left_ms)):
            return True

        # Small cooperative pause before retrying
        _cooperative_sleep(0.05)

