"""Repository for chatbot document display operations (raw SQL only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from .core.generic import GenericRepository


class DocumentDisplayRepository(GenericRepository):
    """Raw-SQL repository for the ``chatbot_document_displays`` table."""

    TABLE = "chatbot_document_displays"

    # ------------------------------------------------------------------
    # Create / Read
    # ------------------------------------------------------------------

    @classmethod
    def create_display(cls, session: Session, data: Dict[str, Any]) -> Optional[int]:
        """Insert a new document display and return its ID.

        Expected keys in ``data``:
        - document_id (UUID as str)
        - document_created_at (datetime or ISO str) – optional; can be None
        - generated_code (str)
        - model_used (str | None)
        - cost (str | float | None)
        - created_at/updated_at optional; set automatically if missing
        """
        now = datetime.utcnow()
        payload: Dict[str, Any] = {
            "document_id": data["document_id"],
            "document_created_at": data.get("document_created_at"),
            "generated_code": data["generated_code"],
            "model_used": data.get("model_used"),
            "cost": str(data.get("cost", "0")),
            "created_at": data.get("created_at", now),
            "updated_at": data.get("updated_at", now),
        }
        return cls.create(session, payload)

    @classmethod
    def get_display_by_id(cls, session: Session, display_id: int) -> Optional[Dict[str, Any]]:
        # GenericRepository.get_by_id for subclasses expects only (session, record_id)
        return super().get_by_id(session, display_id)

    @classmethod
    def get_latest_for_document(cls, session: Session, document_id: str) -> Optional[Dict[str, Any]]:
        sql = f"""
            SELECT id, document_id, document_created_at, generated_code, model_used, cost, created_at, updated_at
            FROM {cls.TABLE}
            WHERE document_id = :document_id
            ORDER BY created_at DESC
            LIMIT 1
        """
        return cls.fetch_one(session, sql, {"document_id": document_id})

    @classmethod
    def get_for_document_version(
        cls, session: Session, document_id: str, document_created_at: datetime | str
    ) -> Optional[Dict[str, Any]]:
        sql = f"""
            SELECT id, document_id, document_created_at, generated_code, model_used, cost, created_at, updated_at
            FROM {cls.TABLE}
            WHERE document_id = :document_id AND document_created_at = :document_created_at
            ORDER BY created_at DESC
            LIMIT 1
        """
        params = {
            "document_id": document_id,
            "document_created_at": document_created_at,
        }
        return cls.fetch_one(session, sql, params)

    @classmethod
    def list_for_document(cls, session: Session, document_id: str) -> List[Dict[str, Any]]:
        sql = f"""
            SELECT id, document_id, document_created_at, generated_code, model_used, cost, created_at, updated_at
            FROM {cls.TABLE}
            WHERE document_id = :document_id
            ORDER BY created_at DESC
        """
        return cls.fetch_all(session, sql, {"document_id": document_id})


