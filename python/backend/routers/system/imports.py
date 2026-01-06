# Consolidated imports
from backend.routers.common import (
    create_router, safe_endpoint, log_simple,
    HTTPException, BaseModel
)
from typing import Optional
import time
from datetime import datetime, timezone

from backend.etl.iphone_backup import list_available_backups
from backend.etl.data_importer import import_live_data, import_backup_data

router = create_router("/import", "Data Import")

class BackupImportRequest(BaseModel):
    backup_path: str

class LiveImportRequest(BaseModel):
    since_timestamp: Optional[int] = None

@router.post("/live")
@safe_endpoint
async def trigger_live_import(request: LiveImportRequest):
    since_date = (
        datetime.fromtimestamp(request.since_timestamp, timezone.utc) 
        if request.since_timestamp else None
    )
    
    log_simple(f"Starting live import since {since_date or 'beginning'}")
    import_live_data(since_date)
    log_simple("Live import completed")
    
    return {"success": True, "timestamp": int(time.time())}

@router.get("/backups")
@safe_endpoint
async def get_backups():
    log_simple("Listing available backups")
    backups = list_available_backups()
    log_simple(f"Found {len(backups)} backups")
    
    return {
        "backups": [
            {
                "path": str(backup["path"]),
                "name": str(backup["name"]),
                "date": str(backup["date"]),
            }
            for backup in backups
        ]
    }

@router.post("/backup")
@safe_endpoint
async def import_backup(request: BackupImportRequest):
    log_simple(f"Starting backup import from {request.backup_path}")
    
    if not request.backup_path:
        raise HTTPException(status_code=422, detail="backup_path is required")
    
    import_backup_data(request.backup_path)
    log_simple("Backup import completed")
    
    # Get backup info for response
    backups = list_available_backups()
    backup_info = next((b for b in backups if b["path"] == request.backup_path), None)
    if not backup_info:
        raise HTTPException(status_code=404, detail="Backup not found")
    
    return {
        "success": True,
        "timestamp": int(time.time()),
        "device_name": str(backup_info["name"]),
        "backup_date": str(backup_info["date"]),
    } 