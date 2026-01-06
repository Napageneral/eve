from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
import uuid
from backend.routers.common import (
    safe_endpoint,
    text,
    Session,
    Depends,
    get_db,
)
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chatbot/threads", tags=["chatbot-threads"])

class CreateEveThreadRequest(BaseModel):
    title: str
    source_chat_id: int | None = None
    prompt_id: str
    user_id: str = "local-user"
    visibility: str = "private"
    contexts: list[dict] | None = None  # Optional contexts to attach at creation

class CreateEveThreadResponse(BaseModel):
    thread_id: str
    created_at: str

@router.post("/create-eve", response_model=CreateEveThreadResponse)
@safe_endpoint
def create_eve_thread(req: CreateEveThreadRequest, session: Session = Depends(get_db)):
    """
    Create a new Eve-generated thread in the database.
    
    This endpoint creates the thread record BEFORE streaming starts,
    ensuring the thread exists when the inbox queries for it.
    
    This is part of the hybrid backend architecture:
    - Python creates the thread (data operation)
    - Next.js /api/chat handles streaming (Vercel AI SDK)
    - Frontend orchestrates both
    """
    thread_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    
    logger.info(f"[threads.create-eve] Creating thread: {req.title}")
    
    # Insert thread into database (no updated_at column exists)
    query = text("""
        INSERT INTO chatbot_chats (id, user_id, title, visibility, created_at)
        VALUES (:id, :user_id, :title, :visibility, :created_at)
    """)
    
    session.execute(query, {
        "id": thread_id,
        "user_id": req.user_id,
        "title": req.title,
        "visibility": req.visibility,
        "created_at": now,
    })
    
    # Optionally store Eve metadata (prompt_id, source_chat_id)
    # Note: This requires chatbot_thread_metadata table to exist
    # If it doesn't exist yet, this section will be skipped
    if req.source_chat_id or req.prompt_id:
        try:
            metadata_query = text("""
                INSERT INTO chatbot_thread_metadata (thread_id, prompt_id, source_chat_id)
                VALUES (:thread_id, :prompt_id, :source_chat_id)
            """)
            session.execute(metadata_query, {
                "thread_id": thread_id,
                "prompt_id": req.prompt_id,
                "source_chat_id": req.source_chat_id,
            })
            logger.info(f"[threads.create-eve] Metadata saved for thread {thread_id}")
        except Exception as e:
            # Metadata is optional; don't fail if table doesn't exist
            logger.warning(f"[threads.create-eve] Metadata insert failed (table may not exist): {e}")
    
    # Save contexts if provided (same logic as messages.py for consistency)
    if req.contexts:
        try:
            for ctx in req.contexts:
                ctx_type = str(ctx.get("type") or "unknown")
                ctx_id = ctx.get("id")
                ctx_name = str(ctx.get("name") or "")
                
                # Resolve name from backend if possible
                if ctx_type == "chat" and ctx_id:
                    try:
                        chat_row = session.execute(text(
                            "SELECT chat_name FROM chats WHERE id = :chat_id"
                        ), {"chat_id": int(ctx_id)}).fetchone()
                        if chat_row:
                            ctx_name = chat_row[0]
                    except Exception:
                        pass  # Keep frontend-provided name
                elif ctx_type == "contact" and ctx_id:
                    try:
                        contact_row = session.execute(text(
                            "SELECT name FROM contacts WHERE id = :contact_id"
                        ), {"contact_id": int(ctx_id)}).fetchone()
                        if contact_row:
                            ctx_name = contact_row[0]
                    except Exception:
                        pass  # Keep frontend-provided name
                
                # Insert context
                row_id = f"{thread_id}:{ctx_type}:{ctx_id}"
                session.execute(text("""
                    INSERT INTO thread_contexts (id, chat_id, context_type, context_id, context_name, added_by_message_id)
                    VALUES (:id, :chat_id, :type, :ctx_id, :name, NULL)
                    ON CONFLICT DO NOTHING
                """), {
                    "id": row_id,
                    "chat_id": thread_id,
                    "type": ctx_type,
                    "ctx_id": str(ctx_id) if ctx_id else None,
                    "name": ctx_name,
                })
            logger.info(f"[threads.create-eve] Saved {len(req.contexts)} contexts for thread {thread_id}")
        except Exception as e:
            logger.warning(f"[threads.create-eve] Context insert failed: {e}")
    
    session.commit()
    
    logger.info(f"[threads.create-eve] Thread created successfully: {thread_id}")
    
    return CreateEveThreadResponse(thread_id=thread_id, created_at=now)


@router.post("/threads/{thread_id}/mark-complete")
@safe_endpoint
def mark_thread_complete(thread_id: str, session: Session = Depends(get_db)):
    """
    Mark an Eve thread as complete after streaming finishes.
    
    Sets is_complete flag in thread metadata for persistent completion state.
    """
    logger.info(f"[threads.mark-complete] Marking thread complete: {thread_id}")
    
    # Update thread metadata with completion flag
    # SQLite json_set creates the key if metadata is null
    query = text("""
        UPDATE chatbot_chats 
        SET metadata = json_set(COALESCE(metadata, '{}'), '$.is_complete', 1)
        WHERE id = :thread_id
    """)
    
    session.execute(query, {"thread_id": thread_id})
    session.commit()
    
    logger.info(f"[threads.mark-complete] Thread marked complete: {thread_id}")
    
    return {"ok": True}

