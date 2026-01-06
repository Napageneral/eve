from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.routers.common import (
    create_router, safe_endpoint,
    text, Session, Depends, get_db,
)
from backend.routers.chatbot.utils import (
    ensure_draft_columns,
    ensure_thread_contexts_index,
    normalize_parts_for_storage,
)

router = create_router("/chatbot/chats", tags=["chatbot"])


@router.post("")
@safe_endpoint
def save_chat(body: Dict[str, Any], session: Session = Depends(get_db)):
    if body.get("user_id"):
        session.execute(
            text(
                """
                INSERT INTO chatbot_users (id, email, password, created_at, updated_at)
                SELECT :id, :email, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                WHERE NOT EXISTS (SELECT 1 FROM chatbot_users WHERE id = :id)
                """
            ),
            {"id": body["user_id"], "email": f"guest-{body['user_id']}"},
        )

    tags_json = None
    if body.get("tags") and isinstance(body["tags"], list):
        tags_json = json.dumps(body["tags"])
    
    session.execute(text(
        """
        INSERT INTO chatbot_chats (id, created_at, title, user_id, visibility, last_read_at, tags)
        VALUES (:id, CURRENT_TIMESTAMP, :title, :user_id, :visibility, CURRENT_TIMESTAMP, :tags)
        ON CONFLICT(id) DO UPDATE SET
            title = EXCLUDED.title,
            visibility = EXCLUDED.visibility,
            user_id = EXCLUDED.user_id,
            tags = COALESCE(EXCLUDED.tags, chatbot_chats.tags),
            last_read_at = COALESCE(chatbot_chats.last_read_at, EXCLUDED.last_read_at)
        """
    ), {
        "id": body["id"],
        "title": body["title"],
        "user_id": body["user_id"],
        "visibility": body["visibility"],
        "tags": tags_json,
    })
    session.commit()
    return {"ok": True}


@router.get("/{id}")
@safe_endpoint
def get_chat_by_id(id: str, session: Session = Depends(get_db)):
    row = session.execute(text(
        """
        SELECT id, created_at, title, user_id, visibility
        FROM chatbot_chats WHERE id = :id
        """
    ), {"id": id}).mappings().first()
    return row


@router.get("")
@safe_endpoint
def get_chats_by_user_id(
    user_id: str,
    limit: int = 50,
    starting_after: Optional[str] = None,
    ending_before: Optional[str] = None,
    session: Session = Depends(get_db),
):
    ensure_draft_columns(session)
    base = """
        SELECT id, created_at, title, user_id, visibility,
               COALESCE(is_starred, FALSE)   AS is_starred,
               COALESCE(is_important, FALSE) AS is_important,
               last_read_at,
               participants_json,
               COALESCE(has_draft, FALSE) AS has_draft,
               draft_text,
               draft_updated_at,
               tags
        FROM chatbot_chats 
        WHERE user_id = :uid
    """
    params: Dict[str, Any] = {"uid": user_id, "limit": limit + 1}

    if starting_after:
        sql = text(base + " AND created_at > (SELECT created_at FROM chatbot_chats WHERE id = :sa)"
                         + " ORDER BY created_at DESC LIMIT :limit")
        params["sa"] = starting_after
    elif ending_before:
        sql = text(base + " AND created_at < (SELECT created_at FROM chatbot_chats WHERE id = :eb)"
                         + " ORDER BY created_at DESC LIMIT :limit")
        params["eb"] = ending_before
    else:
        sql = text(base + " ORDER BY created_at DESC LIMIT :limit")

    rows = session.execute(sql, params).mappings().all()

    chats_list = []
    for row in rows[:limit]:
        d = dict(row)
        if d.get('created_at'):
            d['created_at'] = d['created_at'].isoformat() if hasattr(d['created_at'], 'isoformat') else str(d['created_at'])
        d['createdAt'] = d.get('created_at')
        d['userId'] = d.get('user_id')
        d['isStarred'] = bool(d.get('is_starred') or False)
        d['isImportant'] = bool(d.get('is_important') or False)
        d['lastReadAt'] = d.get('last_read_at')
        # Parse tags JSON field
        try:
            tags_raw = d.get('tags')
            if tags_raw and isinstance(tags_raw, str):
                d['tags'] = json.loads(tags_raw)
            elif isinstance(tags_raw, list):
                d['tags'] = tags_raw
            else:
                d['tags'] = []
        except:
            d['tags'] = []
        try:
            pj = d.get('participants_json')
            d['participants'] = json.loads(pj) if isinstance(pj, str) else pj
        except Exception:
            d['participants'] = None
        d['hasDraft'] = bool(d.get('has_draft') or False)
        if d.get('draft_updated_at'):
            d['draft_updated_at'] = d['draft_updated_at'].isoformat() if hasattr(d['draft_updated_at'],'isoformat') else str(d['draft_updated_at'])
        d['draftUpdatedAt'] = d.get('draft_updated_at')
        d['draftText'] = d.get('draft_text')
        chats_list.append(d)

    return {"chats": chats_list, "hasMore": len(rows) > limit}


