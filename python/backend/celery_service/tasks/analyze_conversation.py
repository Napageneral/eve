from __future__ import annotations

import logging
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor

from celery import shared_task, chain

from backend.celery_service.tasks.base import BaseTaskWithDLQ
import time
from backend.services.metrics.runtime_metrics import RuntimeMetrics
from backend.services.conversations.analysis_workflow import ConversationAnalysisWorkflow
from backend.services.llm import LLMError

logger = logging.getLogger(__name__)


class AnalyzeConversationTask(BaseTaskWithDLQ):
    """Thin Celery task that delegates business logic to ConversationAnalysisWorkflow."""


@shared_task(bind=True, base=AnalyzeConversationTask, name="celery.analyze_conversation", ignore_result=True)
def analyze_conversation_task(
    self,
    convo_id: int,
    chat_id: int,
    ca_row_id: int,
    encoded_text: Optional[str] = None,
    publish_global: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    """Entry-point for analyzing a single conversation.

    All heavy lifting is handled by ConversationAnalysisWorkflow – this wrapper only
    manages task-level concerns (progress, retries, logging).
    """

    logger.debug(
        "[CA-TASK] Starting conversation analysis convo_id=%s chat_id=%s ca_row_id=%s",
        convo_id,
        chat_id,
        ca_row_id,
    )
    # Metrics: mark start
    try:
        RuntimeMetrics.record_ca_task_start()
    except Exception:
        logger.debug("metrics start failed", exc_info=True)
    _t0 = time.monotonic()

    # Publish immediate start (per-run Redis counters) for responsiveness
    try:
        from backend.services.analysis.counter_buffer import buffered_mark_started
        from backend.services.core.event_bus import EventBus
        run_id = kwargs.get("run_id")
        if run_id:
            counts = buffered_mark_started(run_id, ca_row_id)
            EventBus.publish("historic", "analysis_started", {"message": "Task started", "run_id": run_id, **counts}, enrich=False)
    except Exception:
        logger.debug("Runtime start publish failed", exc_info=True)

    try:
        # Use the new progress helper (emits state + event bus messages)
        with self.step("analyzing", 50):
            result = ConversationAnalysisWorkflow.run(
                convo_id=convo_id,
                chat_id=chat_id,
                ca_row_id=ca_row_id,
                encoded_text=encoded_text,
                publish_global=publish_global,
                **kwargs,
            )

        # Publish immediate runtime finish (success)
        try:
            from backend.services.analysis.counter_buffer import buffered_mark_finished, force_flush_all
            from backend.services.core.event_bus import EventBus
            run_id = kwargs.get("run_id")
            if run_id:
                counts = buffered_mark_finished(run_id, ca_row_id, ok=True)
                total = int(counts.get("total", 0))
                processed = int(counts.get("success", 0)) + int(counts.get("failed", 0))
                is_complete = (processed >= total and total > 0)
                payload = {"message": "Task completed", "run_id": run_id, **counts}
                if is_complete:
                    payload.update({
                        "is_complete": True,
                        "status": "completed",
                        "overall_status": "completed",
                        "running": False,
                        "pending": 0,
                        "processing": 0,
                    })
                EventBus.publish("historic", "analysis_completed", payload, enrich=False)
                if is_complete:
                    force_flush_all()  # Ensure all counters are written before completion
                    EventBus.publish("historic", "run_complete", payload, enrich=False)
                    # Route finalize through DB queue to avoid multi-writer contention
                    try:
                        from backend.celery_service.tasks.db_control import historic_status_finalize as _final
                        _final.delay(run_id, int(counts.get("success", 0)), int(counts.get("failed", 0)))
                    except Exception:
                        logger.debug("enqueue historic_status_finalize failed", exc_info=True)
        except Exception:
            logger.debug("Runtime finish publish (success) failed", exc_info=True)

        # Metrics: record finish (success)
        try:
            RuntimeMetrics.record_ca_task_finish((time.monotonic() - _t0) * 1000.0, ok=True)
        except Exception:
            logger.debug("metrics finish(success) failed", exc_info=True)
        return result  # BaseTaskWithDLQ.on_success will emit completion event

    except Exception as exc:  # pragma: no cover – let retry/backoff handle
        logger.error("Conversation analysis failed: %s", exc, exc_info=True)
        # Metrics: record finish (failure)
        try:
            RuntimeMetrics.record_ca_task_finish((time.monotonic() - _t0) * 1000.0, ok=False)
        except Exception:
            logger.debug("metrics finish(failure) failed", exc_info=True)
        # Publish immediate runtime finish (failure)
        try:
            from backend.services.analysis.counter_buffer import buffered_mark_finished
            from backend.services.core.event_bus import EventBus
            run_id = kwargs.get("run_id")
            if run_id:
                counts = buffered_mark_finished(run_id, ca_row_id, ok=False)
                EventBus.publish("historic", "analysis_failed", {"message": "Task failed", "run_id": run_id, **counts}, enrich=False)
        except Exception:
            logger.debug("Runtime finish publish (failure) failed", exc_info=True)
        self.retry_with_backoff(exc)

    finally:
        # No-op: analysis-derived embeddings are now chained after persist in the workflow
        pass


# ---------------------------------------------------------------------------
# Two-stage CA tasks: call LLM (network-heavy) → persist (DB-heavy)
# ---------------------------------------------------------------------------


@shared_task(bind=True, base=AnalyzeConversationTask, name="celery.ca.call_llm", ignore_result=False)
def call_llm_task(
    self,
    convo_id: int,
    chat_id: int,
    ca_row_id: int,
    encoded_text: Optional[str] = None,
    queued_at_ts: Optional[float] = None,
    **kwargs,
) -> Dict[str, Any]:
    logger.debug(
        "[CA.CALL] convo_id=%s chat_id=%s ca_row_id=%s", convo_id, chat_id, ca_row_id
    )
    # Metrics + run-level counters: mark start
    try:
        RuntimeMetrics.record_ca_task_start()
    except Exception:
        logger.debug("metrics start failed", exc_info=True)
    # Best-effort counter update and event so UI reflects processing promptly
    try:
        from backend.services.analysis.counter_buffer import buffered_mark_started
        from backend.services.core.event_bus import EventBus
        run_id = (kwargs or {}).get("run_id")
        if run_id:
            counts = buffered_mark_started(run_id, ca_row_id)
            EventBus.publish(
                "global",
                "analysis_started",
                {"message": "Task started", "run_id": run_id, **counts},
                enrich=False,
            )
    except Exception:
        logger.debug("run mark_started failed", exc_info=True)

    _t0 = time.monotonic()
    queue_lag_ms = ((time.time() - queued_at_ts) * 1000) if queued_at_ts else None

    try:
        # Main LLM call
        payload = ConversationAnalysisWorkflow.run_llm_only(
            convo_id=convo_id,
            chat_id=chat_id,
            ca_row_id=ca_row_id,
            encoded_text=encoded_text,
            **kwargs,
        )

        # Attach run_id so persist can finalize per-run counters
        run_id = (kwargs or {}).get("run_id")
        if run_id:
            try:
                payload["run_id"] = run_id
            except Exception:
                logger.debug("Failed to attach run_id to payload", exc_info=True)
        # Add perf-lite timings to payload for UI
        try:
            timings = (payload.get("llm_response") or {}).get("timings") or {}
            payload["timings"] = {
                "llm_ms": timings.get("llm_ms"),
                "rl_wait_ms": timings.get("rl_wait_ms"),
                "queue_lag_ms": queue_lag_ms,
            }
        except Exception:
            pass

        try:
            RuntimeMetrics.record_ca_task_finish((time.monotonic() - _t0) * 1000.0, ok=True)
        except Exception:
            logger.debug("metrics finish(success) failed", exc_info=True)

        return payload

    except Exception as exc:
        # IMPORTANT: balance runtime metrics inflight on failure
        try:
            RuntimeMetrics.record_ca_task_finish((time.monotonic() - _t0) * 1000.0, ok=False)
        except Exception:
            logger.debug("metrics finish(failure) failed", exc_info=True)

        # No more json_repair – rely on service-level grok-4 fallback and standard retry
        self.retry_with_backoff(exc)


@shared_task(bind=True, base=AnalyzeConversationTask, name="celery.ca.persist", ignore_result=True)
def persist_result_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        logger.error("[CA.PERSIST] Invalid/empty payload from upstream: %r", payload)
        return {"success": False, "error": "invalid upstream payload", "non_retryable": True}

    logger.info(
        "[CA.PERSIST] convo_id=%s chat_id=%s ca_row_id=%s",
        payload.get("convo_id"),
        payload.get("chat_id"),
        payload.get("ca_row_id"),
    )
    # Try bulk enqueue; fall back to direct persist on error
    _t_persist0 = time.monotonic()
    try:
        try:
            from backend.services.conversations.db_bulk_flusher import bulk_flusher
            bulk_flusher.start()
            bulk_flusher.enqueue(payload)
            result = {"success": True, "queued": True}
        except Exception as e:
            logger.error("Bulk enqueue failed, falling back to direct persist: %s", e, exc_info=True)
            result = ConversationAnalysisWorkflow.persist_only(payload)
        persist_ms = (time.monotonic() - _t_persist0) * 1000.0 if not result.get("queued") else None
        # Finalize per-run counters and publish update; if complete, emit run_complete
        try:
            from backend.services.analysis.counter_buffer import buffered_mark_finished, force_flush_all
            from backend.services.analysis.redis_counters import snapshot as _snap
            from backend.services.core.event_bus import EventBus
            run_id = (payload or {}).get("run_id")
            ca_row_id = (payload or {}).get("ca_row_id")
            if run_id and ca_row_id:
                counts = buffered_mark_finished(run_id, int(ca_row_id), ok=True)
                # Publish incremental completion update with counters (throttled)
                try:
                    publish_every = 0
                    try:
                        import os as _os
                        publish_every = int(_os.getenv("ANALYSIS_PROGRESS_PUBLISH_EVERY", "100"))
                    except Exception:
                        publish_every = 100
                    if publish_every <= 1 or (int(counts.get("success", 0)) + int(counts.get("failed", 0))) % publish_every == 0:
                        t = (payload or {}).get("timings") or {}
                        perf_fields = {
                            "timings_llm_ms": t.get("llm_ms"),
                            "timings_rl_wait_ms": t.get("rl_wait_ms"),
                            "timings_queue_lag_ms": t.get("queue_lag_ms"),
                            "timings_persist_ms": persist_ms,
                            "persist_queued": bool(result.get("queued", False)),
                        }
                        EventBus.publish(
                            "global",
                            "analysis_completed",
                            {"message": "Task completed", "run_id": run_id, **counts, **perf_fields},
                            enrich=False,
                        )
                except Exception:
                    logger.debug("analysis_completed publish failed", exc_info=True)
                total = int(counts.get("total", 0))
                processed = int(counts.get("success", 0)) + int(counts.get("failed", 0))
                if processed >= total and total > 0:
                    # Ensure bulk flusher has drained before finalize to avoid tail race
                    try:
                        from backend.services.conversations.db_bulk_flusher import bulk_flusher as _bf
                        _bf.wait_until_empty(timeout_ms=20000)
                    except Exception:
                        logger.debug("bulk_flusher wait_until_empty failed", exc_info=True)
                    force_flush_all()  # Ensure all counters are written
                    final = _snap(run_id)
                    EventBus.publish(
                        "global",
                        "run_complete",
                        {"run_id": run_id, **final, "message": "All tasks completed"},
                        enrich=False,
                    )
                    # Route finalize through DB queue to avoid multi-writer contention
                    try:
                        from backend.celery_service.tasks.db_control import historic_status_finalize as _final
                        _final.delay(run_id, int(counts.get("success", 0)), int(counts.get("failed", 0)))
                    except Exception:
                        logger.debug("enqueue historic_status_finalize failed", exc_info=True)
        except Exception:
            logger.debug("run_complete publish failed", exc_info=True)
        return result
    except Exception as exc:
        # Only log; progress has already been accounted for at the LLM stage
        logger.error("[CA.PERSIST] Failed for ca_row_id=%s: %s", payload.get("ca_row_id"), exc, exc_info=True)
        # Otherwise retry with backoff
        self.retry_with_backoff(exc)


# ---------------------------------------------------------------------------
# Parallel Encoding Optimization
# ---------------------------------------------------------------------------

def encode_one(conversation_data: Dict[str, Any]) -> str:
    """Encode a single conversation for analysis using Eve service."""
    try:
        import requests
        from backend.config import settings
        
        convo_id = conversation_data.get("convo_id")
        chat_id = conversation_data.get("chat_id")
        prompt_name = conversation_data.get("prompt_name", "ConvoAll")
        
        # Handle commitment encoding
        is_commitment = "commitment" in prompt_name.lower()
        if is_commitment:
            raise NotImplementedError(
                "Commitment analysis disabled - CommitmentEncodingService deleted during Eve migration"
            )
        
        # Use Eve encoding service (configurable base URL)
        base_url = getattr(settings, "eve_http_url", "http://127.0.0.1:3031").rstrip("/")
        resp = requests.post(
            f"{base_url}/engine/encode",
            json={'conversation_id': convo_id, 'chat_id': chat_id},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        encoded_text = data.get('encoded_text', '')
        
        if not encoded_text:
            raise ValueError(f"Eve encoding returned empty text for conversation {convo_id}")
        
        return encoded_text
        
    except Exception as e:
        logger.error(f"Failed to encode conversation {conversation_data.get('convo_id')}: {e}")
        return ""


@shared_task(name="celery.ca.encode_batch")
def encode_batch_task(conv_batch: List[Dict[str, Any]]) -> List[str]:
    """Encode multiple conversations in parallel."""
    logger.info(f"[CA.ENCODE_BATCH] Processing {len(conv_batch)} conversations")
    
    try:
        # Use ThreadPoolExecutor for parallel encoding
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(encode_one, c) for c in conv_batch]
            results = [f.result() for f in futures]
        
        logger.info(f"[CA.ENCODE_BATCH] Successfully encoded {len(results)} conversations")
        return results
    except Exception as e:
        logger.error(f"[CA.ENCODE_BATCH] Failed to encode batch: {e}", exc_info=True)
        # Fallback to sequential encoding
        return [encode_one(c) for c in conv_batch]