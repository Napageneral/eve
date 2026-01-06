import logging
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session

from backend.repositories.commitments import CommitmentRepository
from backend.repositories.contacts import ContactRepository
from backend.services.llm import LLMService
from backend.services.core.utils import BaseService, timed
from backend.services.core.constants import (
    STREAM_SCOPE_TEMPLATE, 
    DefaultLLMConfigs, 
    PromptCategories, 
    PromptNames
)

logger = logging.getLogger(__name__)


class CommitmentService(BaseService):
    """Light-weight commitment analysis service (≈300 LOC).

    Preserves the proven two-stage LLM pipeline (extraction → reconciliation)
    while removing event-sourcing, snapshots, Celery chains, and other legacy
    complexity.  All real-time notifications are pushed via Redis Streams / SSE
    using the same helper utilised by global analysis tasks.
    """

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def __init__(self) -> None:
        self.repository = CommitmentRepository()

    def analyze_conversation_commitments(
        self,
        session: Session,
        conversation_id: int,
        chat_id: int,
        encoded_conversation: str,
        *,
        is_realtime: bool = True,
    ) -> Dict[str, Any]:
        """Main entry-point – runs the two-stage pipeline and applies results.

        Args:
            session: Active DB session (transaction control left to caller)
            conversation_id: Conversation being analysed
            chat_id: Owning chat
            encoded_conversation: Conversation text prepared for LLM
            is_realtime: If *True* publish live SSE events
        Returns:
            Dict summarising counts and DB actions performed.
        """
        logger.info(
            f"[COMMIT] Analysing commitments for convo {conversation_id} (chat {chat_id})"
        )

        # ---------- Stage 1: Pure extraction ----------
        extracted = self._extract_commitments_llm(
            encoded_conversation, conversation_id, chat_id
        )
        if not extracted:
            logger.info("[COMMIT] No commitments extracted – skipping reconciliation")
            return {"status": "no_commitments", "count": 0}

        # ---------- Stage 2: Reconciliation ----------
        existing = self.repository.get_active_commitments(session, chat_id)
        inactive = self._get_recent_inactive_commitments(session, chat_id, days=7)

        actions = self._reconcile_commitments_llm(
            extracted, existing, inactive, conversation_id, chat_id
        )

        # Drop any no-op LEAVE actions – we no longer model them
        actions = [a for a in actions if a.get("action") != "LEAVE"]

        # ---------- Apply actions (normalised data) ----------
        results = self._apply_actions(session, actions, conversation_id, chat_id)

        # ---------- Live stream ----------
        if is_realtime:
            from backend.services.conversations.analysis import ConversationAnalysisService
            scope = STREAM_SCOPE_TEMPLATE.format(chat_id=chat_id)
            ConversationAnalysisService.publish_analysis_event(
                scope,
                "updated",
                {
                    "chat_id": chat_id,
                    "conversation_id": conversation_id,
                    "changes": results,
                },
            )

        return {
            "status": "success",
            "extracted": len(extracted),
            "actions": len(actions),
            "results": results,
        }

    # Alias for updated API naming
    process_conversation_commitments = analyze_conversation_commitments

    # ------------------------------------------------------------------
    # Stage 1 – Pure extraction
    # ------------------------------------------------------------------

    @timed("extract_commitments_llm")
    def _extract_commitments_llm(
        self, encoded_text: str, conversation_id: int, chat_id: int
    ) -> List[Dict[str, Any]]:
        """LLM prompt for commitment extraction (CommitmentExtractionLive/v2) via Eve."""
        try:
            from backend.services.eve.client import get_eve_client
            
            logger.debug(f"[COMMIT] Extracting commitments via Eve for convo={conversation_id}, chat={chat_id}")
            
            eve = get_eve_client()
            eve_result = eve.execute_prompt(
                prompt_id="commitment-extraction-live-v2",
                source_chat=chat_id,
                vars={
                    "conversation_id": conversation_id,
                    "chat_id": chat_id,
                    "conversation_text": encoded_text,
                },
                budget_tokens=100000
            )
            
            final_prompt = eve_result["visiblePrompt"]
            context_ledger = eve_result.get("ledger", {})
            response_schema = None  # TODO: Add to Eve response
            
            logger.info(f"[COMMIT] Eve compiled extraction prompt: {len(context_ledger.get('items', []))} slices")

            llm_resp = LLMService.call_llm(
                prompt_str=final_prompt,
                llm_config_dict=DefaultLLMConfigs.EXTRACTION,
                response_schema_dict=response_schema,
            )
            
            # Extract content from LLM response
            content = llm_resp.get("content", {})
            # Content can be dict (structured), str (needs parsing), or list (direct commitments)
            if isinstance(content, str):
                import json
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse LLM response as JSON: {content[:200]}")
                    return []
            
            # Handle different response formats
            if isinstance(content, list):
                # LLM returned list directly
                return content
            elif isinstance(content, dict):
                # LLM returned object with extracted_commitments key
                return content.get("extracted_commitments", [])
            else:
                logger.error(f"Unexpected content type from LLM: {type(content)}")
                return []
        except Exception as exc:
            self._log_error("extract_commitments_llm", exc, conversation_id=conversation_id)
            return []

    # ------------------------------------------------------------------
    # Stage 2 – Reconciliation
    # ------------------------------------------------------------------

    @timed("reconcile_commitments_llm")
    def _reconcile_commitments_llm(
        self,
        extracted: List[Dict[str, Any]],
        existing: List[Dict[str, Any]],
        inactive: List[Dict[str, Any]],
        conversation_id: int,
        chat_id: int,
    ) -> List[Dict[str, Any]]:
        """Ask LLM to map extracted commitments onto existing DB rows via Eve."""
        try:
            from backend.services.eve.client import get_eve_client
            
            logger.debug(f"[COMMIT] Reconciling commitments via Eve for convo={conversation_id}, chat={chat_id}")
            
            eve = get_eve_client()
            eve_result = eve.execute_prompt(
                prompt_id="commitment-reconciliation-v1",
                source_chat=chat_id,
                vars={
                    "chat_id": chat_id,
                    "active_commitments": json.dumps(existing, default=str),
                    "inactive_commitments": json.dumps(inactive, default=str),
                    "extracted_commitments": json.dumps(extracted, default=str),
                },
                budget_tokens=100000
            )
            
            final_prompt = eve_result["visiblePrompt"]
            context_ledger = eve_result.get("ledger", {})
            response_schema = None  # TODO: Add to Eve response
            
            logger.info(f"[COMMIT] Eve compiled reconciliation prompt: {len(context_ledger.get('items', []))} slices")

            llm_resp = LLMService.call_llm(
                prompt_str=final_prompt,
                llm_config_dict=DefaultLLMConfigs.RECONCILIATION,
                response_schema_dict=response_schema,
            )
            return LLMService.parse_json_content(llm_resp).get("actions", [])
        except Exception as exc:
            self._log_error("reconcile_commitments_llm", exc, conversation_id=conversation_id)
            return []

    # ------------------------------------------------------------------
    # DB Apply helpers
    # ------------------------------------------------------------------

    def _apply_actions(
        self,
        session: Session,
        actions: List[Dict[str, Any]],
        conversation_id: int,
        chat_id: int,
    ) -> List[Dict[str, Any]]:
        """Apply CREATE/UPDATE/DELETE directives to the database."""
        results: List[Dict[str, Any]] = []
        for idx, action_item in enumerate(actions):
            action = action_item.get("action")
            try:
                if action == "CREATE":
                    commit_dict = self._build_commitment_dict(
                        session,
                        action_item["extracted_commitment"],
                        conversation_id,
                        chat_id,
                    )
                    commit_id = self.repository.add_commitment(session, commit_dict).commitment_id
                    results.append({"action": "CREATE", "id": commit_id, "success": True})

                elif action == "UPDATE":
                    cid = action_item.get("matched_commitment_id")
                    success = self.repository.update_commitment(
                        session, cid, action_item.get("updates", {})
                    )
                    results.append({"action": "UPDATE", "id": cid, "success": success})

                elif action == "DELETE":
                    cid = action_item.get("matched_commitment_id")
                    status_flag = (
                        "completed"
                        if action_item.get("extracted_commitment", {}).get("commitment_type")
                        == "completion"
                        else "cancelled"
                    )
                    removed = self.repository.remove_commitment(
                        session, cid, status=status_flag, resolution_method="detected"
                    )
                    results.append(
                        {
                            "action": "DELETE",
                            "id": cid,
                            "success": bool(removed),
                            "status": status_flag,
                        }
                    )

                else:
                    logger.debug(f"[COMMIT] Ignoring noop action: {action}")
            except Exception as exc:
                logger.error(f"[COMMIT] Failed to apply action {action}: {exc}")
                results.append({"action": action, "success": False, "error": str(exc)})

        return results 

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    def _build_commitment_dict(
        self,
        session: Session,
        extracted: Dict[str, Any],
        conversation_id: int,
        chat_id: int,
    ) -> Dict[str, Any]:
        """Normalise LLM extracted blob into DB-ready dict."""
        try:
            # Generate unique stable ID
            now = datetime.now(timezone.utc)
            commitment_id = (
                f"commit_{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond:06d}_{conversation_id}"
            )

            # Parse timing → due_date / specificity
            timing_text = extracted.get("timing", "")
            timing_type = extracted.get("timing_type", "none")
            due_date, due_specificity = self._parse_timing(timing_text, timing_type)

            # Resolve to_person_id – basic implementation (fallback to 1)
            to_person_str = extracted.get("to_person", "person_1")
            to_person_id = 1
            if to_person_str.startswith("person_"):
                try:
                    to_person_id = int(to_person_str.split("_", 1)[1])
                except (ValueError, IndexError):
                    pass

            # Get "me" contact id (or default 1)
            me_contact_id = 1
            try:
                me = ContactRepository.get_user_contact(session)
                if me:
                    me_contact_id = me["id"]
            except Exception:
                pass

            return {
                "id": commitment_id,
                "commitment": extracted.get("commitment_text", ""),
                "to_person": f"person_{to_person_id}",
                "to_person_id": to_person_id,
                "conversation_id": conversation_id,
                "chat_id": chat_id,
                "contact_id": me_contact_id,
                "created_date": now.isoformat() + "Z",
                "due_date": due_date.isoformat() if due_date else None,
                "due_specificity": due_specificity,
                "context": extracted.get("context", ""),
                "status": "pending",
                "priority": self._determine_priority_from_extracted(extracted),
                "condition": extracted.get("condition")
                if extracted.get("condition") and extracted.get("condition").strip()
                else None,
                "reminders": None,
                "modifications": [],
            }
        except Exception as exc:
            logger.error(f"[COMMIT] _build_commitment_dict error: {exc}")
            raise

    def _parse_timing(
        self, timing_text: Optional[str], timing_type: str
    ) -> (Optional[datetime.date], str):
        """Very minimal timing parser (keep behaviour parity)."""
        if not timing_text or timing_type == "none":
            return None, "none"
        try:
            from datetime import date, timedelta

            text_lower = timing_text.lower()
            if timing_type == "explicit":
                if "today" in text_lower:
                    return date.today(), "explicit"
                if "tomorrow" in text_lower:
                    return date.today() + timedelta(days=1), "explicit"
            # Relative simple heuristic
            if "week" in text_lower:
                return date.today() + timedelta(days=7), "inferred"
            if "month" in text_lower:
                return date.today() + timedelta(days=30), "inferred"
            return None, timing_type
        except Exception:
            return None, "vague"

    def _determine_priority_from_extracted(self, extracted: Dict[str, Any]) -> str:
        text = (extracted.get("context", "") + " " + extracted.get("commitment_text", "")).lower()
        if any(w in text for w in ["urgent", "asap", "important", "critical"]):
            return "high"
        if any(w in text for w in ["soon", "this week", "quickly"]):
            return "medium"
        return "low"

    # ------------------------------------------------------------------
    # Inactive commitments helper (7-day window)
    # ------------------------------------------------------------------

    def _get_recent_inactive_commitments(
        self, session: Session, chat_id: int, days: int = 7
    ) -> List[Dict[str, Any]]:
        """Fetch recently completed/cancelled commitments for better reconciliation."""
        try:
            return CommitmentRepository.get_recent_inactive_commitments(session, chat_id, days)
        except Exception as exc:
            logger.error(f"[COMMIT] Failed to fetch inactive commitments: {exc}")
            return [] 

    # ---------------------------------------------------------------------
    # Post-LLM hook (moved from ConversationAnalysisService)
    # ---------------------------------------------------------------------
    @staticmethod
    def process_conversation_analysis(
        session,
        *,
        conversation_id: int,
        chat_id: int,
        analysis_data: dict,
        prompt_template: dict,
    ) -> Dict[str, Any] | None:
        """Handle commitment reconciliation after a conversation analysis is saved.

        This centralises all commitment-domain orchestration inside
        CommitmentService for better cohesion.
        Returns whatever ``analyze_conversation_commitments`` yields, or ``None``
        if an unrecoverable error occurred.
        """
        from backend.etl.live_sync.sync_messages import get_last_message_timestamp
        # NOTE: Commitments disabled - CommitmentEncodingService deleted during Eve migration
        # from backend.services.encoding import CommitmentEncodingService
        from backend.services.conversations.analysis import ConversationAnalysisService

        _log = logging.getLogger(__name__)
        try:
            # Determine whether this is a live analysis based on the template name
            template_name = (prompt_template or {}).get("name", "").lower()
            is_live_analysis = "live" in template_name

            # Ensure we can calculate recency for live scopes
            latest_message = get_last_message_timestamp(chat_id)
            if not latest_message:
                _log.error("[COMMITMENT] No messages found for chat %s", chat_id)

            # NOTE: Commitments disabled - raise error if somehow triggered
            raise NotImplementedError(
                "Commitment analysis is currently disabled. "
                "CommitmentEncodingService was deleted during Eve migration. "
                "To re-enable, implement commitment encoding in Eve service."
            )
            
            # OLD CODE (for reference):
            # from backend.services.conversations.analysis import ConversationAnalysisService
            # convo_data = ConversationAnalysisService.load_conversation(conversation_id, chat_id)
            # encoded_conv = CommitmentEncodingService.encode_conversation_for_commitments(
            #     convo_data, chat_id, is_realtime=is_live_analysis
            # )
            # svc = CommitmentService()
            return svc.analyze_conversation_commitments(
                session,
                conversation_id,
                chat_id,
                encoded_conv,
                is_realtime=is_live_analysis,
            )
        except Exception as exc:  # noqa: BLE001  (broad for safety)
            _log.error(
                "[COMMITMENT] Failed to process commitment analysis: %s", exc,
                exc_info=True,
            )
            return None 