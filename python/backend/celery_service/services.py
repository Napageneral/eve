"""
Service layer for submitting Celery tasks.
Replaces temporal/services.py functions.
"""
from typing import Optional, Dict, Any
from fastapi import HTTPException
from kombu.exceptions import OperationalError
from backend.celery_service.app import celery_app
from backend.celery_service.constants import ANALYSIS_QUEUE, BULK_QUEUE
from backend.celery_service.broker_health import broker_is_alive
from backend.services.conversations.bulk_workflow import BulkAnalysisWorkflowService
from backend.celery_service.tasks.analyze_conversation import analyze_conversation_task
from backend.celery_service.models.conversation import LLMConfig
from backend.repositories.conversation_analysis import ConversationAnalysisRepository
from backend.db.session_manager import new_session
import logging
import uuid

logger = logging.getLogger(__name__)

class StartedTaskInfo:
    """Information about a started task (like StartedWorkflowInfo)."""
    def __init__(self, task_id: str, conversation_analysis_id: Optional[int] = None, message: str = "", run_id: Optional[str] = None):
        self.task_id = task_id
        self.conversation_analysis_id = conversation_analysis_id
        self.message = message
        self.run_id = run_id
    
    def to_api(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "conversation_analysis_id": self.conversation_analysis_id,
            "message": self.message,
            "run_id": self.run_id,
        }

async def start_bulk_analysis(
    chat_id: int,
    user_id: int,
    llm_override: Optional[LLMConfig] = None,
    prompt_name: Optional[str] = None,
    prompt_version: Optional[int] = None,
    prompt_category: Optional[str] = None,
    auth_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    **kwargs  # Accept additional kwargs for compatibility
) -> StartedTaskInfo:
    """
    Start a bulk analysis job for all conversations in a chat.
    Replaces the Temporal version.
    """
    logger.info(f"Starting bulk analysis for chat_id: {chat_id}, user_id: {user_id}")
    
    # Check broker health before attempting to publish
    if not broker_is_alive(celery_app.conf.broker_url):
        logger.error("Message broker is unreachable")
        raise HTTPException(status_code=503, detail="Message broker unreachable")
    
    # Generate idempotency key if not provided
    final_idempotency_key = idempotency_key or str(uuid.uuid4())
    
    # Create the workflow
    bulk_workflow = BulkAnalysisWorkflowService.create_bulk_analysis_workflow(
        chat_id=chat_id,
        user_id=user_id,
        llm_config_override=llm_override.dict() if llm_override else None,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        prompt_category=prompt_category,
        auth_token=auth_token,
        pre_encode=True  # Pre-encode for better performance
    )
    
    if not bulk_workflow:
        return StartedTaskInfo(
            task_id="",
            message="No conversations found to analyze"
        )
    
    # Submit the group to Celery with proper error handling
    try:
        result = bulk_workflow.apply_async(
            queue=ANALYSIS_QUEUE,
            task_id=f"bulk-{chat_id}-{final_idempotency_key[:12]}"
        )
    except OperationalError as e:
        logger.exception("Failed to publish bulk analysis task")
        raise HTTPException(status_code=503, detail=f"Broker publish failed: {e}")
    
    logger.info(f"Started bulk analysis group: {result.id}")
    
    return StartedTaskInfo(
        task_id=result.id,
        message=f"Bulk analysis started for chat {chat_id}"
    )

