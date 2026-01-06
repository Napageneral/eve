from __future__ import annotations

from typing import Any, Dict, Optional

from backend.routers.common import (
    create_router, safe_endpoint, log_simple, HTTPException, BaseModel, db
)
from backend.repositories.document_displays import DocumentDisplayRepository

router = create_router("/chatbot", "Document Displays")


class GenerateDocumentDisplayRequest(BaseModel):
    document_id: str
    document_created_at: Optional[str] = None  # ISO8601
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


@router.get("/documents/{document_id}/display")
@safe_endpoint
def get_latest_document_display(document_id: str):
    """Return the latest display for a document (any version)."""
    with db.session_scope() as session:
        row = DocumentDisplayRepository.get_latest_for_document(session, document_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"No display found for document {document_id}")
        return row


@router.get("/documents/{document_id}/versions/{created_at}/display")
@safe_endpoint
def get_document_display_for_version(document_id: str, created_at: str):
    with db.session_scope() as session:
        row = DocumentDisplayRepository.get_for_document_version(session, document_id, created_at)
        if not row:
            raise HTTPException(status_code=404, detail=f"No display for document {document_id} at {created_at}")
        return row


@router.get("/document-displays/{display_id}")
@safe_endpoint
def get_document_display_by_id(display_id: int):
    with db.session_scope() as session:
        row = DocumentDisplayRepository.get_display_by_id(session, display_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Display {display_id} not found")
        return row


@router.get("/documents/{document_id}/displays")
@safe_endpoint
def list_document_displays(document_id: str):
    """Return all displays for a document (newest first)."""
    with db.session_scope() as session:
        rows = DocumentDisplayRepository.list_for_document(session, document_id)
        return {"rows": rows}


@router.post("/documents/{document_id}/generate-display", status_code=202)
@safe_endpoint
def queue_generate_document_display(document_id: str, body: GenerateDocumentDisplayRequest):
    log_simple("Queuing document display generation task")

    from backend.celery_service.tasks.generate_document_display import generate_document_display_task

    created_at_iso = body.document_created_at
    result = generate_document_display_task.apply_async(
        args=[document_id],
        kwargs={
            "document_created_at": created_at_iso,
            "model": body.model,
            "max_tokens": body.max_tokens,
            "temperature": body.temperature,
        },
        queue='chatstats-display',
    )

    return {"task_id": result.id, "status": "queued", "message": "Document display generation task queued"}


@router.post("/documents/{document_id}/regenerate-display", status_code=202)
@safe_endpoint
def queue_regenerate_document_display(document_id: str, body: GenerateDocumentDisplayRequest):
    # For now identical to generate; explicit endpoint to mirror requirements
    return queue_generate_document_display(document_id, body)


