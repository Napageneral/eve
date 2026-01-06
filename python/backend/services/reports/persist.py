"""
Report persistence services – DB read/write helpers & idempotency
"""
from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services.core.utils import BaseService, timed, with_session
from backend.services.core.event_bus import EventBus
from backend.db.session_manager import new_session

logger = logging.getLogger(__name__)


class ReportPersistService(BaseService):
    """Insert / read / delete rows in the `reports` table."""

    @staticmethod
    @timed("save_report")
    @with_session(commit=True)
    def save_report(
        prompt_template_id: int,
        combined_prompt_text: str,
        model_used: str,
        response_text: str,
        cost: float,
        context_selections: Dict[str, int],
        chat_id: Optional[int] = None,
        contact_id: Optional[int] = None,
        title: Optional[str] = None,
        suggested_preview_description: Optional[str] = None,
        session: Session | None = None,
    ) -> int:
        result = session.execute(
            text(
                """
                INSERT INTO reports
                    (prompt_template_id, combined_prompt_text, model_used, response_text,
                     chat_id, contact_id, title, cost, context_selections,
                     suggested_preview_description, created_at, updated_at)
                VALUES
                    (:prompt_template_id, :combined_prompt_text, :model_used, :response_text,
                     :chat_id, :contact_id, :title, :cost, :context_selections,
                     :suggested_preview_description, :created_at, :updated_at)
                RETURNING id
            """
            ),
            {
                "prompt_template_id": prompt_template_id,
                "combined_prompt_text": combined_prompt_text,
                "model_used": model_used,
                "response_text": response_text,
                "chat_id": chat_id,
                "contact_id": contact_id,
                "title": title,
                "cost": str(cost),
                "context_selections": json.dumps(context_selections),
                "suggested_preview_description": suggested_preview_description,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            },
        ).first()
        report_id = result[0]
        logger.info("Saved report with ID %s", report_id)
        return report_id

    @staticmethod
    @timed("get_report")
    @with_session(commit=False)
    def get_report(report_id: int, session=None) -> Optional[Dict[str, Any]]:
        return ReportRepository.get_report(session, report_id)

    @staticmethod
    @timed("delete_report")
    @with_session(commit=True)
    def delete_report(report_id: int, session=None) -> bool:
        return ReportRepository.delete_report(session, report_id)


class ReportIdempotencyService(BaseService):
    """Compute / check / mark idempotency keys."""

    @staticmethod
    def compute_idempotency_key(prompt_template_id: int, placeholder_to_cs_id: Dict[str, int]) -> str:
        key_string = f"template:{prompt_template_id}|contexts:{json.dumps(placeholder_to_cs_id, sort_keys=True)}"
        return hashlib.sha256(key_string.encode()).hexdigest()

    @staticmethod
    @timed("check_existing_report")
    @with_session(commit=False)
    def check_existing_report(idempotency_key: str, session=None) -> Optional[Dict[str, Any]]:
        result = session.execute(
            text(
                """
                SELECT id, title, created_at
                FROM reports
                WHERE idempotency_key = :key
                ORDER BY created_at DESC
                LIMIT 1
            """
            ),
            {"key": idempotency_key},
        ).first()
        if result:
            return {"report_id": result.id, "title": result.title, "created_at": result.created_at}
        return None

    @staticmethod
    @timed("mark_report_idempotency")
    @with_session(commit=True)
    def mark_report_with_idempotency(report_id: int, idempotency_key: str, session=None):
        session.execute(
            text("UPDATE reports SET idempotency_key = :key WHERE id = :report_id"),
            {"report_id": report_id, "key": idempotency_key},
        )
        logger.info("Marked report %s with key %s", report_id, idempotency_key)


class ReportEventsService(BaseService):
    """Publish report-generation events onto Redis Streams."""

    @staticmethod
    def publish_report_event(event_type: str, data: Dict[str, Any], scope: Optional[str] = None):
        if scope is None:
            if "task_id" in data:
                scope = f"task:{data['task_id']}"
            elif "chat_id" in data:
                scope = str(data["chat_id"])
            else:
                scope = "global"
        EventBus.publish(scope, event_type, data)

    # Convenience summary helper (used by UI)
    @staticmethod
    def get_report_generation_summary(chat_id: int | None = None, contact_id: int | None = None) -> Dict[str, Any]:
        from sqlalchemy import text as _sql_text
        with new_session() as session:
            base_where: List[str] = []
            params: dict[str, Any] = {}
            if chat_id:
                base_where.append("chat_id = :chat_id")
                params["chat_id"] = chat_id
            if contact_id:
                base_where.append("contact_id = :contact_id")
                params["contact_id"] = contact_id
            where_clause = f"WHERE {' AND '.join(base_where)}" if base_where else ""
            report_stats = session.execute(
                _sql_text(
                    f"""
                    SELECT COUNT(*)                         AS total,
                           COUNT(CASE WHEN created_at > datetime('now', '-1 hour') THEN 1 END) AS recent
                    FROM reports {where_clause}
                    """
                ),
                params,
            ).first()
            display_stats = session.execute(
                _sql_text(
                    f"""
                    SELECT COUNT(*)
                    FROM report_displays rd JOIN reports r ON rd.report_id = r.id {where_clause}
                    """
                ),
                params,
            ).first()
            return {
                "reports": {
                    "total": report_stats.total,
                    "recent": report_stats.recent,
                },
                "displays": {
                    "total": display_stats.total,
                },
            }


__all__ = [
    "ReportPersistService",
    "ReportIdempotencyService",
    "ReportEventsService",
] 