def start_global_analysis(
    llm_override: Optional[LLMConfig] = None,
    prompt_name: Optional[str] = None,
    prompt_version: Optional[int] = None,
    prompt_category: Optional[str] = None,
    auth_token: Optional[str] = None,
    **kwargs,
) -> StartedTaskInfo:
    """
    Start global analysis for all unanalyzed conversations across all chats.
    """
    logger.info("Starting global analysis for all unanalyzed conversations")
    run_id = f"ga-{uuid.uuid4().hex[:12]}"

    # Fast UI flip: publish a lightweight initializing event (best effort)
    try:
        from backend.services.core.event_bus import EventBus
        EventBus.publish(
            "global",
            "run_starting",
            {"run_id": run_id, "message": "Initializing global analysis…", "percentage": 0, "status": "processing", "overall_status": "processing", "running": True},
            enrich=False,
        )
    except Exception:
        logger.debug("Failed to publish run_starting", exc_info=True)

    # Check broker health before attempting to publish
    if not broker_is_alive(celery_app.conf.broker_url):
        logger.error("Message broker is unreachable")
        raise HTTPException(status_code=503, detail="Message broker unreachable")

    # Create the global workflow (Celery group)
    global_workflow = BulkAnalysisWorkflowService.create_global_analysis_workflow(
        pre_encode=True,  # CHANGED: do all encoding upfront - saves 23k DB calls
        llm_config_override=llm_override.dict() if llm_override else None,
        prompt_name=prompt_name or "ConvoAll",
        prompt_version=prompt_version or 1,
        prompt_category=prompt_category or "conversation_analysis",
        auth_token=auth_token,
        publish_global=True,
        run_id=run_id,
        **kwargs,
    )

    if not global_workflow:
        # Publish completion event for empty case so UI can render immediately
        try:
            from backend.services.core.event_bus import EventBus
            EventBus.publish(
                "global",
                "run_complete",
                {
                    "run_id": run_id,
                    "message": "No conversations need analysis",
                    "total_convos": 0,
                    "successful_convos": 0,
                    "failed_convos": 0,
                    "pending_convos": 0,
                    "processing_convos": 0,
                    "percentage": 100,
                    "is_complete": True,
                    "status": "completed",
                    "overall_status": "completed",
                    "running": False,
                },
                enrich=False,
            )
        except Exception:
            logger.debug("Failed to publish run_complete for empty global workflow", exc_info=True)
        return StartedTaskInfo(
            task_id="",
            message="No conversations need analysis",
        )

    # Seed per-run counters BEFORE submitting tasks and publish initial event
    try:
        from backend.services.analysis.redis_counters import seed as seed_counters, snapshot as counters_snapshot
        from backend.services.analysis.redis_counters import seed_with_items as seed_items
        tasks = getattr(global_workflow, "tasks", None)
        ca_ids = getattr(global_workflow, "ca_ids", None)
        if ca_ids:
            seed_items(run_id, list(ca_ids))
            total_count = len(ca_ids)
        else:
            total_count = len(tasks) if tasks is not None else 0
            seed_counters(run_id, total_count)
        snap = counters_snapshot(run_id)

        from backend.services.core.event_bus import EventBus
        EventBus.publish(
            "global",
            "run_seeded",
            {
                "run_id": run_id,
                **snap,
                "message": f"Queued {total_count} conversations",
                "total_convos": total_count,           # explicit for UI
                "pending_convos": total_count,         # explicit for UI
                "processing_convos": 0,
                "successful_convos": 0,
                "failed_convos": 0,
                "percentage": 0,
                "is_complete": False,
                "status": "processing",
                "overall_status": "processing",
                "running": True,
            },
            enrich=False,
        )
    except Exception as e:
        logger.debug("Failed to seed counters", exc_info=True)
        try:
            from backend.services.core.event_bus import EventBus
            EventBus.publish(
                "global",
                "run_starting",
                {
                    "run_id": run_id,
                    "message": "Initializing global analysis…",
                    "percentage": 0,
                    "status": "processing",
                    "overall_status": "processing",
                    "running": True,
                },
                enrich=False,
            )
        except Exception:
            logger.debug("Failed to publish fallback run_starting", exc_info=True)

    # Submit the group to Celery AFTER seeding counters (no chord backend required)
    try:
        result = global_workflow.apply_async(task_id=f"global-{uuid.uuid4().hex[:12]}")
    except OperationalError as e:
        logger.exception("Failed to publish global analysis task")
        raise HTTPException(status_code=503, detail=f"Broker publish failed: {e}")

    try:
        emb_count = len(getattr(global_workflow, "embedding_sigs", []) or [])
        ca_count = len(getattr(global_workflow, "tasks", []) or [])
        logger.info("[SERVICES] Started global analysis group: %s | ca_tasks=%s emb_sigs=%s", result.id, ca_count, emb_count)
    except Exception:
        logger.info(f"Started global analysis group: {result.id}")

    # Best-effort started event containing task id
    try:
        from backend.services.core.event_bus import EventBus
        EventBus.publish("historic", "run_started", {"task_id": result.id, "run_id": run_id}, enrich=False)
    except Exception:
        logger.debug("Failed to publish run_started", exc_info=True)

    # No waiter – deterministic completion via counters

    # Embeddings backstop and parallel group enqueue disabled – per-conversation analysis embeddings are chained

    return StartedTaskInfo(
        task_id=result.id,
        message="Global analysis started",
        run_id=run_id,
    )