@router.patch("/{id}")
@safe_endpoint
def update_chat(id: str, body: Dict[str, Any], session: Session = Depends(get_db)):
    ensure_draft_columns(session)
    updated = False
    draft_changed = False

    if "title" in (body or {}):
        session.execute(text("UPDATE chatbot_chats SET title = :v WHERE id = :id"), {"v": body["title"], "id": id})
        updated = True
    if "visibility" in (body or {}):
        session.execute(text("UPDATE chatbot_chats SET visibility = :v WHERE id = :id"), {"v": body["visibility"], "id": id})
        updated = True
    if "is_starred" in (body or {}):
        session.execute(text("UPDATE chatbot_chats SET is_starred = :v WHERE id = :id"), {"v": bool(body["is_starred"]), "id": id})
        updated = True
    if "is_important" in (body or {}):
        session.execute(text("UPDATE chatbot_chats SET is_important = :v WHERE id = :id"), {"v": bool(body["is_important"]), "id": id})
        updated = True
    if "last_read_at" in (body or {}):
        session.execute(text("UPDATE chatbot_chats SET last_read_at = :v WHERE id = :id"), {"v": body["last_read_at"], "id": id})
        updated = True

    if "participants" in (body or {}):
        try:
            payload = json.dumps(body["participants"]) if not isinstance(body["participants"], str) else body["participants"]
        except Exception:
            payload = json.dumps([])
        session.execute(text("UPDATE chatbot_chats SET participants_json = :v WHERE id = :id"), {"v": payload, "id": id})
        updated = True

    if "draft_text" in (body or {}):
        raw = body.get("draft_text") or ""
        trimmed = raw.strip() if isinstance(raw, str) else str(raw).strip()
        if trimmed:
            session.execute(text(
                """
                UPDATE chatbot_chats
                SET has_draft = TRUE, draft_text = :t, draft_updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                """
            ), {"t": trimmed, "id": id})
        else:
            session.execute(text(
                """
                UPDATE chatbot_chats
                SET has_draft = FALSE, draft_text = NULL, draft_updated_at = NULL
                WHERE id = :id
                """
            ), {"id": id})
        updated = True
        draft_changed = True

    if updated:
        session.commit()

    resp: Dict[str, Any] = {"ok": True, "updated": updated}
    if draft_changed:
        row = session.execute(text(
            """
            SELECT COALESCE(has_draft, FALSE) AS has_draft, draft_text, draft_updated_at
            FROM chatbot_chats WHERE id = :id
            """
        ), {"id": id}).mappings().first()
        if row:
            resp.update({
                "has_draft": bool(row.get("has_draft") or False),
                "hasDraft": bool(row.get("has_draft") or False),
                "draft_text": row.get("draft_text"),
                "draftText": row.get("draft_text"),
                "draft_updated_at": row.get("draft_updated_at").isoformat() if getattr(row.get("draft_updated_at"), "isoformat", None) else row.get("draft_updated_at"),
                "draftUpdatedAt": row.get("draft_updated_at").isoformat() if getattr(row.get("draft_updated_at"), "isoformat", None) else row.get("draft_updated_at"),
            })
    return resp


