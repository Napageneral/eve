"""
DLQ Service - Business logic for Dead Letter Queue operations
Moved from celery_service/activities/dlq.py
"""
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from backend.db.session_manager import new_session
from backend.repositories.dlq import DLQRepository

logger = logging.getLogger(__name__)


class DLQService:
    """Service for Dead Letter Queue operations."""

    @staticmethod
    def store_failed_task(task_id: str, task_name: str, args: list, kwargs: dict, error_msg: str, queue_name: str = 'unknown'):
        """
        Store a failed task in the Dead Letter Queue storage.
        Moved from activities/dlq.py
        
        Args:
            task_id: The unique task ID
            task_name: Name of the failed task
            args: Original task arguments
            kwargs: Original task keyword arguments  
            error_msg: Error message from the failure
            queue_name: Name of the queue the task was in
        """
        try:
            with new_session() as session:
                # Use repository to store failed task
                dlq_id = DLQRepository.store_failed_task(
                    session=session,
                    task_id=task_id,
                    task_name=task_name,
                    args=args,
                    kwargs=kwargs,
                    error_msg=error_msg,
                    queue_name=queue_name
                )
                
                session.commit()
                
                if dlq_id:
                    logger.info(f"Stored task {task_id} in DLQ with ID {dlq_id}: {task_name}")
                
                # Log detailed failure information
                logger.error(f"DLQ: Task {task_name}[{task_id}] from queue '{queue_name}' failed permanently: {error_msg}")
                
                # Could add additional alerting here (email, Slack, etc.)
                
        except Exception as e:
            logger.error(f"Failed to store task {task_id} in DLQ: {e}")

    @staticmethod
    def process_dlq_items():
        """
        Process items in the DLQ.
        Pure function for cleanup, reporting, or retry logic.
        Moved from activities/dlq.py
        """
        try:
            with new_session() as session:
                # Get unresolved failed tasks from the last 24 hours using repository
                failed_tasks = DLQRepository.get_unresolved_tasks(session, hours=24)
                
                if not failed_tasks:
                    logger.info("No failed tasks in DLQ to process")
                    return
                
                # Group by task type for reporting
                task_failures = {}
                for task in failed_tasks:
                    task_type = task['task_name']
                    if task_type not in task_failures:
                        task_failures[task_type] = []
                    task_failures[task_type].append(task)
                
                # Log summary
                logger.info(f"DLQ Processing: Found {len(failed_tasks)} unresolved failed tasks")
                for task_type, tasks in task_failures.items():
                    logger.info(f"  {task_type}: {len(tasks)} failures")
                
                # Could implement retry logic here
                # Could send alerts/reports here
                # Could auto-resolve old items here
                
        except Exception as e:
            logger.error(f"Error processing DLQ items: {e}")

    @staticmethod
    def retry_dlq_task(failed_task_id: int) -> bool:
        """
        Retry a task from the DLQ.
        Moved from activities/dlq.py
        
        Args:
            failed_task_id: Database ID of the failed task record
        
        Returns:
            True if retry was successful, False otherwise
        """
        try:
            # Import here to avoid circular imports
            from backend.db.models import FailedTask
            
            with new_session() as session:
                failed_task = session.query(FailedTask).filter_by(id=failed_task_id).first()
                
                if not failed_task:
                    logger.error(f"Failed task {failed_task_id} not found in DLQ")
                    return False
                
                if failed_task.resolved:
                    logger.warning(f"Failed task {failed_task_id} is already resolved")
                    return False
                
                # Parse args and kwargs
                args = json.loads(failed_task.args) if failed_task.args else []
                kwargs = json.loads(failed_task.kwargs) if failed_task.kwargs else {}
                
                # Attempt to requeue the original task
                from backend.celery_service.app import celery_app
                task_func = celery_app.tasks.get(failed_task.task_name)
                
                if not task_func:
                    logger.error(f"Task function {failed_task.task_name} not found")
                    return False
                
                # Requeue the task
                result = task_func.apply_async(args=args, kwargs=kwargs)
                
                # Mark as resolved
                failed_task.resolved = True
                failed_task.resolved_at = datetime.utcnow()
                session.commit()
                
                logger.info(f"Successfully requeued failed task {failed_task_id} as {result.id}")
                return True
                
        except Exception as e:
            logger.error(f"Error retrying DLQ task {failed_task_id}: {e}")
            return False

    @staticmethod
    def get_dlq_stats() -> Dict[str, Any]:
        """
        Get statistics about the Dead Letter Queue.
        Moved from activities/dlq.py
        
        Returns:
            Dictionary with DLQ statistics
        """
        try:
            with new_session() as session:
                return DLQRepository.get_dlq_stats(session)
                
        except Exception as e:
            logger.error(f"Error getting DLQ stats: {e}")
            return {
                'total_failed_tasks': 0,
                'unresolved_tasks': 0,
                'failed_last_24h': 0,
                'failure_types': [],
                'error': str(e)
            } 