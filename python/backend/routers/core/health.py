# Consolidated imports
from backend.routers.common import create_router, safe_endpoint, log_simple, text
import time

router = create_router("/health", "Health")

_app_start_time = time.time()

@router.get("/")
@router.get("")  # Combined both routes
@safe_endpoint
async def health_check():
    current = time.time()
    return {
        "status": "ok",
        "service": "backend",
        "message": "Backend service is running",
        "uptime_seconds": round(current - _app_start_time, 3),
        "timestamp": current,
    }

@router.get("/detailed")
@safe_endpoint
async def detailed_health_check():
    log_simple("Getting detailed health status")
    
    # Lazy import to avoid circular dependency
    from backend.main import celery_connection_status, startup_times, app_start_time

    current = time.time()
    return {
        "status": "ok",
        "service": "backend",
        "celery": celery_connection_status,
        "message": "Backend service is running with detailed status",
        "uptime_seconds": round(current - app_start_time, 3),
        "startup_timing": startup_times,
        "timestamp": current,
    }

@router.get("/database")
@safe_endpoint
async def database_health_check():
    log_simple("Checking database health")
    
    from backend.db.cleanup import check_database_health
    from backend.config import DB_PATH
    from backend.db.session_manager import new_session

    health_info = check_database_health(DB_PATH)

    # Test database connection
    connection_test = {"success": False, "error": None, "response_time": None}
    start = time.time()
    try:
        with new_session() as session:
            session.execute(text("SELECT 1 as test")).fetchone()
            connection_test["success"] = True
    except Exception as e:
        connection_test["error"] = str(e)
    finally:
        connection_test["response_time"] = time.time() - start

    status = "ok" if health_info.get("writable") and connection_test["success"] else "error"
    log_simple(f"Database health check: {status}")

    return {
        "status": status,
        "database_path": DB_PATH,
        "health_info": health_info,
        "connection_test": connection_test,
        "timestamp": time.time(),
    }

@router.get("/rps-status")
@safe_endpoint
async def rps_status_check():
    """Get current circuit breaker RPS status."""
    try:
        from backend.infra.redis import get_redis
        r = get_redis()
        current_rps = int(r.get("llm:global_rps") or 450)
        last_error_ts = float(r.get("llm:last_error_ts") or 0)
        time_since_error = time.time() - last_error_ts if last_error_ts > 0 else None
        
        return {
            "status": "ok",
            "current_rps": current_rps,
            "last_error_ago_seconds": time_since_error,
            "floor": 5,
            "ceiling": 450,
            "timestamp": time.time(),
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "timestamp": time.time(),
        } 