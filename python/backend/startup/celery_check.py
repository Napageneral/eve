"""Background Celery broker health-check."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Any

__all__ = ["schedule", "get_status"]

log = logging.getLogger(__name__)

_status: Dict[str, Any] = {"status": "unknown", "last_check": None, "error": None}


async def _check() -> None:
    start = time.time()
    try:
        from backend.celery_service.broker_health import broker_is_alive

        alive = await asyncio.to_thread(broker_is_alive)
        _status.update({
            "status": "connected" if alive else "disconnected",
            "last_check": time.time(),
            "error": None,
        })
        log.info("[CELERY] Broker health: %s (%.3fs)", alive, time.time() - start)
    except Exception as exc:  # pragma: no cover – best-effort task
        _status.update({
            "status": "error",
            "last_check": time.time(),
            "error": str(exc),
        })
        log.error("[CELERY] Health check failed after %.3fs – %s", time.time() - start, exc)


def schedule() -> None:  # noqa: D401 – simple
    """Launch the broker health-check in the background."""
    asyncio.create_task(_check())


def get_status() -> Dict[str, Any]:
    return _status.copy() 