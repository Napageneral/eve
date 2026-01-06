from __future__ import annotations

from typing import Any, Dict, Optional, List
from pydantic import BaseModel

from backend.routers.common import (
    create_router, safe_endpoint, text, Session, Depends, get_db, log_simple
)
from backend.db.session_manager import new_session
from backend.services.reports.persist import ReportEventsService

router = create_router("/chatbot/documents", tags=["chatbot"])

# For single-user Electron app, bake in a constant owner for any legacy codepaths
LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001"


def _resolve_context_name(session: Session, ctx: Dict[str, Any]) -> str:
    """Resolve context name from backend tables (chat/contact names).
    
    Falls back to frontend-provided name if resolution fails.
    """
    ctx_type = str(ctx.get("type", "unknown"))
    ctx_id = ctx.get("id") or ctx.get("context_id")
    ctx_name = str(ctx.get("name") or ctx.get("context_name") or "")
    
    # Resolve from backend tables for accuracy
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
    
    return ctx_name


class DocumentUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    append: Optional[str] = None
    kind: Optional[str] = None
    user_id: Optional[str] = None
    origin_chat_id: Optional[str] = None
    tags: Optional[List[str]] = None
    contexts: Optional[List[Dict[str, Any]]] = None


class UpdateContextsRequest(BaseModel):
    contexts: List[Dict[str, Any]]
    mode: str = "replace"  # "replace" or "add"


@router.post("")
@safe_endpoint
def save_document(body: Dict[str, Any], session: Session = Depends(get_db)):
    if body.get("user_id"):
        session.execute(text(
            """
            INSERT INTO chatbot_users (id, email, password, created_at, updated_at)
            SELECT :id, :email, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (SELECT 1 FROM chatbot_users WHERE id = :id)
            """
        ), {"id": body["user_id"], "email": f"guest-{body['user_id']}"} )
    session.execute(text(
        """
        INSERT INTO chatbot_documents (id, created_at, title, content, kind, user_id, origin_chat_id, tags)
        VALUES (:id, CURRENT_TIMESTAMP, :title, :content, :kind, :user_id, :origin_chat_id, :tags)
       """
    ), {
        "id": body["id"],
        "title": body["title"],
        "content": body.get("content"),
        "kind": body["kind"],
        "user_id": body["user_id"],
        "origin_chat_id": body.get("origin_chat_id"),
        "tags": (None if body.get("tags") is None else __import__('json').dumps(body.get("tags"))),
    })
    
    # Snapshot thread contexts to document
    if body.get("origin_chat_id"):
        try:
            # Get all contexts from the origin thread
            thread_contexts = session.execute(text(
                """
                SELECT context_type, context_id, context_name
                FROM thread_contexts
                WHERE chat_id = :chat_id
                """
            ), {"chat_id": body["origin_chat_id"]}).mappings().all()
            
            # Copy each context to document_contexts
            for ctx in thread_contexts:
                row_id = f"{body['id']}:{ctx['context_type']}:{ctx.get('context_id', '')}"
                session.execute(text(
                    """
                    INSERT INTO document_contexts (id, document_id, context_type, context_id, context_name, added_at)
                    VALUES (:id, :doc_id, :type, :ctx_id, :name, CURRENT_TIMESTAMP)
                    ON CONFLICT DO NOTHING
                    """
                ), {
                    "id": row_id,
                    "doc_id": body["id"],
                    "type": ctx["context_type"],
                    "ctx_id": ctx.get("context_id"),
                    "name": ctx.get("context_name"),
                })
        except Exception as e:
            # Non-fatal - document still saves even if context snapshot fails
            log_simple(f"Failed to snapshot contexts for document {body['id']}: {e}")
    
    session.commit()
    # SSE: notify clients a new document version exists
    try:
        ReportEventsService.publish_report_event(
            "document_saved",
            {
                "document_id": str(body["id"]),
                "user_id": str(body.get("user_id") or ""),
                "chat_id": body.get("origin_chat_id"),
            },
        )
    except Exception:
        pass
    # Enqueue background display generation based on simple in-code rules
    try:
        from backend.celery_service.tasks.generate_document_display import generate_document_display_task
        # Auto-generate for text documents tagged with 'display:auto' or kind 'text'
        tags = body.get("tags") or []
        should_generate = (body.get("kind") in ("text", "sheet")) or (isinstance(tags, list) and ("display:auto" in tags))
        if should_generate:
            log_simple(f"Auto-queue display generation for new document {body['id']}")
            generate_document_display_task.apply_async(args=[body["id"]], kwargs={}, queue='chatstats-display')
    except Exception:
        pass
    try:
        from backend.celery_service.tasks.embeddings import embed_artifacts_for_user_task
        if body.get("user_id"):
            embed_artifacts_for_user_task.delay(str(body.get("user_id")))
    except Exception:
        pass
    return {"ok": True}


