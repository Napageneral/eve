from __future__ import annotations

from typing import Dict
import time

from backend.infra.redis import get_redis


STATE = lambda run_id: f"analysis:run:{run_id}:state"
ITEMS = lambda run_id: f"analysis:run:{run_id}:items"


def seed(run_id: str, total: int) -> None:
    """Initialize per-run counters in Redis (authoritative)."""
    r = get_redis()
    # Seed state and clamp to non-negative values
    r.hset(
        STATE(run_id),
        mapping={
            "total": max(0, int(total or 0)),
            "pending": max(0, int(total or 0)),
            "processing": 0,
            "success": 0,
            "failed": 0,
            "ts": str(time.time()),
            "start_ts": int(time.time()),
        },
    )
    # Intentionally do not prefill ITEMS (we won't enumerate ids here)


_LUA_MARK_STARTED = """
local items = KEYS[1]; local state = KEYS[2]; local id = ARGV[1];
local current = redis.call('HGET', items, id)
if not current then
  redis.call('HSET', items, id, 'processing')
  redis.call('HINCRBY', state, 'pending', -1)
  redis.call('HINCRBY', state, 'processing', 1)
elseif current == 'pending' then
  redis.call('HSET', items, id, 'processing')
  redis.call('HINCRBY', state, 'pending', -1)
  redis.call('HINCRBY', state, 'processing', 1)
end
return redis.call('HMGET', state, 'total','pending','processing','success','failed')
"""

_LUA_MARK_FINISHED = """
local items = KEYS[1]; local state = KEYS[2]; local id = ARGV[1]; local ok = ARGV[2];
local dest = (ok == '1') and 'success' or 'failed'
local current = redis.call('HGET', items, id)

if not current then
  -- finished without an observed start → treat as pending→dest (clamp pending)
  redis.call('HSET', items, id, dest)
  local pd = tonumber(redis.call('HGET', state, 'pending') or '0')
  if pd > 0 then redis.call('HINCRBY', state, 'pending', -1) end
  redis.call('HINCRBY', state, dest, 1)
elseif current == 'processing' then
  redis.call('HSET', items, id, dest)
  redis.call('HINCRBY', state, 'processing', -1)
  redis.call('HINCRBY', state, dest, 1)
elseif current == 'pending' then
  redis.call('HSET', items, id, dest)
  local pd2 = tonumber(redis.call('HGET', state, 'pending') or '0')
  if pd2 > 0 then redis.call('HINCRBY', state, 'pending', -1) end
  redis.call('HINCRBY', state, dest, 1)
end

return redis.call('HMGET', state, 'total','pending','processing','success','failed')
"""


def _coerce(vals) -> Dict[str, int]:
    t, pd, prc, succ, fail = [int(v or 0) for v in vals]
    return {"total": t, "pending": pd, "processing": prc, "success": succ, "failed": fail}


def mark_started(run_id: str, ca_id: int) -> Dict[str, int]:
    r = get_redis()
    vals = r.eval(_LUA_MARK_STARTED, 2, ITEMS(run_id), STATE(run_id), str(int(ca_id)))
    return _coerce(vals)


def mark_finished(run_id: str, ca_id: int, ok: bool) -> Dict[str, int]:
    r = get_redis()
    vals = r.eval(_LUA_MARK_FINISHED, 2, ITEMS(run_id), STATE(run_id), str(int(ca_id)), "1" if ok else "0")
    return _coerce(vals)


def snapshot(run_id: str) -> Dict[str, int | float | bool]:
    """Return UI-ready snapshot for this run."""
    r = get_redis()
    vals = r.hmget(STATE(run_id), "total", "pending", "processing", "success", "failed", "start_ts")
    t = max(0, int(vals[0] or 0))
    pd = max(0, int(vals[1] or 0))
    prc = max(0, int(vals[2] or 0))
    succ = max(0, int(vals[3] or 0))
    fail = max(0, int(vals[4] or 0))
    try:
        start_ts = int(vals[5] or 0)
    except Exception:
        start_ts = 0
    processed = succ + fail
    pct = (succ / t * 100.0) if t else 0.0
    # naive QPS from start_ts
    qps = 0.0
    if start_ts:
        try:
            elapsed = max(1.0, time.time() - float(start_ts))
            qps = round(processed / elapsed, 2)
        except Exception:
            qps = 0.0
    is_complete = (processed >= t and t > 0)
    # Prefer completion if processed >= total even if transient counters linger
    if is_complete:
        pd = 0
        prc = 0
        status = "completed"
    else:
        status = "processing" if (pd + prc) > 0 else "not_started"
    return {
        "total_convos": t,
        "pending_convos": pd,
        "processing_convos": prc,
        "successful_convos": succ,
        "failed_convos": fail,
        "processed_convos": processed,
        "percentage": round(pct, 2),
        "percent_complete": round(pct, 2),
        "is_complete": is_complete,
        "status": status,
        "overall_status": status,
        "running": (pd + prc) > 0,
        "qps": qps,
    }



