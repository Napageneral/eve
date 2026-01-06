"""Basic CRUD operations for commitments"""

# Consolidated imports
from backend.routers.common import (
    create_router, safe_endpoint, log_simple,
    HTTPException, Query, Depends, BaseModel, Session, get_db
)
from typing import List, Optional, Dict, Any
from datetime import date, timedelta, datetime

from backend.repositories.commitments import CommitmentRepository

router = create_router("/commitments", "Commitments")

class CommitmentFeedbackRequest(BaseModel):
    feedback_type: str  # completed, cancelled, reschedule, snooze
    user_response: Optional[str] = None

class UpdateCommitmentRequest(BaseModel):
    due_date: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None

class SnoozeCommitmentRequest(BaseModel):
    days: int

@router.get("/active")
@safe_endpoint
async def get_active_commitments(session: Session = Depends(get_db)):
    log_simple("Getting active commitments")
    
    repository = CommitmentRepository()
    commitments = repository.get_active_commitments(session)
    return {"commitments": commitments}

@router.get("/all")
@safe_endpoint
async def get_all_commitments(
    include_inactive: bool = Query(True),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None), 
    contact_id: Optional[int] = Query(None),
    session: Session = Depends(get_db)
):
    log_simple(f"Getting commitments: inactive={include_inactive}, contact={contact_id}")
    
    repository = CommitmentRepository()
    
    # Parse date filters if provided
    start_date_obj = None
    end_date_obj = None
    if start_date:
        start_date_obj = datetime.fromisoformat(start_date.replace('Z', '+00:00')).date()
    if end_date:
        end_date_obj = datetime.fromisoformat(end_date.replace('Z', '+00:00')).date()
    
    # Get commitments with filters
    commitments = repository.get_all_commitments(
        session, 
        include_inactive=include_inactive,
        start_date=start_date_obj,
        end_date=end_date_obj,
        contact_id=contact_id
    )
    
    return {"commitments": commitments}

@router.post("/{commitment_id}/complete")
@safe_endpoint
async def complete_commitment(commitment_id: str, session: Session = Depends(get_db)):
    log_simple(f"Completing commitment {commitment_id}")
    
    repository = CommitmentRepository()
    commitment = repository.remove_commitment(session, commitment_id, "completed", "user_confirmed")
    
    if not commitment:
        raise HTTPException(status_code=404, detail="Commitment not found")
    
    return {"status": "completed", "commitment_id": commitment_id}

@router.post("/{commitment_id}/cancel")
@safe_endpoint
async def cancel_commitment(commitment_id: str, session: Session = Depends(get_db)):
    log_simple(f"Cancelling commitment {commitment_id}")
    
    repository = CommitmentRepository()
    commitment = repository.remove_commitment(session, commitment_id, "cancelled", "user_confirmed")
    
    if not commitment:
        raise HTTPException(status_code=404, detail="Commitment not found")
    
    return {"status": "cancelled", "commitment_id": commitment_id}

@router.post("/{commitment_id}/snooze")
@safe_endpoint
async def snooze_commitment(
    commitment_id: str, 
    snooze_request: SnoozeCommitmentRequest, 
    session: Session = Depends(get_db)
):
    log_simple(f"Snoozing commitment {commitment_id} by {snooze_request.days} days")
    
    repository = CommitmentRepository()
    commitment = repository.find_commitment(session, commitment_id)
    
    if not commitment:
        raise HTTPException(status_code=404, detail="Commitment not found")
    
    # Calculate new due date
    if commitment.get("due_date"):
        current_due = date.fromisoformat(commitment["due_date"])
        new_due = current_due + timedelta(days=snooze_request.days)
    else:
        new_due = date.today() + timedelta(days=snooze_request.days)
    
    # Create modification entry
    modification_entry = {
        "type": "snooze",
        "old_value": commitment.get("due_date", "no date"),
        "new_value": new_due.isoformat(),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "days": snooze_request.days
    }
    
    # Update commitment
    modifications = commitment.get("modifications", [])
    modifications.append(modification_entry)
    
    updates = {
        "due_date": new_due,
        "due_specificity": "explicit",
        "modifications": modifications
    }
    
    success = repository.update_commitment(session, commitment_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail="Failed to update commitment")
    
    return {
        "status": "snoozed", 
        "commitment_id": commitment_id,
        "new_due_date": new_due.isoformat(),
        "days_snoozed": snooze_request.days
    }

@router.put("/{commitment_id}")
@safe_endpoint
async def update_commitment(
    commitment_id: str, 
    update: UpdateCommitmentRequest, 
    session: Session = Depends(get_db)
):
    log_simple(f"Updating commitment {commitment_id}")
    
    repository = CommitmentRepository()
    
    # Build updates dict
    updates = {}
    if update.due_date is not None:
        try:
            updates["due_date"] = date.fromisoformat(update.due_date)
            updates["due_specificity"] = "explicit"
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")
    if update.priority is not None:
        updates["priority"] = update.priority
    if update.status is not None:
        updates["status"] = update.status
    if update.notes is not None:
        updates["notes"] = update.notes
    
    success = repository.update_commitment(session, commitment_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail="Commitment not found")
    
    return {"status": "updated", "commitment_id": commitment_id, "updates": updates} 