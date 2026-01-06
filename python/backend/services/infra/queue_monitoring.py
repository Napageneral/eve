from __future__ import annotations

"""QueueMonitoringService – runtime queue & worker stats plus DLQ maintenance."""

import logging
from datetime import datetime
from typing import Dict, Any

from celery import current_app

from backend.config import settings

# Consolidated broker type constant
BROKER_TYPE = settings.broker_type
from backend.infra.redis import get_redis
from backend.db.session_manager import db
from backend.repositories.dlq import DLQRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------

def get_queue_lengths() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "broker_type": BROKER_TYPE,
        "queues": {},
        "total_pending": 0,
        "error": None,
    }

    if BROKER_TYPE != "redis":
        info["error"] = "Queue monitoring only implemented for redis broker"
        return info

    try:
        r = get_redis()
        queue_keys = {
            "analysis": "chatstats-analysis",
            "db": "chatstats-db",
            "bulk": "chatstats-bulk",
            "dlq": "chatstats-dlq",
        }
        total = 0
        for name, key in queue_keys.items():
            try:
                length = r.llen(key)
                info["queues"][name] = {"length": length, "redis_key": key}
                total += length
            except Exception as exc:
                info["queues"][name] = {"length": 0, "error": str(exc), "redis_key": key}
        info["total_pending"] = total
    except Exception as exc:
        info["error"] = str(exc)
        logger.error("Queue length retrieval failed: %s", exc)
    return info

# ---------------------------------------------------------------------------
# Worker helpers
# ---------------------------------------------------------------------------

def get_worker_stats() -> Dict[str, Any]:
    stats = {
        "timestamp": datetime.utcnow().isoformat(),
        "active_workers": 0,
        "workers": {},
        "total_active_tasks": 0,
        "error": None,
    }
    try:
        insp = current_app.control.inspect()
        active = insp.active() or {}
        registered = insp.registered() or {}

        stats["active_workers"] = len(active)
        for worker, tasks in active.items():
            stats["workers"][worker] = {
                "active": len(tasks),
                "registered_tasks": len(registered.get(worker, [])),
            }
            stats["total_active_tasks"] += len(tasks)
    except Exception as exc:
        stats["error"] = str(exc)
        logger.error("Worker stats retrieval failed: %s", exc)
    return stats

# ---------------------------------------------------------------------------
# Composite helper
# ---------------------------------------------------------------------------

def get_comprehensive_status() -> Dict[str, Any]:
    return {"queues": get_queue_lengths(), "workers": get_worker_stats()}

# ---------------------------------------------------------------------------
# Maintenance helpers
# ---------------------------------------------------------------------------

def clear_queue(queue_name: str) -> Dict[str, Any]:
    if BROKER_TYPE != "redis":
        return {"error": "clear_queue only supports redis broker"}
    try:
        r = get_redis()
        deleted = r.delete(queue_name)
        return {"queue": queue_name, "deleted": deleted}
    except Exception as exc:
        logger.error("Failed to clear queue %s: %s", queue_name, exc)
        return {"error": str(exc)}


def purge_old_dlq_items(days_old: int = 30) -> Dict[str, Any]:
    try:
        with db.session_scope() as session:
            deleted = DLQRepository.purge_before(session, days_old=days_old)
            session.commit()
        return {"deleted": deleted, "days_old": days_old}
    except Exception as exc:
        logger.error("purge_old_dlq_items failed: %s", exc)
        return {"error": str(exc)} 