def start_ranked_analysis(
    chat_ids: list[int],
    llm_override: Optional[LLMConfig] = None,
    prompt_name: Optional[str] = None,
    prompt_version: Optional[int] = None,
    prompt_category: Optional[str] = None,
    auth_token: Optional[str] = None,
    **kwargs,
) -> StartedTaskInfo:
    """Start ranked analysis for a selected set of chats under a single run_id."""
    logger.info("Starting ranked analysis for %s chats", len(chat_ids) if chat_ids else 0)
    run_id = f"ra-{uuid.uuid4().hex[:12]}"

    # Fast UI flip: publish a lightweight initializing event
    try:
        from backend.services.core.event_bus import EventBus
        EventBus.publish(
            "global",
            "run_starting",
            {"run_id": run_id, "message": "Initializing ranked analysis…", "percentage": 0, "status": "processing", "overall_status": "processing", "running": True},
            enrich=False,
        )
    except Exception:
        logger.debug("Failed to publish ranked run_starting", exc_info=True)

    # Check broker
    if not broker_is_alive(celery_app.conf.broker_url):
        logger.error("Message broker is unreachable")
        raise HTTPException(status_code=503, detail="Message broker unreachable")

    # Assemble workflow
    ranked_workflow = BulkAnalysisWorkflowService.create_ranked_analysis_workflow(
        chat_ids,
        pre_encode=True,
        llm_config_override=llm_override.dict() if llm_override else None,
        prompt_name=prompt_name or "ConvoAll",
        prompt_version=prompt_version or 1,
        prompt_category=prompt_category or "conversation_analysis",
        auth_token=auth_token,
        publish_global=True,
        run_id=run_id,
        **kwargs,
    )

    if not ranked_workflow:
        try:
            from backend.services.core.event_bus import EventBus
            EventBus.publish(
                "global",
                "run_complete",
                {"run_id": run_id, "message": "No conversations need analysis", "total_convos": 0, "successful_convos": 0, "failed_convos": 0, "pending_convos": 0, "processing_convos": 0, "percentage": 100, "is_complete": True, "status": "completed", "overall_status": "completed", "running": False},
                enrich=False,
            )
        except Exception:
            logger.debug("Failed to publish ranked run_complete (empty)", exc_info=True)
        return StartedTaskInfo(task_id="", message="No conversations need analysis", run_id=run_id)

    # Seed counters BEFORE enqueue
    try:
        from backend.services.analysis.redis_counters import seed as seed_counters, seed_with_items as seed_items, snapshot as counters_snapshot
        ca_ids = getattr(ranked_workflow, "ca_ids", None)
        if ca_ids:
            seed_items(run_id, list(ca_ids))
            total_count = len(ca_ids)
        else:
            # Fallback if attachment failed
            seed_counters(run_id, 0)
            total_count = 0
        snap = counters_snapshot(run_id)
        from backend.services.core.event_bus import EventBus
        EventBus.publish(
            "global",
            "run_seeded",
            {"run_id": run_id, **snap, "message": f"Queued {total_count} conversations", "total_convos": total_count, "pending_convos": total_count, "processing_convos": 0, "successful_convos": 0, "failed_convos": 0, "percentage": 0, "is_complete": False, "status": "processing", "overall_status": "processing", "running": True},
            enrich=False,
        )
    except Exception:
        logger.debug("Failed to seed ranked counters", exc_info=True)

    # Enqueue the group now
    try:
        result = ranked_workflow.apply_async(task_id=f"ranked-{uuid.uuid4().hex[:12]}")
    except OperationalError as e:
        logger.exception("Failed to publish ranked analysis task")
        raise HTTPException(status_code=503, detail=f"Broker publish failed: {e}")

    try:
        emb_count = len(getattr(ranked_workflow, "embedding_sigs", []) or [])
        ca_count = len(getattr(ranked_workflow, "tasks", []) or [])
        logger.info("[SERVICES] Started ranked analysis group: %s | ca_tasks=%s emb_sigs=%s", result.id, ca_count, emb_count)
    except Exception:
        logger.info("Started ranked analysis group: %s", result.id)
    try:
        from backend.services.core.event_bus import EventBus
        EventBus.publish("historic", "run_started", {"task_id": result.id, "run_id": run_id}, enrich=False)
    except Exception:
        logger.debug("Failed to publish ranked run_started", exc_info=True)

    # Embeddings backstop and parallel group enqueue disabled – per-conversation analysis embeddings are chained

    return StartedTaskInfo(task_id=result.id, message="Ranked analysis started", run_id=run_id)

