from celery import Task
import logging
import time
from typing import Optional, Dict, Any
from contextlib import contextmanager
import os
import random

from backend.services.infra.dlq import DLQService
from backend.services.core.event_bus import EventBus


class BaseTaskWithDLQ(Task):
    """Shared base Task providing DLQ support, retries, progress + lifecycle events, and timing helpers."""

    # Automatically retry on *any* uncaught exception
    autoretry_for = (Exception,)

    # Number of times we will retry before marking as permanently failed
    # Increased to 120+ for extreme network resilience over 24 hours
    max_retries = 120

    # Delay (seconds) - NOT USED, see retry_with_backoff for custom schedule
    default_retry_delay = 20

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(f"{self.__module__}.{self.__class__.__name__}")

    # ------------------------------------------------------------------
    # Celery lifecycle hooks
    # ------------------------------------------------------------------
    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: N802 – Celery API
        """Invoked by Celery when the task has failed and will not be retried again."""
        # Always push to DLQ for visibility / manual re-queue
        self.logger.error(
            "Task %s failed after %s retries: %s", task_id, self.request.retries, exc,
        )
        DLQService.store_failed_task(
            task_id=task_id,
            task_name=self.name,
            args=args,
            kwargs=kwargs,
            error_msg=str(exc),
            queue_name=getattr(self.request, "delivery_info", {}).get("routing_key")
                       or getattr(self.request, "hostname", None)
                       or "unknown",
        )
        self._publish_failure_event(task_id, exc, permanent=True)
        try:
            # Also push a simple status flag so UIs can flip state immediately
            EventBus.publish(
                scope=f"task:{task_id}",
                event_type="status",
                data={"task_id": task_id, "status": "failed", "error": str(exc)},
            )
        except Exception:
            pass

        # Finalize per-run counters (move any lingering 'processing' → 'failed') and possibly emit run_complete
        try:
            from backend.services.analysis.counter_buffer import buffered_mark_finished, force_flush_all
            from backend.services.analysis.redis_counters import snapshot as _snap
            from backend.services.core.event_bus import EventBus as _EB
            from backend.celery_service.tasks.db_control import historic_status_finalize as _finalize

            run_id = None
            ca_row_id = None

            # call_llm_task / analyze_conversation: args = [convo_id, chat_id, ca_row_id]; kwargs: { 'run_id': ... }
            if isinstance(kwargs, dict):
                run_id = kwargs.get("run_id")
            if isinstance(args, (list, tuple)) and len(args) >= 3 and isinstance(args[2], (int, str)):
                try:
                    ca_row_id = int(args[2])
                except Exception:
                    ca_row_id = None

            # persist_result_task: args = [payload_dict]
            if (run_id is None or ca_row_id is None) and args and isinstance(args[0], dict):
                payload = args[0]
                run_id = run_id or payload.get("run_id") or (payload.get("kwargs") or {}).get("run_id")
                _cid = payload.get("ca_row_id")
                try:
                    ca_row_id = ca_row_id or (int(_cid) if _cid is not None else None)
                except Exception:
                    ca_row_id = None

            if run_id and ca_row_id is not None:
                counts = buffered_mark_finished(str(run_id), int(ca_row_id), ok=False)
                total = int(counts.get("total", 0))
                processed = int(counts.get("success", 0)) + int(counts.get("failed", 0))
                if processed >= total and total > 0:
                    force_flush_all()  # Ensure all counters are written before completion
                    final = _snap(str(run_id))
                    _EB.publish(
                        "global",
                        "run_complete",
                        {"run_id": run_id, **final, "message": "Run completed with failures"},
                        enrich=False,
                    )
                    # Also finalize persistent historic status via DB queue so UI can reflect completion
                    try:
                        _finalize.delay(str(run_id), int(counts.get("success", 0)), int(counts.get("failed", 0)))
                    except Exception:
                        pass
        except Exception:
            # never crash on telemetry
            pass

        # Best-effort: update ConversationAnalysis row status to 'failed'
        try:
            from backend.db.session_manager import new_session as _new_session
            from backend.repositories.conversation_analysis import ConversationAnalysisRepository as _CAR
            ca_id = None
            if isinstance(args, (list, tuple)) and len(args) >= 3 and isinstance(args[2], (int, str)):
                try:
                    ca_id = int(args[2])
                except Exception:
                    ca_id = None
            if ca_id is not None:
                with _new_session() as _s:
                    try:
                        _CAR.update_status(_s, ca_id, "failed", error_message=str(exc))
                    except Exception:
                        pass
        except Exception:
            pass

    def on_retry(self, exc, task_id, args, kwargs, einfo):  # noqa: N802 – Celery API
        """Invoked by Celery right *before* a retry is scheduled."""
        retry_count = self.request.retries
        self.logger.warning(
            "Task %s retry %s/%s – %s", task_id, retry_count, self.max_retries, exc,
        )
        self._publish_retry_event(task_id, exc, retry_count)
        try:
            EventBus.publish(
                scope=f"task:{task_id}",
                event_type="status",
                data={"task_id": task_id, "status": "retrying", "error": str(exc), "retry_count": retry_count},
            )
        except Exception:
            pass

    def on_success(self, retval, task_id, args, kwargs):  # noqa: N802 – Celery API
        """Invoked by Celery when the task finishes successfully."""
        self.logger.debug("Task %s completed successfully", task_id)
        # Automatically emit completion telemetry
        # Re-use existing helper to keep event schema consistent
        self.publish_completion(retval)

    # ------------------------------------------------------------------
    # Helper: progress / completion / failure events -----------------------------------------------
    # ------------------------------------------------------------------
    def publish_progress(self, progress: int, status: str, message: str = "", **extra_data):
        """Emit a *transient* progress update for this task via the event bus."""
        try:
            EventBus.publish(
                scope=f"task:{self.request.id}",
                event_type="progress",
                data={
                    "task_id": self.request.id,
                    "progress": int(progress),
                    "status": status,
                    "message": message,
                    **extra_data,
                },
            )
        except Exception as e:  # pragma: no cover – never crash because of telemetry
            self.logger.debug("Failed to publish progress event: %s", e)

    def publish_completion(self, result: Dict[str, Any], message: str = "Task completed successfully"):
        """Emit a completion event once the task finishes successfully."""
        try:
            EventBus.publish(
                scope=f"task:{self.request.id}",
                event_type="completed",
                data={
                    "task_id": self.request.id,
                    "message": message,
                    "result": result,
                },
            )
        except Exception as e:  # pragma: no cover
            self.logger.debug("Failed to publish completion event: %s", e)

    # Internal helpers ---------------------------------------------------------------------------
    def _publish_failure_event(self, task_id: str, exc: Exception, permanent: bool = False):
        try:
            EventBus.publish(
                scope=f"task:{task_id}",
                event_type="failed",
                data={
                    "task_id": task_id,
                    "error": str(exc),
                    "permanent": permanent,
                },
            )
        except Exception as e:  # pragma: no cover
            self.logger.debug("Failed to publish failure event: %s", e)

    def _publish_retry_event(self, task_id: str, exc: Exception, retry_count: int):
        try:
            EventBus.publish(
                scope=f"task:{task_id}",
                event_type="retry",
                data={
                    "task_id": task_id,
                    "error": str(exc),
                    "retry_count": retry_count,
                    "max_retries": self.max_retries,
                },
            )
        except Exception as e:  # pragma: no cover
            self.logger.debug("Failed to publish retry event: %s", e)

    # ------------------------------------------------------------------
    # Progress helper context manager --------------------------------------------------------------
    # ------------------------------------------------------------------
    @contextmanager
    def step(self, stage: str, progress_pct: int, message: Optional[str] = None):
        """Convenience context manager to wrap a work stage.

        Example::

            with self.step("encoding", 25):
                encode_stuff()

        When entering, we emit a *PROGRESS* state update via Celery and our event bus.
        Any exception raised inside the context will propagate normally so outer retry
        logic can handle it.
        """

        if message is None:
            message = f"Processing {stage}"

        try:
            # Update Celery task meta so callers polling get_state can see progress
            self.update_state(
                state="PROGRESS",
                meta={
                    "stage": stage,
                    "progress": int(progress_pct),
                    "message": message,
                },
            )

            # Publish via websocket/event bus for UI consumers
            self.publish_progress(progress_pct, stage, message)

            yield  # Execute the wrapped block

        except Exception:
            # Let caller handle/log but ensure we always re-raise so Celery marks failure
            self.logger.exception("Error in stage %s", stage)
            raise


    # ------------------------------------------------------------------
    # Retry helper ---------------------------------------------------------------------------------
    # ------------------------------------------------------------------
    def retry_with_backoff(self, exc: Exception):
        """Apply custom backoff schedule optimized for flaky network conditions.
        
        Schedule designed for poor connectivity/low bandwidth:
        - Retries 1-6: Every 20s (first 2 minutes - fast recovery)
        - Retries 7-25: Every 60s (next ~20 minutes - medium persistence)
        - Retries 26+: Every 15min (next 24+ hours - long-term resilience)
        """
        
        retry_num = self.request.retries + 1
        
        # Custom backoff schedule for network resilience
        if retry_num <= 6:
            # First 2 minutes: Retry every 20 seconds
            countdown = 20
        elif retry_num <= 25:
            # Next 20 minutes: Retry every minute
            countdown = 60
        else:
            # Next 24+ hours: Retry every 15 minutes
            countdown = 900  # 15 minutes
        
        # Add jitter to avoid thundering herd
        countdown += random.randint(0, 10)
        
        self.logger.warning(
            "Retrying %s in %ds (attempt %d/%d): %s",
            self.name,
            countdown,
            retry_num,
            self.max_retries,
            exc,
        )

        raise self.retry(exc=exc, countdown=countdown)

    # ------------------------------------------------------------------
    # Misc helpers --------------------------------------------------------------------------------
    # ------------------------------------------------------------------
    @staticmethod
    def log_performance(operation: str, start_time: float) -> float:
        """Helper to log elapsed time for *operation* and return the value."""
        elapsed = time.time() - start_time
        logging.getLogger(__name__).info("%s completed in %.2fs", operation, elapsed)
        return elapsed 