@router.post("/{id}/update")
@safe_endpoint
def update_document_version(id: str, req: DocumentUpdateRequest, session: Session = Depends(get_db)):
    latest = session.execute(text(
        """
        SELECT id, title, content, kind, user_id, origin_chat_id, tags
        FROM chatbot_documents WHERE id = :id ORDER BY created_at DESC LIMIT 1
        """
    ), {"id": id}).mappings().first()

    base = latest or {"id": id, "title": None, "content": None, "kind": None, "user_id": None, "origin_chat_id": None}
    new_content = req.content
    if new_content is None and req.append:
        new_content = (base.get("content") or "") + req.append

    session.execute(text(
        """
        INSERT INTO chatbot_documents (id, created_at, title, content, kind, user_id, origin_chat_id, tags)
        VALUES (:id, CURRENT_TIMESTAMP, :title, :content, :kind, :user_id, :origin_chat_id, :tags)
        """
    ), {
        "id": id,
        "title": req.title if req.title is not None else base.get("title"),
        "content": new_content if new_content is not None else base.get("content"),
        "kind": req.kind if req.kind is not None else base.get("kind"),
        "user_id": req.user_id if req.user_id is not None else base.get("user_id"),
        "origin_chat_id": req.origin_chat_id if req.origin_chat_id is not None else base.get("origin_chat_id"),
        "tags": (__import__('json').dumps(req.tags) if req.tags is not None else (base.get("tags"))),
    })
    
    # Update contexts if provided
    if req.contexts is not None:
        try:
            # Replace all contexts (delete old, add new)
            session.execute(text(
                "DELETE FROM document_contexts WHERE document_id = :doc_id"
            ), {"doc_id": id})
            
            # Add new contexts
            for ctx in req.contexts:
                ctx_name = _resolve_context_name(session, ctx)
                ctx_type = str(ctx.get("type", "unknown"))
                ctx_id = ctx.get("id") or ctx.get("context_id")
                
                row_id = f"{id}:{ctx_type}:{ctx_id or ''}"
                session.execute(text(
                    """
                    INSERT INTO document_contexts (id, document_id, context_type, context_id, context_name, added_at)
                    VALUES (:id, :doc_id, :type, :ctx_id, :name, CURRENT_TIMESTAMP)
                    ON CONFLICT DO NOTHING
                    """
                ), {
                    "id": row_id,
                    "doc_id": id,
                    "type": ctx_type,
                    "ctx_id": str(ctx_id) if ctx_id else None,
                    "name": ctx_name,
                })
        except Exception as e:
            log_simple(f"Failed to update contexts for document {id}: {e}")
    
    session.commit()
    # SSE: notify clients an updated document version exists
    try:
        ReportEventsService.publish_report_event(
            "document_saved",
            {
                "document_id": str(id),
                "user_id": str(req.user_id or base.get("user_id") or ""),
                "chat_id": req.origin_chat_id or base.get("origin_chat_id"),
            },
        )
    except Exception:
        pass
    # Auto-enqueue for this specific version
    try:
        from backend.celery_service.tasks.generate_document_display import generate_document_display_task
        should_generate = (req.kind in (None, "text", "sheet")) or (isinstance(req.tags, list) and ("display:auto" in req.tags))
        if should_generate:
            log_simple(f"Auto-queue display generation for updated document {id}")
            generate_document_display_task.apply_async(args=[id], kwargs={}, queue='chatstats-display')
    except Exception:
        pass
    return {"ok": True}