async def trigger_single_analysis(
    conversation_id: int,
    chat_id: int,
    user_id: int,
    prompt_name: str,
    prompt_version: int,
    prompt_category: str,
    llm_override: Optional[LLMConfig] = None,
    auth_token: Optional[str] = None,
    ca_record_id: Optional[int] = None,
    **kwargs  # Accept additional kwargs for compatibility
) -> StartedTaskInfo:
    """
    Trigger analysis for a single conversation.
    Replaces the Temporal version.
    """
    logger.info(f"Triggering single analysis for conversation: {conversation_id}")
    
    # Check broker health before attempting to publish
    if not broker_is_alive(celery_app.conf.broker_url):
        logger.error("Message broker is unreachable")
        raise HTTPException(status_code=503, detail="Message broker unreachable")
    
    # Prepare the CA record
    with new_session() as session:
        if ca_record_id:
            ca_id = ca_record_id
        else:
            try:
                # All prompts now managed by Eve - prepare with eve_prompt_id instead
                # Legacy path: use trigger_analysis_pass() for new analyses
                logger.warning(
                    f"Legacy analysis path used for conversation {conversation_id}. "
                    f"Use trigger_analysis_pass() instead which handles eve_prompt_id."
                )
                # Create CA record without prompt_template_id (will use eve_prompt_id in modern path)
                ca_id = ConversationAnalysisRepository.prepare_for_analysis(
                    session, conversation_id, prompt_template_id=None, eve_prompt_id="convo-all-v1"
                )
            except Exception as e:
                logger.error(f"Failed to prepare analysis for conversation {conversation_id}: {str(e)}")
                raise
        
        # Update with task ID once we have it
        task_kwargs = {
            'llm_config_override': llm_override.dict() if llm_override else None,
            'prompt_name': prompt_name,
            'prompt_version': prompt_version,
            'prompt_category': prompt_category,  # Add prompt_category to task kwargs
            'auth_token': auth_token
        }
        
        # Submit the task with proper error handling
        try:
            result = analyze_conversation_task.apply_async(
                args=[conversation_id, chat_id, ca_id],
                kwargs=task_kwargs,
                queue=ANALYSIS_QUEUE,
                task_id=f"conv-{chat_id}-{conversation_id}-{uuid.uuid4().hex[:8]}"
            )
        except OperationalError as e:
            logger.exception("Failed to publish single analysis task")
            raise HTTPException(status_code=503, detail=f"Broker publish failed: {e}")
        
        # Update CA record with task ID (instead of workflow ID)
        ConversationAnalysisRepository.update_temporal_workflow_id(session, ca_id, result.id)
        session.commit()
    
    logger.info(f"Started analysis task: {result.id} for CA_ID: {ca_id}")
    
    return StartedTaskInfo(
        task_id=result.id,
        conversation_analysis_id=ca_id,
        message=f"Analysis started for conversation {conversation_id}"
    )

def build_llm_override(model_name: Optional[str], temperature: Optional[float], 
                      max_tokens: Optional[int]) -> Optional[LLMConfig]:
    """
    Build LLM override configuration from individual parameters.
    Maintains compatibility with temporal services.
    """
    if any([model_name, temperature, max_tokens]):
        return LLMConfig(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens
        )
    return None
