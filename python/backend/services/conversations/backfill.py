from __future__ import annotations

"""BackfillService – administrative helpers for (re)running analysis passes on
historical conversations.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from backend.db.session_manager import db
from backend.repositories.conversations import ConversationRepository
from backend.celery_service.analysis_passes import (
    get_pending_passes,
    trigger_analysis_pass,
    get_pass_config,
    ANALYSIS_PASSES,
)

logger = logging.getLogger(__name__)


class BackfillService:
    """Collection of static helpers for backfilling analysis passes."""

    # ------------------------------------------------------------------
    # Single-pass backfill
    # ------------------------------------------------------------------

    @staticmethod
    def backfill_analysis_pass(
        pass_name: str,
        *,
        chat_id: Optional[int] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        if pass_name not in ANALYSIS_PASSES:
            raise ValueError(f"Unknown analysis pass: {pass_name}")

        config = get_pass_config(pass_name)
        if not config.get("enabled", True):
            msg = f"Analysis pass '{pass_name}' is disabled"
            logger.warning(msg)
            return {"error": msg}

        triggered = skipped = errors = 0
        convo_ids: List[int] = []

        try:
            with db.session_scope() as session:
                conversations = ConversationRepository.list_for_backfill(
                    session, chat_id=chat_id, limit=limit
                )
                logger.info("Found %s conversations to check for pass '%s'", len(conversations), pass_name)

                for conv_id, conv_chat_id in conversations:
                    try:
                        pending = get_pending_passes(session, conv_id)
                        if pass_name in pending:
                            convo_ids.append(conv_id)
                            if dry_run:
                                triggered += 1
                                continue
                            trigger_analysis_pass(conv_id, conv_chat_id, pass_name)
                            triggered += 1
                        else:
                            skipped += 1
                    except Exception as exc:
                        logger.error("Error processing conversation %s: %s", conv_id, exc, exc_info=True)
                        errors += 1
        except Exception as exc:
            logger.error("Backfill failure: %s", exc, exc_info=True)
            return {"error": str(exc)}

        return {
            "pass_name": pass_name,
            "chat_id": chat_id,
            "limit": limit,
            "dry_run": dry_run,
            "triggered": triggered,
            "skipped": skipped,
            "errors": errors,
            "conversation_ids": convo_ids[:100],
        }

    # ------------------------------------------------------------------
    # Multi-pass helper
    # ------------------------------------------------------------------

    @staticmethod
    def backfill_all_pending_passes(
        *,
        chat_id: Optional[int] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        totals = {"triggered": 0, "skipped": 0, "errors": 0}

        enabled_passes = {k: v for k, v in ANALYSIS_PASSES.items() if v.get("enabled", True)}
        logger.info("Backfilling %s passes (enabled)", len(enabled_passes))

        for pass_name in enabled_passes:
            res = BackfillService.backfill_analysis_pass(
                pass_name,
                chat_id=chat_id,
                limit=limit,
                dry_run=dry_run,
            )
            results[pass_name] = res
            if "error" not in res:
                totals["triggered"] += res["triggered"]
                totals["skipped"] += res["skipped"]
                totals["errors"] += res["errors"]
            else:
                totals["errors"] += 1

        summary = {
            "total_passes": len(enabled_passes),
            **totals,
            "chat_id": chat_id,
            "limit": limit,
            "dry_run": dry_run,
        }
        return {"summary": summary, "results": results} 