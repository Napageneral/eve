"""
Batched, idempotent progress counters with immediate local feedback.

- Debounces duplicate transitions by reading the previous item state from Redis
  (so task retries are safe).
- Maintains a local running total to return immediately to callers for UI snappiness.
- Flushes in small batches or after a short interval (~500ms).
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Tuple

from backend.infra.redis import get_redis


def _state_key(run_id: str) -> str:
    return f"analysis:run:{run_id}:state"


def _items_key(run_id: str) -> str:
    return f"analysis:run:{run_id}:items"


class CounterBuffer:
    def __init__(self, flush_size: int = 20, flush_interval: float = 0.5):
        self.flush_size = int(flush_size)
        self.flush_interval = float(flush_interval)
        self.buffer: List[Tuple[str, int, str]] = []
        self.lock = threading.Lock()
        self.last_flush = time.monotonic()

        # Local mirrors for instant UI feedback
        self.local_counts: Dict[str, Dict[str, int]] = {}
        self.local_items: Dict[str, Dict[int, str]] = {}

    def add(self, run_id: str, ca_id: int, new_state: str) -> Dict[str, int]:
        with self.lock:
            if run_id not in self.local_counts:
                self._init_counts(run_id)
            if run_id not in self.local_items:
                self.local_items[run_id] = {}

            prev_local = self.local_items[run_id].get(ca_id)
            if prev_local is None:
                prev_local = self._read_item_state(run_id, ca_id) or "pending"
                self.local_items[run_id][ca_id] = prev_local

            if prev_local == new_state:
                self._maybe_flush_locked()
                return self.local_counts[run_id].copy()

            self._apply_transition_local(run_id, prev_local, new_state)
            self.local_items[run_id][ca_id] = new_state

            self.buffer.append((run_id, ca_id, new_state))

            self._maybe_flush_locked()
            return self.local_counts[run_id].copy()

    def force_flush(self):
        with self.lock:
            self._flush_locked()

    def _maybe_flush_locked(self):
        if not self.buffer:
            return
        if (
            len(self.buffer) >= self.flush_size
            or (time.monotonic() - self.last_flush) >= self.flush_interval
        ):
            self._flush_locked()

    def _flush_locked(self):
        if not self.buffer:
            return

        r = get_redis()
        grouped: Dict[str, Dict[int, str]] = {}
        for run_id, ca_id, new_state in self.buffer:
            grouped.setdefault(run_id, {})[ca_id] = new_state

        pipe = r.pipeline()
        for run_id, mapping in grouped.items():
            items_k = _items_key(run_id)
            state_k = _state_key(run_id)

            ca_ids = list(mapping.keys())
            fields = [str(c) for c in ca_ids]
            prev_states = r.hmget(items_k, fields)

            pending_dec = processing_dec = success_inc = failed_inc = 0
            processing_inc = pending_inc = success_dec = failed_dec = 0

            to_set: Dict[str, str] = {}

            for idx, ca_id in enumerate(ca_ids):
                prev = prev_states[idx].decode("utf-8") if prev_states[idx] else "pending"
                new = mapping[ca_id]
                if prev == new:
                    continue

                if prev == "pending" and new == "processing":
                    pending_dec += 1; processing_inc += 1
                elif prev == "processing" and new == "success":
                    processing_dec += 1; success_inc += 1
                elif prev == "processing" and new == "failed":
                    processing_dec += 1; failed_inc += 1
                elif prev == "pending" and new == "success":
                    pending_dec += 1; success_inc += 1
                elif prev == "pending" and new == "failed":
                    pending_dec += 1; failed_inc += 1
                else:
                    if prev == "success" and new == "failed":
                        success_dec += 1; failed_inc += 1
                    elif prev == "failed" and new == "success":
                        failed_dec += 1; success_inc += 1
                    elif prev == "success" and new == "processing":
                        success_dec += 1; processing_inc += 1
                    elif prev == "failed" and new == "processing":
                        failed_dec += 1; processing_inc += 1
                    elif prev == "processing" and new == "pending":
                        processing_dec += 1; pending_inc += 1

                to_set[str(ca_id)] = new

            # Clamp decrements so counters never go negative even if state drifted earlier
            try:
                cur_pd, cur_pr, cur_su, cur_fa = r.hmget(state_k, "pending", "processing", "success", "failed")
                cur_pd_i = int(cur_pd or 0); cur_pr_i = int(cur_pr or 0); cur_su_i = int(cur_su or 0); cur_fa_i = int(cur_fa or 0)
            except Exception:
                cur_pd_i = cur_pr_i = cur_su_i = cur_fa_i = 0

            pending_dec = min(pending_dec, max(0, cur_pd_i))
            processing_dec = min(processing_dec, max(0, cur_pr_i))
            success_dec = min(success_dec, max(0, cur_su_i))
            failed_dec = min(failed_dec, max(0, cur_fa_i))

            if pending_dec:
                pipe.hincrby(state_k, "pending", -pending_dec)
            if pending_inc:
                pipe.hincrby(state_k, "pending",  pending_inc)
            if processing_dec:
                pipe.hincrby(state_k, "processing", -processing_dec)
            if processing_inc:
                pipe.hincrby(state_k, "processing",  processing_inc)
            if success_inc:
                pipe.hincrby(state_k, "success",  success_inc)
            if success_dec:
                pipe.hincrby(state_k, "success", -success_dec)
            if failed_inc:
                pipe.hincrby(state_k, "failed",   failed_inc)
            if failed_dec:
                pipe.hincrby(state_k, "failed",  -failed_dec)

            if to_set:
                pipe.hset(items_k, mapping=to_set)

        pipe.execute()
        self.buffer.clear()
        self.last_flush = time.monotonic()

    def _init_counts(self, run_id: str):
        r = get_redis()
        raw = r.hgetall(_state_key(run_id)) or {}

        def _i(k: bytes, default=0):
            try:
                return int(raw.get(k if isinstance(k, bytes) else k.encode("utf-8"), default))
            except Exception:
                return int(default)

        self.local_counts[run_id] = {
            "total": _i(b"total", 0),
            "pending": _i(b"pending", 0),
            "processing": _i(b"processing", 0),
            "success": _i(b"success", 0),
            "failed": _i(b"failed", 0),
        }

    def _read_item_state(self, run_id: str, ca_id: int) -> str | None:
        try:
            r = get_redis()
            v = r.hget(_items_key(run_id), str(ca_id))
            return v.decode("utf-8") if v else None
        except Exception:
            return None

    def _apply_transition_local(self, run_id: str, prev: str, new: str) -> None:
        c = self.local_counts[run_id]

        def dec(name):
            c[name] = max(0, c.get(name, 0) - 1)

        def inc(name):
            c[name] = c.get(name, 0) + 1

        if prev == new:
            return
        if prev == "pending" and new == "processing":
            dec("pending"); inc("processing")
        elif prev == "processing" and new == "success":
            dec("processing"); inc("success")
        elif prev == "processing" and new == "failed":
            dec("processing"); inc("failed")
        elif prev == "pending" and new == "success":
            dec("pending"); inc("success")
        elif prev == "pending" and new == "failed":
            dec("pending"); inc("failed")
        else:
            if prev in ("pending", "processing", "success", "failed"):
                dec(prev)
            if new in ("pending", "processing", "success", "failed"):
                inc(new)

        # Final clamp to avoid any negative drift from prior inconsistent state
        for key in ("pending", "processing", "success", "failed"):
            if c.get(key, 0) < 0:
                c[key] = 0


_buffer = CounterBuffer()


def buffered_mark_started(run_id: str, ca_id: int) -> Dict[str, int]:
    return _buffer.add(str(run_id), int(ca_id), "processing")


def buffered_mark_finished(run_id: str, ca_id: int, ok: bool) -> Dict[str, int]:
    return _buffer.add(str(run_id), int(ca_id), "success" if ok else "failed")


def force_flush_all():
    _buffer.force_flush()


