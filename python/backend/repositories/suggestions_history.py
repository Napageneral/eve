"""Repository for suggestions history operations."""
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from .core.generic import GenericRepository
import logging

logger = logging.getLogger(__name__)


class SuggestionsHistoryRepository(GenericRepository):
    """Repository for Smart Cues suggestions history."""
    
    TABLE = "suggestions_history"
    
    # Currently used directly in: app/backend/routers/chatbot/suggestions_history.py
    # TODO: Migrate direct SQL access to use this repository
    
    @classmethod
    def get_for_chat(cls, session: Session, chat_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get suggestion history for a chat."""
        from backend.db.sql import fetch_all
        
        sql = """
            SELECT * FROM suggestions_history
            WHERE chat_id = :chat_id
            ORDER BY suggested_at DESC
            LIMIT :limit
        """
        return fetch_all(session, sql, {"chat_id": chat_id, "limit": limit})
    
    @classmethod
    def record_suggestion(cls, session: Session, chat_id: str, suggestion_type: str, 
                         suggestion_data: Dict[str, Any]) -> None:
        """Record a new suggestion."""
        from backend.db.sql import execute_write
        import json
        
        sql = """
            INSERT INTO suggestions_history 
            (chat_id, suggestion_type, suggestion_data, suggested_at, was_used)
            VALUES (:chat_id, :suggestion_type, :suggestion_data, CURRENT_TIMESTAMP, FALSE)
        """
        execute_write(session, sql, {
            "chat_id": chat_id,
            "suggestion_type": suggestion_type,
            "suggestion_data": json.dumps(suggestion_data)
        })
    
    @classmethod
    def mark_used(cls, session: Session, suggestion_id: int) -> None:
        """Mark a suggestion as used."""
        from backend.db.sql import execute_write
        
        sql = """
            UPDATE suggestions_history
            SET was_used = TRUE, used_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """
        execute_write(session, sql, {"id": suggestion_id})

