from fastapi import HTTPException

from backend.routers.common import create_router, safe_endpoint
from backend.services.analysis.redis_counters import snapshot


router = create_router("/analysis/run", "Analysis Progress")


@router.get("/{run_id}/progress")
@safe_endpoint
async def get_run_progress(run_id: str):
    snap = snapshot(run_id)
    total = int(snap.get("total", snap.get("total_convos", 0)) or 0)
    running = bool(snap.get("running", False))
    if total == 0 and not running:
        raise HTTPException(status_code=404, detail="Run not found")

    # Normalize field names for UI consumption
    return {
        "run_id": str(run_id),
        "total": total,
        "pending": int(snap.get("pending", snap.get("pending_convos", 0)) or 0),
        "processing": int(snap.get("processing", snap.get("processing_convos", 0)) or 0),
        "success": int(snap.get("success", snap.get("successful_convos", 0)) or 0),
        "failed": int(snap.get("failed", snap.get("failed_convos", 0)) or 0),
        "percent_complete": float(snap.get("percent_complete", snap.get("percentage", 0.0)) or 0.0),
        "qps": float(snap.get("qps", 0.0) or 0.0),
        "overall_status": snap.get("overall_status", snap.get("status", "processing")),
    }


