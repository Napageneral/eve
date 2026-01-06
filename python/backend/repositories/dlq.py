"""
Repository for Dead Letter Queue operations.
Handles failed task storage and retrieval.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from .core.generic import GenericRepository
import json
import logging

logger = logging.getLogger(__name__)

class DLQRepository(GenericRepository):
    """Repository for Dead Letter Queue operations."""
    
    TABLE = "failed_tasks"
    
    @classmethod
    def store_failed_task(
        cls, 
        session: Session,
        task_id: str,
        task_name: str,
        args: list,
        kwargs: dict,
        error_msg: str,
        queue_name: str = 'unknown'
    ) -> Optional[int]:
        """Store or update a failed task in the DLQ."""
        now = datetime.utcnow()

        # Attempt upsert. If the row already exists, we will increment retry_count next.
        row_id = cls.upsert(
            session,
            "failed_tasks",
            {"task_id": task_id},
            {
                "task_name": task_name,
                "queue_name": queue_name,
                "args": json.dumps(args) if args else None,
                "kwargs": json.dumps(kwargs) if kwargs else None,
                "error_message": error_msg,
                "failed_at": now,
                "retry_count": 0,
                "resolved": False,
                "last_retry_at": now,
            },
        )

        # Bump retry_count and update error metadata for existing rows (no 'status' column on failed_tasks).
        cls.execute(
            session,
            """
            UPDATE failed_tasks
            SET retry_count = retry_count + 1,
                error_message = :error_message,
                last_retry_at = :now
            WHERE id = :id
            """,
            {"id": row_id, "error_message": error_msg, "now": now},
        )

        return row_id
    
    @classmethod
    def get_unresolved_tasks(cls, session: Session, hours: int = 24) -> List[Dict[str, Any]]:
        """Get unresolved failed tasks from the last N hours."""
        since = datetime.utcnow() - timedelta(hours=hours)
        sql = """
            SELECT * FROM failed_tasks
            WHERE resolved = 0 AND failed_at >= :since
            ORDER BY failed_at DESC
        """
        return cls.fetch_all(session, sql, {"since": since})
    
    @classmethod
    def get_dlq_stats(cls, session: Session) -> Dict[str, Any]:
        """Get DLQ statistics."""
        stats = {
            'total_failed_tasks': cls.count(session),
            'unresolved_tasks': cls.count(session, resolved=False),
            'failed_last_24h': 0,
            'failure_types': []
        }
        
        # Failed in last 24h
        since_24h = datetime.utcnow() - timedelta(hours=24)
        stats['failed_last_24h'] = cls.fetch_scalar(session,
            "SELECT COUNT(*) FROM failed_tasks WHERE failed_at >= :since",
            {"since": since_24h}
        ) or 0
        
        # Most common failure types
        sql = """
            SELECT task_name, COUNT(*) as count
            FROM failed_tasks
            WHERE resolved = 0
            GROUP BY task_name
            ORDER BY count DESC
            LIMIT 10
        """
        failure_types = cls.fetch_all(session, sql)
        stats['failure_types'] = [
            {'task_name': ft['task_name'], 'count': ft['count']} 
            for ft in failure_types
        ]
        
        return stats
    
    @classmethod
    def mark_resolved(cls, session: Session, task_id: str) -> int:
        """Mark a task as resolved."""
        sql = """
            UPDATE failed_tasks 
            SET resolved = 1, resolved_at = :resolved_at 
            WHERE task_id = :task_id
        """
        return cls.execute(session, sql, {
            "task_id": task_id,
            "resolved_at": datetime.utcnow()
        })
    
    @classmethod
    def get_task_by_id(cls, session: Session, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a failed task by its task ID."""
        sql = "SELECT * FROM failed_tasks WHERE task_id = :task_id"
        return cls.fetch_one(session, sql, {"task_id": task_id})

    # ------------------------------------------------------------------
    # Maintenance helpers
    # ------------------------------------------------------------------

    @classmethod
    def purge_before(cls, session: Session, days_old: int = 30) -> int:
        """Delete resolved tasks older than given days. Returns rows deleted."""
        cutoff = datetime.utcnow() - timedelta(days=days_old)
        sql = """
            DELETE FROM failed_tasks
            WHERE resolved = 1 AND resolved_at < :cutoff
        """
        result = cls.execute_write(session, sql, {"cutoff": cutoff})
        return result.rowcount if result else 0
    
    @classmethod
    def get_recent_failures(cls, session: Session, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent failures ordered by failure time."""
        sql = """
            SELECT * FROM failed_tasks
            ORDER BY failed_at DESC
            LIMIT :limit
        """
        return cls.fetch_all(session, sql, {"limit": limit}) 