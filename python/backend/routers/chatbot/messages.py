from __future__ import annotations

import json
from typing import Any, Dict, List

from backend.routers.common import (
    create_router, safe_endpoint,
    text, Session, Depends, get_db,
)
from backend.routers.chatbot.utils import normalize_parts_for_storage

router = create_router("/chatbot/messages", tags=["chatbot"])


@router.get("")
@safe_endpoint
def get_messages_by_chat_id(chat_id: str, session: Session = Depends(get_db)):
    rows = session.execute(text(
        """
        SELECT id, chat_id, role, parts, attachments, created_at
        FROM chatbot_messages_v2
        WHERE chat_id = :chat_id
        ORDER BY created_at ASC
        """
    ), {"chat_id": chat_id}).mappings().all()

    for row in rows:
        try:
            row["parts"] = normalize_parts_for_storage(row.get("parts"), row.get("role"))
        except Exception:
            pass
    return rows


@router.post("")
@safe_endpoint
def save_messages(body: Dict[str, Any], session: Session = Depends(get_db)):
    messages: List[Dict[str, Any]] = body.get("messages", [])
    contexts: List[Dict[str, Any]] = body.get("contexts", [])
    if not messages:
        return {"ok": True}

    ins = text(
        """
        INSERT INTO chatbot_messages_v2 (id, chat_id, role, parts, attachments, created_at)
        VALUES (:id, :chat_id, :role, :parts, :attachments, COALESCE(:created_at, CURRENT_TIMESTAMP))
        ON CONFLICT(id) DO NOTHING
        """
    )

    for m in messages:
        normalized_parts = normalize_parts_for_storage(m.get("parts", []), m.get("role"))
        parts_json = json.dumps(normalized_parts)
        attachments_json = json.dumps(m.get("attachments", []))
        params = {
            "id": m["id"],
            "chat_id": m.get("chat_id") or m.get("chatId"),
            "role": m["role"],
            "parts": parts_json,
            "attachments": attachments_json,
            "created_at": m.get("createdAt") or m.get("created_at") or None,
        }
        session.execute(ins, params)

    if contexts and messages:
        chat_id_val = messages[0].get("chat_id") or messages[0].get("chatId")
        msg_id_val = messages[0].get("id")
        for ctx in contexts:
            try:
                ctx_type = str(ctx.get("type") or ctx.get("context_type") or "unknown")
                ctx_ref = str(ctx.get("context_id") or ctx.get("id") or "")
                row_id = f"{chat_id_val}:{ctx_type}:{ctx_ref}"
                
                # Resolve name: for chat/contact contexts, look up actual name from backend tables
                ctx_name = str(ctx.get("name") or ctx.get("context_name") or "")
                if ctx_type == "chat" and ctx_ref:
                    try:
                        chat_row = session.execute(text(
                            "SELECT chat_name FROM chats WHERE id = :chat_id"
                        ), {"chat_id": int(ctx_ref)}).fetchone()
                        if chat_row:
                            ctx_name = chat_row[0]
                    except Exception:
                        pass  # Keep frontend-provided name as fallback
                elif ctx_type == "contact" and ctx_ref:
                    try:
                        contact_row = session.execute(text(
                            "SELECT name FROM contacts WHERE id = :contact_id"
                        ), {"contact_id": int(ctx_ref)}).fetchone()
                        if contact_row:
                            ctx_name = contact_row[0]
                    except Exception:
                        pass  # Keep frontend-provided name as fallback
                
                session.execute(text(
                    """
                    INSERT INTO thread_contexts (id, chat_id, context_type, context_id, context_name, added_by_message_id)
                    VALUES (:id, :chat_id, :type, :ctx_id, :name, :msg_id)
                    ON CONFLICT DO NOTHING
                    """
                ), {
                    "id": row_id,
                    "chat_id": str(chat_id_val),
                    "type": ctx_type,
                    "ctx_id": ctx_ref,
                    "name": ctx_name,
                    "msg_id": str(msg_id_val),
                })
            except Exception:
                pass

    session.commit()
    try:
        # Best-effort: schedule embeddings for this chat
        chat_id_val = messages[0].get("chat_id") or messages[0].get("chatId")
        user_id_val = None  # unknown here
        from backend.celery_service.tasks.embeddings import embed_messages_for_chat_task
        embed_messages_for_chat_task.delay(str(chat_id_val), user_id_val)
    except Exception:
        pass
    return {"ok": True}


@router.delete("")
@safe_endpoint
def delete_messages_after_timestamp(body: Dict[str, Any], session: Session = Depends(get_db)):
    session.execute(text(
        """
        DELETE FROM chatbot_messages_v2
        WHERE chat_id = :chat_id AND created_at >= :ts
        """
    ), {"chat_id": body["chat_id"], "ts": body["after_timestamp"]})
    session.commit()
    return {"ok": True}


@router.get("/{id}")
@safe_endpoint
def get_message_by_id(id: str, session: Session = Depends(get_db)):
    return session.execute(text(
        """
        SELECT id, chat_id, role, parts, attachments, created_at
        FROM chatbot_messages_v2 WHERE id = :id
        """
    ), {"id": id}).mappings().first()


