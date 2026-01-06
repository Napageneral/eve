"""Repository for historic analysis status operations."""

import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from .core.generic import GenericRepository

logger = logging.getLogger(__name__)


class HistoricAnalysisRepository(GenericRepository):
    """Repository for historic analysis status tracking."""
    
    TABLE = "historic_analysis_status"
    
    @classmethod
    def get_status_by_user(cls, session: Session, user_id: int) -> Optional[Dict[str, Any]]:
        """Get analysis status for a user."""
        return cls.fetch_one(session,
            "SELECT * FROM historic_analysis_status WHERE user_id = :user_id",
            {"user_id": user_id}
        )
    
    @classmethod
    def get_status_by_run_id(cls, session: Session, run_id: str) -> Optional[Dict[str, Any]]:
        """Get analysis status by run_id."""
        return cls.fetch_one(session,
            "SELECT * FROM historic_analysis_status WHERE run_id = :run_id",
            {"run_id": run_id}
        )
    
    @classmethod
    def upsert_status(
        cls, 
        session: Session,
        user_id: int,
        run_id: str,
        total_conversations: int,
        status: str = "running"
    ) -> None:
        """Insert or update historic analysis status."""
        from datetime import datetime
        now = datetime.utcnow()
        
        cls.execute(session, """
            INSERT INTO historic_analysis_status
                (user_id, status, started_at, run_id, total_conversations, analyzed_conversations, failed_conversations, created_at, updated_at)
            VALUES
                (:user_id, :status, :now, :run_id, :total, 0, 0, :now, :now)
            ON CONFLICT (user_id) DO UPDATE SET
                status = :status,
                started_at = :now,
                run_id = :run_id,
                total_conversations = :total,
                analyzed_conversations = 0,
                failed_conversations = 0,
                updated_at = CURRENT_TIMESTAMP
        """, {
            "user_id": user_id,
            "status": status,
            "now": now,
            "run_id": run_id,
            "total": total
        })
    
    @classmethod
    def finalize_status(
        cls,
        session: Session, 
        run_id: str,
        analyzed_count: int,
        failed_count: int
    ) -> None:
        """Mark analysis as completed."""
        cls.execute(session, """
            UPDATE historic_analysis_status
            SET status = 'completed',
                completed_at = CURRENT_TIMESTAMP,
                analyzed_conversations = :analyzed,
                failed_conversations = :failed,
                updated_at = CURRENT_TIMESTAMP
            WHERE run_id = :run_id
        """, {
            "analyzed": analyzed_count,
            "failed": failed_count,
            "run_id": run_id
        })

