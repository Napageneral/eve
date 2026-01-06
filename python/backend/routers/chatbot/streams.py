from __future__ import annotations

from typing import Any, Dict

from backend.routers.common import create_router, safe_endpoint, text, Session, Depends, get_db

router = create_router("/chatbot/streams", tags=["chatbot"])


@router.post("")
@safe_endpoint
def create_stream_id(body: Dict[str, Any], session: Session = Depends(get_db)):
    session.execute(text(
        """
        INSERT INTO chatbot_streams (id, chat_id, created_at) VALUES (:id, :chat_id, CURRENT_TIMESTAMP)
        """
    ), {"id": body["id"], "chat_id": body["chat_id"]})
    session.commit()
    return {"ok": True}


@router.get("")
@safe_endpoint
def get_stream_ids_by_chat_id(chat_id: str, session: Session = Depends(get_db)):
    rows = session.execute(text(
        """
        SELECT id FROM chatbot_streams WHERE chat_id = :cid ORDER BY created_at ASC
        """
    ), {"cid": chat_id}).all()
    return [{"id": r[0]} for r in rows]


