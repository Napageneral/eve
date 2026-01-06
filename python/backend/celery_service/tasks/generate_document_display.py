from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from backend.celery_service.app import celery_app
from backend.celery_service.tasks.base import BaseTaskWithDLQ
from backend.services.chatbot.document_display import DocumentDisplayWorkflowService

logger = logging.getLogger(__name__)


class GenerateDocumentDisplayTask(BaseTaskWithDLQ):
    """Celery task that generates a display for a chatbot document."""


@celery_app.task(bind=True, base=GenerateDocumentDisplayTask, name="celery.generate_document_display")
def generate_document_display_task(
    self,
    document_id: str,
    document_created_at: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    logger.info("[DOC-DISPLAY-TASK] Generating display for document_id=%s created_at=%s", document_id, document_created_at)

    # Parse created_at if provided
    created_at_dt: Optional[datetime] = None
    if document_created_at:
        try:
            created_at_dt = datetime.fromisoformat(document_created_at.replace('Z', '+00:00'))
        except Exception:
            created_at_dt = None

    llm_override: Dict[str, Any] = {}
    if model is not None:
        llm_override["model_name"] = model
    if temperature is not None:
        llm_override["temperature"] = temperature
    if max_tokens is not None:
        llm_override["max_tokens"] = max_tokens

    try:
        with self.step("generating_document_display", 50):
            result = DocumentDisplayWorkflowService.generate(
                document_id=document_id,
                document_created_at=created_at_dt,
                llm_override=llm_override or None,
            )
        return result
    except Exception as exc:
        logger.error("Document display generation failed: %s", exc, exc_info=True)
        self.retry_with_backoff(exc)