@router.delete("/{id}")
@safe_endpoint
def delete_chat(id: str, session: Session = Depends(get_db)):
    session.execute(text("DELETE FROM chatbot_suggestions WHERE document_id IN (SELECT id FROM chatbot_documents WHERE user_id = (SELECT user_id FROM chatbot_chats WHERE id = :id))"), {"id": id})
    session.execute(text("DELETE FROM chatbot_votes_v2 WHERE chat_id = :id"), {"id": id})
    session.execute(text("DELETE FROM chatbot_messages_v2 WHERE chat_id = :id"), {"id": id})
    session.execute(text("DELETE FROM chatbot_streams WHERE chat_id = :id"), {"id": id})
    row = session.execute(text("DELETE FROM chatbot_chats WHERE id = :id RETURNING id, title"), {"id": id}).mappings().first()
    session.commit()
    return row


def _extract_visible_text(parts: Any, role: str) -> str:
    try:
        normalized = normalize_parts_for_storage(parts, role)
        texts: List[str] = []
        for p in normalized:
            if not isinstance(p, dict):
                continue
            meta = (p.get("experimental_metadata") or {})
            if meta.get("hidden") is True:
                continue
            if p.get("type") == "text":
                t = str(p.get("text") or "").strip()
                if t:
                    texts.append(t)
        return " ".join(texts).strip()[:200]
    except Exception:
        return ""


