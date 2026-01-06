from __future__ import annotations

import logging
from celery import shared_task
from backend.celery_service.tasks.base import BaseTaskWithDLQ
from backend.db.session_manager import new_session
from backend.services.conversations.analysis import ConversationAnalysisService
# NOTE: Commitments are disabled (analysis_passes.py has enabled=False)
# CommitmentEncodingService was deleted as part of Eve migration
# When commitments are re-enabled, encoding will use Eve service
from backend.services.commitments import CommitmentService

logger = logging.getLogger(__name__)

@shared_task(
    bind=True,
    base=BaseTaskWithDLQ,
    name="celery.process_commitment_analysis",
    max_retries=3,
    default_retry_delay=60,
)
def process_commitment_analysis_task(
    self,
    conversation_id: int,
    chat_id: int,
    *,
    is_realtime: bool = True,
):
    """Analyze commitments for one conversation (Stage 1 extraction → Stage 2 reconciliation).

    Args:
        conversation_id: ID of the conversation to analyze.
        chat_id: Chat that owns the conversation.
        is_realtime: Whether to publish live SSE events (defaults to ``True``).
    Returns:
        Dict summarising extraction counts, actions taken, and DB changes – the
        same shape returned by ``CommitmentService.analyze_conversation_commitments``.
    """

    logger.info(
        "[COMMIT-TASK] Starting commitment analysis convo=%s chat=%s realtime=%s",
        conversation_id,
        chat_id,
        is_realtime,
    )

    try:
        with new_session() as session:
            # NOTE: Commitments are disabled - if somehow triggered, raise error
            # TODO: When re-enabling, use Eve service for encoding
            raise NotImplementedError(
                "Commitment analysis is currently disabled. "
                "CommitmentEncodingService was deleted during Eve migration. "
                "To re-enable, implement commitment encoding in Eve service."
            )
            
            # OLD CODE (commented out for reference):
            # convo_data = ConversationAnalysisService.load_conversation(conversation_id, chat_id)
            # encoded_conv = CommitmentEncodingService.encode_conversation_for_commitments(
            #     convo_data, chat_id, is_realtime=is_realtime
            # )

            # Run full two-stage pipeline
            svc = CommitmentService()
            result = svc.analyze_conversation_commitments(
                session,
                conversation_id,
                chat_id,
                encoded_conv,
                is_realtime=is_realtime,
            )

            session.commit()
            logger.info(
                "[COMMIT-TASK] Completed commitment analysis convo=%s status=%s",
                conversation_id,
                result.get("status"),
            )
            return result

    except Exception as exc:
        logger.error(
            "[COMMIT-TASK] Failure convo=%s chat=%s error=%s",
            conversation_id,
            chat_id,
            exc,
            exc_info=True,
        )
        # Let BaseTaskWithDLQ handle retries / DLQ
        self.retry_with_backoff(exc)