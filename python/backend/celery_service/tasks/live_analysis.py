"""
Live analysis task for triggering analysis when new messages are synced.
Replaces the unreliable Redis pub/sub event system with reliable Celery tasks.
"""
from celery import shared_task
from backend.celery_service.tasks.base import BaseTaskWithDLQ
from backend.celery_service.analysis_passes import trigger_analysis_pass, get_live_passes
from backend.db.session_manager import new_session
from backend.repositories.conversations import ConversationRepository
from datetime import datetime, timedelta
import logging
import time

logger = logging.getLogger("backend.celery.live_analysis")

@shared_task(bind=True, name='celery.handle_new_messages_synced', base=BaseTaskWithDLQ, ignore_result=True)
def handle_new_messages_synced_task(self, chat_counts: dict, conversation_ids: list, timestamp: str):
    """
    Celery task to handle new messages and trigger live analysis.
    """
    started = time.perf_counter()
    task_id = getattr(self.request, 'id', None)
    logger.debug(
        "[LiveAnalysis] start: chats=%d convs=%d task_id=%s",
        len(chat_counts or {}),
        len(conversation_ids or []),
        task_id,
    )
    
    try:
        live_passes = get_live_passes()
        logger.debug(f"[LiveAnalysis] Enabled live passes: {list(live_passes.keys())}")
        
        if not live_passes:
            logger.warning("[LiveAnalysis] No live passes configured!")
            return {"status": "no_passes", "passes_available": 0}
        
        triggered_tasks = []
        
        if conversation_ids:
            logger.debug(f"[LiveAnalysis] Processing specific conversation IDs: {conversation_ids}")
            with new_session() as session:
                for conv_id in conversation_ids:
                    conv = ConversationRepository.get_by_id(session, conv_id)
                    if conv:
                        for pass_name, config in live_passes.items():
                            try:
                                sub_id = trigger_analysis_pass(conv_id, conv["chat_id"], pass_name)
                                if sub_id:
                                    triggered_tasks.append({"pass": pass_name, "conversation_id": conv_id, "task_id": sub_id})
                            except Exception as e:
                                logger.error(f"[LiveAnalysis] Failed to trigger '{pass_name}' for conversation {conv_id}: {e}", exc_info=True)
                    else:
                        logger.error(f"[LiveAnalysis] Conversation {conv_id} not found in database!")
        else:
            logger.debug("[LiveAnalysis] No conversation IDs provided, using fallback method")
            with new_session() as session:
                for chat_id in chat_counts.keys():
                    recent_convs = ConversationRepository.list_recent_for_chat(session, chat_id, minutes=5)
                    for conv in recent_convs:
                        for pass_name, config in live_passes.items():
                            try:
                                sub_id = trigger_analysis_pass(conv["id"], chat_id, pass_name)
                                if sub_id:
                                    triggered_tasks.append({"pass": pass_name, "conversation_id": conv["id"], "task_id": sub_id})
                            except Exception as e:
                                logger.error(f"[LiveAnalysis] Failed to trigger '{pass_name}' for conversation {conv['id']}: {e}", exc_info=True)
        
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        logger.debug(
            "[LiveAnalysis] ok: tasks=%d elapsed_ms=%d task_id=%s",
            len(triggered_tasks),
            elapsed_ms,
            task_id,
        )
        return {
            "status": "success",
            "triggered_tasks": triggered_tasks,
            "total_tasks": len(triggered_tasks),
            "timestamp": timestamp,
            "elapsed_ms": elapsed_ms,
        }
        
    except Exception as e:
        logger.error(f"[LiveAnalysis] error: {e}", exc_info=True)
        raise  # Celery will handle retries 