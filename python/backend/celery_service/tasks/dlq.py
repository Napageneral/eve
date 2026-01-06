"""
Dead Letter Queue (DLQ) Celery tasks - orchestration layer
"""
from celery import shared_task
from backend.celery_service.app import celery_app
from backend.celery_service.tasks.base import BaseTaskWithDLQ
import logging

logger = logging.getLogger(__name__)

@celery_app.task(queue='chatstats-dlq', name='celery.send_to_dlq', base=BaseTaskWithDLQ)
def send_to_dlq_task(task_id: str, task_name: str, args: list, kwargs: dict, error_msg: str, queue_name: str = 'unknown'):
    """
    Celery task wrapper for sending failed tasks to DLQ.
    """
    from backend.services.infra.dlq import DLQService
    return DLQService.store_failed_task(task_id, task_name, args, kwargs, error_msg, queue_name)

@celery_app.task(queue='chatstats-dlq', name='celery.process_dlq_items', base=BaseTaskWithDLQ)
def process_dlq_items_task():
    """
    Scheduled Celery task for processing DLQ items.
    """
    from backend.services.infra.dlq import DLQService
    return DLQService.process_dlq_items()

@celery_app.task(queue='chatstats-dlq', name='celery.retry_dlq_task', base=BaseTaskWithDLQ)
def retry_dlq_task_task(failed_task_id: int):
    """
    Celery task wrapper for retrying DLQ items.
    """
    from backend.services.infra.dlq import DLQService
    return DLQService.retry_dlq_task(failed_task_id) 