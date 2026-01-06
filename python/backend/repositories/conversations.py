"""
Repository for conversation operations.
Handles all database operations related to conversations table.
"""
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from .core.generic import GenericRepository
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ConversationRepository(GenericRepository):
    """Repository for conversation operations."""
    
    TABLE = "conversations"
    
    @classmethod
    def get_conversations_for_chat(cls, session: Session, chat_id: int, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get conversations for a specific chat."""
        sql = """
            SELECT 
                c.*,
                COUNT(m.id) as message_count,
                MIN(m.timestamp) as actual_start_time,
                MAX(m.timestamp) as actual_end_time
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.chat_id = :chat_id
            GROUP BY c.id
            ORDER BY c.start_time DESC
        """
        if limit:
            sql += f" LIMIT {limit}"
        
        return cls.fetch_all(session, sql, {"chat_id": chat_id})
    
    @classmethod
    def load_single_conversation_by_id(cls, session: Session, conversation_id: int, chat_id: int) -> Optional[Dict[str, Any]]:
        """
        Load a single conversation with all its details including messages, attachments, and reactions.
        This is the core conversation loading function.
        """
        # Get conversation details
        conversation_sql = """
            SELECT 
                c.*,
                COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.id = :conversation_id AND c.chat_id = :chat_id
            GROUP BY c.id
        """
        conversation = cls.fetch_one(session, conversation_sql, {
            "conversation_id": conversation_id,
            "chat_id": chat_id
        })
        
        if not conversation:
            return None
        
        # Get messages for this conversation
        messages_sql = """
            SELECT 
                m.*,
                c.name as sender_name,
                c.is_me as sender_is_me
            FROM messages m
            LEFT JOIN contacts c ON m.sender_id = c.id
            WHERE m.conversation_id = :conversation_id
            ORDER BY m.timestamp ASC
        """
        messages = cls.fetch_all(session, messages_sql, {"conversation_id": conversation_id})
        
        # Get attachments for this conversation's messages
        if messages:
            message_ids = [m["id"] for m in messages]
            placeholders = ",".join([":mid" + str(i) for i in range(len(message_ids))])
            params = {f"mid{i}": message_ids[i] for i in range(len(message_ids))}
            
            attachments_sql = f"""
                SELECT 
                    a.*
                FROM attachments a
                WHERE a.message_id IN ({placeholders})
                ORDER BY a.message_id, a.id
            """
            attachments = cls.fetch_all(session, attachments_sql, params)
        else:
            attachments = []
        
        # Get reactions for this conversation's messages
        if messages:
            message_guids = [m["guid"] for m in messages if m.get("guid")]
            if message_guids:
                guid_placeholders = ",".join([":guid" + str(i) for i in range(len(message_guids))])
                guid_params = {f"guid{i}": message_guids[i] for i in range(len(message_guids))}
                
                reactions_sql = f"""
                    SELECT 
                        r.*,
                        c.name as sender_name
                    FROM reactions r
                    LEFT JOIN contacts c ON r.sender_id = c.id
                    WHERE r.original_message_guid IN ({guid_placeholders})
                    ORDER BY r.original_message_guid, r.id
                """
                reactions = cls.fetch_all(session, reactions_sql, guid_params)
            else:
                reactions = []
        else:
            reactions = []
        
        # Build the conversation data structure
        return cls._build_single_conversation_dict(conversation, messages, attachments, reactions)
    
    @classmethod
    def _build_single_conversation_dict(
        cls,
        conversation: Dict[str, Any],
        messages: List[Dict[str, Any]],
        attachments: List[Dict[str, Any]],
        reactions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Assemble a conversation dictionary from fetched data."""
        
        # Build attachment lookup keyed by message.id for quick access
        attachment_map: Dict[int, List[Dict[str, Any]]] = {}
        for att in attachments:
            attachment_map.setdefault(att["message_id"], []).append({
                "id": att["id"],
                "mime_type": att["mime_type"],
                "file_name": att["file_name"],
                "is_sticker": bool(att["is_sticker"]),
                "guid": att.get("guid"),
                "uti": att.get("uti"),
            })

        # Build reaction lookup keyed by original_message_guid
        reaction_map: Dict[str, List[Dict[str, Any]]] = {}
        for react in reactions:
            reaction_map.setdefault(react["original_message_guid"], []).append({
                "reaction_type": react["reaction_type"],
                "sender_id": react["sender_id"],
                "sender_name": react["sender_name"],
                "is_from_me": bool(react.get("is_from_me", False)),
            })

        # Convert message dicts and attach related data
        enriched_messages = []
        for msg in messages:
            message_dict = {
                "id": msg["id"],
                "guid": msg["guid"],
                # Normalize column name: messages table stores text in 'content'
                "text": msg.get("text") or msg.get("content"),
                "content": msg.get("content"),
                "timestamp": msg["timestamp"],
                "sender_id": msg["sender_id"],
                "sender_name": msg["sender_name"],
                "is_from_me": bool(msg.get("is_from_me", False)),
                "sender_is_me": bool(msg.get("sender_is_me", False)),
                "conversation_id": msg["conversation_id"],
                "chat_id": msg["chat_id"],
                "attachments": attachment_map.get(msg["id"], []),
                "reactions": reaction_map.get(msg["guid"], []) if msg.get("guid") else [],
            }
            enriched_messages.append(message_dict)

        # Build final conversation dict
        return {
            "id": conversation["id"],
            "chat_id": conversation["chat_id"],
            "start_time": conversation["start_time"],
            "end_time": conversation["end_time"],
            "summary": conversation.get("summary"),
            "summary_cum_text": conversation.get("summary_cum_text"),
            "topics_cum_text": conversation.get("topics_cum_text"),
            "entities_cum_text": conversation.get("entities_cum_text"),
            "emotions_cum_text": conversation.get("emotions_cum_text"),
            "humor_cum_text": conversation.get("humor_cum_text"),
            "message_count": conversation.get("message_count", len(enriched_messages)),
            "messages": enriched_messages,
        }
    
    @classmethod
    def get_conversations_with_message_counts(cls, session: Session, chat_id: int) -> List[Dict[str, Any]]:
        """Get conversations for a chat with message counts and basic stats."""
        sql = """
            SELECT 
                c.id,
                c.chat_id,
                c.start_time,
                c.end_time,
                c.summary,
                COUNT(m.id) as message_count,
                COUNT(DISTINCT m.sender_id) as unique_senders,
                MIN(m.timestamp) as first_message_time,
                MAX(m.timestamp) as last_message_time
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.chat_id = :chat_id
            GROUP BY c.id
            ORDER BY c.start_time DESC
        """
        return cls.fetch_all(session, sql, {"chat_id": chat_id})
    
    @classmethod
    def delete_conversation(cls, session: Session, conversation_id: int) -> int:
        """Delete a conversation and return number of affected rows."""
        sql = "DELETE FROM conversations WHERE id = :conversation_id"
        result = cls.execute_write(session, sql, {"conversation_id": conversation_id})
        return result.rowcount if result else 0
    
    @classmethod
    def get_conversation_stats(cls, session: Session, chat_id: int) -> Dict[str, Any]:
        """Get statistics about conversations in a chat."""
        sql = """
            SELECT 
                COUNT(*) as total_conversations,
                AVG(
                    (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id)
                ) as avg_messages_per_conversation,
                MIN(c.start_time) as earliest_conversation,
                MAX(c.end_time) as latest_conversation
            FROM conversations c
            WHERE c.chat_id = :chat_id
        """
        return cls.fetch_one(session, sql, {"chat_id": chat_id}) or {}
    
    @classmethod
    def get_conversations_by_date_range(cls, session: Session, chat_id: int, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Get conversations within a specific date range."""
        sql = """
            SELECT 
                c.*,
                COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.chat_id = :chat_id 
            AND c.start_time >= :start_date 
            AND c.start_time <= :end_date
            GROUP BY c.id
            ORDER BY c.start_time ASC
        """
        return cls.fetch_all(session, sql, {
            "chat_id": chat_id,
            "start_date": start_date,
            "end_date": end_date
        })
    
    @classmethod
    def get_previous_conversations(cls, session: Session, chat_id: int, current_conversation_id: int, cutoff_date) -> List[Dict[str, Any]]:
        """Get previous conversations for a chat, excluding the current one and within cutoff date."""
        sql = """
            SELECT c.*
            FROM conversations c
            WHERE c.chat_id = :chat_id
              AND c.id != :current_conversation_id
              AND c.end_time >= :cutoff_date
            ORDER BY c.end_time DESC
        """
        return cls.fetch_all(session, sql, {
            "chat_id": chat_id,
            "current_conversation_id": current_conversation_id,
            "cutoff_date": cutoff_date
        })

    # ------------------------------------------------------------------
    # Backfill helpers
    # ------------------------------------------------------------------

    @classmethod
    def list_for_backfill(
        cls,
        session: Session,
        chat_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[tuple[int, int]]:
        """Return conversation (id, chat_id) tuples for backfill, newest first."""

        sql = "SELECT id, chat_id FROM conversations"
        params: Dict[str, Any] = {}

        if chat_id is not None:
            sql += " WHERE chat_id = :chat_id"
            params["chat_id"] = chat_id

        sql += " ORDER BY id DESC"

        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit

        rows = cls.fetch_all(session, sql, params)
        return [(row["id"], row["chat_id"]) for row in rows]

    # ------------------------------------------------------------------
    # Live-analysis helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_conversations_for_chat_before_id(
        cls,
        session: Session,
        chat_id: int,
        current_conversation_id: int,
        cutoff_date: datetime,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return up to `limit` conversations in the same chat that ended before the
        current conversation and after the provided cutoff date. The most recent
        conversations are returned first, each including its messages, attachments
        and reactions so downstream services can consume them directly.
        """
        sql = (
            """
            SELECT id
            FROM conversations
            WHERE chat_id = :chat_id
              AND id < :current_conversation_id
              AND end_time >= :cutoff_date
            ORDER BY id DESC
            LIMIT :limit
            """
        )
        rows = cls.fetch_all(
            session,
            sql,
            {
                "chat_id": chat_id,
                "current_conversation_id": current_conversation_id,
                "cutoff_date": cutoff_date,
                "limit": limit,
            },
        )

        # Hydrate each conversation with its full details
        conversations: List[Dict[str, Any]] = []
        for row in rows:
            conv = cls.load_single_conversation_by_id(session, row["id"], chat_id)
            if conv:
                conversations.append(conv)
        return conversations

    

    @classmethod
    def get_by_id(cls, session: Session, conversation_id: int) -> Optional[Dict]:
        """Return conversation row as dict or None."""
        sql = "SELECT * FROM conversations WHERE id = :cid"
        return cls.fetch_one(session, sql, {"cid": conversation_id})

    @classmethod
    def list_recent_for_chat(
        cls, session: Session, chat_id: int, minutes: int = 5
    ) -> List[Dict]:
        """List conversations whose `end_time` is within the last `minutes`."""
        sql = """
            SELECT *
            FROM conversations
            WHERE chat_id = :chat_id
              AND end_time >= NOW() - INTERVAL ':minutes minutes'
            ORDER BY end_time DESC
        """
        # Some SQL dialects need interval casting; use timedelta in Python for portability
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        sql = sql.replace(":minutes", str(minutes))
        return cls.fetch_all(session, sql, {"chat_id": chat_id, "cutoff": cutoff})

    # ------------------------------------------------------------------
    # Historical analysis helper (ordered list)
    # ------------------------------------------------------------------

    @classmethod
    def list_for_history(
        cls,
        session: Session,
        chat_id: Optional[int] = None,
    ) -> List[tuple[int, int]]:
        """Return a list of (conversation_id, chat_id) tuples chronologically ascending.

        This replicates the behaviour previously inline in ``commitment_history``
        but keeps SQL in the repository layer per project guidelines.
        """

        if chat_id is None:
            sql = """
                SELECT id, chat_id
                FROM conversations
                ORDER BY end_time ASC
            """
            rows = cls.fetch_all(session, sql)
        else:
            sql = """
                SELECT id, chat_id
                FROM conversations
                WHERE chat_id = :cid
                ORDER BY end_time ASC
            """
            rows = cls.fetch_all(session, sql, {"cid": chat_id})

        return [(row["id"], row["chat_id"]) for row in rows] 