import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple

from celery import shared_task, chain
from backend.celery_service.tasks.base import BaseTaskWithDLQ

from backend.db.session_manager import new_session
from backend.services.commitments import CommitmentService
# NOTE: Commitments are disabled - CommitmentEncodingService deleted during Eve migration
# from backend.services.encoding import CommitmentEncodingService
from backend.services.commitments.commitment_history_workflow import HistoricalCommitmentWorkflowService


logger = logging.getLogger(__name__)

# raw SQL helper removed – conversation listing now lives in ConversationRepository.list_for_history()

# ---------------------------------------------------------------------------
# Atomic tasks used in the chain
# ---------------------------------------------------------------------------

@shared_task(name="celery.initialize_historical_analysis", base=BaseTaskWithDLQ)
def initialize_analysis(scope: str, total: int):
    from backend.services.conversations.analysis import ConversationAnalysisService
    logger.info(f"[HIST/INIT] Initializing analysis – scope={scope}, total={total}")
    ConversationAnalysisService.publish_analysis_event(
        scope,
        "started",
        {"total": total, "timestamp": datetime.utcnow().isoformat()},
    )
    return {"initialized": True, "total": total}


@shared_task(name="celery.process_conversation", base=BaseTaskWithDLQ)
def process_conversation(conv_id: int, conv_chat_id: int, index: int, total: int, scope: str):
    from backend.services.conversations.analysis import ConversationAnalysisService
    logger.info(
        f"[HIST/PROC] {index+1}/{total} – conversation {conv_id} (chat {conv_chat_id})"
    )
    try:
        with new_session() as session:
            # NOTE: Commitments disabled - raise error if somehow triggered
            raise NotImplementedError(
                "Commitment analysis is currently disabled. "
                "CommitmentEncodingService was deleted during Eve migration. "
                "To re-enable, implement commitment encoding in Eve service."
            )
            
            # OLD CODE (for reference):
            # conv_data = ConversationAnalysisService.load_conversation(conv_id, conv_chat_id)
            # encoded = CommitmentEncodingService.encode_conversation_for_commitments(
            #     conv_data, conv_chat_id, is_realtime=False
            # )
            # service = CommitmentService()
            # service.process_conversation_commitments(
            #     session, conv_id, conv_chat_id, encoded, is_realtime=False
            # )
            # session.commit()

        progress = ((index + 1) / total) * 100 if total else 100
        ConversationAnalysisService.publish_analysis_event(
            scope,
            "progress",
            {
                "conversation_id": conv_id,
                "index": index + 1,
                "total": total,
                "progress": progress,
            },
        )
        return {"conversation_id": conv_id, "status": "success"}
    except Exception as exc:
        logger.error(f"[HIST/PROC] Failed conv {conv_id}: {exc}", exc_info=True)
        ConversationAnalysisService.publish_analysis_event(
            scope,
            "error",
            {"conversation_id": conv_id, "error": str(exc)},
        )
        raise


@shared_task(name="celery.finalize_historical_analysis", base=BaseTaskWithDLQ)
def finalize_analysis(scope: str, total: int):
    from backend.services.conversations.analysis import ConversationAnalysisService
    logger.info(f"[HIST/FINAL] Completed historical analysis – scope={scope}")
    ConversationAnalysisService.publish_analysis_event(
        scope,
        "completed",
        {"total": total, "timestamp": datetime.utcnow().isoformat()},
    )
    return {"status": "completed", "total": total}

@shared_task(name="celery.process_single_historical_conversation", base=BaseTaskWithDLQ)

def process_single_historical_conversation(conv_id: int, conv_chat_id: int, index: int, total: int, scope: str):
    """Backward-compatibility wrapper that delegates to process_conversation."""
    return process_conversation(conv_id, conv_chat_id, index, total, scope)

# ---------------------------------------------------------------------------
# Entrypoint – builds the chain and launches it
# ---------------------------------------------------------------------------


@shared_task(bind=True, base=BaseTaskWithDLQ, name="celery.analyze_historical_commitments")
def analyze_historical_commitments_task(self, chat_id: Optional[int] = None):
    """Driver task: constructs chain of per-conversation tasks for durability."""
    from backend.services.conversations.analysis import ConversationAnalysisService

    analysis_chain, scope, total = HistoricalCommitmentWorkflowService.build_chain(chat_id)

    if not analysis_chain:
        logger.warning("[HIST] No conversations found – aborting")
        ConversationAnalysisService.publish_analysis_event(scope, "completed", {"total": 0})
        return {"status": "no_conversations", "total": 0}

    result = analysis_chain.apply_async()
    logger.info("[HIST] Chain submitted – ID=%s tasks=%s", result.id, total)
    return {"status": "started", "chain_id": result.id, "total": total, "scope": scope} 