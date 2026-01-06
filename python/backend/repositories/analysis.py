"""
Repository for analysis operations.
Handles complex SQL queries for analysis data consolidation.
"""
from typing import Dict, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
from .core.generic import GenericRepository
import logging

logger = logging.getLogger(__name__)


class AnalysisRepository(GenericRepository):
    """Repository for analysis operations."""
    
    TABLE = "analysis_results"  # Primary table, but handles multiple tables
    
    @classmethod
    def get_chat_consolidated_data(cls, session: Session, chat_id: int) -> List[Dict[str, Any]]:
        """Get consolidated analysis data for a specific chat."""
        # Base conversations query
        conversations_sql = """
            SELECT 
              c.id as conversation_id,
              c.chat_id,
              c.summary as conversation_summary,
              NULL as summary_cum_text,
              NULL as topics_cum_text,
              NULL as entities_cum_text,
              NULL as emotions_cum_text,
              NULL as humor_cum_text,
              MIN(m.id) as first_message_id,
              MIN(m.guid) as first_message_guid,
              strftime('%Y-%m-%dT%H:%M:%SZ', c.start_time) as conv_start_date,
              cont.id as contact_id,
              cont.name as contact_name
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            LEFT JOIN contacts cont ON cont.id = m.sender_id
            WHERE c.chat_id = :chat_id
            GROUP BY c.id, cont.id
            ORDER BY c.start_time ASC
        """
        conversations = session.execute(text(conversations_sql), {"chat_id": chat_id}).fetchall()
        
        # Query each dimension using raw SQL for consistency
        emotions_data = session.execute(text("""
            SELECT conversation_id, contact_id, emotion_type as title, '' as description 
            FROM emotions WHERE chat_id = :chat_id
        """), {"chat_id": chat_id}).fetchall()
        
        humor_data = session.execute(text("""
            SELECT conversation_id, contact_id, snippet as title, 
                   snippet as description
            FROM humor_items WHERE chat_id = :chat_id
        """), {"chat_id": chat_id}).fetchall()
        
        topics_data = session.execute(text("""
            SELECT conversation_id, contact_id, title as title, '' as description 
            FROM topics WHERE chat_id = :chat_id
        """), {"chat_id": chat_id}).fetchall()
        
        entities_data = session.execute(text("""
            SELECT conversation_id, contact_id, title as title, '' as description 
            FROM entities WHERE chat_id = :chat_id
        """), {"chat_id": chat_id}).fetchall()
        
        # Helper function to group rows
        def group_rows(rows):
            grouped = {}
            for row in rows:
                key = f"{row.conversation_id}_{row.contact_id}"
                if key not in grouped:
                    grouped[key] = []
                    
                grouped[key].append({
                    "title": row.title,
                    "description": row.description
                })
            return grouped
        
        # Group each dimension
        emotions_map = group_rows(emotions_data)
        humor_map = group_rows(humor_data)
        topics_map = group_rows(topics_data)
        entities_map = group_rows(entities_data)
        
        # Build final result
        result = []
        for conv in conversations:
            key = f"{conv.conversation_id}_{conv.contact_id}"
            
            conversation_entry = {
                "conversation_id": conv.conversation_id,
                "chat_id": conv.chat_id,
                "conversation_summary": conv.conversation_summary,
                "summary_cum_text": conv.summary_cum_text,
                "topics_cum_text": conv.topics_cum_text,
                "entities_cum_text": conv.entities_cum_text,
                "emotions_cum_text": conv.emotions_cum_text,
                "humor_cum_text": conv.humor_cum_text,
                "first_message_id": conv.first_message_id,
                "first_message_guid": conv.first_message_guid,
                "conv_start_date": conv.conv_start_date,
                "contact_id": conv.contact_id,
                "contact_name": conv.contact_name,
                "emotions": emotions_map.get(key, []),
                "humor": humor_map.get(key, []),
                "topics": topics_map.get(key, []),
                "entities": entities_map.get(key, [])
            }
            
            result.append(conversation_entry)
        
        return result 