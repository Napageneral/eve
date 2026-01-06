from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.routers.common import (
    create_router, safe_endpoint,
    text, Session, Depends, get_db,
)

router = create_router("/chatbot/suggestions-history", tags=["chatbot"])


def _ensure_table(session: Session) -> None:
    session.execute(text(
        """
        CREATE TABLE IF NOT EXISTS suggestions_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            suggestion_id TEXT NOT NULL,
            title TEXT,
            subtitle TEXT,
            rationale TEXT,
            source_refs TEXT,
            payload_refs TEXT,
            context_selection_id INTEGER,
            suggested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            dismissed_at TIMESTAMP NULL,
            accepted_at TIMESTAMP NULL
        )
        """
    ))


@router.post("")
@safe_endpoint
def log_event(body: Dict[str, Any], session: Session = Depends(get_db)):
    _ensure_table(session)

    chat_id = str(body.get("chat_id"))
    suggestion_id = str(body.get("suggestion_id"))
    event = str(body.get("event") or "impression").lower()
    ts = str(body.get("timestamp") or "")

    if event == "impression":
        session.execute(text(
            """
            INSERT INTO suggestions_history (
                chat_id, suggestion_id, title, subtitle, rationale, source_refs, payload_refs, context_selection_id, suggested_at
            ) VALUES (
                :chat_id, :suggestion_id, :title, :subtitle, :rationale, :source_refs, :payload_refs, :context_selection_id,
                COALESCE(NULLIF(:ts, ''), CURRENT_TIMESTAMP)
            )
            """
        ), {
            "chat_id": chat_id,
            "suggestion_id": suggestion_id,
            "title": body.get("title"),
            "subtitle": body.get("subtitle"),
            "rationale": body.get("rationale"),
            "source_refs": json.dumps(body.get("source_refs") or []),
            "payload_refs": json.dumps(body.get("payload_refs") or []),
            "context_selection_id": body.get("context_selection_id"),
            "ts": ts,
        })
        session.commit()
        return {"ok": True}

    # Update the most recent row for this suggestion/chat without a terminal timestamp
    col = "dismissed_at" if event == "dismiss" else ("accepted_at" if event == "accept" else None)
    if not col:
        return {"ok": False, "error": "unknown_event"}

    row = session.execute(text(
        """
        SELECT id FROM suggestions_history
        WHERE chat_id = :chat_id AND suggestion_id = :suggestion_id AND {col} IS NULL
        ORDER BY suggested_at DESC
        LIMIT 1
        """.format(col=col)
    ), {"chat_id": chat_id, "suggestion_id": suggestion_id}).first()

    if row:
        session.execute(text(
            f"UPDATE suggestions_history SET {col} = COALESCE(NULLIF(:ts, ''), CURRENT_TIMESTAMP) WHERE id = :id"
        ), {"id": row[0], "ts": ts})
        session.commit()
        return {"ok": True}

    # If no matching row, insert a minimal record with terminal timestamp
    session.execute(text(
        f"""
        INSERT INTO suggestions_history (chat_id, suggestion_id, suggested_at, {col})
        VALUES (:chat_id, :suggestion_id, CURRENT_TIMESTAMP, COALESCE(NULLIF(:ts, ''), CURRENT_TIMESTAMP))
        """
    ), {"chat_id": chat_id, "suggestion_id": suggestion_id, "ts": ts})
    session.commit()
    return {"ok": True}


@router.get("")
@safe_endpoint
def list_history(chat_id: str, hours: int = 48, session: Session = Depends(get_db)):
    _ensure_table(session)
    rows = session.execute(text(
        """
        SELECT suggestion_id, title, subtitle, rationale, source_refs, payload_refs,
               context_selection_id, suggested_at, dismissed_at, accepted_at
        FROM suggestions_history
        WHERE chat_id = :chat_id AND suggested_at >= DATETIME('now', :offset)
        ORDER BY suggested_at DESC
        LIMIT 500
        """
    ), {"chat_id": chat_id, "offset": f"-{int(max(1, hours))} hours"}).mappings().all()
    # Decode arrays
    out: List[Dict[str, Any]] = []
    for r in rows:
        obj = dict(r)
        try:
            obj["source_refs"] = json.loads(obj.get("source_refs") or "[]")
        except Exception:
            obj["source_refs"] = []
        try:
            obj["payload_refs"] = json.loads(obj.get("payload_refs") or "[]")
        except Exception:
            obj["payload_refs"] = []
        out.append(obj)
    return {"history": out}


