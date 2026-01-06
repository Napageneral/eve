"""SSE progress streaming for analysis operations"""

# Consolidated imports
from backend.routers.common import (
    create_router, safe_endpoint, log_simple,
    Query, text, db
)
from backend.routers.sse_utils import (
    stream_redis_updates, create_progress_event, 
    create_completion_event, encode_sse_event
)

from fastapi import Request
from starlette.responses import StreamingResponse
import json
import asyncio
import re

router = create_router("/analysis/streaming", "Progress Streaming")

# Lightweight chat-specific snapshot used by /status; live updates come via Redis stream SSE
async def _get_chat_counters(chat_id: int) -> dict:
    with db.session_scope() as session:
        result = session.execute(text("""
            SELECT 
              COUNT(DISTINCT c.id) AS total,
              COUNT(DISTINCT CASE WHEN ca.status='success' THEN ca.conversation_id END) AS completed,
              COUNT(DISTINCT CASE WHEN ca.status='processing' THEN ca.conversation_id END) AS processing,
              COUNT(DISTINCT CASE WHEN ca.status IN ('failed','error') THEN ca.conversation_id END) AS failed,
              COUNT(DISTINCT CASE WHEN ca.status='pending' THEN ca.conversation_id END) AS pending
            FROM conversations c
            LEFT JOIN conversation_analyses ca ON ca.conversation_id=c.id
              AND ca.prompt_template_id=(SELECT id FROM prompt_templates WHERE name='ConvoAll' AND version=1)
            WHERE c.chat_id=:chat_id
        """), {"chat_id": chat_id}).first()
    total = (result.total or 0) if result else 0
    completed = (result.completed or 0) if result else 0
    processing = (result.processing or 0) if result else 0
    failed = (result.failed or 0) if result else 0
    pending = (result.pending or 0) if result else 0
    not_started = total - (completed + processing + failed + pending)
    pct = (completed / total * 100) if total else 0.0
    is_complete = (completed + failed) >= total and total > 0
    if is_complete:
        if failed == 0 and completed == total:
            status = "completed"
        elif failed > 0:
            status = "partial_success" if completed > 0 else "failed"
        else:
            status = "completed"
    else:
        status = "processing" if (processing + pending + not_started) > 0 else "not_started"
    return {
        "chat_id": chat_id,
        "total_convos": total,
        "successful_convos": completed,
        "processing_convos": processing,
        "failed_convos": failed,
        "pending_convos": pending,
        "not_started_convos": not_started,
        "percentage": round(pct, 2),
        "is_complete": is_complete,
        "overall_status": status,
        "status": status,
        "processed_convos": completed,
    }

@router.get("/status")
@safe_endpoint
async def get_analysis_status_snapshot(
    request: Request,
    chat_id: str | None = Query(None, description="Chat ID or 'global' for all chats"),
    run_id: str | None = Query(None, description="Global analysis run_id for snapshot (authoritative Redis)"),
):
    """Return a one-shot JSON snapshot.

    - If run_id is provided: return per-run Redis counters (authoritative global run).
    - Else if chat_id refers to a specific chat: return chat counters from DB.
    - Else: return an empty default snapshot.
    """
    if run_id:
        # reduce log noise: this endpoint can be polled frequently; skip info-level
        from backend.services.analysis.redis_counters import snapshot as get_snapshot
        return get_snapshot(run_id)
    if chat_id and chat_id != "global":
        log_simple(f"Returning chat counters for chat_id: {chat_id}")
        return await _get_chat_counters(int(chat_id))
    return {
        "total_convos": 0,
        "successful_convos": 0,
        "processing_convos": 0,
        "failed_convos": 0,
        "pending_convos": 0,
        "not_started_convos": 0,
        "percentage": 0,
        "is_complete": False,
        "running": False,
        "status": "not_started",
        "overall_status": "not_started",
    }

    

