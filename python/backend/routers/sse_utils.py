"""Server-Sent Events (SSE) utilities for streaming responses.

This module contains helper functions for working with SSE streams,
reducing boilerplate in router files that need real-time streaming.
"""

import json
import time
import asyncio
import logging
from typing import Optional, Dict, Any, AsyncGenerator
import redis.asyncio as aioredis
# Always read events from the same Redis used for metrics/counters publishing
try:
    from backend.infra.redis import METRICS_REDIS_URL as STREAM_REDIS_URL
except Exception:
    # Fallback to app broker URL if metrics URL is unavailable
    from backend.config import settings as _settings
    STREAM_REDIS_URL = _settings.broker_url

logger = logging.getLogger(__name__)


def encode_sse_event(
    event: Optional[str] = None, 
    data: Optional[str] = None, 
    id: Optional[str] = None, 
    retry: Optional[int] = None
) -> str:
    """Encode an SSE event according to the spec."""
    lines = []
    if id:
        lines.append(f"id: {id}")
    if event:
        lines.append(f"event: {event}")
    if retry is not None:
        lines.append(f"retry: {retry}")
    if data is not None:
        # Handle multi-line data
        for line in data.splitlines():
            lines.append(f"data: {line}")
    lines.append("")  # Empty line to terminate event
    return "\n".join(lines) + "\n"


def parse_stream_id(stream_id: str) -> tuple[int, int]:
    """Parse Redis stream ID into comparable tuple."""
    try:
        parts = stream_id.split('-', 1)
        if len(parts) == 2:
            return (int(parts[0]), int(parts[1]))
        elif len(parts) == 1:
            return (int(parts[0]), 0)
        else:
            return (-1, -1)
    except (ValueError, AttributeError):
        return (-1, -1)


def compare_redis_ids(id1: str, id2: str) -> str:
    """Return the lexicographically larger Redis stream ID."""
    parsed1 = parse_stream_id(id1)
    parsed2 = parse_stream_id(id2)
    return id1 if parsed1 >= parsed2 else id2


async def create_redis_connection() -> aioredis.Redis:
    """Create Redis connection for streaming."""
    return aioredis.from_url(STREAM_REDIS_URL, decode_responses=True)


async def stream_redis_updates(
    stream_key: str,
    start_id: str = "0",
    timeout_ms: int = 30000,
    heartbeat_interval: int = 2
) -> AsyncGenerator[str, None]:
    """Stream updates from Redis with heartbeat and error handling."""
    redis = await create_redis_connection()
    last_heartbeat = time.time()
    current_id = start_id
    
    try:
        while True:
            try:
                # Read from Redis stream
                result = await redis.xread({stream_key: current_id}, block=1000, count=10)
                
                if result:
                    for stream, messages in result:
                        for message_id, fields in messages:
                            current_id = message_id
                            yield encode_sse_event(data=json.dumps(fields))
                            last_heartbeat = time.time()
                
                # Send heartbeat if needed
                if time.time() - last_heartbeat >= heartbeat_interval:
                    yield encode_sse_event(event="heartbeat", data="ping")
                    last_heartbeat = time.time()
                    
            except asyncio.TimeoutError:
                # Send heartbeat on timeout
                yield encode_sse_event(event="heartbeat", data="ping")
                last_heartbeat = time.time()
                
    except Exception as e:
        logger.error(f"Redis streaming error: {e}")
        yield encode_sse_event(event="error", data=json.dumps({"error": str(e)}))
    finally:
        await redis.close()


def create_progress_event(
    status: str,
    progress: Optional[float] = None,
    message: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None
) -> str:
    """Create standardized progress event."""
    event_data = {"status": status}
    if progress is not None:
        event_data["progress"] = progress
    if message:
        event_data["message"] = message
    if data:
        event_data.update(data)
    
    return encode_sse_event(event="progress", data=json.dumps(event_data))


def create_completion_event(result: Any, success: bool = True) -> str:
    """Create standardized completion event."""
    event_data = {
        "status": "complete" if success else "error",
        "result": result
    }
    return encode_sse_event(event="complete", data=json.dumps(event_data)) 