from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

from backend.routers.common import create_router, safe_endpoint, text, Session, Depends, get_db

router = create_router("/chatbot/users", tags=["chatbot"])


@router.get("")
@safe_endpoint
def get_user(email: str, session: Session = Depends(get_db)):
    return session.execute(text(
        """
        SELECT id, email, password, created_at, updated_at
        FROM chatbot_users WHERE email = :email
        """
    ), {"email": email}).mappings().all()


@router.post("")
@safe_endpoint
def create_user(body: Dict[str, Any], session: Session = Depends(get_db)):
    session.execute(text(
        """
        INSERT INTO chatbot_users (id, email, password, created_at, updated_at)
        VALUES (:id, :email, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    ), {"id": body.get("id"), "email": body["email"], "password": body.get("password")})
    session.commit()
    return {"ok": True}


@router.post("/guest")
@safe_endpoint
def create_guest_user(body: Dict[str, Any], session: Session = Depends(get_db)):
    row = session.execute(text(
        """
        INSERT INTO chatbot_users (id, email, password, created_at, updated_at)
        VALUES (:id, :email, :password, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id, email
        """
    ), {"id": body.get("id"), "email": body["email"], "password": body.get("password")}).mappings().first()
    session.commit()
    return row


@router.get("/{id}/message-count")
@safe_endpoint
def get_message_count_by_user_id(id: str, hours: int = 24, session: Session = Depends(get_db)):
    cutoff_iso = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    row = session.execute(text(
        """
        SELECT COUNT(*) AS count
        FROM chatbot_messages_v2 m
        JOIN chatbot_chats c ON m.chat_id = c.id
        WHERE c.user_id = :uid AND m.created_at >= :cutoff
        """
    ), {"uid": id, "cutoff": cutoff_iso}).first()
    return {"count": int(row[0]) if row and row[0] is not None else 0}


