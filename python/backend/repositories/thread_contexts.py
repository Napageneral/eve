"""Repository for thread context operations."""
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from .core.generic import GenericRepository
import logging

logger = logging.getLogger(__name__)


class ThreadContextRepository(GenericRepository):
    """Repository for thread context operations (chatbot thread contexts)."""
    
    TABLE = "thread_contexts"
    
    # TODO: Migrate direct DB access from:
    # - app/backend/routers/chatbot/chats.py
    # - app/backend/routers/chatbot/messages.py
    # - app/backend/routers/chatbot/utils.py
    # - Frontend code (TBD during frontend review)
    
    @classmethod
    def get_contexts_for_thread(cls, session: Session, chat_id: str) -> List[Dict[str, Any]]:
        """Get all contexts for a thread."""
        from backend.db.sql import fetch_all
        
        sql = """
            SELECT * FROM thread_contexts 
            WHERE chat_id = :chat_id 
            ORDER BY added_at DESC
        """
        return fetch_all(session, sql, {"chat_id": chat_id})
    
    @classmethod
    def add_context_to_thread(cls, session: Session, chat_id: str, context_data: Dict[str, Any]) -> None:
        """Add a context to a thread."""
        from backend.db.sql import execute_write
        
        sql = """
            INSERT INTO thread_contexts (chat_id, context_type, context_data, added_at)
            VALUES (:chat_id, :context_type, :context_data, CURRENT_TIMESTAMP)
        """
        execute_write(session, sql, {
            "chat_id": chat_id,
            "context_type": context_data.get("type"),
            "context_data": context_data
        })

