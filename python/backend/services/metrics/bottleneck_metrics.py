"""Metrics to identify QPS bottlenecks."""

import time
import threading
from typing import Dict, Any, Optional
from contextlib import contextmanager
from backend.infra.redis import get_redis

class BottleneckMetrics:
    """Track where workers spend time to identify bottlenecks."""
    
    _local = threading.local()
    
    @classmethod
    @contextmanager
    def track_stage(cls, stage: str):
        """Context manager to track time in different stages."""
        start = time.monotonic()
        try:
            yield
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            cls._record_stage_time(stage, duration_ms)
    
    @classmethod
    def _record_stage_time(cls, stage: str, duration_ms: float):
        """Record stage timing to Redis."""
        try:
            r = get_redis()
            pipe = r.pipeline()
            
            # Track distribution
            pipe.lpush(f"bottleneck:{stage}:ms", int(duration_ms))
            pipe.ltrim(f"bottleneck:{stage}:ms", 0, 999)
            
            # Track per-second throughput
            sec_key = f"bottleneck:{stage}:count:{int(time.time())}"
            pipe.incr(sec_key)
            pipe.expire(sec_key, 60)
            
            pipe.execute()
        except Exception:
            pass
    
    @classmethod
    def get_snapshot(cls) -> Dict[str, Any]:
        """Get current bottleneck metrics."""
        r = get_redis()
        stages = [
            "rate_limit_wait",
            "redis_queue_fetch", 
            "task_decode",
            "llm_call",
            "redis_counter_update",
            "total_task"
        ]
        
        snapshot = {}
        for stage in stages:
            # Get timing distribution
            samples = [int(x) for x in r.lrange(f"bottleneck:{stage}:ms", 0, 999) or []]
            if samples:
                samples.sort()
                n = len(samples)
                snapshot[stage] = {
                    "p50": samples[n//2] if n > 0 else 0,
                    "p95": samples[int(n*0.95)] if n > 0 else 0,
                    "p99": samples[int(n*0.99)] if n > 0 else 0,
                    "count": n
                }
            else:
                snapshot[stage] = {
                    "p50": 0,
                    "p95": 0,
                    "p99": 0,
                    "count": 0
                }
            
            # Get current throughput
            now = int(time.time())
            tps = sum(
                int(r.get(f"bottleneck:{stage}:count:{now-i}") or 0)
                for i in range(10)
            ) / 10.0
            snapshot[stage]["tps"] = round(tps, 1)
        
        return snapshot