def _fetch_chats_with_latest(session: Session, user_id: str, limit: int,
                             starting_after: Optional[str], ending_before: Optional[str]) -> Dict[str, Any]:
    ensure_draft_columns(session)
    ensure_thread_contexts_index(session)

    base = """
        WITH latest AS (
            SELECT chat_id, MAX(created_at) AS max_ts
            FROM chatbot_messages_v2
            GROUP BY chat_id
        )
        SELECT c.id, c.created_at, c.title, c.user_id, c.visibility,
               COALESCE(c.is_starred, FALSE)   AS is_starred,
               COALESCE(c.is_important, FALSE) AS is_important,
               c.last_read_at,
               c.participants_json,
               COALESCE(c.has_draft, FALSE) AS has_draft,
               c.draft_text,
               c.draft_updated_at,
               c.tags,
               l.max_ts AS latest_message_at
        FROM chatbot_chats c
        LEFT JOIN latest l ON l.chat_id = c.id
        WHERE c.user_id = :uid
    """
    params: Dict[str, Any] = {"uid": user_id, "limit": limit + 1}

    if starting_after:
        sql = text(base + " AND created_at > (SELECT created_at FROM chatbot_chats WHERE id = :sa)"
                         + " ORDER BY COALESCE(latest_message_at, c.created_at) DESC LIMIT :limit")
        params["sa"] = starting_after
    elif ending_before:
        sql = text(base + " AND created_at < (SELECT created_at FROM chatbot_chats WHERE id = :eb)"
                         + " ORDER BY COALESCE(latest_message_at, c.created_at) DESC LIMIT :limit")
        params["eb"] = ending_before
    else:
        sql = text(base + " ORDER BY COALESCE(latest_message_at, c.created_at) DESC LIMIT :limit")

    chat_rows = session.execute(sql, params).mappings().all()
    chats = []
    for row in chat_rows[:limit]:
        d = dict(row)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat() if hasattr(d["created_at"], "isoformat") else str(d["created_at"])  # type: ignore[assignment]
        d["createdAt"] = d.get("created_at")
        d["userId"] = d.get("user_id")
        d["isStarred"] = bool(d.get("is_starred") or False)
        d["isImportant"] = bool(d.get("is_important") or False)
        d["lastReadAt"] = d.get("last_read_at")
        # Parse tags JSON field
        try:
            tags_raw = d.get('tags')
            if tags_raw and isinstance(tags_raw, str):
                d['tags'] = json.loads(tags_raw)
            elif isinstance(tags_raw, list):
                d['tags'] = tags_raw
            else:
                d['tags'] = []
        except:
            d['tags'] = []
        try:
            pj = d.get("participants_json")
            d["participants"] = json.loads(pj) if isinstance(pj, str) else pj
        except Exception:
            d["participants"] = None
        d["hasDraft"] = bool(d.get("has_draft") or False)
        if d.get("draft_updated_at"):
            d["draft_updated_at"] = d["draft_updated_at"].isoformat() if hasattr(d["draft_updated_at"], "isoformat") else str(d["draft_updated_at"])  # type: ignore[assignment]
        d["draftUpdatedAt"] = d.get("draft_updated_at")
        d["draftText"] = d.get("draft_text")
        chats.append(d)

    has_more = len(chat_rows) > limit

    chat_ids = [c["id"] for c in chats]
    latest_by_chat: Dict[str, Dict[str, Any]] = {}
    if chat_ids:
        placeholders = ", ".join(f":c{i}" for i in range(len(chat_ids)))
        latest_sql = text(f"""
            SELECT m.id, m.chat_id, m.role, m.parts, m.created_at
            FROM chatbot_messages_v2 m
            JOIN (
                SELECT chat_id, MAX(created_at) AS max_ts
                FROM chatbot_messages_v2
                WHERE chat_id IN ({placeholders})
                GROUP BY chat_id
            ) latest ON latest.chat_id = m.chat_id AND latest.max_ts = m.created_at
        """)
        rows = session.execute(latest_sql, {f"c{i}": cid for i, cid in enumerate(chat_ids)}).mappings().all()
        for r in rows:
            cid = r.get("chat_id")
            latest_by_chat[str(cid)] = {
                "last_message_at": r["created_at"].isoformat() if hasattr(r.get("created_at"), "isoformat") else str(r.get("created_at")),
                "last_role": r.get("role"),
                "last_snippet": _extract_visible_text(r.get("parts"), r.get("role")),
            }

    contexts_by_chat: Dict[str, List[Dict[str, Any]]] = {}
    if chat_ids:
        placeholders_ctx = ", ".join(f":t{i}" for i in range(len(chat_ids)))
        ctx_sql = text(f"""
            SELECT chat_id, context_type, context_id, context_name, MAX(added_at) AS added_at
            FROM thread_contexts
            WHERE chat_id IN ({placeholders_ctx})
            GROUP BY chat_id, context_type, context_id, context_name
        """)
        ctx_rows = session.execute(ctx_sql, {f"t{i}": cid for i, cid in enumerate(chat_ids)}).mappings().all()
        for r in ctx_rows:
            cid = str(r.get("chat_id"))
            contexts_by_chat.setdefault(cid, []).append({
                "type": r.get("context_type"),
                "id": r.get("context_id"),
                "name": r.get("context_name"),
                "addedAt": r.get("added_at").isoformat() if hasattr(r.get("added_at"), "isoformat") else str(r.get("added_at")),
            })

    enriched = []
    for c in chats:
        meta = latest_by_chat.get(str(c["id"])) or {}
        last_ts = meta.get("last_message_at")
        last_read = c.get("lastReadAt")
        has_unread = False
        try:
            if last_ts and (not last_read or (str(last_ts) > str(last_read))):
                has_unread = True
        except Exception:
            has_unread = False

        merged_participants = c.get("participants") if isinstance(c.get("participants"), list) else []
        if not merged_participants:
            ctxs = contexts_by_chat.get(str(c["id"])) or []
            for x in ctxs:
                if x.get("type") in ("chat", "contact") and x.get("name"):
                    merged_participants.append({"name": x.get("name"), "id": x.get("id"), "kind": x.get("type")})
            merged_participants = merged_participants[:5]

        enriched.append({
            **c,
            "lastMessageAt": meta.get("last_message_at"),
            "lastRole": meta.get("last_role"),
            "lastSnippet": meta.get("last_snippet", ""),
            "hasUnread": has_unread,
            "participants": merged_participants,
            "contexts": contexts_by_chat.get(str(c["id"])) or [],
        })

    return {"chats": enriched, "hasMore": has_more}


@router.get("/with-latest")
@safe_endpoint
def get_chats_with_latest_by_user_id(
    user_id: str,
    limit: int = 50,
    starting_after: Optional[str] = None,
    ending_before: Optional[str] = None,
    session: Session = Depends(get_db),
):
    return _fetch_chats_with_latest(session, user_id, limit, starting_after, ending_before)


# Legacy alias to keep /chatbot/chats-with-latest working without code duplication
legacy_router = create_router("/chatbot", tags=["chatbot"])


@legacy_router.get("/chats-with-latest")
@safe_endpoint
def get_chats_with_latest_by_user_id_legacy(
    user_id: str,
    limit: int = 50,
    starting_after: Optional[str] = None,
    ending_before: Optional[str] = None,
    session: Session = Depends(get_db),
):
    return _fetch_chats_with_latest(session, user_id, limit, starting_after, ending_before)


