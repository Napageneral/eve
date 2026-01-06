"""
Ask Eve dynamic report generation task
"""
from backend.celery_service.app import celery_app
from backend.celery_service.tasks.base import BaseTaskWithDLQ
from backend.services.ask_eve.ask_eve import AskEveWorkflowService
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)

# Removed ~300 lines of embedded business logic. The task now delegates to AskEveWorkflowService.

# Task-specific defaults kept for future use if needed (not directly used here).


DEFAULT_MODEL = "gemini-2.5-pro-preview-05-06"  # kept for backwards compat
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TOKENS = 8000


# ---------------------------------------------------------------------------
# Lightweight Celery task wrapper
# ---------------------------------------------------------------------------


class AskEveTask(BaseTaskWithDLQ):
    """Inherits DLQ + retry behaviour from BaseTaskWithDLQ without extra overrides."""


@celery_app.task(bind=True, base=AskEveTask, name="celery.ask_eve")
def ask_eve_task(
    self,
    question: str,
    context_type: str,
    context_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    chat_ids: Optional[List[int]] = None,
    context_selection_id: Optional[int] = None,
    resolve_now: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    """Thin wrapper around AskEveWorkflowService.run."""

    logger.info(
        "[ASK-EVE-TASK] Processing question='%s' for %s=%s",
        question[:50],
        context_type,
        context_id,
    )

    try:
        with self.step("processing_question", 50, "Running Ask Eve workflow"):
            result = AskEveWorkflowService.run(
                question=question,
                context_type=context_type,
                context_id=context_id,
                start_date=start_date,
                end_date=end_date,
                chat_ids=chat_ids,
                context_selection_id=context_selection_id,
                resolve_now=resolve_now,
                **kwargs,
            )

        return result

    except Exception as exc:
        logger.error("Ask Eve failed: %s", exc, exc_info=True)
        self.retry_with_backoff(exc) 