def seed_with_items(run_id: str, ca_ids) -> None:
    """Initialize both summary counters and the per-item state map.

    Parameters
    ----------
    run_id:
        Unique identifier for the global run
    ca_ids:
        Iterable of conversation_analysis row IDs to pre-register as 'pending'
    """
    r = get_redis()
    total = int(len(ca_ids) or 0)
    pipe = r.pipeline()
    pipe.hset(
        STATE(run_id),
        mapping={
            "total": total,
            "pending": total,
            "processing": 0,
            "success": 0,
            "failed": 0,
            "ts": str(time.time()),
        },
    )
    if ca_ids:
        mapping = {str(int(cid)): "pending" for cid in ca_ids}
        pipe.hset(ITEMS(run_id), mapping=mapping)
    pipe.execute()


def items_by_state(run_id: str):
    """Return CA row IDs grouped by state (pending/processing/success/failed)."""
    r = get_redis()
    raw = r.hgetall(ITEMS(run_id)) or {}
    out = {"pending": [], "processing": [], "success": [], "failed": []}
    for k, v in raw.items():
        state = v if isinstance(v, str) else (v.decode() if hasattr(v, "decode") else str(v))
        key_int = int(k if isinstance(k, str) else (k.decode() if hasattr(k, "decode") else k))
        out.setdefault(state, []).append(key_int)
    return out


def force_finalize(run_id: str, states=("pending", "processing")):
    """Force residual items into 'failed' and re-balance summary counters.

    This is intended for dev/admin settlement of stuck runs.
    """
    r = get_redis()
    items = items_by_state(run_id)
    forced = []
    pipe = r.pipeline()
    for st in states:
        for cid in items.get(st, []) or []:
            pipe.hset(ITEMS(run_id), str(cid), "failed")
            if st == "pending":
                pipe.hincrby(STATE(run_id), "pending", -1)
            elif st == "processing":
                pipe.hincrby(STATE(run_id), "processing", -1)
            pipe.hincrby(STATE(run_id), "failed", 1)
            forced.append(cid)
    pipe.execute()
    snap = snapshot(run_id)
    return {"forced_failed": forced, **snap}


def reconcile(run_id: str) -> Dict[str, int | float | bool]:
    """Rebuild STATE from ITEMS (single source of truth) and return snapshot."""
    r = get_redis()
    raw = r.hgetall(ITEMS(run_id)) or {}
    counts = {"pending": 0, "processing": 0, "success": 0, "failed": 0}
    for v in raw.values():
        st = v if isinstance(v, str) else (v.decode() if hasattr(v, "decode") else str(v))
        if st in counts:
            counts[st] += 1
    total = sum(counts.values())
    # If ITEMS is empty but STATE suggests non-zero, fall back to STATE to avoid zeroing a live run
    if total == 0:
        vals = r.hmget(STATE(run_id), "total", "pending", "processing", "success", "failed")
        try:
            t, pd, prc, succ, fail = [max(0, int(v or 0)) for v in vals]
            if (t + pd + prc + succ + fail) > 0:
                counts = {"pending": pd, "processing": prc, "success": succ, "failed": fail}
                total = t or (pd + prc + succ + fail)
        except Exception:
            pass
    pipe = r.pipeline()
    pipe.hset(
        STATE(run_id),
        mapping={
            "total": max(0, total),
            "pending": max(0, counts["pending"]),
            "processing": max(0, counts["processing"]),
            "success": max(0, counts["success"]),
            "failed": max(0, counts["failed"]),
            "ts": str(time.time()),
        },
    )
    pipe.execute()
    return snapshot(run_id)


def list_snapshots():
    """Return snapshots for all runs present in Redis."""
    r = get_redis()
    run_ids = []
    try:
        for key in r.scan_iter("analysis:run:*:state"):
            k = key.decode() if hasattr(key, "decode") else str(key)
            parts = k.split(":")
            if len(parts) >= 4:
                run_ids.append(parts[2])
    except Exception:
        run_ids = []
    out = []
    for rid in sorted(set(run_ids)):
        try:
            snap = snapshot(rid)
            out.append({"run_id": rid, **snap})
        except Exception:
            out.append({"run_id": rid})
    return out
