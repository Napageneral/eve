"""Base classes for analysis repositories to eliminate code duplication."""
from typing import Dict, List, Any
from sqlalchemy.orm import Session
from ..core.generic import GenericRepository

class AnalysisItemRepository(GenericRepository):
    """Base repository for analysis items (emotions, humor, entities, topics)."""
    
    # Subclasses must define these
    TABLE = ""
    ITEM_NAME_FIELD = ""  # e.g., "emotion_type", "topic_name", "category", "entity_name"
    EXTRA_FIELDS = []  # Additional fields beyond standard ones (e.g., ["snippet"] for humor)
    
    @classmethod
    def get_by_chat(cls, session: Session, chat_id: int) -> List[Dict[str, Any]]:
        """Get items for a specific chat with contact names and conversation dates."""
        # Build field list
        fields = [
            "t.id", "t.conversation_id", "t.chat_id", "t.contact_id",
            "c.name as contact_name", f"t.{cls.ITEM_NAME_FIELD}",
            "t.description", "strftime('%Y-%m-%dT%H:%M:%SZ', conv.start_time) as date"
        ]
        fields.extend([f"t.{field}" for field in cls.EXTRA_FIELDS])
        
        sql = f"""
            SELECT {', '.join(fields)}
            FROM {cls.TABLE} t
            LEFT JOIN contacts c ON t.contact_id = c.id
            LEFT JOIN conversations conv ON t.conversation_id = conv.id
            WHERE t.chat_id = :chat_id
            ORDER BY conv.start_time DESC
        """
        return cls.fetch_all(session, sql, {"chat_id": chat_id})
    
    @classmethod
    def get_by_conversation(cls, session: Session, conversation_id: int) -> List[Dict[str, Any]]:
        """Get items for a specific conversation."""
        sql = f"""
            SELECT t.*, c.name as contact_name
            FROM {cls.TABLE} t
            LEFT JOIN contacts c ON t.contact_id = c.id
            WHERE t.conversation_id = :conversation_id
            ORDER BY t.id
        """
        return cls.fetch_all(session, sql, {"conversation_id": conversation_id})
    
    @classmethod
    def delete_by_conversation(cls, session: Session, conversation_id: int) -> int:
        """Delete all items for a conversation. Returns number of deleted records."""
        sql = f"DELETE FROM {cls.TABLE} WHERE conversation_id = :conversation_id"
        return cls.execute(session, sql, {"conversation_id": conversation_id})
    
    @classmethod
    def delete_by_chat(cls, session: Session, chat_id: int) -> int:
        """Delete all items for a chat. Returns number of deleted records."""
        sql = f"DELETE FROM {cls.TABLE} WHERE chat_id = :chat_id"
        return cls.execute(session, sql, {"chat_id": chat_id}) 