@router.get("/redis_stream/{stream_key}")
@safe_endpoint
async def stream_redis_data(
    stream_key: str,
    start_id: str = Query("0", description="Redis stream ID to start from")
):
    """Stream data from Redis streams via SSE."""
    log_simple(f"Streaming Redis data from {stream_key}")
    
    return StreamingResponse(
        stream_redis_updates(stream_key, start_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    ) 


# New endpoint: Stream queue events filtered by scope (e.g., 'global', 'chat:123')
@router.get("/queue/stream")
@safe_endpoint
async def stream_queue_events(
    request: Request,
    scope: str = Query(..., description="Scope to filter events (e.g., 'global', 'chat:123', 'task:abc')"),
    run_id: str | None = Query(None, description="Global analysis run_id to filter 'global' events"),
    start_id: str = Query("$", description="Redis stream ID to start from; use '$' for only new events"),
):
    """Stream task events from Redis Streams via SSE, filtered by scope."""
    log_simple(f"Starting event stream for scope: {scope}")

    from backend.routers.sse_utils import encode_sse_event, create_redis_connection
    import redis.asyncio as aioredis

    async def event_generator():
        # Flush headers immediately with an initial heartbeat to help clients mark the connection as open
        yield encode_sse_event(event="heartbeat", data="hello")

        redis = None
        stream_key = "task_events:analysis"
        last_id = start_id or "$"
        consecutive_errors = 0
        last_heartbeat = asyncio.get_event_loop().time()
        heartbeat_interval_sec = 1.0

        try:
            while True:
                # Stop promptly if client disconnects
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    # Best effort; continue streaming
                    pass

                try:
                    # (Re)connect lazily to Redis if needed
                    if redis is None:
                        try:
                            redis = await create_redis_connection()
                            consecutive_errors = 0
                        except Exception as e:
                            consecutive_errors += 1
                            log_simple(f"Redis connect error (attempt {consecutive_errors}): {e}", "warning")
                            await asyncio.sleep(0.5)
                            # keep streaming heartbeats while we retry connecting
                            now = asyncio.get_event_loop().time()
                            if now - last_heartbeat >= heartbeat_interval_sec:
                                yield encode_sse_event(event="heartbeat", data="ping")
                                last_heartbeat = now
                            continue

                    # Read from Redis stream with a short block for high-frequency delivery
                    result = await redis.xread({stream_key: last_id}, block=50, count=100)
                    consecutive_errors = 0  # Reset error counter on success

                    if result:
                        for _stream, messages in result:
                            for message_id, fields in messages:
                                last_id = message_id
                                # Filter by scope; for global scope optionally filter by run_id if provided
                                # Also accept "historic" scope when requesting "global" for backward compatibility
                                scope_match = fields.get("scope") == scope or (scope == "global" and fields.get("scope") == "historic")
                                if scope_match and (run_id is None or fields.get("run_id") == run_id):
                                    # Normalize numeric/boolean values
                                    normalized = {}
                                    for k, v in fields.items():
                                        if isinstance(v, str):
                                            # Fast numeric/boolean normalization using regex
                                            if re.fullmatch(r"-?\d+", v):
                                                normalized[k] = int(v)
                                                continue
                                            if re.fullmatch(r"-?\d+\.\d+", v):
                                                normalized[k] = float(v)
                                                continue
                                            if v.lower() in ("true", "false"):
                                                normalized[k] = (v.lower() == "true")
                                                continue
                                        normalized[k] = v

                                    # Light enrichment for runtime counter payloads → UI-ready
                                    if all(k in normalized for k in ("total", "pending", "processing", "success", "failed")):
                                        try:
                                            t = int(normalized.get("total", 0))
                                            pd = int(normalized.get("pending", 0))
                                            prc = int(normalized.get("processing", 0))
                                            succ = int(normalized.get("success", 0))
                                            fail = int(normalized.get("failed", 0))
                                            processed = max(0, succ + fail)
                                            pct = round((succ / t * 100.0), 2) if t else 0.0
                                            is_complete = (processed >= t and t > 0)
                                            # Prefer completion if processed >= total even if transient counters linger
                                            if is_complete:
                                                pd = 0
                                                prc = 0
                                            status = "completed" if is_complete else ("processing" if (pd + prc) > 0 else "not_started")
                                            normalized.update({
                                                "total_convos": max(0, t),
                                                "pending_convos": max(0, pd),
                                                "processing_convos": max(0, prc),
                                                "successful_convos": max(0, succ),
                                                "failed_convos": max(0, fail),
                                                "processed_convos": processed,
                                                "percentage": pct,
                                                "status": status,
                                                "overall_status": status,
                                                "is_complete": is_complete,
                                                "running": (pd + prc) > 0,
                                            })
                                        except Exception:
                                            pass

                                    # Include Redis ID for resume and debugging
                                    normalized["redis_id"] = message_id
                                    yield encode_sse_event(event="message", data=json.dumps(normalized), id=message_id)

                    # Heartbeat on a timer, not every loop
                    now = asyncio.get_event_loop().time()
                    if now - last_heartbeat >= heartbeat_interval_sec:
                        try:
                            if run_id:
                                from backend.services.analysis.redis_counters import snapshot as counters_snapshot
                                snap = counters_snapshot(run_id)
                                snap["scope"] = scope
                                snap["run_id"] = run_id
                                yield encode_sse_event(event="message", data=json.dumps(snap))
                        except Exception:
                            pass
                        yield encode_sse_event(event="heartbeat", data="ping")
                        last_heartbeat = now

                except aioredis.ConnectionError as e:
                    consecutive_errors += 1
                    log_simple(f"Redis connection error (attempt {consecutive_errors}): {e}", "warning")
                    if consecutive_errors > 3:
                        # Drop the connection object and keep the SSE alive; we'll retry connect
                        try:
                            await redis.close()
                        except Exception:
                            pass
                        redis = None
                        consecutive_errors = 0
                        await asyncio.sleep(0.5)
                        continue
                    await asyncio.sleep(0.5)
                    try:
                        await redis.close()
                    except Exception:
                        pass
                    redis = None

                except Exception as e:
                    log_simple(f"Unexpected error in SSE stream: {e}", "error")
                    raise

                # no fixed sleep; rely on xread block and heartbeats

        except Exception as e:
            log_simple(f"SSE streaming error: {e}", "error")
            yield encode_sse_event(event="error", data=json.dumps({"error": str(e)}))
        finally:
            try:
                if redis is not None:
                    await redis.close()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )