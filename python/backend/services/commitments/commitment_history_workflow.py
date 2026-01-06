from __future__ import annotations

"""Historical Commitment Workflow Service – assembles the Celery chain that runs
commitment extraction/reconciliation for an entire chat (or globally).
"""

import logging
from typing import Optional, List, Tuple
from celery import chain

from backend.db.session_manager import new_session
from backend.repositories.conversations import ConversationRepository

logger = logging.getLogger(__name__)


class HistoricalCommitmentWorkflowService:
    """Factory for building the historical-analysis Celery chain."""

    @staticmethod
    def build_chain(chat_id: Optional[int] = None):
        """Return a Celery `chain` that processes historical commitments.

        Args:
            chat_id: If provided, restrict analysis to this chat; otherwise run
                globally across all chats.
        Returns:
            Celery signature or ``None`` if no conversations are pending.
        """

        scope = "commitments:global" if chat_id is None else f"commitments:{chat_id}"

        # Fetch conversations in-order
        with new_session() as session:
            convs: List[Tuple[int, int]] = ConversationRepository.list_for_history(
                session, chat_id
            )
            total = len(convs)

        if total == 0:
            logger.info("[HIST/SVC] No conversations found for history analysis (chat=%s)", chat_id)
            return None, scope, total

        # Build sequential chain: init → each conversation → finalize
        from backend.celery_service.tasks.commitment_history import (
            initialize_analysis,
            process_conversation,
            finalize_analysis,
        )
        
        analysis_chain = initialize_analysis.s(scope, total)
        for idx, (conv_id, conv_chat_id) in enumerate(convs):
            analysis_chain |= process_conversation.s(conv_id, conv_chat_id, idx, total, scope)
        analysis_chain |= finalize_analysis.s(scope, total)

        return analysis_chain, scope, total 