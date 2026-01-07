"""
Define analysis passes that can be run on conversations
"""
from typing import Dict, Any, Optional, List
# Repo imports
from backend.db.session_manager import db
from backend.db.models import ConversationAnalysis, PromptTemplate
import logging

logger = logging.getLogger(__name__)

# Define available analysis passes
ANALYSIS_PASSES = {
    # LIVE PASSES (run on every message)
    "commitments_live": {
        "prompt_name": "CommitmentExtractionLive",  # Legacy DB name (for tracking only)
        "prompt_version": 1,
        "prompt_category": "conversation_analysis",
        "eve_prompt_id": "commitment-extraction-live-v2",  # Actual prompt loaded from Eve
        "description": "Real-time commitment extraction for live conversations",
        "priority": 1,
        "enabled": False,  # DISABLED - half-baked commitment code
        "pass_type": "live"
    },
    # Add a live variant of the basic pass so it runs on each ingestion
    "basic_live": {
        "prompt_name": "ConvoAll",  # Legacy DB name (for tracking only)
        "prompt_version": 1,
        "prompt_category": "conversation_analysis",
        "eve_prompt_id": "convo-all-v1",  # Actual prompt loaded from Eve
        "description": "Live basic extraction for conversations (entities, topics, etc.)",
        "priority": 2,
        "enabled": True,  # ENABLED - working
        "pass_type": "live"
    },
    
    # BATCH PASSES (run on sealed conversations)
    "basic": {
        "prompt_name": "ConvoAll",  # Legacy DB name (for tracking only)
        "prompt_version": 1,
        "prompt_category": "conversation_analysis",
        "eve_prompt_id": "convo-all-v1",  # Actual prompt loaded from Eve
        "description": "Basic extraction of entities, topics, emotions, humor",
        "priority": 1,
        "enabled": True,  # ENABLED - working
        "pass_type": "batch"
    }
}

def get_live_passes() -> Dict[str, Dict[str, Any]]:
    """Get all enabled live analysis passes"""
    live_passes = {k: v for k, v in ANALYSIS_PASSES.items() 
                   if v.get("enabled", True) and v.get("pass_type") == "live"}
    names = sorted(list(live_passes.keys()))
    logger.info("[CA.TRIGGER] live passes enabled count=%d names=%s", len(names), names)
    return live_passes

def get_batch_passes() -> Dict[str, Dict[str, Any]]:
    """Get all enabled batch analysis passes"""
    return {k: v for k, v in ANALYSIS_PASSES.items() 
            if v.get("enabled", True) and v.get("pass_type", "batch") == "batch"}

def get_enabled_passes() -> Dict[str, Dict[str, Any]]:
    """Get all enabled analysis passes"""
    return {k: v for k, v in ANALYSIS_PASSES.items() if v.get("enabled", True)}

def get_pending_passes(session, conversation_id: int, user_id: Optional[int] = None) -> List[str]:
    """Return list of analysis pass names that still need to run for a conversation."""
    
    from backend.repositories.conversation_analysis import ConversationAnalysisRepository

    # Get completed eve_prompt_ids for this conversation
    completed_eve_ids = set(
        ConversationAnalysisRepository.list_completed_eve_prompt_ids_for_conversation(
            session, conversation_id
        )
    )

    pending: List[str] = []
    enabled_passes = get_enabled_passes()

    for pass_name, cfg in enabled_passes.items():
        eve_prompt_id = cfg.get("eve_prompt_id")
        # All passes should have eve_prompt_id now
        if eve_prompt_id and eve_prompt_id not in completed_eve_ids:
            pending.append(pass_name)

    pending.sort(key=lambda n: enabled_passes[n].get("priority", 999))
    return pending

def get_pass_config(pass_name: str) -> Optional[Dict[str, Any]]:
    """Get configuration for a specific pass"""
    return ANALYSIS_PASSES.get(pass_name)

