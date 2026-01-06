"""Live Sync streaming endpoints.

This module now uses Server-Sent Events (SSE) exclusively and removes legacy
WebSocket code. The SSE endpoint sends an initial history batch, signals when
the subscription is ready, and then streams incremental updates from the in-
process `ChatMessageHub`. A periodic heartbeat keeps proxies from timing out.
"""

from backend.routers.common import create_router, log_simple, db

from fastapi import Request
from starlette.responses import StreamingResponse
import asyncio
import json

from backend.routers.sse_utils import encode_sse_event
from backend.services.core.chat_message_hub import message_hub

router = create_router("/live-sync", "Live Sync")


def _serialize_message(row_dict):
    """Convert DB row → API message dict for frontend."""
    ts = row_dict.get("timestamp")
    return {
        "id": row_dict.get("id"),
        "chat_id": row_dict.get("chat_id"),
        "sender_id": row_dict.get("sender_id"),
        "sender_name": row_dict.get("sender_name"),
        "content": row_dict.get("content"),
        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "is_from_me": bool(row_dict.get("is_from_me")),
        "message_type": row_dict.get("message_type"),
        "service_name": row_dict.get("service_name"),
        "guid": row_dict.get("guid"),
        "associated_message_guid": row_dict.get("associated_message_guid"),
        "reply_to_guid": row_dict.get("reply_to_guid"),
        "reaction_count": row_dict.get("reaction_count") or 0,
        "attachments": [],
        "conversation_id": row_dict.get("conversation_id"),
    }


@router.get("/stream/chat/{chat_id}/messages")
async def sse_chat_messages(request: Request, chat_id: int):
    """
    SSE endpoint that:
    1) Sends entire message history for the chat as an `initial` event
    2) Emits a `ready` event when live subscription is active
    3) Streams incremental `update` events for new messages
    4) Periodically emits `heartbeat` to keep connection alive
    """

    async def event_generator():
        queue = None
        try:
            # Step 1: Send initial message history
            with db.session_scope() as session:
                cursor = session.connection().connection.cursor()
                cursor.execute(
                    """
                    SELECT 
                        m.*, 
                        c.name AS sender_name
                    FROM messages m
                    LEFT JOIN contacts c ON m.sender_id = c.id
                    WHERE m.chat_id = ?
                    ORDER BY m.timestamp ASC
                    """,
                    (chat_id,),
                )
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()

                initial_messages = []
                for row in rows:
                    row_dict = dict(zip(columns, row))
                    initial_messages.append(_serialize_message(row_dict))

                # Always send initial event (possibly empty)
                yield encode_sse_event(
                    event="initial",
                    data=json.dumps({"messages": initial_messages}),
                )

            # Step 2: Subscribe to live updates
            queue = message_hub.subscribe(chat_id)

            yield encode_sse_event(
                event="ready",
                data=json.dumps({"status": "subscribed", "chat_id": chat_id}),
            )

            # Step 3: Stream incremental updates with heartbeat
            while True:
                if await request.is_disconnected():
                    break
                try:
                    messages = await asyncio.wait_for(queue.get(), timeout=30)
                    yield encode_sse_event(
                        event="update",
                        data=json.dumps({"messages": messages}),
                    )
                except asyncio.TimeoutError:
                    # periodic ping – no logging to avoid noise
                    yield encode_sse_event(event="heartbeat", data="ping")

        except Exception as e:
            log_simple(f"Error in SSE stream for chat {chat_id}: {e}", "error")
            yield encode_sse_event(event="error", data=json.dumps({"error": str(e)}))
        finally:
            if queue is not None:
                message_hub.unsubscribe(chat_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stream/chats")
async def sse_chats(request: Request):
    """Stream recency/meta updates for chats.

    Initial event returns the ordered list by last_message_time desc.
    Subsequent events include minimal deltas: { type, chat_id, last_message_time?, title? }.
    """

    from backend.db.session_manager import db as _db

    async def gen():
        q = None
        try:
            # Initial snapshot
            with _db.session_scope() as session:
                from backend.db.sql import fetch_all
                rows = fetch_all(session, "SELECT * FROM chats ORDER BY last_message_date DESC")
                yield encode_sse_event(event="initial", data=json.dumps({"chats": rows}))
            # Subscribe for live deltas
            q = message_hub.subscribe_all_chats()
            yield encode_sse_event(event="ready", data=json.dumps({"status": "subscribed"}))
            while True:
                if await request.is_disconnected():
                    break
                try:
                    delta = await asyncio.wait_for(q.get(), timeout=30)
                    yield encode_sse_event(event="update", data=json.dumps(delta))
                except asyncio.TimeoutError:
                    yield encode_sse_event(event="heartbeat", data="ping")
        except Exception as e:
            log_simple(f"[SSE] /stream/chats error: {e}", "error")
            yield encode_sse_event(event="error", data=json.dumps({"error": str(e)}))
        finally:
            if q is not None:
                message_hub.unsubscribe_all_chats(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stream/contacts")
async def sse_contacts(request: Request):
    """Stream recency/meta updates for contacts.

    Initial event returns contacts ordered by last_message_time desc (similar to iMessage).
    """

    from backend.repositories.contacts import ContactRepository
    from backend.db.session_manager import db as _db

    async def gen():
        q = None
        try:
            with _db.session_scope() as session:
                rows = ContactRepository.get_contacts_with_stats(session)
                # order by lastMessageTime desc to mirror iMessage
                rows_sorted = sorted(rows, key=lambda r: r.get("lastMessageTime") or 0, reverse=True)
                yield encode_sse_event(event="initial", data=json.dumps({"contacts": rows_sorted}))
            q = message_hub.subscribe_all_contacts()
            yield encode_sse_event(event="ready", data=json.dumps({"status": "subscribed"}))
            while True:
                if await request.is_disconnected():
                    break
                try:
                    delta = await asyncio.wait_for(q.get(), timeout=30)
                    yield encode_sse_event(event="update", data=json.dumps(delta))
                except asyncio.TimeoutError:
                    yield encode_sse_event(event="heartbeat", data="ping")
        except Exception as e:
            log_simple(f"[SSE] /stream/contacts error: {e}", "error")
            yield encode_sse_event(event="error", data=json.dumps({"error": str(e)}))
        finally:
            if q is not None:
                message_hub.unsubscribe_all_contacts(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "X-Accel-Buffering": "no",
        },
    )