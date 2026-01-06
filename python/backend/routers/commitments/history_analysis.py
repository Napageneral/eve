"""Historical commitment analysis operations"""

# Consolidated imports
from backend.routers.common import (
    create_router, safe_endpoint, log_simple,
    HTTPException, Query, Depends, Session, get_db
)
from typing import Optional

from backend.repositories.commitments import CommitmentRepository
from backend.db.models import Commitment

router = create_router("/commitments/history", "Commitment History")

@router.post("/analyze-history")
@safe_endpoint
async def analyze_commitment_history(chat_id: Optional[int] = Query(None), session: Session = Depends(get_db)):
    log_simple(f"Starting historical commitment analysis for chat_id: {chat_id}")
    
    # Check for existing tasks
    from backend.celery_service.app import celery_app
    
    active_tasks = celery_app.control.inspect().active()
    if active_tasks:
        for worker, tasks in active_tasks.items():
            for task in tasks:
                if task.get('name') == 'celery.analyze_historical_commitments':
                    task_args = task.get('args', [])
                    if len(task_args) > 0:
                        task_chat_id = task_args[0]
                        if (chat_id is None and task_chat_id is None) or (chat_id == task_chat_id):
                            return {
                                "status": "already_running",
                                "task_id": task.get('id'),
                                "message": f"Historical analysis already in progress for {'all chats' if chat_id is None else f'chat {chat_id}'}"
                            }
    
    # Start new analysis
    from backend.celery_service.tasks.commitment_history import analyze_historical_commitments_task
    
    task = analyze_historical_commitments_task.delay(chat_id)
    log_simple(f"Task submitted: {task.id}")
    
    # Try to get setup details
    try:
        result = task.get(timeout=10)
        return {
            "status": result.get("status", "started"),
            "task_id": task.id,
            "chain_id": result.get("chain_id"),
            "chat_id": chat_id,
            "total_conversations": result.get("total_conversations", 0),
            "message": result.get("message", f"Historical analysis started for {'all chats' if chat_id is None else f'chat {chat_id}'}")
        }
    except Exception:
        # Fallback response
        return {
            "status": "started",
            "task_id": task.id,
            "chat_id": chat_id,
            "message": f"Historical analysis started for {'all chats' if chat_id is None else f'chat {chat_id}'}"
        }

@router.get("/check-running-analysis")
@safe_endpoint
async def check_running_historical_analysis(chat_id: Optional[int] = Query(None)):
    log_simple(f"Checking for running analysis for chat_id: {chat_id}")
    
    from backend.celery_service.app import celery_app
    
    active_tasks = celery_app.control.inspect().active()
    if not active_tasks:
        return {"is_running": False}
    
    # Look for historical analysis tasks
    for worker, tasks in active_tasks.items():
        for task in tasks:
            if task.get('name') == 'celery.analyze_historical_commitments':
                task_args = task.get('args', [])
                task_chat_id = task_args[0] if len(task_args) > 0 else None
                
                if (chat_id is None and task_chat_id is None) or (chat_id == task_chat_id):
                    result = celery_app.AsyncResult(task.get('id'))
                    return {
                        "is_running": True,
                        "task_id": task.get('id'),
                        "chat_id": task_chat_id,
                        "status": result.state,
                        "info": result.info if isinstance(result.info, dict) else {}
                    }
    
    # Check for chain tasks
    for worker, tasks in active_tasks.items():
        for task in tasks:
            if task.get('name') in ['celery.process_single_historical_conversation', 
                                   'celery.initialize_historical_analysis',
                                   'celery.finalize_historical_analysis']:
                task_kwargs = task.get('kwargs', {})
                global_chat_id = task_kwargs.get('global_analysis_chat_id')
                
                if (chat_id is None and global_chat_id is None) or (chat_id == global_chat_id):
                    return {
                        "is_running": True,
                        "task_id": task.get('id'),
                        "chat_id": global_chat_id,
                        "status": "PROGRESS",
                        "current_task": task.get('name'),
                        "info": {
                            "index": task_kwargs.get('index', 0),
                            "total": task_kwargs.get('total', 0),
                            "conversation_id": task_kwargs.get('conversation_id')
                        }
                    }
    
    return {"is_running": False}

@router.get("/history")
@safe_endpoint
async def get_commitment_history(
    contact_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    session: Session = Depends(get_db)
):
    log_simple(f"Getting commitment history: contact={contact_id}, status={status}")
    
    query = session.query(Commitment)
    
    if contact_id:
        query = query.filter(Commitment.to_person_id == contact_id)
    if status:
        query = query.filter(Commitment.status == status)
    
    commitments = query.order_by(Commitment.created_date.desc()).limit(limit).all()
    
    return {
        "commitments": [
            {
                "id": c.commitment_id,
                "commitment": c.commitment_text,
                "status": c.status,
                "created_date": c.created_date.isoformat() if c.created_date else None,
                "due_date": c.due_date.isoformat() if c.due_date else None,
                "completed_date": c.completed_date.isoformat() if c.completed_date else None,
                "resolution_method": c.resolution_method,
                "priority": c.priority,
                "context": c.context
            }
            for c in commitments
        ]
    }

@router.get("/stats")
@safe_endpoint
async def get_commitment_stats(session: Session = Depends(get_db)):
    log_simple("Getting commitment statistics")
    
    repository = CommitmentRepository()
    active_commitments = repository.get_active_commitments(session)
    
    # Count by status and priority
    status_counts = {}
    priority_counts = {}
    
    for commitment in active_commitments:
        status = commitment.get("status", "pending")
        priority = commitment.get("priority", "medium")
        
        status_counts[status] = status_counts.get(status, 0) + 1
        priority_counts[priority] = priority_counts.get(priority, 0) + 1
    
    completed_count = session.query(Commitment).filter_by(status="completed").count()
    
    return {
        "active_total": len(active_commitments),
        "completed_total": completed_count,
        "status_breakdown": status_counts,
        "priority_breakdown": priority_counts
    } 