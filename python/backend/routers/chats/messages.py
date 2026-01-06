# Consolidated imports from common utilities
from backend.routers.common import (
    create_router, safe_endpoint, log_simple,
    Depends, text, Session, get_db, db
)
from backend.routers.shared_models import ChatBlockRequest
from backend.repositories.chats import ChatRepository
from backend.repositories.analysis_items.emotions import EmotionsRepository
from backend.repositories.analysis_items.humor import HumorRepository
from backend.repositories.analysis_items.entities import EntitiesRepository
from backend.repositories.analysis_items.topics import TopicsRepository
from backend.repositories.messages import MessageRepository
from backend.repositories.analysis import AnalysisRepository
from backend.services.conversations.wrapped_analysis import WrappedAnalysisService

from backend.db.session_manager import db as _db  # explicit import for clarity

# ------------------------------------------------------------
# Router setup
# ------------------------------------------------------------

router = create_router("/chats", "Chats")

# ------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------

@router.get("")
async def list_chats():
    """Return list of all chats ordered by recency (same as SSE initial event)."""
    try:
        log_simple("GET /chats endpoint called", level="debug")
    except Exception:
        pass
    with db.session_scope() as session:
        from backend.db.sql import fetch_all
        import logging
        logger = logging.getLogger(__name__)
        
        # Match the SSE /stream/chats query (line 143 in live_sync.py)
        rows = fetch_all(session, "SELECT * FROM chats ORDER BY last_message_date DESC")
        
        # DEBUG LOGGING
        logger.info(f"[CHAT-DEBUG] /api/chats returning {len(rows)} chats")
        if rows:
            logger.info(f"[CHAT-DEBUG] First chat sample: {rows[0]}")
            logger.info(f"[CHAT-DEBUG] First chat keys: {list(rows[0].keys())}")
        
        return rows

@router.get("/{chat_id}/messages")
@safe_endpoint
async def read_chat_messages(chat_id: int, session: Session = Depends(get_db)):
    """Return chat messages with attachments & reactions using the unified API."""
    messages = MessageRepository.get_messages(
        session,
        chat_id=chat_id,
        include_attachments=True,
        include_reactions=True,
    )
    return {
        "messages": messages,
        "chat_id": chat_id,
        "since_timestamp": None,
        "total_count": len(messages),
    }

@router.get("/{chat_id}/emotions")
@safe_endpoint
async def read_chat_emotions(chat_id: int):
    with db.session_scope() as session:
        return EmotionsRepository.get_by_chat(session, chat_id)

@router.get("/{chat_id}/humor")
@safe_endpoint
async def read_chat_humor(chat_id: int):
    with db.session_scope() as session:
        return HumorRepository.get_by_chat(session, chat_id)

@router.get("/{chat_id}/entities")
@safe_endpoint
async def read_chat_entities(chat_id: int):
    with db.session_scope() as session:
        return EntitiesRepository.get_by_chat(session, chat_id)

@router.get("/{chat_id}/topics")
@safe_endpoint
async def read_chat_topics(chat_id: int):
    with db.session_scope() as session:
        return TopicsRepository.get_by_chat(session, chat_id)

@router.get("/{chat_id}/consolidated-data")
@safe_endpoint
async def read_chat_consolidated_data(chat_id: int):
    with db.session_scope() as session:
        return AnalysisRepository.get_chat_consolidated_data(session, chat_id)

@router.post("/{chat_id}/block")
@safe_endpoint
async def toggle_chat_block(chat_id: int, request: ChatBlockRequest):
    with db.session_scope() as session:
        result = ChatRepository.toggle_chat_block(session, chat_id, request.is_blocked)
        session.commit()
        return result

@router.get("/{chat_id}/data")
@safe_endpoint
async def get_chat_data_endpoint(chat_id: int, start_date: str, end_date: str):
    return WrappedAnalysisService.get_chat_data(chat_id, start_date, end_date)

@router.get("/{chat_id}/conversations")
@safe_endpoint
async def load_full_conversations_endpoint(chat_id: int, year: int | None = None):
    return WrappedAnalysisService.load_full_conversations(chat_id, year)

@router.get("/{chat_id}/activity-timeline")
@safe_endpoint
async def get_chat_activity_timeline_endpoint(chat_id: int):
    return WrappedAnalysisService.get_chat_activity_timeline(chat_id)

@router.get("/{chat_id}/activity-date-range")
@safe_endpoint
async def get_chat_activity_for_date_range_endpoint(chat_id: int, start_date: str, end_date: str):
    return WrappedAnalysisService.get_chat_activity_for_date_range(chat_id, start_date, end_date) 


@router.get("/rank-by-volume")
@safe_endpoint
async def rank_chats_by_volume(limit: int = 10, since: str | None = None, session: Session = Depends(get_db)):
    """Return top chats by message count since a given date (defaults to last 365 days).

    Query params:
      - limit: number of chats to return
      - since: ISO date (YYYY-MM-DD); if omitted, uses current_date - 365 days
    """
    # Determine default since date inside SQL for portability
    # SQLite strftime supports date arithmetic via 'now','-365 day'
    date_expr = ":since" if since else "date('now','-365 day')"

    sql = text(f"""
        SELECT 
            c.id AS id,
            MAX(m.timestamp) AS last_message_at,
            COUNT(m.id) AS message_count,
            MAX(COALESCE(c.chat_name, c.chat_identifier, '')) AS display_name
        FROM messages m
        JOIN chats c ON c.id = m.chat_id
        WHERE DATE(m.timestamp) >= {date_expr}
        GROUP BY c.id
        ORDER BY message_count DESC, last_message_at DESC
        LIMIT :limit
    """)
    params = {"limit": limit}
    if since:
        params["since"] = since
    rows = session.execute(sql, params).mappings().all()
    data = []
    for r in rows:
        name_val = r.get("display_name")
        if not name_val:
            name_val = f"Chat {r.get('id')}"
        data.append({
            "id": r.get("id"),
            "name": name_val,
            "messageCount": int(r.get("message_count") or 0),
            "lastMessageAt": r.get("last_message_at").isoformat() if getattr(r.get("last_message_at"), "isoformat", None) else str(r.get("last_message_at")),
        })
    return {"since": since, "limit": limit, "chats": data}