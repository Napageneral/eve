from typing import Dict, Any
from datetime import datetime
import json
import logging
import time

logger = logging.getLogger(__name__)

class EventBus:
    """Centralized event publishing to Redis Streams."""
    
    @staticmethod
    def publish(
        scope: str,
        event_type: str,
        data: Dict[str, Any] | None = None,
        stream_key: str | None = None,
        *,
        enrich: bool = True,
    ):
        """
        Publish event to Redis Streams pipeline.
        
        Args:
            scope: Event scope (e.g., chat_id, "global", "task:123")
            event_type: Type of event 
            data: Optional event data
            stream_key: Optional custom Redis Stream key
        """
        from backend.infra.redis import get_redis as get_redis_client
        
        data = data or {}
        
        # Build payload
        payload = {
            "type": event_type,
            "scope": str(scope),
            "ts": datetime.utcnow().isoformat(),
        }
        
        # Serialize data values
        for k, v in data.items():
            if v is None:
                continue
            if isinstance(v, (int, float, bool)):
                payload[k] = str(v)
            elif isinstance(v, (dict, list)):
                payload[k] = json.dumps(v)
            else:
                payload[k] = str(v)
        
        try:
            # No DB enrichment here. If callers include run_id or counters, we forward them as-is.
            r = get_redis_client()
            key = stream_key or "task_events:analysis"
            r.xadd(key, payload, maxlen=200_000, approximate=True)
            logger.debug(f"Published {event_type} to {key} for scope {scope}")
        except Exception as e:
            logger.warning(f"Failed to publish event: {e}")


def asyncio_run_safe(coro):
    """Run an async coroutine from sync context safely.
    Tries to reuse running loop via asyncio.run_coroutine_threadsafe; falls back to asyncio.run.
    """
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Best-effort: schedule and wait briefly
            from concurrent.futures import TimeoutError as _Timeout
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                return fut.result(timeout=2)
            except _Timeout:
                return None
        else:
            return loop.run_until_complete(coro)
    except Exception:
        try:
            import asyncio as _a
            return _a.run(coro)
        except Exception:
            return None

# Create a default instance for easy importing
event_bus = EventBus() 