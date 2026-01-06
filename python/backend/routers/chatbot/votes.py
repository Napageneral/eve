from __future__ import annotations

from typing import Any, Dict

from backend.routers.common import create_router, safe_endpoint, text, Session, Depends, get_db

router = create_router("/chatbot/votes", tags=["chatbot"])


@router.post("")
@safe_endpoint
def vote_message(body: Dict[str, Any], session: Session = Depends(get_db)):
    sel = text("SELECT 1 FROM chatbot_votes_v2 WHERE chat_id = :cid AND message_id = :mid")
    exists = session.execute(sel, {"cid": body["chat_id"], "mid": body["message_id"]}).first()
    if exists:
        session.execute(text(
            """
            UPDATE chatbot_votes_v2
            SET is_upvoted = :u, created_at = CURRENT_TIMESTAMP
            WHERE chat_id = :cid AND message_id = :mid
            """
        ), {"u": bool(body["is_upvoted"]), "cid": body["chat_id"], "mid": body["message_id"]})
    else:
        session.execute(text(
            """
            INSERT INTO chatbot_votes_v2 (chat_id, message_id, is_upvoted, created_at)
            VALUES (:cid, :mid, :u, CURRENT_TIMESTAMP)
            """
        ), {"u": bool(body["is_upvoted"]), "cid": body["chat_id"], "mid": body["message_id"]})
    session.commit()
    return {"ok": True}


@router.get("")
@safe_endpoint
def get_votes_by_chat_id(chat_id: str, session: Session = Depends(get_db)):
    return session.execute(text(
        """
        SELECT chat_id, message_id, is_upvoted, created_at
        FROM chatbot_votes_v2 WHERE chat_id = :cid
        """
    ), {"cid": chat_id}).mappings().all()


