Eve LLM Throughput & Rate Limiting Learnings

This doc records lessons from tuning Celery + Redis + LiteLLM orchestration.
It’s a reference to avoid re-debugging the same issues in future runs.

1. Error Classes & Root Causes

Internal limiter trips

Raised in LLMService._execute_llm_call.

[LLM-GATE] rate_limited … in logs.

Means our Redis hybrid limiter had no token.

Provider hard limits (429s)

[LLM-PROVIDER] … status=429 retry_after=<s> request_id=<id>

True vendor rate limit; must back off.

Transport resets

APIConnectionError: Connection reset by peer

No request ID → it’s not quota, it’s LB/socket churn.

Usually triggered by too many concurrent H2 streams from one worker.

Malformed JSON

“Failed to parse JSON…” warnings.

Harmless if parser degrades gracefully, but each costs a full LLM latency.

2. Why We Saw Stalls & Plateaus

Long global provider holds (30s) froze all workers → inflight drained to ~0 → UI hung.

All workers syncing in phase → periodic under-grant of tokens → bursts then starvation.

One SQLite writer → lock storms until we chunked commits + moved all writes onto DB queue.

Small convos using max_tokens=10k → inflated p95 to 9–12s even when not needed.

Per-worker concurrency too high → HTTP/2 stream resets; provider LB closed sockets.

Plateaued TPS exactly matched inflight / p95_latency.

3. Knobs & What They Do
Concurrency / Inflight

CELERY_ANALYSIS_WORKER_PROCS × CELERY_ANALYSIS_CONCURRENCY = inflight slots.

Rule of thumb:

inflight ≈ target_RPS × p95_latency


Too few inflight = low TPS.

Too many per-proc lanes = resets.

Internal Limiter

CHATSTATS_LLM_GLOBAL_RPS: overall cap.

CHATSTATS_LIMITER_SYNC_INTERVAL_MS:

100ms → chunky.

30–50ms → smoother.

CHATSTATS_LIMITER_LOCAL_HEADROOM:

0.9–1.0 conservative.

1.1–1.2 better utilization, riskier bursts.

CHATSTATS_LIMITER_MAX_BLOCK_MS:

30–60s → lets us survive Retry-After.

Provider Holds

Now scoped per provider (e.g. llm:gemini), not global.

CHATSTATS_PROVIDER_HOLD_CAP_S: 0.5–1.0 → prevents long freezes.

Celery Prefetch

1 = safe, but underfeeds greenlets.

2 = sweet spot at high latency.

LiteLLM / httpx

Pool sizing: 1500–2000 conns per worker proc, half for keepalive.

Retries: 2–3 (not 6).

GEVENT_THREADPOOL_SIZE=64 for DNS/TLS.

If resets persist: LITELLM_HTTP2=0 → fall back to h1.1.

DB Writer

Single dedicated worker, --concurrency=1.

Commit policy: every N rows or M ms (whichever first).

DB_BULK_COMMIT_CHUNK=30, DB_BULK_COMMIT_MS=30.

4. New Optimizations
Dynamic Max Tokens

Compute est input tokens.

Tiered ceilings:

≤800 in → max 2k

≤2.5k in → max 4k

≤5k in → max 6k

else → full 10k

Preserves tall ceiling for big convos; cuts latency for small ones.

Net effect: lower p95 → more TPS.

Worker Layout

Better: more procs, fewer lanes each.

e.g. 14 procs × 220 conc = ~3080 inflight.

Each worker manages fewer concurrent streams → fewer resets.

Lane Cap (Semaphore)

Per-proc limit (LLM_LANES_PER_PROC=120).

Prevents single worker from flooding provider streams.

Provider Holds

Scoped + capped = micro-stalls vanish, no more 30s global freezes.

5. Operating Playbook

Check perf metrics

timings_rl_wait_ms → >0 means limiter bound.

timings_llm_ms → rising? need more inflight.

provider_hold_ms → vendor throttling.

To raise TPS:

Increase inflight = procs × conc.

Raise global RPS gradually (+20–40).

Confirm rl_wait_ms ~0 before raising again.

If 429s:

Stop raising RPS.

Vendor ceiling reached.

If resets:

Lower per-proc conc.

Cap lanes with semaphore.

Test h1.1.

6. Key Formulas

Little’s Law:

RPS ≈ inflight / p95_latency


Don’t chase RPS beyond vendor’s actual ceiling; focus on reducing p95 and keeping inflight full.

7. Future Improvements

Automate dynamic max_tokens during pre-encode.

Auto-size inflight based on rolling p95.

Shard load across multiple API keys if vendor ceiling is too low.

Expose UI metrics: TPS, inflight, limiter waits, provider holds.

Add provider_429s_30s graph.

📌 Bottom line:
We started with DB locks, 30s freezes, and retry storms. Now we have stable ~230–280 QPS with capped holds, dynamic tokens, and worker layout tuned. Future scaling depends on inflight vs latency and provider behavior.