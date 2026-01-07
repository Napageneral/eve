"""In-process bulk flusher for DB persists.

Batches payloads and commits them in a single transaction to avoid per-row
commit overhead. Intended to run inside the DB worker process only.
"""

import threading
import time
import random
import os
from collections import deque
from typing import Any, Deque, Dict, List

from backend.db.session_manager import new_session
from sqlalchemy.exc import OperationalError as _OperationalError
# Keep hot path light; metrics optional
try:
    from backend.services.metrics.bottleneck_metrics import BottleneckMetrics  # type: ignore
except Exception:
    class BottleneckMetrics:  # type: ignore
        @staticmethod
        def track_stage(name: str):
            from contextlib import contextmanager
            @contextmanager
            def _cm():
                yield
            return _cm()


class _BulkDBFlusher:
    def __init__(self, max_batch: int = 500, max_wait_ms: int = 100):
        self.buf: Deque[Dict[str, Any]] = deque()
        self.lock = threading.Lock()
        self.max_batch = int(max_batch)
        self.max_wait_ms = int(max_wait_ms)
        self._evt = threading.Event()
        self._started = False
        self._stop = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        t = threading.Thread(target=self._run, name="DBBulkFlusher", daemon=True)
        t.start()

    def stop(self) -> None:
        self._stop = True
        self._evt.set()

    def enqueue(self, payload: Dict[str, Any]) -> None:
        with self.lock:
            self.buf.append(payload)
            if len(self.buf) >= self.max_batch:
                self._evt.set()

    def _drain(self, n: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with self.lock:
            for _ in range(min(n, len(self.buf))):
                out.append(self.buf.popleft())
        return out

    def _run(self) -> None:
        last_flush = time.monotonic()
        while not self._stop:
            timeout_s = self.max_wait_ms / 1000.0
            now = time.monotonic()
            wait = max(0.0, timeout_s - (now - last_flush))
            self._evt.wait(wait)
            self._evt.clear()

            batch = self._drain(self.max_batch)
            if not batch:
                continue

            with BottleneckMetrics.track_stage("db_flush_batch_ms"):
                self._flush_batch(batch)
            last_flush = time.monotonic()

    def wait_until_empty(self, timeout_ms: int = 10000) -> bool:
        """Best-effort drain wait: returns True if buffer drained before timeout.

        This nudges the flusher thread via the internal event and waits in small
        intervals until the internal buffer is empty or the timeout elapses.
        """
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while time.monotonic() < deadline:
            with self.lock:
                pending = len(self.buf)
            if pending == 0:
                return True
            # Nudge the flusher and sleep briefly
            self._evt.set()
            time.sleep(0.02)
        return False

    def _flush_batch(self, batch: List[Dict[str, Any]]) -> None:
        # Perform a single transaction for the batch.
        from backend.services.conversations.analysis import (
            ConversationAnalysisService,
        )
        from backend.db.session_manager import new_session
        from backend.services.core.event_bus import EventBus
        from backend.services.analysis.redis_counters import mark_finished
        from backend.repositories.conversation_analysis import ConversationAnalysisRepository as _CAR
        import logging as _logging
        _log = _logging.getLogger(__name__)

        LOCK_STRINGS = ("database is locked", "database is busy", "db is locked")

        def _commit_with_retry(session, max_tries: int = 5) -> None:
            """Commit with short exponential backoff on transient SQLite locks."""
            for i in range(max_tries):
                try:
                    session.commit()
                    return
                except _OperationalError as e:  # pragma: no cover – timing-dependent
                    msg = str(e).lower()
                    if any(s in msg for s in LOCK_STRINGS):
                        # 50ms → 100ms → 200ms → capped at 500ms (+ jitter)
                        base = min(0.5, 0.05 * (2 ** i))
                        time.sleep(base + random.random() * 0.05)
                        try:
                            session.rollback()
                        except Exception:
                            pass
                        continue
                    raise

        with new_session() as session:
            # Commit every N payloads to shorten SQLite writer lock duration
            try:
                chunk_size = int(os.getenv("DB_BULK_COMMIT_CHUNK", "30"))
            except Exception:
                chunk_size = 30
            try:
                commit_ms = int(os.getenv("DB_BULK_COMMIT_MS", "30"))
            except Exception:
                commit_ms = 30
            processed = 0
            last_commit = time.monotonic()
            for payload in batch:
                llm_resp = (payload.get("llm_response") or {})
                ca_id = int(payload.get("ca_row_id"))
                run_id = (payload.get("run_id") or (payload.get("kwargs") or {}).get("run_id"))
                try:
                    # Handle both legacy (DB prompt ID) and Eve (no DB ID) prompts
                    prompt_db_id = payload.get("prompt_template_db_id")
                    if prompt_db_id is not None:
                        prompt_db_id = int(prompt_db_id)
                    
                    ConversationAnalysisService.save_analysis_results(
                        llm_response_content_str=(llm_resp.get("content") or "{}"),
                        conversation_id=int(payload["convo_id"]),
                        chat_id=int(payload["chat_id"]),
                        cost=llm_resp.get("usage", {}).get("total_cost", 0.0),
                        input_tokens=llm_resp.get("usage", {}).get("input_tokens", 0),
                        output_tokens=llm_resp.get("usage", {}).get("output_tokens", 0),
                        model_name=payload.get("model_name", ""),
                        prompt_template_db_id=prompt_db_id,  # Now can be None
                        conversation_analysis_row_id=ca_id,
                        eve_prompt_id=payload.get("eve_prompt_id"),  # Track Eve prompt ID
                    )

                    # After analysis is persisted, enqueue embeddings for both:
                    # - full conversation (encoded)
                    # - analysis-derived facets (summary/topics/entities/emotions/humor)
                    #
                    # NOTE: This runs inside the DB worker. The embed tasks themselves are routed
                    # to the embeddings queue (gevent workers) so we avoid blocking the writer.
                    try:
                        from backend.celery_service.tasks.embeddings import (
                            embed_conversation_task,
                            embed_analyses_for_conversation_task,
                        )
                        convo_id = int(payload["convo_id"])
                        chat_id = int(payload["chat_id"])
                        embed_conversation_task.delay(convo_id, chat_id, None)
                        embed_analyses_for_conversation_task.delay(convo_id, chat_id, run_id=run_id)
                    except Exception:
                        _log.debug("[DBBulkFlusher] Failed to enqueue embeddings tasks", exc_info=True)
                except Exception as e:
                    _msg = str(e).lower()
                    # Transient SQLite lock – requeue and retry shortly
                    if isinstance(e, _OperationalError) and any(s in _msg for s in LOCK_STRINGS):
                        try:
                            with self.lock:
                                self.buf.appendleft(payload)
                            time.sleep(0.02 + random.random() * 0.05)
                        except Exception:
                            pass
                        continue

                    # Non-transient → mark failed and publish
                    _log.error("[DBBulkFlusher] Persist failed for ca_id=%s: %s", ca_id, e, exc_info=True)
                    try:
                        _CAR.update_status(session, ca_id, "failed", error_message=str(e))
                    except Exception:
                        _log.debug("Failed to update CA status to failed", exc_info=True)
                    if run_id:
                        try:
                            counts = mark_finished(str(run_id), ca_id, ok=False)
                            EventBus.publish(
                                "global",
                                "analysis_failed",
                                {"message": "Task failed", "run_id": run_id, **counts},
                                enrich=False,
                            )
                        except Exception:
                            _log.debug("Failed to publish analysis_failed for run_id=%s", run_id, exc_info=True)
                # periodic commit to release write lock sooner
                processed += 1
                now = time.monotonic()
                if processed >= chunk_size or (now - last_commit) * 1000.0 >= commit_ms:
                    _commit_with_retry(session)
                    processed = 0
                    last_commit = now
                # continue with next payload
            # final commit for any tail items
            _commit_with_retry(session)


bulk_flusher = _BulkDBFlusher(
    max_batch=int(__import__("os").getenv("DB_BULK_MAX_BATCH", "500")),
    max_wait_ms=int(__import__("os").getenv("DB_BULK_MAX_WAIT_MS", "100")),
)



