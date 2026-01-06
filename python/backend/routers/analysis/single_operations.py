"""Single conversation analysis operations"""

# Consolidated imports
from backend.routers.common import (
    create_router, safe_endpoint, log_simple,
    HTTPException, Query, Depends
)
from typing import Optional
from datetime import datetime

from backend.celery_service.services import trigger_single_analysis
from backend.celery_service.models.conversation import LLMConfig

def build_llm_override(model_name: Optional[str], temperature: Optional[float], max_tokens: Optional[int]) -> Optional[LLMConfig]:
    if not (model_name or temperature is not None or max_tokens is not None):
        return None
    return LLMConfig(
        model_name=model_name or "",
        temperature=temperature,
        max_tokens=max_tokens,
    )
router = create_router("/analysis/single", "Single Analysis")

# Simplified auth placeholder
async def get_current_user_id_placeholder() -> int:
    """Fixed user ID for development. Replace with proper auth in production."""
    return 1

@router.post("/conversations/trigger_single_analysis", status_code=202)
@safe_endpoint
async def trigger_single_conversation_analysis_queue(
    conversation_id: Optional[int] = Query(None),
    chat_id: Optional[int] = Query(None),
    model_name: Optional[str] = Query(None),
    temperature: Optional[float] = Query(None),
    max_tokens: Optional[int] = Query(None),
    req_prompt_name: Optional[str] = Query(None, alias="prompt_name"),
    req_prompt_version: Optional[int] = Query(None, alias="prompt_version"),
    req_prompt_category: Optional[str] = Query(None, alias="prompt_category"),
    auth_token_for_metrics: Optional[str] = Query(None),
    current_user_id: int = Depends(get_current_user_id_placeholder)
):
    log_simple(f"Triggering single analysis: conversation {conversation_id}, chat {chat_id}")
    
    # Validate required parameters
    if not conversation_id or not chat_id:
        raise HTTPException(status_code=400, detail="conversation_id and chat_id are required")
    
    llm_override = build_llm_override(model_name, temperature, max_tokens)

    started = await trigger_single_analysis(
        conversation_id=conversation_id,
        chat_id=chat_id,
        user_id=current_user_id,
        prompt_name=req_prompt_name or "ConvoAll",
        prompt_version=req_prompt_version or 1,
        prompt_category=req_prompt_category or "conversation_analysis",
        llm_override=llm_override,
        auth_token=auth_token_for_metrics,
    )

    return {
        "success": bool(started.task_id),
        "message": started.message,
        "task_id": started.task_id,
        "conversation_analysis_id": started.conversation_analysis_id,
        "conversation_id": conversation_id,
        "chat_id": chat_id,
        "user_id": current_user_id,
        "prompt_name": req_prompt_name or "ConvoAll",
        "prompt_version": req_prompt_version or 1,
        "prompt_category": req_prompt_category or "conversation_analysis",
    }

@router.get("/analysis/active-tasks")
@safe_endpoint
async def get_active_analysis_tasks():
    log_simple("Getting active analysis tasks")
    
    from backend.db.session_manager import new_session
    from backend.repositories.conversation_analysis import ConversationAnalysisRepository
    from backend.db.sql import fetch_all
    
    with new_session() as session:
        # Get active chat analyses
        active_chat_analyses = fetch_all(session, """
            SELECT DISTINCT c.chat_id, ch.chat_name
            FROM conversation_analyses ca
            JOIN conversations c ON ca.conversation_id = c.id
            LEFT JOIN chats ch ON c.chat_id = ch.id
            WHERE ca.status IN ('processing', 'pending')
            ORDER BY c.chat_id
        """)
        
        active_chats = []
        for row in active_chat_analyses:
            chat_id = row['chat_id']
            chat_name = row.get('chat_name', f'Chat {chat_id}')
            
            summary = ConversationAnalysisRepository.get_chat_analysis_summary(session, chat_id)
            
            # Only include if there are actually active tasks
            if summary['processing'] > 0 or summary['queued'] > 0:
                active_chats.append({
                    'chat_id': chat_id,
                    'chat_name': chat_name,
                    'processing': summary['processing'],
                    'queued': summary['queued'],
                    'total': summary['total']
                })
        
        # Check if global analysis is active
        total_processing = fetch_all(session, """
            SELECT COUNT(*) as count 
            FROM conversation_analyses 
            WHERE status IN ('processing', 'pending')
        """)
        
        global_analysis_active = total_processing[0]['count'] > 0 if total_processing else False
        
        result = {
            "active_chats": active_chats,
            "global_analysis_active": global_analysis_active,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        log_simple(f"Found {len(active_chats)} active chats, global active: {global_analysis_active}")
        return result 