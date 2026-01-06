from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from .base import BaseRepository

__all__ = ["StatusMixin"]


class StatusMixin(BaseRepository):
    """Reusable helper for simple *status + retry* updates.

    Intended for tables that track a `status` column (``TEXT``) and possibly a
    numeric ``retry_count``.  Consumers call::

        StatusMixin.set_status(
            session,
            "conversation_analyses",
            "id",
            ca_id,
            CA_STATUS_FAILED,
            extra={"error_message": "Boom!"},
            bump_retry=True,
        )
    """

    @staticmethod
    def set_status(
        session: Session,
        table: str,
        id_col: str,
        record_id: Any,
        status: str,
        *,
        extra: Optional[Dict[str, Any]] = None,
        bump_retry: bool = False,
    ) -> int:
        """Generic UPDATE for status, optional retry-count increment & extra cols."""

        fields = ["status = :status", "updated_at = :now"]
        params: Dict[str, Any] = {
            "status": status,
            "now": datetime.utcnow(),
            "record_id": record_id,
        }

        if bump_retry:
            fields.append("retry_count = retry_count + 1")

        if extra:
            for key, value in extra.items():
                fields.append(f"{key} = :{key}")
                params[key] = value

        sql = f"UPDATE {table} SET {', '.join(fields)} WHERE {id_col} = :record_id"
        return BaseRepository.execute(session, sql, params) 