@router.patch("/{id}/contexts")
@safe_endpoint
def update_document_contexts(id: str, req: UpdateContextsRequest, session: Session = Depends(get_db)):
    """Update contexts for a document without creating a new document version.
    
    This allows updating context associations independently of document content.
    
    Args:
        id: Document ID
        req.contexts: List of context objects with type, id, name
        req.mode: "replace" (default) or "add"
            - replace: Delete all existing contexts and add new ones
            - add: Merge new contexts with existing ones
    
    Example:
        PATCH /api/chatbot/documents/{id}/contexts
        {
            "contexts": [
                {"type": "chat", "id": 3, "name": "Casey Adams"},
                {"type": "contact", "id": 5, "name": "John Doe"}
            ],
            "mode": "replace"
        }
    """
    mode = req.mode or "replace"
    
    if mode == "replace":
        # Delete existing contexts
        session.execute(text(
            "DELETE FROM document_contexts WHERE document_id = :doc_id"
        ), {"doc_id": id})
    
    # Add new contexts
    for ctx in req.contexts:
        ctx_name = _resolve_context_name(session, ctx)
        ctx_type = str(ctx.get("type", "unknown"))
        ctx_id = ctx.get("id") or ctx.get("context_id")
        
        row_id = f"{id}:{ctx_type}:{ctx_id or ''}"
        session.execute(text(
            """
            INSERT INTO document_contexts (id, document_id, context_type, context_id, context_name, added_at)
            VALUES (:id, :doc_id, :type, :ctx_id, :name, CURRENT_TIMESTAMP)
            ON CONFLICT DO NOTHING
            """
        ), {
            "id": row_id,
            "doc_id": id,
            "type": ctx_type,
            "ctx_id": str(ctx_id) if ctx_id else None,
            "name": ctx_name,
        })
    
    session.commit()
    
    # Notify clients that contexts changed (so inbox can refresh)
    try:
        ReportEventsService.publish_report_event(
            "document_contexts_updated",
            {
                "document_id": str(id),
                "context_count": len(req.contexts),
            },
        )
    except Exception:
        pass
    
    return {"ok": True, "contexts_updated": len(req.contexts)}


@router.get("/{id}")
@safe_endpoint
def get_documents_by_id(id: str, session: Session = Depends(get_db)):
    return session.execute(text(
        """
        SELECT id, created_at, title, content, kind, user_id, origin_chat_id
        FROM chatbot_documents WHERE id = :id ORDER BY created_at ASC
        """
    ), {"id": id}).mappings().all()


@router.get("/{id}/latest")
@safe_endpoint
def get_document_by_id_latest(id: str, session: Session = Depends(get_db)):
    return session.execute(text(
        """
        SELECT id, created_at, title, content, kind, user_id, origin_chat_id, tags
        FROM chatbot_documents WHERE id = :id ORDER BY created_at DESC LIMIT 1
        """
    ), {"id": id}).mappings().first()


@router.get("")
@safe_endpoint
def get_all_documents_single_user(session: Session = Depends(get_db)):
    """Return latest version per document for this single-user app, with read/display flags and contexts.

    This endpoint bakes in a constant owner and removes the need for user_id on the client.
    """
    sql = text(
        """
        WITH latest_docs AS (
          SELECT d.*
          FROM chatbot_documents d
          JOIN (
            SELECT id, MAX(created_at) AS max_created
            FROM chatbot_documents
            GROUP BY id
          ) m ON m.id = d.id AND m.max_created = d.created_at
        ), latest_displays AS (
          SELECT dd.document_id, dd.id AS latest_display_id, dd.created_at AS display_created_at
          FROM chatbot_document_displays dd
          JOIN (
            SELECT document_id, MAX(created_at) AS max_created
            FROM chatbot_document_displays
            GROUP BY document_id
          ) md ON md.document_id = dd.document_id AND md.max_created = dd.created_at
        )
        SELECT 
          d.id,
          d.created_at,
          d.title,
          d.kind,
          d.origin_chat_id as chat_id,
          c.title as chat_title,
          d.tags,
          (ld.latest_display_id IS NOT NULL) AS has_display,
          ld.latest_display_id,
          ld.display_created_at,
          (d.created_at > COALESCE(r.last_read_at, '1970-01-01')) AS unread,
          (ld.display_created_at IS NOT NULL AND ld.display_created_at > COALESCE(r.display_read_at, '1970-01-01')) AS display_unread
        FROM latest_docs d
        LEFT JOIN chatbot_chats c ON c.id = d.origin_chat_id
        LEFT JOIN latest_displays ld ON ld.document_id = d.id
        LEFT JOIN chatbot_document_reads_simple r ON r.document_id = d.id
        ORDER BY d.created_at DESC
        """
    )
    
    docs = session.execute(sql, {}).mappings().all()
    
    # Fetch contexts for all documents (same pattern as chats.py)
    doc_ids = [str(d["id"]) for d in docs]
    contexts_by_doc = {}
    
    if doc_ids:
        placeholders = ", ".join(f":d{i}" for i in range(len(doc_ids)))
        ctx_sql = text(f"""
            SELECT document_id, context_type, context_id, context_name
            FROM document_contexts
            WHERE document_id IN ({placeholders})
        """)
        ctx_rows = session.execute(ctx_sql, {f"d{i}": did for i, did in enumerate(doc_ids)}).mappings().all()
        
        for r in ctx_rows:
            doc_id = str(r["document_id"])
            if doc_id not in contexts_by_doc:
                contexts_by_doc[doc_id] = []
            contexts_by_doc[doc_id].append({
                "type": r["context_type"],
                "id": r.get("context_id"),
                "name": r.get("context_name"),
            })
    
    # Attach contexts to each document
    result = []
    for d in docs:
        doc_dict = dict(d)
        doc_dict["contexts"] = contexts_by_doc.get(str(d["id"]), [])
        result.append(doc_dict)
    
    return result


