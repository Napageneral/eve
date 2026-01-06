from __future__ import annotations

import time
from typing import Dict, Any, List, Optional
import os

from backend.infra.redis import get_redis


# Keep a bounded list of latencies for quick quantiles; tune via env if needed
MAX_LAT_SAMPLES = int(os.getenv("CHATSTATS_METRICS_MAX_SAMPLES", "10000"))


def _now_sec() -> int:
    return int(time.time())


def _sec_bucket(prefix: str, sec: Optional[int] = None) -> str:
    if sec is None:
        sec = _now_sec()
    return f"{prefix}:{sec}"


def _percentiles(samples: List[int]) -> Dict[str, int | float]:
    if not samples:
        return {"p50": 0, "p95": 0, "p99": 0, "avg": 0.0, "count": 0}
    arr = sorted(samples)
    n = len(arr)

    def pct(p: float) -> int:
        if n == 0:
            return 0
        k = max(0, min(n - 1, int(round(p * (n - 1)))))
        return arr[k]

    avg = sum(arr) / n
    return {"p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99), "avg": round(avg, 1), "count": n}


class RuntimeMetrics:
    """
    Ultra-light metrics aggregator stored in Redis:
      - Throughput buckets: per-second "finished" counters
      - Latency ring buffer: LPUSH durations; LTRIM to MAX_LAT_SAMPLES
      - LLM call counters: tokens + latencies
    """

    # ---------------- Conversation Analysis task metrics ----------------

    @staticmethod
    def record_ca_task_start() -> None:
        r = get_redis()
        pipe = r.pipeline()
        pipe.incr("metrics:ca:inflight")
        b = _sec_bucket("metrics:ca:started")
        pipe.incr(b)
        pipe.expire(b, 120)
        pipe.execute()

    @staticmethod
    def record_ca_task_finish(duration_ms: float, ok: bool) -> None:
        r = get_redis()
        pipe = r.pipeline()
        pipe.decr("metrics:ca:inflight")
        b = _sec_bucket("metrics:ca:finished")
        pipe.incr(b)
        pipe.expire(b, 120)
        pipe.lpush("metrics:ca:latency_ms", int(duration_ms))
        pipe.ltrim("metrics:ca:latency_ms", 0, MAX_LAT_SAMPLES - 1)
        if not ok:
            pipe.incr("metrics:ca:errors_total")
        pipe.execute()

    # ---------------- LLM call metrics ----------------------------------

    @staticmethod
    def record_llm_call(model: str, duration_ms: float, input_tokens: Optional[int], output_tokens: Optional[int]) -> None:
        r = get_redis()
        pipe = r.pipeline()
        b = _sec_bucket("metrics:llm:calls")
        pipe.incr(b)
        pipe.expire(b, 120)
        pipe.lpush("metrics:llm:latency_ms", int(duration_ms))
        pipe.ltrim("metrics:llm:latency_ms", 0, MAX_LAT_SAMPLES - 1)
        pipe.hincrby("metrics:llm:agg", "count_calls", 1)
        if input_tokens is not None:
            pipe.hincrby("metrics:llm:agg", "sum_input_tokens", int(input_tokens))
        if output_tokens is not None:
            pipe.hincrby("metrics:llm:agg", "sum_output_tokens", int(output_tokens))
        pipe.execute()

    # ---------------- Stage timing & queue metrics ----------------------

    @staticmethod
    def record_stage(stage: str, duration_ms: float) -> None:
        r = get_redis()
        pipe = r.pipeline()
        pipe.lpush(f"metrics:stage:{stage}", int(duration_ms))
        pipe.ltrim(f"metrics:stage:{stage}", 0, MAX_LAT_SAMPLES - 1)
        pipe.execute()

    @staticmethod
    def _stage_percentiles(stage: str) -> Dict[str, int | float]:
        r = get_redis()
        samples = [int(x) for x in (r.lrange(f"metrics:stage:{stage}", 0, 4999) or [])]
        return _percentiles(samples)

    @staticmethod
    def _queue_depths() -> Dict[str, int]:
        r = get_redis()
        depths: Dict[str, int] = {}
        for q in ("chatstats-analysis", "chatstats-db", "chatstats-bulk"):
            try:
                depths[q] = int(r.llen(q) or 0)
            except Exception:
                depths[q] = -1
        return depths

    # ---------------- Snapshot helpers ----------------------------------

    @staticmethod
    def _sum_buckets(prefix: str, window_secs: int) -> int:
        r = get_redis()
        now = _now_sec()
        keys = [_sec_bucket(prefix, s) for s in range(now - window_secs + 1, now + 1)]
        vals = r.mget(keys)
        return sum(int(v or 0) for v in vals)

    @staticmethod
    def snapshot() -> Dict[str, Any]:
        r = get_redis()
        # Throughput (finished tasks)
        curr = int(r.get(_sec_bucket("metrics:ca:finished")) or 0)
        tps10 = RuntimeMetrics._sum_buckets("metrics:ca:finished", 10) / 10.0
        tps60 = RuntimeMetrics._sum_buckets("metrics:ca:finished", 60) / 60.0
        inflight = int(r.get("metrics:ca:inflight") or 0)
        errors_total = int(r.get("metrics:ca:errors_total") or 0)

        # Latency (from last N samples)
        lat_samples = [int(x) for x in (r.lrange("metrics:ca:latency_ms", 0, 4999) or [])]
        lat = _percentiles(lat_samples)

        # LLM side
        llm_curr = int(r.get(_sec_bucket("metrics:llm:calls")) or 0)
        llm_rps10 = RuntimeMetrics._sum_buckets("metrics:llm:calls", 10) / 10.0
        llm_rps60 = RuntimeMetrics._sum_buckets("metrics:llm:calls", 60) / 60.0
        llm_lat_samples = [int(x) for x in (r.lrange("metrics:llm:latency_ms", 0, 4999) or [])]
        llm_lat = _percentiles(llm_lat_samples)
        agg = r.hgetall("metrics:llm:agg") or {}
        calls = int(agg.get("count_calls") or 0)
        avg_in = (int(agg.get("sum_input_tokens") or 0) / calls) if calls else 0.0
        avg_out = (int(agg.get("sum_output_tokens") or 0) / calls) if calls else 0.0

        return {
            "ts": int(time.time()),
            "ca": {
                "current_tps": curr,
                "avg_tps_10s": round(tps10, 2),
                "avg_tps_60s": round(tps60, 2),
                "inflight": inflight,
                "errors_total": errors_total,
                "latency_ms": lat,
            },
            "llm": {
                "current_rps": llm_curr,
                "avg_rps_10s": round(llm_rps10, 2),
                "avg_rps_60s": round(llm_rps60, 2),
                "latency_ms": llm_lat,
                "avg_input_tokens": round(avg_in, 1),
                "avg_output_tokens": round(avg_out, 1),
                "count_calls": calls,
            },
            "stages": {
                "encode_ms": RuntimeMetrics._stage_percentiles("encode_ms"),
                "llm_ms": RuntimeMetrics._stage_percentiles("llm_ms"),
                "persist_ms": RuntimeMetrics._stage_percentiles("persist_ms"),
                "analysis_queue_lag_ms": RuntimeMetrics._stage_percentiles("analysis_queue_lag_ms"),
                "db_queue_lag_ms": RuntimeMetrics._stage_percentiles("db_queue_lag_ms"),
            },
            "queues": RuntimeMetrics._queue_depths(),
        }


