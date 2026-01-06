"""Chat Repository - All database operations related to chats."""

import logging
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from .core.generic import GenericNamedRepository

logger = logging.getLogger(__name__)


class ChatRepository(GenericNamedRepository):
    """Repository for all chat-related database operations"""
    
    TABLE = "chats"
    NAME_COL = "chat_name"
    
    @classmethod
    def toggle_chat_block(cls, session: Session, chat_id: int, is_blocked: bool) -> Dict[str, Any]:
        """Toggle chat block status."""
        session.execute(
            text("UPDATE chats SET is_blocked = :is_blocked WHERE id = :chat_id"),
            {"is_blocked": 1 if is_blocked else 0, "chat_id": chat_id},
        )
        return {"success": True}
    
    @classmethod
    def get_unanalyzed_chats(cls, session: Session) -> List[Dict[str, Any]]:
        """Get chats with unanalyzed conversations."""
        return cls.fetch_all(session, """
            SELECT DISTINCT ch.id as chat_id, ch.chat_identifier, ch.chat_name
            FROM chats ch
            WHERE EXISTS (
                SELECT 1 
                FROM conversations c
                LEFT JOIN conversation_analyses ca ON ca.conversation_id = c.id
                WHERE c.chat_id = ch.id 
                AND (ca.id IS NULL OR ca.status NOT IN ('success', 'processing'))
            )
            ORDER BY ch.id
        """)
    
    @classmethod
    def get_chat_participants_with_contacts(cls, session: Session, chat_id: int) -> List[Dict[str, Any]]:
        """Get all participants for a chat with contact details."""
        sql = """
            SELECT cp.*, c.name, c.is_me
            FROM chat_participants cp
            JOIN contacts c ON cp.contact_id = c.id
            WHERE cp.chat_id = :chat_id
        """
        return cls.fetch_all(session, sql, {"chat_id": chat_id}) 