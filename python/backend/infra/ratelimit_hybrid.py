"""
Hybrid rate limiter with local token buckets and periodic Redis sync.
Reduces Redis contention by 100x while maintaining global rate limits.
"""

from __future__ import annotations
import os
import time
import threading
import random
from typing import Optional
import redis
from functools import lru_cache

try:
    import gevent
except Exception:
    gevent = None

from backend.infra.redis import get_redis
# Intentionally keep this module metrics-free; hot path must be ultra-light.


class LocalTokenBucket:
    """Thread-safe local token bucket with periodic Redis sync."""
    
    def __init__(self, key: str, global_limit: int, sync_interval: float | None = None):
        self.key = key
        self.global_limit = global_limit
        self.default_limit = global_limit  # Store default for fallback
        # Store RPS key for dynamic lookup
        self.rps_redis_key = "llm:global_rps"
        
        # Allow tuning via env (milliseconds); default 100ms
        try:
            _ms = float(os.getenv("CHATSTATS_LIMITER_SYNC_INTERVAL_MS", "100"))
        except Exception:
            _ms = 100.0
        self.sync_interval = (sync_interval if sync_interval is not None else _ms / 1000.0)
        
        # Local state
        self.local_tokens = 0
        # Start slightly in the past with a random phase so processes desynchronize their first sync
        self.last_sync = time.time() - random.random() * (self.sync_interval if self.sync_interval else 0.05)
        self.lock = threading.Lock()
        
        # One bucket per process (gevent runs many greenlets in one OS thread).
        # Split global RPS across processes and scale by sync interval.
        try:
            worker_count = int(os.getenv("CELERY_ANALYSIS_WORKER_PROCS", "1"))
        except Exception:
            worker_count = 1
        try:
            headroom = float(os.getenv("CHATSTATS_LIMITER_LOCAL_HEADROOM", "1.2"))
        except Exception:
            headroom = 1.2
        per_sync = (global_limit * self.sync_interval * headroom) / max(1, worker_count)
        self.local_quota = max(1, int(per_sync))
        
    def acquire(self, redis_client: redis.Redis) -> bool:
        """Try to acquire a token, syncing with Redis periodically."""
        with self.lock:
            now = time.time()
            
            # Refill local bucket periodically from Redis
            if now - self.last_sync > self.sync_interval:
                self._sync_with_redis(redis_client, now)
                
            # Try local acquisition
            if self.local_tokens > 0:
                self.local_tokens -= 1
                return True
                
            # Local exhausted, try emergency Redis fetch
            if self._emergency_fetch(redis_client, now):
                return True
                
            return False
    
    def _sync_with_redis(self, r: redis.Redis, now: float):
        """Periodic sync to claim tokens from global pool."""
        try:
            # Re-read global limit from Redis (may have changed via net_sentry)
            try:
                self.global_limit = int(r.get(self.rps_redis_key) or self.default_limit)
            except Exception:
                self.global_limit = self.default_limit
            
            # Recalculate local quota based on new limit
            try:
                worker_count = int(os.getenv("CELERY_ANALYSIS_WORKER_PROCS", "1"))
            except Exception:
                worker_count = 1
            try:
                headroom = float(os.getenv("CHATSTATS_LIMITER_LOCAL_HEADROOM", "1.2"))
            except Exception:
                headroom = 1.2
            per_sync = (self.global_limit * self.sync_interval * headroom) / max(1, worker_count)
            self.local_quota = max(1, int(per_sync))
            
            # Use a Lua script for atomic token claiming
            script = """
            local key = KEYS[1]
            local now_s = tonumber(ARGV[1])
            local request = tonumber(ARGV[2])
            local limit = tonumber(ARGV[3])
            
            local bucket_key = key .. ":" .. now_s
            local used = tonumber(redis.call('GET', bucket_key) or 0)
            local available = math.max(0, limit - used)
            local granted = math.min(request, available)
            
            if granted > 0 then
                redis.call('INCRBY', bucket_key, granted)
                redis.call('EXPIRE', bucket_key, 2)
            end
            
            return granted
            """
            
            granted = r.eval(script, 1, f"rl:{self.key}", 
                           int(now), self.local_quota, self.global_limit)
            
            self.local_tokens = int(granted or 0)
            self.last_sync = now
            
        except Exception:
            # On Redis error, grant minimal tokens to keep running
            self.local_tokens = min(2, self.local_quota)
            self.last_sync = now
    
    def _emergency_fetch(self, r: redis.Redis, now: float) -> bool:
        """Emergency single token fetch when local is exhausted."""
        try:
            script = """
            local key = KEYS[1]
            local now_s = tonumber(ARGV[1])
            local limit = tonumber(ARGV[2])
            
            local bucket_key = key .. ":" .. now_s
            local used = tonumber(redis.call('GET', bucket_key) or 0)
            
            if used < limit then
                redis.call('INCR', bucket_key)
                redis.call('EXPIRE', bucket_key, 2)
                return 1
            end
            return 0
            """
            
            result = r.eval(script, 1, f"rl:{self.key}", int(now), self.global_limit)
            return int(result or 0) == 1
            
        except Exception:
            return False


# Thread-local storage for token buckets
_local_buckets = threading.local()


@lru_cache(maxsize=1)
def _get_limiter_redis() -> redis.Redis:
    """Return a Redis client for the limiter role."""
    url = os.getenv("CHATSTATS_LIMITER_REDIS_URL")
    if not url:
        return get_redis()
    pool = redis.ConnectionPool.from_url(
        url,
        max_connections=int(os.getenv("LIMITER_REDIS_MAX_CONNECTIONS", "5000")),
        socket_keepalive=True,
        health_check_interval=30,
    )
    return redis.Redis(connection_pool=pool)


def _cooperative_sleep(seconds: float) -> None:
    """Cooperative sleep that works with gevent."""
    if seconds <= 0:
        return
    try:
        if gevent is not None:
            gevent.sleep(seconds)
            return
    except Exception:
        pass
    time.sleep(seconds)


def acquire_slot_optimized(key: str, limit_per_sec: int, max_wait_ms: int = 250) -> bool:
    """Optimized token acquisition with local caching."""
    # Get or create local bucket for this key
    if not hasattr(_local_buckets, 'buckets'):
        _local_buckets.buckets = {}
    
    if key not in _local_buckets.buckets:
        _local_buckets.buckets[key] = LocalTokenBucket(key, limit_per_sec)
    
    bucket = _local_buckets.buckets[key]
    r = _get_limiter_redis()
    
    # Try immediate acquisition
    if bucket.acquire(r):
        return True
    
    # If failed, wait and retry once
    sleep_ms = min(250, max_wait_ms)
    jitter_ms = random.randint(0, min(50, sleep_ms // 4))
    _cooperative_sleep((sleep_ms + jitter_ms) / 1000.0)
    
    return bucket.acquire(r)


# Provider hold functionality (same as original)
def set_hold(key: str, seconds: float, reason: str = "") -> None:
    """Set a temporary provider hold for `key`."""
    try:
        ms = max(0, int(float(seconds) * 1000))
    except Exception:
        ms = 0
    if ms <= 0:
        return
    r = _get_limiter_redis()
    k = f"rl:{key}:hold"
    try:
        ttl = r.pttl(k)
    except Exception:
        ttl = None
    if ttl is None or (isinstance(ttl, int) and ttl < ms - 100):
        try:
            r.psetex(k, ms, reason or "1")
        except Exception:
            pass


def hold_remaining_ms(key: str) -> int:
    """Return remaining hold time in ms for `key` (0 if none)."""
    try:
        r = _get_limiter_redis()
        t = r.pttl(f"rl:{key}:hold")
        return int(t) if isinstance(t, int) and t > 0 else 0
    except Exception:
        return 0


def wait_for_slot_optimized(key: str, limit_per_sec: int, max_block_ms: int = 5000) -> bool:
    """
    Cooperatively block up to `max_block_ms` to acquire one token using hybrid approach.
    Respects any provider hold set for `key`.
    """
    deadline = time.monotonic() + (max_block_ms / 1000.0)
    
    while True:
        # Honor provider hold
        rem = hold_remaining_ms(key)
        if rem > 0:
            _cooperative_sleep(min(rem, 250) / 1000.0)
            
        slice_left_ms = int(max(0, (deadline - time.monotonic()) * 1000))
        if slice_left_ms <= 0:
            return False
            
        if acquire_slot_optimized(key, limit_per_sec, max_wait_ms=min(250, slice_left_ms)):
            return True
            
        # Small cooperative pause before retrying
        _cooperative_sleep(0.05)
