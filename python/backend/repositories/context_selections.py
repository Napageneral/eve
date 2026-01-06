from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from .core.generic import GenericRepository


class ContextSelectionRepository(GenericRepository):
    """Provides helpers for reading & updating context selections with their definitions."""

    TABLE = "context_selections"

    # ---------------------------------------------------------------------
    # Read helpers
    # ---------------------------------------------------------------------

    @classmethod
    def get_with_definition(
        cls, session: Session, selection_id: int
    ) -> Optional[Dict[str, Any]]:
        """Return a single context selection plus its definition details."""
        sql = """
            SELECT cs.id,
                   cs.context_definition_id,
                   cs.parameter_values,
                   cs.resolved_content,
                   cs.token_count,
                   cs.created_at,
                   cd.name               AS definition_name,
                   cd.retrieval_function_ref,
                   cd.description
            FROM context_selections cs
            JOIN context_definitions cd ON cs.context_definition_id = cd.id
            WHERE cs.id = :id
        """
        return cls.fetch_one(session, sql, {"id": selection_id})

    # ---------------------------------------------------------------------
    # Write helpers
    # ---------------------------------------------------------------------

    @classmethod
    def update_content(
        cls,
        session: Session,
        selection_id: int,
        resolved_content: str,
        token_count: int,
    ) -> int:
        """Update rendered content & token count for a context selection."""
        sql = """
            UPDATE context_selections
            SET resolved_content = :resolved_content,
                token_count      = :token_count,
                updated_at       = :updated_at
            WHERE id = :id
        """
        return cls.execute(
            session,
            sql,
            {
                "resolved_content": resolved_content,
                "token_count": token_count,
                "updated_at": datetime.utcnow(),
                "id": selection_id,
            },
        ) 