from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)


def normalize_parts_for_storage(parts: Any, role: str) -> List[Dict[str, Any]]:
    """Normalize message parts and mark compiled prompt content as hidden when appropriate.

    This mirrors the logic previously embedded in the chatbot router so the UI never flashes
    compiled instruction text. It is intentionally tolerant of malformed inputs.
    """
    try:
        plist: List[Dict[str, Any]]
        if isinstance(parts, str):
            plist = json.loads(parts)
        else:
            plist = list(parts or [])
    except Exception:
        plist = []

    has_compiled_marker = any(
        isinstance(p, dict)
        and (
            p.get("experimental_metadata", {}).get("hasCompiledContext") is True
            or p.get("experimental_metadata", {}).get("isCompiledPrompt") is True
        )
        for p in plist
    )
    instruction_markers = (
        "IMPORTANT: Provide a comprehensive, well-structured report.",
        "IMPORTANT: Ensure your response is detailed",
        "If tool calls are available, DELIVER THE FINAL REPORT VIA THE `createDocument` TOOL",
        "DELIVER THE FINAL REPORT VIA THE createDocument",
        "FORMAT INSTRUCTIONS:",
        "--- Instructions:",
    )

    def mark_hidden(p: Dict[str, Any]) -> Dict[str, Any]:
        meta = dict(p.get("experimental_metadata") or {})
        meta["hidden"] = True
        q = dict(p)
        q["experimental_metadata"] = meta
        return q

    normalized: List[Dict[str, Any]] = []
    for p in plist:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            text_val = p.get("text") or ""
            meta = p.get("experimental_metadata") or {}
            if meta.get("isCompiledPromptShort") is True:
                normalized.append(p)
                continue
            if meta.get("hidden") is True:
                normalized.append(p)
                continue
            if isinstance(text_val, str) and text_val.startswith("[HIDDEN_PROMPT]"):
                normalized.append(mark_hidden(p))
                continue
            if (role == "user" and any(m in text_val for m in instruction_markers)) or (
                role == "user" and isinstance(text_val, str) and len(text_val) > 5000 and has_compiled_marker
            ):
                normalized.append(mark_hidden(p))
                continue
        normalized.append(p)

    return normalized


def ensure_draft_columns(session: Session) -> None:
    """Idempotently ensure draft-related columns exist on chatbot_chats.

    Uses SQLite PRAGMA inspection and IF NOT EXISTS for portability.
    """
    try:
        dialect = getattr(getattr(session, "bind", None), "dialect", None)
        name = getattr(dialect, "name", "") if dialect else ""
        is_sqlite = str(name).lower().startswith("sqlite")
    except Exception:
        is_sqlite = False

    if is_sqlite:
        existing: set[str] = set()
        try:
            rows = session.execute(text("PRAGMA table_info('chatbot_chats')")).all()
            for r in rows:
                try:
                    colname = r[1] if isinstance(r, (list, tuple)) else (r.get("name") if hasattr(r, "get") else None)
                    if colname:
                        existing.add(str(colname))
                except Exception:
                    pass
        except Exception:
            existing = set()

        stmts: list[str] = []
        if "draft_text" not in existing:
            stmts.append("ALTER TABLE chatbot_chats ADD COLUMN draft_text TEXT")
        if "draft_updated_at" not in existing:
            stmts.append("ALTER TABLE chatbot_chats ADD COLUMN draft_updated_at TIMESTAMP")
        if "has_draft" not in existing:
            stmts.append("ALTER TABLE chatbot_chats ADD COLUMN has_draft BOOLEAN DEFAULT 0")
        stmts.append("CREATE INDEX IF NOT EXISTS idx_chatbot_chats_has_draft ON chatbot_chats (has_draft)")

        for s in stmts:
            try:
                session.execute(text(s))
            except Exception:
                logger.debug("[chatbot] draft schema stmt skipped/failed (sqlite): %s", s)
        try:
            session.commit()
        except Exception:
            pass
        return

    for s in [
        "ALTER TABLE chatbot_chats ADD COLUMN IF NOT EXISTS draft_text TEXT",
        "ALTER TABLE chatbot_chats ADD COLUMN IF NOT EXISTS draft_updated_at TIMESTAMPTZ",
        "ALTER TABLE chatbot_chats ADD COLUMN IF NOT EXISTS has_draft BOOLEAN DEFAULT FALSE",
        "CREATE INDEX IF NOT EXISTS idx_chatbot_chats_has_draft ON chatbot_chats (has_draft)",
    ]:
        try:
            session.execute(text(s))
        except Exception:
            logger.debug("[chatbot] draft migration statement skipped/failed: %s", s)
    try:
        session.commit()
    except Exception:
        pass


def ensure_thread_contexts_index(session: Session) -> None:
    """Ensure unique index exists for de-duplicating thread contexts."""
    try:
        session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_thread_ctx_unique ON thread_contexts (chat_id, context_type, context_id)"
        ))
        session.commit()
    except Exception:
        logger.debug("[chatbot] ensure thread_contexts index skipped/failed")


