# Consolidated imports
from backend.routers.common import (
    create_router, safe_endpoint, log_simple, to_dict,
    Query, Depends, BaseModel, text, Session, get_db
)
from typing import Optional
from datetime import datetime

from backend.etl.etl_conversations import etl_conversations
from backend.db.models import AppSettings
from backend.db.session_manager import db
from backend.repositories.app_settings import AppSettingsRepository
from backend.repositories.contacts import ContactRepository

router = create_router("/database", "Database")

class SetAppSettingRequest(BaseModel):
    key: str
    value: str

@router.get("/stats")
@safe_endpoint
async def read_database_stats():
    log_simple("Getting database stats")
    with db.session_scope() as session:
        return StatsRepository.get_database_stats(session)

@router.post("/chats/{chat_id}/analysis/etl-conversations")
@safe_endpoint
async def regenerate_conversations_endpoint(
    chat_id: int, 
    gap_threshold: int = Query(1800), 
    session: Session = Depends(get_db)
):
    log_simple(f"ETL conversations for chat {chat_id}")
    imported_count, updated_count = etl_conversations(chat_id=chat_id, gap_threshold=gap_threshold)
    return {"success": True, "imported_count": imported_count, "updated_count": updated_count}

@router.get("/contacts")
@safe_endpoint
async def read_contacts(session: Session = Depends(get_db)):
    log_simple("Getting contacts")
    
    contacts = ContactRepository.get_contacts_with_stats(session)
    
    log_simple(f"Retrieved {len(contacts)} contacts")
    return contacts

@router.get("/app-settings/{key}")
@safe_endpoint
async def get_app_setting(key: str, session: Session = Depends(get_db)):
    log_simple(f"Getting app setting: {key}")
    
    value = AppSettingsRepository.get_app_setting(session, key)
    return {"key": key, "value": value}

@router.post("/app-settings")
@safe_endpoint
async def set_app_setting(request: SetAppSettingRequest, session: Session = Depends(get_db)):
    log_simple(f"Setting app setting: {request.key}")
    
    result = AppSettingsRepository.set_app_setting(session, request.key, request.value)
    session.commit()
    return result

@router.get("/onboarding-status")
@safe_endpoint
async def get_onboarding_status(session: Session = Depends(get_db)):
    log_simple("Checking onboarding status")
    
    completed = AppSettingsRepository.get_onboarding_status(session)
    return {"completed": completed}

@router.post("/onboarding-complete")
@safe_endpoint
async def mark_onboarding_complete(session: Session = Depends(get_db)):
    log_simple("Marking onboarding complete")
    
    result = AppSettingsRepository.mark_onboarding_complete(session)
    session.commit()
    return result 