def trigger_analysis_pass(conversation_id: int, chat_id: int, pass_name: str):
    """
    Trigger a specific analysis pass for a conversation.
    
    This is the ONLY way analysis should be triggered.
    """
    logger.info("[CA.TRIGGER] pass=%s convo_id=%s chat_id=%s", pass_name, conversation_id, chat_id)
    
    if pass_name not in ANALYSIS_PASSES:
        logger.error("[CA.TRIGGER] Unknown pass: %s", pass_name)
        raise ValueError(f"Unknown analysis pass: {pass_name}")
    
    config = ANALYSIS_PASSES[pass_name]
    
    if not config.get("enabled", True):
        logger.error("[CA.TRIGGER] pass disabled skip pass=%s convo_id=%s", pass_name, conversation_id)
        return None
    
    # Import here to avoid circular dependency
    from celery import chain as _chain
    import time as _time
    from backend.celery_service.app import get_celery_app
    from backend.db.session_manager import new_session
    from backend.repositories.conversation_analysis import ConversationAnalysisRepository
    
    # Create CA record first (using eve_prompt_id for new Eve-based tracking)
    with new_session() as session:
        try:
            # All prompts now managed by Eve - eve_prompt_id is required
            eve_prompt_id = config.get("eve_prompt_id")
            if not eve_prompt_id:
                logger.error(
                    "[CA.TRIGGER] missing eve_prompt_id pass=%s convo_id=%s",
                    pass_name,
                    conversation_id,
                )
                return None
            
            prompt_template_id = None  # Legacy field, always None for Eve prompts

            # Check for existing analysis (by eve_prompt_id or template_id)
            existing = ConversationAnalysisRepository.get_by_conversation_and_prompt(
                session, conversation_id, prompt_template_id, eve_prompt_id
            )

            is_live = config.get("pass_type") == "live"
            if existing:
                status = (existing.get("status") or "").lower()
                if is_live and status == "success":
                    ConversationAnalysisRepository.update_status(session, existing["id"], "pending")
                    session.commit()
                    logger.info("[CA.TRIGGER] reset existing live analysis to pending pass=%s convo_id=%s", pass_name, conversation_id)
                elif status in ["processing", "success"]:
                    logger.info(
                        "[CA.TRIGGER] skip enqueue (already %s) pass=%s convo_id=%s",
                        status,
                        pass_name,
                        conversation_id,
                    )
                    return None

            ca_row_id = ConversationAnalysisRepository.prepare_for_analysis(
                session, conversation_id, prompt_template_id, eve_prompt_id
            )
            session.commit()
        except Exception as exc:
            logger.error("[CA.TRIGGER] fail prepare pass=%s convo_id=%s err=%s", pass_name, conversation_id, exc, exc_info=True)
            return None
    
    # Check if Celery is available
    try:
        from backend.celery_service.app import celery_app
        celery_available = True
    except Exception as e:
        logger.error("[CA.TRIGGER] Celery app not available: %s", e)
        celery_available = False
    
    if not celery_available:
        logger.error("[CA.TRIGGER] Cannot enqueue analysis (celery unavailable) pass=%s convo_id=%s", pass_name, conversation_id)
        return None
    
    # Use the two-stage CA tasks: network-heavy LLM call (analysis queue) → DB persist (db queue)
    try:
        app = get_celery_app()
        sig_call = app.signature(
            "celery.ca.call_llm",
            args=[
                conversation_id,
                chat_id,
                ca_row_id,
                None,  # encoded_text
                _time.time(),  # queued_at_ts (for queue lag metrics)
            ],
            kwargs={
                "prompt_name": config["prompt_name"],
                "prompt_version": config["prompt_version"],
                "prompt_category": config["prompt_category"],
            },
        ).set(queue="chatstats-analysis")
        sig_persist = app.signature("celery.ca.persist").set(queue="chatstats-db")
        workflow = _chain(sig_call, sig_persist)
        result = workflow.apply_async()
        logger.info(
            "[CA.TRIGGER] enqueued pass=%s convo_id=%s task_id=%s",
            pass_name,
            conversation_id,
            getattr(result, 'id', None),
        )
        return getattr(result, 'id', None)
    except Exception as e:
        logger.error("[CA.TRIGGER] enqueue failed pass=%s convo_id=%s err=%s", pass_name, conversation_id, e, exc_info=True)
        return None

def trigger_all_pending_passes(conversation_id: int, chat_id: int) -> List[str]:
    """
    Trigger all pending analysis passes for a conversation
    
    Args:
        conversation_id: ID of the conversation
        chat_id: ID of the chat
        
    Returns:
        List of triggered pass names
    """
    triggered = []
    
    with db.session_scope() as session:
        pending = get_pending_passes(session, conversation_id)
    
    for pass_name in pending:
        try:
            trigger_analysis_pass(conversation_id, chat_id, pass_name)
            triggered.append(pass_name)
        except Exception as e:
            logger.error(f"Failed to trigger pass '{pass_name}' for conversation {conversation_id}: {e}", exc_info=True)
    
    return triggered

def add_analysis_pass(
    name: str,
    prompt_name: str,
    prompt_version: int,
    prompt_category: str,
    description: str,
    priority: int = 999,
    enabled: bool = True
) -> None:
    """
    Add a new analysis pass to the system
    
    Args:
        name: Unique name for the pass
        prompt_name: Name of the prompt template
        prompt_version: Version of the prompt template
        prompt_category: Category of the prompt template
        description: Human-readable description
        priority: Execution priority (lower runs first)
        enabled: Whether the pass is enabled
    """
    if name in ANALYSIS_PASSES:
        raise ValueError(f"Analysis pass '{name}' already exists")
    
    ANALYSIS_PASSES[name] = {
        "prompt_name": prompt_name,
        "prompt_version": prompt_version,
        "prompt_category": prompt_category,
        "description": description,
        "priority": priority,
        "enabled": enabled
    }
    
    logger.info(f"Added new analysis pass: {name}")

def disable_analysis_pass(name: str) -> None:
    """Disable an analysis pass"""
    if name not in ANALYSIS_PASSES:
        raise ValueError(f"Unknown analysis pass: {name}")
    
    ANALYSIS_PASSES[name]["enabled"] = False
    logger.info(f"Disabled analysis pass: {name}")

def enable_analysis_pass(name: str) -> None:
    """Enable an analysis pass"""
    if name not in ANALYSIS_PASSES:
        raise ValueError(f"Unknown analysis pass: {name}")
    
    ANALYSIS_PASSES[name]["enabled"] = True
    logger.info(f"Enabled analysis pass: {name}") 