@router.patch("/{id}/read")
@safe_endpoint
def mark_document_read(id: str, body: Dict[str, Any] | None = None, session: Session = Depends(get_db)):
    user_id = LOCAL_USER_ID
    session.execute(text(
        """
        INSERT INTO chatbot_document_reads_simple (document_id, last_read_at, created_at, updated_at)
        VALUES (:doc_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (document_id)
        DO UPDATE SET last_read_at = EXCLUDED.last_read_at, updated_at = CURRENT_TIMESTAMP
        """
    ), {"doc_id": id})
    session.commit()
    return {"ok": True}


@router.patch("/{id}/unread")
@safe_endpoint
def mark_document_unread(id: str, body: Dict[str, Any] | None = None, session: Session = Depends(get_db)):
    user_id = LOCAL_USER_ID
    session.execute(text(
        """
        INSERT INTO chatbot_document_reads_simple (document_id, last_read_at, display_read_at, created_at, updated_at)
        VALUES (:doc_id, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (document_id)
        DO UPDATE SET last_read_at = NULL, display_read_at = NULL, updated_at = CURRENT_TIMESTAMP
        """
    ), {"doc_id": id})
    session.commit()
    return {"ok": True}


@router.patch("/{id}/display-read")
@safe_endpoint
def mark_display_read(id: str, body: Dict[str, Any] | None = None, session: Session = Depends(get_db)):
    user_id = LOCAL_USER_ID
    # Mark display_read_at; also treat as document read (display implies doc read)
    session.execute(text(
        """
        INSERT INTO chatbot_document_reads_simple (document_id, last_read_at, display_read_at, created_at, updated_at)
        VALUES (:doc_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (document_id)
        DO UPDATE SET display_read_at = CURRENT_TIMESTAMP, last_read_at = COALESCE(chatbot_document_reads_simple.last_read_at, CURRENT_TIMESTAMP), updated_at = CURRENT_TIMESTAMP
        """
    ), {"doc_id": id})
    session.commit()
    return {"ok": True}


@router.delete("/{id}")
@safe_endpoint
def delete_document_by_id(id: str, session: Session = Depends(get_db)):
    session.execute(text("DELETE FROM chatbot_suggestions WHERE document_id = :id"), {"id": id})
    rows = session.execute(text(
        """
        DELETE FROM chatbot_documents WHERE id = :id RETURNING id, created_at
        """
    ), {"id": id}).mappings().all()
    session.commit()
    return rows


@router.delete("/{id}/after-timestamp")
@safe_endpoint
def delete_documents_by_id_after_timestamp(id: str, body: Dict[str, Any], session: Session = Depends(get_db)):
    session.execute(text(
        """
        DELETE FROM chatbot_suggestions WHERE document_id = :id AND document_created_at > :ts
        """
    ), {"id": id, "ts": body["after_timestamp"]})
    rows = session.execute(text(
        """
        DELETE FROM chatbot_documents WHERE id = :id AND created_at > :ts
        RETURNING id, created_at
        """
    ), {"id": id, "ts": body["after_timestamp"]}).mappings().all()
    session.commit()
    return rows


@router.post("/suggestions")
@safe_endpoint
def save_suggestions(body: Dict[str, Any], session: Session = Depends(get_db)):
    suggestions: List[Dict[str, Any]] = body.get("suggestions", [])
    if not suggestions:
        return {"ok": True}
    ins = text(
        """
        INSERT INTO chatbot_suggestions (
            id, document_id, document_created_at, original_text, suggested_text, description, is_resolved, user_id, created_at
        ) VALUES (
            :id, :document_id, :document_created_at, :original_text, :suggested_text, :description, :is_resolved, :user_id, :created_at
        ) ON CONFLICT(id) DO NOTHING
        """
    )
    for s in suggestions:
        session.execute(ins, s)
    session.commit()
    return {"ok": True}


@router.get("/suggestions")
@safe_endpoint
def get_suggestions_by_document_id(document_id: str):
    sql = text(
        """
        SELECT id, document_id, document_created_at, original_text, suggested_text, description, is_resolved, user_id, created_at
        FROM chatbot_suggestions WHERE document_id = :id
        """
    )
    with new_session() as s:
        return s.execute(sql, {"id": document_id}).mappings().all()


