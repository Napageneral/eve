"""
Streamlined Repository for message operations.
Consolidates multiple similar methods into a unified interface.
"""
from datetime import datetime
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from .core.generic import GenericRepository
from .core.mixins import JSONFieldMixin

class MessageRepository(GenericRepository, JSONFieldMixin):
    """Streamlined repository for message operations."""
    
    TABLE = "messages"
    
    @classmethod
    def get_messages(cls, session: Session, 
                    chat_id: Optional[int] = None,
                    conversation_id: Optional[int] = None,
                    since_timestamp: Optional[datetime] = None,
                    start_date: Optional[datetime] = None,
                    end_date: Optional[datetime] = None,
                    search_term: Optional[str] = None,
                    include_attachments: bool = False,
                    include_reactions: bool = False,
                    limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Unified message retrieval with flexible filtering."""
        # Build WHERE conditions
        conditions = []
        params = {}
        
        if chat_id is not None:
            conditions.append("m.chat_id = :chat_id")
            params["chat_id"] = chat_id
            
        if conversation_id is not None:
            conditions.append("m.conversation_id = :conversation_id")
            params["conversation_id"] = conversation_id
            
        if since_timestamp is not None:
            conditions.append("m.timestamp > :since_timestamp")
            params["since_timestamp"] = since_timestamp
            
        if start_date is not None:
            conditions.append("m.timestamp >= :start_date")
            params["start_date"] = start_date
            
        if end_date is not None:
            conditions.append("m.timestamp <= :end_date")
            params["end_date"] = end_date
            
        if search_term is not None:
            conditions.append("m.content LIKE :search_term")
            params["search_term"] = f"%{search_term}%"
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Build query based on what's needed
        base_fields = "m.*, c.name as sender_name"
        base_joins = "FROM messages m LEFT JOIN contacts c ON m.sender_id = c.id"
        
        if include_attachments and include_reactions:
            sql = f"""
                SELECT {base_fields},
                    GROUP_CONCAT(DISTINCT 
                        CASE WHEN a.id IS NOT NULL 
                        THEN json_object('id', a.id, 'file_name', a.file_name, 
                                       'mime_type', a.mime_type, 'is_sticker', a.is_sticker)
                        END
                    ) as attachments_json,
                    GROUP_CONCAT(DISTINCT 
                        CASE WHEN r.id IS NOT NULL
                        THEN json_object('reaction_type', r.reaction_type, 
                                       'sender_id', r.sender_id, 'sender_name', rc.name)
                        END
                    ) as reactions_json
                {base_joins}
                LEFT JOIN attachments a ON a.message_id = m.id
                LEFT JOIN reactions r ON r.original_message_guid = m.guid
                LEFT JOIN contacts rc ON r.sender_id = rc.id
                WHERE {where_clause}
                GROUP BY m.id
                ORDER BY m.timestamp ASC
                {"LIMIT :limit" if limit else ""}
            """
        else:
            sql = f"""
                SELECT {base_fields}
                {base_joins}
                WHERE {where_clause}
                ORDER BY m.timestamp ASC
                {"LIMIT :limit" if limit else ""}
            """
        
        if limit:
            params["limit"] = limit
            
        messages = cls.fetch_all(session, sql, params)
        
        # Post-process JSON fields if needed
        if include_attachments or include_reactions:
            for msg in messages:
                if msg.get('attachments_json'):
                    msg['attachments'] = [cls.safe_json_loads(a) for a in msg['attachments_json'].split(',') if a]
                    del msg['attachments_json']
                if msg.get('reactions_json'):
                    msg['reactions'] = [cls.safe_json_loads(r) for r in msg['reactions_json'].split(',') if r]
                    del msg['reactions_json']
        
        return messages
    
    @classmethod
    def get_last_timestamp(cls, session: Session, chat_id: int) -> Optional[datetime]:
        """Get timestamp of the last message in a chat."""
        return cls.fetch_scalar(session, 
            "SELECT MAX(timestamp) FROM messages WHERE chat_id = :chat_id",
            {"chat_id": chat_id})
    
    @classmethod
    def count_by_chat(cls, session: Session, chat_id: int) -> int:
        """Get message count for a chat."""
        return cls.count(session, chat_id=chat_id)
    
    # Phase 2 cleanup: removed legacy wrapper methods (get_chat_messages, load_messages, etc.). 