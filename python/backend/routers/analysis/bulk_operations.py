"""Bulk analysis operations for chats"""

# Consolidated imports
from backend.routers.common import (
    create_router, safe_endpoint, log_simple,
    HTTPException, Query, Depends
)
from backend.routers.shared_models import BulkAnalysisRequest

from typing import Optional

# NEW imports to actually queue Celery work
from backend.celery_service.services import start_bulk_analysis
from backend.celery_service.models.conversation import LLMConfig

def build_llm_override(model_name: Optional[str], temperature: Optional[float], max_tokens: Optional[int]) -> Optional[LLMConfig]:
    if not (model_name or temperature is not None or max_tokens is not None):
        return None
    return LLMConfig(
        model_name=model_name or "",
        temperature=temperature,
        max_tokens=max_tokens,
    )

router = create_router("/analysis/bulk", "Bulk Analysis")

# Simplified auth placeholder - consider extracting to common auth utilities
async def get_current_user_id_placeholder() -> int:
    """Fixed user ID for development. Replace with proper auth in production."""
    return 1  # TODO: Replace with actual auth

@router.post("/chats/{chat_id}/start_bulk_analysis", status_code=202)
@safe_endpoint
async def start_bulk_analysis_queue(
    chat_id: int,
    model_name: Optional[str] = Query(None),
    temperature: Optional[float] = Query(None),
    max_tokens: Optional[int] = Query(None),
    prompt_name: Optional[str] = Query(None),
    prompt_version: Optional[int] = Query(None),
    prompt_category: Optional[str] = Query(None),
    auth_token_for_metrics: Optional[str] = Query(None),
    idempotency_key: Optional[str] = Query(None),
    current_user_id: int = Depends(get_current_user_id_placeholder)
):
    """Start a bulk analysis job for all conversations in a chat."""
    log_simple(f"Starting bulk analysis for chat {chat_id}")
    
    llm_override = build_llm_override(model_name, temperature, max_tokens)

    started = await start_bulk_analysis(
        chat_id=chat_id,
        user_id=current_user_id,
        llm_override=llm_override,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        prompt_category=prompt_category,
        auth_token=auth_token_for_metrics,
        idempotency_key=idempotency_key,
    )

    return {
        "success": bool(started.task_id),
        "message": started.message,
        "task_id": started.task_id,
        "run_id": started.run_id,
        "chat_id": chat_id,
        "user_id": current_user_id,
        "idempotency_key": idempotency_key,
    }