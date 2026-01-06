"""Admin Celery queue utilities (moved from queue_admin_router.py)"""
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, Optional
import logging

from backend.services.infra.queue_monitoring import (
    get_queue_lengths,
    get_worker_stats,
    get_comprehensive_status,
    clear_queue,
    purge_old_dlq_items,
)
from backend.services.infra.dlq import DLQService
from backend.celery_service.tasks.dlq import retry_dlq_task_task as retry_dlq_task
from backend.db.session_manager import new_session
from backend.db.models import FailedTask
from backend.config import settings

CHATSTATS_BROKER_URL = settings.broker_url

router = APIRouter(tags=["Queue Admin"])
logger = logging.getLogger(__name__)

@router.get("/status")
async def get_queue_status():
    """Comprehensive queue + worker status"""
    try:
        status = get_comprehensive_status()
        return {"success": True, "data": status}
    except Exception as e:
        logger.error(f"Error getting queue status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/queues")
async def get_queues_info():
    try:
        return {"success": True, "data": get_queue_lengths()}
    except Exception as e:
        logger.error(f"Error getting queue info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/workers")
async def get_workers_info():
    try:
        return {"success": True, "data": get_worker_stats()}
    except Exception as e:
        logger.error(f"Error getting worker info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/dlq/stats")
async def get_dlq_statistics():
    try:
        return {"success": True, "data": DLQService.get_dlq_stats()}
    except Exception as e:
        logger.error(f"Error getting DLQ stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/dlq/items")
async def get_dlq_items(limit: int = Query(50), resolved: Optional[bool] = Query(None)):
    try:
        with new_session() as session:
            query = session.query(FailedTask)
            if resolved is not None:
                query = query.filter(FailedTask.resolved == resolved)
            items = query.order_by(FailedTask.failed_at.desc()).limit(limit).all()
            return {
                "success": True,
                "data": [
                    {
                        "id": i.id,
                        "task_id": i.task_id,
                        "task_name": i.task_name,
                        "queue_name": i.queue_name,
                        "error_message": i.error_message,
                        "failed_at": i.failed_at.isoformat() if i.failed_at else None,
                        "retry_count": i.retry_count,
                        "last_retry_at": i.last_retry_at.isoformat() if i.last_retry_at else None,
                        "resolved": i.resolved,
                        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
                    }
                    for i in items
                ],
            }
    except Exception as e:
        logger.error(f"Error getting DLQ items: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/dlq/retry/{failed_task_id}")
async def retry_failed_task(failed_task_id: int):
    try:
        if retry_dlq_task.delay(failed_task_id):
            return {"success": True, "message": f"Retry task queued for failed task {failed_task_id}"}
        raise HTTPException(status_code=404, detail="Failed task not found or already resolved")
    except Exception as e:
        logger.error(f"Error retrying failed task {failed_task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/queue/{queue_name}/clear")
async def clear_specific_queue(queue_name: str):
    try:
        result = clear_queue(queue_name)
        if result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"Error clearing queue {queue_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/dlq/purge")
async def purge_old_dlq(days_old: int = Query(30)):
    try:
        result = purge_old_dlq_items(days_old)
        if result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"Error purging old DLQ items: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
async def queue_health_check():
    try:
        status = get_comprehensive_status()
        return {
            "success": True,
            "data": {
                "healthy": status.get("healthy", False),
                "timestamp": status.get("timestamp"),
                "broker_type": status.get("broker_type"),
                "total_pending_tasks": status.get("queues", {}).get("total_pending", 0),
                "active_workers": status.get("workers", {}).get("active_workers", 0),
                "unresolved_dlq_items": status.get("dlq", {}).get("unresolved_tasks", 0),
                "issues": status.get("issues", []),
            },
        }
    except Exception as e:
        logger.error(f"Error in queue health check: {e}")
        return {"success": False, "error": str(e)}

@router.post("/purge_queues")
async def purge_all_queues() -> Dict[str, Any]:
    try:
        from backend.celery_service.app import celery_app
        result = celery_app.control.purge()
        return {"status": "success", "message": "All queues purged", "purged_counts": result}
    except Exception as e:
        logger.error(f"Failed to purge queues: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/task/{task_id}")
