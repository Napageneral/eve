import logging
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)


class DocumentReadsRepository:
    TABLE = 'chatbot_document_reads'

    @staticmethod
    def upsert_read(session: Session, user_id: str, document_id: str, *, mark_display: bool = False) -> None:
        column = 'display_read_at' if mark_display else 'last_read_at'
        sql = text(
            f"""
            INSERT INTO {DocumentReadsRepository.TABLE} (user_id, document_id, {column})
            VALUES (:user_id, :document_id, now())
            ON CONFLICT (user_id, document_id)
            DO UPDATE SET {column} = EXCLUDED.{column}, updated_at = now()
            """
        )
        session.execute(sql, {"user_id": user_id, "document_id": document_id})

    @staticmethod
    def clear_read(session: Session, user_id: str, document_id: str) -> None:
        sql = text(
            f"""
            INSERT INTO {DocumentReadsRepository.TABLE} (user_id, document_id, last_read_at, display_read_at)
            VALUES (:user_id, :document_id, NULL, NULL)
            ON CONFLICT (user_id, document_id)
            DO UPDATE SET last_read_at = NULL, display_read_at = NULL, updated_at = now()
            """
        )
        session.execute(sql, {"user_id": user_id, "document_id": document_id})

    @staticmethod
    def get_for_user(session: Session, user_id: str, document_id: str) -> Optional[Dict[str, Any]]:
        sql = text(
            f"""
            SELECT user_id, document_id, last_read_at, display_read_at, created_at, updated_at
            FROM {DocumentReadsRepository.TABLE}
            WHERE user_id = :user_id AND document_id = :document_id
            """
        )
        row = session.execute(sql, {"user_id": user_id, "document_id": document_id}).mappings().first()
        return dict(row) if row else None


