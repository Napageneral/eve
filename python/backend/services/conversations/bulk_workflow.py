"""
Bulk analysis workflow - chat/global batching operations
"""
from backend.db.session_manager import new_session
from backend.repositories.conversation_analysis import ConversationAnalysisRepository
from backend.services.core.workflow_base import WorkflowBase
import logging

logger = logging.getLogger(__name__)


class BulkAnalysisWorkflowService(WorkflowBase):
    """Prepare conversation analysis tasks for chat-specific or global bulk workflows.

    This service is intentionally *pure* – it does **not** enqueue Celery tasks.
    Callers (tasks/bulk_analyze.py) are responsible for converting the returned
    dictionaries into Celery group signatures.
    """

    # Public helpers -------------------------------------------------------------------
    @staticmethod
    def prepare_chat_analysis(
        chat_id: int,
        pre_encode: bool = True,
        **kwargs,
    ) -> list[dict]:
        """Return a list of analysis task parameter dicts for *one* chat.

        Each entry has:
            {
                "conv_id": int,
                "chat_id": int,
                "ca_id": int,
                "encoded_text": str | None,
                "kwargs": dict,  # any extra kwargs to forward to task
            }
        """

        logger.info("[BULK-WORKFLOW] Preparing chat analysis for chat %s", chat_id)

        # ------------------------------------------------------------------
        # 1. Determine conversation IDs
        # ------------------------------------------------------------------
        from .analysis import ConversationAnalysisService
        conversation_ids = ConversationAnalysisService.list_conversation_ids(chat_id)
        if not conversation_ids:
            logger.info("No conversations found for chat %s", chat_id)
            return []

        # ------------------------------------------------------------------
        # 2. Optionally pre-encode text in one batch
        # ------------------------------------------------------------------
        encoded_texts: dict[int, str] = {}
        if pre_encode:
            try:
                batch_results = ConversationAnalysisService.fetch_and_encode_batch(
                    chat_id, conversation_ids
                )
                encoded_texts = {conv_id: text for conv_id, text in batch_results}
            except Exception as e:
                logger.warning("Pre-encoding failed for chat %s: %s", chat_id, e)

        # ------------------------------------------------------------------
        # 3. Prepare CA records and build task param dicts
        # ------------------------------------------------------------------
        tasks: list[dict] = []
        with new_session() as session:
            # All prompts now managed by Eve - use eve_prompt_id
            # Map legacy prompt names to Eve IDs
            prompt_name = kwargs.get("prompt_name", "ConvoAll")
            eve_prompt_id = "convo-all-v1" if prompt_name == "ConvoAll" else f"{prompt_name.lower()}-v1"

            for conv_id in conversation_ids:
                try:
                    ca_id = ConversationAnalysisRepository.prepare_for_analysis(
                        session,
                        conv_id,
                        prompt_template_id=None,  # Legacy DB prompts removed
                        eve_prompt_id=eve_prompt_id,  # Eve prompt ID
                    )

                    tasks.append(
                        {
                            "conv_id": conv_id,
                            "chat_id": chat_id,
                            "ca_id": ca_id,
                            "encoded_text": encoded_texts.get(conv_id),
                            "kwargs": kwargs,
                        }
                    )
                except ValueError as ve:
                    if "already" in str(ve):
                        logger.info("Skipping conversation %s: %s", conv_id, ve)
                        continue
                    raise

            session.commit()

        logger.info("Prepared %s tasks for chat %s", len(tasks), chat_id)
        return tasks

    # ------------------------------------------------------------------
    @staticmethod
    def prepare_global_analysis(pre_encode: bool = True, **kwargs) -> list[dict]:
        """Return task params for *all* unanalyzed conversations (global run)."""

        logger.info("[BULK-WORKFLOW] Preparing global analysis tasks")

        tasks: list[dict] = []

        if pre_encode:
            # Existing path (heavier): pre-encode everything before queuing
            from .analysis import ConversationAnalysisService
            conversations = ConversationAnalysisService.fetch_and_encode_all_conversations()
            if not conversations:
                logger.info("No conversations need analysis – global workflow empty")
                return []
            with new_session() as session:
                # All prompts now managed by Eve - use eve_prompt_id
                # Map legacy prompt names to Eve IDs
                prompt_name = kwargs.get("prompt_name", "ConvoAll")
                eve_prompt_id = "convo-all-v1" if prompt_name == "ConvoAll" else f"{prompt_name.lower()}-v1"

                for conv_id, conv_data in conversations.items():
                    try:
                        ca_id = ConversationAnalysisRepository.prepare_for_analysis(
                            session,
                            conv_id,
                            prompt_template_id=None,  # Legacy DB prompts removed
                            eve_prompt_id=eve_prompt_id,  # Eve prompt ID
                        )
                        tasks.append(
                            {
                                "conv_id": conv_id,
                                "chat_id": conv_data["chat_id"],
                                "ca_id": ca_id,
                                "encoded_text": conv_data["encoded_text"],
                                "kwargs": {**kwargs, "publish_global": True, "run_id": kwargs.get("run_id")},
                            }
                        )
                    except ValueError as ve:
                        if "already" in str(ve):
                            continue
                        logger.error("Error preparing conversation %s: %s", conv_id, ve)

                session.commit()

            logger.info("Prepared %s global analysis tasks", len(tasks))
            return tasks

        # Fast path (no pre-encode): queue immediately, each task encodes on worker
        with new_session() as session:
            # All prompts now managed by Eve - no need for legacy prompt_template_id lookup
            rows = ConversationAnalysisRepository.get_unanalyzed_conversations(session)
            if not rows:
                logger.info("No conversations need analysis – global workflow empty")
                return []
            
            # All prompts now managed by Eve - use eve_prompt_id
            # Map legacy prompt names to Eve IDs
            prompt_name = kwargs.get("prompt_name", "ConvoAll")
            eve_prompt_id = "convo-all-v1" if prompt_name == "ConvoAll" else f"{prompt_name.lower()}-v1"

            for row in rows:
                try:
                    ca_id = ConversationAnalysisRepository.prepare_for_analysis(
                        session,
                        row["conv_id"],
                        prompt_template_id=None,  # Legacy DB prompts removed
                        eve_prompt_id=eve_prompt_id,  # Eve prompt ID
                    )
                    tasks.append(
                        {
                            "conv_id": row["conv_id"],
                            "chat_id": row["chat_id"],
                            "ca_id": ca_id,
                            "encoded_text": None,  # encode inside each task
                            "kwargs": {**kwargs, "publish_global": True, "run_id": kwargs.get("run_id")},
                        }
                    )
                except ValueError as ve:
                    if "already" in str(ve):
                        continue
                    logger.error("Error preparing conversation %s: %s", row["conv_id"], ve)
            session.commit()

        logger.info("Prepared %s global analysis tasks (no pre-encode)", len(tasks))
        return tasks

    # ------------------------------------------------------------------
    # Internal helpers ---------------------------------------------------
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_prompt_template(session, **kwargs) -> int:
        """Determine which prompt_template_id to use for analysis."""

        if kwargs.get("prompt_template_id"):
            return kwargs["prompt_template_id"]

        # All prompts now managed by Eve - legacy DB lookup no longer used
        # Modern path uses eve_prompt_id from ANALYSIS_PASSES config
        logger.warning(
            "Legacy prompt_template_id lookup called. "
            "Use trigger_analysis_pass() which handles eve_prompt_id."
        )
        
        # Return None - modern code path uses eve_prompt_id instead
        return None 

    # ------------------------------------------------------------------
    # Celery assembly helpers – keep Celery-specific logic out of tasks
    # ------------------------------------------------------------------

    @staticmethod
    def build_celery_group(task_params: list[dict]):
        """Convert the list produced by `prepare_*_analysis` helpers into a Celery `group`.

        This consolidates the Celery-specific wiring (signature creation + `group(...)`) so
        that tasks remain very thin wrappers.  We purposely import Celery and the task
        lazily inside the method to avoid any potential circular import woes during module
        initialization.
        """

        # Guard – nothing to do
        if not task_params:
            return None

        # Local imports to sidestep import cycles (tasks already depend on this module)
        from celery import group  # type: ignore – imported lazily
        from backend.celery_service.tasks.analyze_conversation import (
            call_llm_task,  # type: ignore – runtime import
            persist_result_task,  # type: ignore – runtime import
        )
        from backend.celery_service.tasks.embeddings import (
            embed_conversation_task,  # type: ignore – runtime import
            embed_analyses_for_conversation_task,  # type: ignore – runtime import
        )

        analysis_tasks = []
        from time import time as _now
        for p in task_params:
            sig_a = call_llm_task.s(
                p["conv_id"],
                p["chat_id"],
                p["ca_id"],
                encoded_text=p.get("encoded_text"),
                queued_at_ts=_now(),
                **p.get("kwargs", {}),
            ).set(queue="chatstats-analysis")
            sig_b = persist_result_task.s().set(queue="chatstats-db")
            # Chain per-conversation analysis-derived embeddings immediately after persist
            sig_c = embed_analyses_for_conversation_task.si(
                int(p["conv_id"]), int(p["chat_id"]), (p.get("kwargs", {}) or {}).get("run_id")
            ).set(queue="chatstats-embeddings")

            # Build per-conversation task collection (chain + optional convo embedding)
            per_convo_tasks = [ (sig_a | sig_b) | sig_c ]

            # If we have pre-encoded text, also embed the raw conversation text
            try:
                enc = p.get("encoded_text")
                if isinstance(enc, str) and enc.strip():
                    emb_sig = embed_conversation_task.s(
                        int(p["conv_id"]),
                        int(p["chat_id"]),
                        enc,
                    ).set(queue="chatstats-embeddings")
                    # Run independently alongside the chain for this conversation
                    per_convo_tasks.append(emb_sig)
            except Exception:
                pass

            # Group the per-conversation tasks so both are enqueued
            analysis_tasks.append(group(per_convo_tasks))

            # In parallel: queue conversation embedding when encoded text is available
            try:
                enc = p.get("encoded_text")
                if isinstance(enc, str) and enc.strip():
                    try:
                        logger.info(
                            "[BULK-WORKFLOW] Queueing conversation embedding conv_id=%s chat_id=%s len=%s",
                            p.get("conv_id"), p.get("chat_id"), len(enc)
                        )
                    except Exception:
                        pass
                    emb_sig = embed_conversation_task.s(
                        int(p["conv_id"]),
                        int(p["chat_id"]),
                        enc,
                    ).set(queue="chatstats-analysis")
                    embedding_tasks.append(emb_sig)
            except Exception:
                pass

        group_sig = group(analysis_tasks)
        # Attach the exact set of CA IDs for authoritative seeding of Redis counters.
        try:
            group_sig.ca_ids = [p["ca_id"] for p in task_params]  # type: ignore[attr-defined]
        except Exception:
            pass
        # No embedding_sigs/task_params attachments; embeddings are chained per conversation
        try:
            pre_count = sum(1 for _p in task_params if isinstance(_p.get("encoded_text"), str) and _p.get("encoded_text").strip())
            logger.info(
                "[BULK-WORKFLOW] Built Celery group: convo_groups=%d, pre_encoded=%d",
                len(analysis_tasks), pre_count
            )
        except Exception:
            pass
        return group_sig 

    # ------------------------------------------------------------------
    # High-level workflow factory helpers (formerly in tasks/bulk_analyze)
    # ------------------------------------------------------------------

    @staticmethod
    def create_global_analysis_workflow(**kwargs):
        """Return a Celery `group` that analyzes *all* unanalyzed conversations.

        Publishes the same UI events that the old `tasks.bulk_analyze.create_global_analysis_workflow`
        helper emitted so that front-end behaviour is preserved.
        """

        import logging as _logging

        _logger = _logging.getLogger(__name__)
        _logger.info("[BULK-WORKFLOW] Creating global analysis workflow")

        from .analysis import ConversationAnalysisService
        from backend.services.core.event_bus import EventBus

        # Publish a fast planned total before heavy work begins
        try:
            with new_session() as session:
                planned_rows = ConversationAnalysisRepository.get_unanalyzed_conversations(session)
                planned_total = len(planned_rows)
            EventBus.publish(
                "global",
                "analysis_planned",
                {
                    "run_id": kwargs.get("run_id"),
                    "total_convos": planned_total,
                    "processed_convos": 0,
                    "percentage": 0,
                    "status": "initializing",
                    "message": f"Preparing {planned_total} conversations…",
                },
            )
        except Exception as e:
            _logger.debug(f"Failed to publish planned count: {e}")

        # Early spinner event
        EventBus.publish(
            "global",
            "analysis_init",
            {"run_id": kwargs.get("run_id"), "message": "Initializing global analysis...", "status": "initializing"}
        )

        # Decide whether to pre-encode based on planned size (large runs benefit from fewer DB reads)
        try:
            _preencode_threshold = int(__import__("os").getenv("CHATSTATS_PREENCODE_MIN", "10000"))
        except Exception:
            _preencode_threshold = 10000
        # Build task params. By default we DO NOT auto pre-encode to keep queueing instant.
        # If you want auto pre-encode for large runs, set CHATSTATS_ENABLE_AUTO_PREENCODE=1.
        _auto_pre = False
        try:
            _auto_pre = __import__("os").getenv("CHATSTATS_ENABLE_AUTO_PREENCODE", "0").lower() in ("1", "true", "yes")
        except Exception:
            _auto_pre = False
        if _auto_pre and "pre_encode" not in kwargs:
            kwargs["pre_encode"] = planned_total >= _preencode_threshold

        task_params = BulkAnalysisWorkflowService.prepare_global_analysis(**kwargs)

        if not task_params:
            EventBus.publish(
                "global",
                "analysis_complete",
                {"message": "No conversations needed analysis", "is_complete": True}
            )
            return None

        # Send detailed start event with counts
        EventBus.publish(
            "global",
            "analysis_started",
            {
                "run_id": kwargs.get("run_id"),
                "total_convos": len(task_params),
                "task_count": len(task_params),
                "processed_convos": 0,
                "percentage": 0,
                "status": "processing",
                "message": f"Starting analysis of {len(task_params)} conversations"
            }
        )
        group_sig = BulkAnalysisWorkflowService.build_celery_group(task_params)
        # Also attach ca_ids at the workflow level for upstream seeding
        try:
            group_sig.ca_ids = [p["ca_id"] for p in task_params]  # type: ignore[attr-defined]
        except Exception:
            pass
        return group_sig

    @staticmethod
    def create_bulk_analysis_workflow(chat_id: int, user_id: int, **kwargs):
        """Return a Celery `group` for bulk-analyzing all conversations in *one* chat."""

        import logging as _logging

        _logger = _logging.getLogger(__name__)
        _logger.info("[BULK-WORKFLOW] Creating chat workflow chat_id=%s user_id=%s", chat_id, user_id)

        task_params = BulkAnalysisWorkflowService.prepare_chat_analysis(chat_id, **kwargs)

        if not task_params:
            _logger.warning("No conversations found for chat %s", chat_id)
            return None

        from .analysis import ConversationAnalysisService
        ConversationAnalysisService.publish_analysis_event(
            str(chat_id),
            "analysis_started",
            {
                "total_convos": len(task_params),
                "chat_id": chat_id,
                "task_count": len(task_params),
            },
        )

        return BulkAnalysisWorkflowService.build_celery_group(task_params)

    # NEW: Ranked (Top N) analysis workflow spanning multiple chats under one run_id
    @staticmethod
    def create_ranked_analysis_workflow(chat_ids: list[int], **kwargs):
        """Return a Celery group that analyzes all conversations for the given chat_ids.

        Mirrors the global workflow shape so the UI can reuse the same SSE stream
        (scope='global') keyed by a single run_id.
        """

        import logging as _logging
        _logger = _logging.getLogger(__name__)
        _logger.info("[BULK-WORKFLOW] Creating ranked analysis workflow for %s chats", len(chat_ids) if chat_ids else 0)

        if not chat_ids:
            return None

        # Build task params by concatenating each chat's task list
        task_params: list[dict] = []
        for cid in chat_ids:
            try:
                per_chat = BulkAnalysisWorkflowService.prepare_chat_analysis(cid, **kwargs)
                if per_chat:
                    task_params.extend(per_chat)
            except Exception as e:
                _logger.warning("prepare_chat_analysis failed for chat %s: %s", cid, e)

        if not task_params:
            from backend.services.core.event_bus import EventBus
            EventBus.publish(
                "global",
                "analysis_complete",
                {"message": "No conversations needed analysis", "is_complete": True},
            )
            return None

        # Publish a start event with counts so UI can flip spinners instantly
        try:
            from backend.services.core.event_bus import EventBus
            EventBus.publish(
                "global",
                "analysis_started",
                {
                    "run_id": kwargs.get("run_id"),
                    "total_convos": len(task_params),
                    "task_count": len(task_params),
                    "processed_convos": 0,
                    "percentage": 0,
                    "status": "processing",
                    "message": f"Starting ranked analysis of {len(task_params)} conversations",
                },
            )
        except Exception as e:
            _logger.debug("Failed to publish ranked analysis_started: %s", e)

        group_sig = BulkAnalysisWorkflowService.build_celery_group(task_params)
        try:
            group_sig.ca_ids = [p["ca_id"] for p in task_params]  # type: ignore[attr-defined]
        except Exception:
            pass
        return group_sig


__all__ = ["BulkAnalysisWorkflowService"] 