async def get_task_status(task_id: str) -> Dict[str, Any]:
    try:
        from backend.celery_service.app import celery_app
        from celery.result import AsyncResult
        result = AsyncResult(task_id, app=celery_app)
        return {
            "task_id": task_id,
            "status": result.status,
            "result": result.result,
            "traceback": result.traceback,
            "successful": result.successful(),
            "failed": result.failed(),
        }
    except Exception as e:
        logger.error(f"Failed to get task status for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/activate_subscription")
async def dev_activate_subscription() -> Dict[str, Any]:
    logger.info("Development subscription activation called")
    return {
        "status": "success",
        "message": "Development subscription activated (placeholder)",
        "note": "This is a development endpoint only",
    }

@router.get("/debug/celery-tasks")
async def debug_celery_tasks():
    """Debug endpoint to list registered Celery tasks"""
    from backend.celery_service.app import get_celery_app
    
    app = get_celery_app()
    tasks = list(app.tasks.keys())
    
    return {
        "total_tasks": len(tasks),
        "ask_eve_registered": "celery.ask_eve" in tasks,
        "all_tasks": sorted(tasks)
    }

@router.get("/debug/celery-status")
async def debug_celery_status():
    """Comprehensive Celery status check"""
    from backend.celery_service.app import get_celery_app
    import redis
    
    app = get_celery_app()
    inspector = app.control.inspect()
    
    # Get registered tasks
    tasks = list(app.tasks.keys())
    
    # Get worker info
    active_workers = inspector.active() or {}
    worker_stats = inspector.stats() or {}
    active_queues = inspector.active_queues() or {}
    
    # Check Redis queue lengths
    queue_info = {}
    try:
        r = redis.from_url(CHATSTATS_BROKER_URL)
        for queue_name in ['chatstats-report', 'chatstats-analysis', 'chatstats-bulk', 'chatstats-display']:
            queue_info[queue_name] = r.llen(queue_name)
        r.close()
    except Exception as e:
        queue_info["error"] = str(e)
    
    return {
        "registered_tasks": {
            "total": len(tasks),
            "ask_eve_registered": "celery.ask_eve" in tasks,
            "all_tasks": sorted(tasks)
        },
        "workers": {
            "total_active": len(active_workers),
            "worker_names": list(active_workers.keys()),
            "worker_stats": worker_stats,
            "active_queues": active_queues
        },
        "queues": {
            "lengths": queue_info,
            "expected_queues": ['chatstats-report', 'chatstats-analysis', 'chatstats-bulk', 'chatstats-display']
        },
        "broker_url": CHATSTATS_BROKER_URL.split('@')[-1] if '@' in CHATSTATS_BROKER_URL else CHATSTATS_BROKER_URL  # Hide credentials
    }

@router.get("/debug/404-test")
async def debug_404_test():
    """Debug endpoint to test if API routing is working correctly"""
    logger.info("404 debug endpoint called successfully")
    return {
        "status": "success",
        "message": "API routing is working correctly",
        "endpoint": "/api/queue/admin/debug/404-test",
        "timestamp": "2024-01-01T00:00:00Z"
    }

@router.get("/debug/global-analysis-status")
async def debug_global_analysis_status():
    """Debug endpoint to check global analysis status without WebSocket"""
    try:
        from backend.db.session_manager import new_session
        from backend.repositories.conversation_analysis import ConversationAnalysisRepository
        from backend.db.sql import fetch_all
        
        with new_session() as session:
            # Get summary for all chats
            all_chats = fetch_all(session, "SELECT id, chat_name FROM chats ORDER BY id")
            
            total_chats = len(all_chats)
            analysis_summary = {
                "total_chats": total_chats,
                "chat_details": []
            }
            
            for chat_row in all_chats:
                chat_id = chat_row["id"]
                chat_name = chat_row["chat_name"]
                summary = ConversationAnalysisRepository.get_chat_analysis_summary(session, chat_id)
                
                analysis_summary["chat_details"].append({
                    "chat_id": chat_id,
                    "chat_name": chat_name,
                    "summary": summary
                })
            
            return {
                "status": "success",
                "message": "Global analysis status retrieved successfully",
                "data": analysis_summary
            }
    except Exception as e:
        logger.error(f"Failed to get global analysis status: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to get global analysis status: {str(e)}"
        } 