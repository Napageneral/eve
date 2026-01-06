"""Document Display Service – generate and persist JSX displays for documents."""

from __future__ import annotations

from typing import Any, Dict, Optional
from datetime import datetime
import logging
import re

from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.services.core.utils import BaseService, timed, with_session
from backend.services.reports.persist import ReportEventsService
from backend.services.core.constants import TaskDefaults
from backend.services.llm import LLMConfigResolver, LLMService
from backend.repositories.document_displays import DocumentDisplayRepository
from backend.db.session_manager import new_session

logger = logging.getLogger(__name__)

CODE_BLOCK_START = re.compile(r'^```[\w]*\n')
CODE_BLOCK_END = re.compile(r'\n```$')


class DocumentDisplayService(BaseService):
    """Business logic for generating a display tied to a document version."""

    @staticmethod
    @timed("load_document_snapshot")
    @with_session(commit=False)
    def load_document_snapshot(document_id: str, document_created_at: Optional[datetime] = None, session: Optional[Session] = None) -> Dict[str, Any]:
        """Load the latest (or specific version) of a chatbot document.

        Returns {id, created_at, title, content, kind, user_id, origin_chat_id}.
        """
        if document_created_at is not None:
            sql = text(
                """
                SELECT id, created_at, title, content, kind, user_id, origin_chat_id
                FROM chatbot_documents
                WHERE id = :id AND created_at = :created_at
                LIMIT 1
                """
            )
            row = session.execute(sql, {"id": document_id, "created_at": document_created_at}).mappings().first()
        else:
            sql = text(
                """
                SELECT id, created_at, title, content, kind, user_id, origin_chat_id
                FROM chatbot_documents
                WHERE id = :id
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            row = session.execute(sql, {"id": document_id}).mappings().first()

        if not row:
            raise ValueError(f"Document ID={document_id} not found")

        return dict(row)

    @staticmethod
    def _clean_code(code: str) -> str:
        code = CODE_BLOCK_START.sub("", code)
        code = CODE_BLOCK_END.sub("", code)
        return code.replace("```", "").strip()

    @staticmethod
    @timed("save_document_display")
    def save(document_id: str, document_created_at: Optional[datetime], generated_code: str, model_used: str, cost: float) -> int:
        with new_session() as session:
            display_id = DocumentDisplayRepository.create_display(
                session,
                {
                    "document_id": document_id,
                    "document_created_at": document_created_at,
                    "generated_code": generated_code,
                    "model_used": model_used,
                    "cost": cost,
                },
            )
            session.commit()
            return display_id


class DocumentDisplayWorkflowService(BaseService):
    @staticmethod
    def generate(document_id: str, document_created_at: Optional[datetime] = None, llm_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        doc = DocumentDisplayService.load_document_snapshot(document_id, document_created_at)

        # Prompt: edge-to-edge wrapper but allow playful, tasteful accents inside.
        prompt = (
            "You are generating a mobile-first React JSX snippet for an in-app panel.\n"
            "Hard constraints (must follow exactly):\n"
            "- Top-level element must fill the host container; use 100% width and natural height.\n"
            "- No OUTER chrome on the top-level wrapper: no outer padding/margin/border/border-radius/shadow.\n"
            "- Internal spacing/styling is welcome via nested elements (padding/margin within sections).\n"
            "- No external fetches, no scripts, no global styles/reset; inline className utility styles only.\n"
            "- Return ONLY raw JSX (no markdown fences, no export statements, no render call).\n\n"
            "Design guidance (bring back color tastefully):\n"
            "- Use playful accent colors, soft gradients, and subtle shadows INSIDE sections, buttons, or chips.\n"
            "- Keep content scannable (short headings, concise bullets, compact whitespace).\n"
            "- Prefer rounded elements and lively accents, but keep the page itself edge-to-edge.\n\n"
            f"Document Title: {doc.get('title') or ''}\n"
            "Document Content:\n"
            f"{doc.get('content') or ''}\n"
        )

        cfg = LLMConfigResolver.resolve_config(
            base_config={
                "model_name": TaskDefaults.DISPLAY_MODEL,
                "temperature": TaskDefaults.DISPLAY_TEMPERATURE,
                "max_tokens": TaskDefaults.DISPLAY_MAX_TOKENS,
            },
            prompt_config=None,
            user_override=llm_override,
        )

        resp = LLMService.call_llm(prompt_str=prompt, llm_config_dict=cfg)
        code = DocumentDisplayService._clean_code(resp.get("content", ""))
        display_id = DocumentDisplayService.save(
            document_id=document_id,
            document_created_at=doc.get("created_at"),
            generated_code=code,
            model_used=cfg["model_name"],
            cost=resp.get("usage", {}).get("total_cost", 0.0),
        )
        # Emit a completion event to the SSE stream for frontends to react (no polling)
        try:
            ReportEventsService.publish_report_event(
                "display_completed",
                {
                    "document_id": document_id,
                    "display_id": display_id,
                },
            )
        except Exception:
            logger.debug("Failed to publish display_completed event for document", exc_info=True)

        return {"success": True, "display_id": display_id, "cost": resp.get("usage", {}).get("total_cost", 0.0)}


__all__ = [
    "DocumentDisplayService",
    "DocumentDisplayWorkflowService",
]


