"""Contact Repository - All database operations related to contacts."""

import logging
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from .core.generic import GenericNamedRepository

logger = logging.getLogger(__name__)

# Contact statistics query moved from query_fragments.py
_CONTACT_STATS_SQL = """
SELECT c.id, c.name,
       COALESCE(SUM(ch.total_messages), 0) as total_messages,
       COUNT(DISTINCT conv.id) as total_conversations,
       COUNT(DISTINCT ch.id) as number_of_chats,
       MAX(ch.last_message_date) as last_message_time,
       MIN(ch.created_date) as first_message_time
FROM contacts c
JOIN chat_participants cp ON c.id = cp.contact_id
JOIN chats ch ON cp.chat_id = ch.id
LEFT JOIN conversations conv ON ch.id = conv.chat_id
GROUP BY c.id, c.name
ORDER BY total_messages DESC
"""


class ContactRepository(GenericNamedRepository):
    """Repository for all contact-related database operations"""
    
    TABLE = "contacts"
    
    @classmethod
    def get_contacts_with_stats(cls, session: Session) -> List[Dict[str, Any]]:
        """Get all contacts with their statistics."""
        rows = cls.fetch_all(session, _CONTACT_STATS_SQL)
        
        contacts = []
        for row in rows:
            contacts.append({
                "id": row["id"],
                "name": row["name"],
                "totalMessages": row["total_messages"],
                "totalConversations": row["total_conversations"] or 0,
                "numberOfChats": row["number_of_chats"] or 0,
                "lastMessageTime": cls.convert_timestamp(row["last_message_time"]),
                "firstMessageTime": cls.convert_timestamp(row["first_message_time"]),
            })
        
        return contacts
    
    @classmethod
    def get_user_contact(cls, session: Session) -> Optional[Dict[str, Any]]:
        """Get the user's contact (is_me = TRUE)."""
        return cls.get_by_field(session, cls.TABLE, "is_me", True)

    # ------------------------------------------------------------------
    # Context-selection / analysis helpers (moved from AnalysisResultsRepository)
    # ------------------------------------------------------------------

    @classmethod
    def get_name_map_for_chat(cls, session: Session, chat_id: int) -> Dict[str, int]:
        """Return a mapping of participant **name → contact_id** for the given chat.

        Includes the global `contacts.id = 1` row (the canonical "me" contact) so
        that name look-ups always succeed, even in edge cases where the current
        user isn’t explicitly in `chat_participants` for the thread being
        analyzed.
        """
        rows = cls.fetch_all(
            session,
            """
            SELECT c.name, c.id
            FROM contacts c
            JOIN chat_participants cp ON cp.contact_id = c.id
            WHERE cp.chat_id = :chat_id
            UNION
            SELECT name, id FROM contacts WHERE id = 1
            """,
            {"chat_id": chat_id},
        )

        # Filter out NULL / empty names to avoid key errors downstream.
        return {row["name"]: row["id"] for row in rows if row["name"]}

    @classmethod
    def get_me_contact_id_for_chat(cls, session: Session, chat_id: int) -> int:
        """Return the contact_id representing the current user ("me") for a chat.

        Falls back to the global id=1 contact if the chat has no `is_me` row.
        """
        row = cls.fetch_one(
            session,
            """
            SELECT c.id
            FROM contacts c
            JOIN chat_participants cp ON cp.contact_id = c.id
            WHERE cp.chat_id = :chat_id AND c.is_me = 1
            UNION
            SELECT id FROM contacts WHERE id = 1
            LIMIT 1
            """,
            {"chat_id": chat_id},
        )

        # Safe fallback if somehow even the default row doesn’t exist.
        return row["id"] if row else 1 