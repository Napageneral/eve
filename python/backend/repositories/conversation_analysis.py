"""
Repository for conversation analysis operations.
Centralizes all database operations related to conversation analysis.
"""
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.celery_service.constants import (
    CA_STATUS_PENDING,
    CA_STATUS_SUCCESS,
    CA_STATUS_FAILED,
    CA_STATUS_PROCESSING,
    CA_STATUS_FAILED_TO_QUEUE,
    CA_STATUS_SKIPPED,
    CA_RETRIABLE_STATUSES
)
from .core.generic import GenericRepository
from .core.status_mixin import StatusMixin

class ConversationAnalysisRepository(GenericRepository):
    """Repository for all conversation analysis database operations."""
    
    TABLE = "conversation_analyses"
    
    @classmethod
    def _update_ca_status(cls, session: Session, ca_id: int, status: str, 
                         error_message: str = None, temporal_workflow_id: str = None,
                         increment_retry: bool = False) -> None:
        """
        Consolidated helper to update conversation analysis status.
        
        Args:
            session: Database session
            ca_id: Conversation analysis ID
            status: New status to set
            error_message: Optional error message
            temporal_workflow_id: Optional workflow ID
            increment_retry: Whether to increment retry count
        """
        extra: Dict[str, Any] = {}
        if error_message is not None:
            extra["error_message"] = error_message
        if temporal_workflow_id is not None:
            extra["temporal_workflow_id"] = temporal_workflow_id

        StatusMixin.set_status(
            session,
            "conversation_analyses",
            "id",
            ca_id,
            status,
            extra=extra or None,
            bump_retry=increment_retry,
        )

    @classmethod
    def get_conversation_chat_id(cls, session: Session, conversation_id: int) -> Optional[int]:
        """Get chat_id for a conversation."""
        result = cls.fetch_one(session, 
            "SELECT chat_id FROM conversations WHERE id = :conv_id",
            {"conv_id": conversation_id})
        return result["chat_id"] if result else None
    
    @classmethod
    def _create_ca_record(cls, session: Session, conversation_id: int, 
                         prompt_template_id: int = None, eve_prompt_id: str = None) -> int:
        """
        Consolidated helper to create a new conversation analysis record.
        
        Args:
            conversation_id: Conversation to analyze
            prompt_template_id: (Legacy) Database prompt template ID
            eve_prompt_id: (New) Eve prompt ID
        
        Returns:
            ID of the created record
        """
        now = datetime.utcnow()
        
        if eve_prompt_id:
            # New Eve-based record
            result = session.execute(
                text("""
                    INSERT INTO conversation_analyses
                    (conversation_id, prompt_template_id, eve_prompt_id, status, retry_count, created_at, updated_at)
                    VALUES (:cid, NULL, :eid, :status, 0, :now, :now)
                    RETURNING id
                """),
                {
                    "cid": conversation_id,
                    "eid": eve_prompt_id,
                    "status": CA_STATUS_PENDING,
                    "now": now
                }
            )
        else:
            # Legacy template-based record
            result = session.execute(
                text("""
                    INSERT INTO conversation_analyses
                    (conversation_id, prompt_template_id, status, retry_count, created_at, updated_at)
                    VALUES (:cid, :pid, :status, 0, :now, :now)
                    RETURNING id
                """),
                {
                    "cid": conversation_id, 
                    "pid": prompt_template_id,
                    "status": CA_STATUS_PENDING, 
                    "now": now
                }
            )
        return result.scalar_one()
    
    @classmethod
    def _get_existing_ca(cls, session: Session, conversation_id: int, 
                        prompt_template_id: int = None, eve_prompt_id: str = None) -> Optional[Dict[str, Any]]:
        """
        Consolidated helper to get existing conversation analysis record.
        
        Args:
            conversation_id: Conversation to look up
            prompt_template_id: (Legacy) Database prompt template ID
            eve_prompt_id: (New) Eve prompt ID
        
        Returns:
            Dictionary with CA record data, or None if not found
        """
        if eve_prompt_id:
            # New Eve-based lookup
            sql = """
            SELECT id, status, temporal_workflow_id
            FROM conversation_analyses
            WHERE conversation_id = :cid AND eve_prompt_id = :eid
            LIMIT 1
            """
            row = session.execute(
                text(sql), 
                {"cid": conversation_id, "eid": eve_prompt_id}
            ).mappings().first()
        else:
            # Legacy template-based lookup
            sql = """
            SELECT id, status, temporal_workflow_id
            FROM conversation_analyses
            WHERE conversation_id = :cid AND prompt_template_id = :pid
            LIMIT 1
            """
            row = session.execute(
                text(sql), 
                {"cid": conversation_id, "pid": prompt_template_id}
            ).mappings().first()
        
        return dict(row) if row else None

    @classmethod
    def get_prompt_template_id(
        cls,
        session: Session, 
        name: str, 
        version: int, 
        category: str
    ) -> Optional[int]:
        """
        Thin wrapper around prompt_repo.get_id so call-sites in activities don't break.
        
        Args:
            session: SQLAlchemy session
            name: Prompt template name
            version: Prompt template version
            category: Prompt template category
            
        Returns:
            Prompt template ID if found, None otherwise
        """
        return prompt_repo.PromptRepository.get_template_id(session, name, version, category)

    @classmethod
    def prepare_for_analysis(
        cls,
        session: Session, 
        conversation_id: int, 
        prompt_template_id: int = None,
        eve_prompt_id: str = None
    ) -> int:
        """
        Ensure there is a CA row in PENDING state and return its id.
        Handles: create, retry if FAILED/FAILED_TO_QUEUE/SKIPPED,
        ignore if already SUCCESS.
        
        Args:
            session: SQLAlchemy session
            conversation_id: ID of the conversation to analyze
            prompt_template_id: (Legacy) ID of the prompt template to use
            eve_prompt_id: (New) Eve prompt ID (e.g., "convo-all-v1")
            
        At least one of prompt_template_id or eve_prompt_id must be provided.
            
        Returns:
            The ID of the CA record
            
        Raises:
            ValueError: If analysis already exists and is in a non-retriable state
        """
        if not prompt_template_id and not eve_prompt_id:
            raise ValueError("Either prompt_template_id or eve_prompt_id must be provided")
        
        existing_record = cls._get_existing_ca(session, conversation_id, prompt_template_id, eve_prompt_id)

        if existing_record is None:
            # No existing record, create a new one
            return cls._create_ca_record(session, conversation_id, prompt_template_id, eve_prompt_id)

        # Row exists - check status
        if existing_record["status"] == CA_STATUS_SUCCESS:
            raise ValueError("Analysis already completed successfully")

        if existing_record["status"] in CA_RETRIABLE_STATUSES:
            # Reset status for retry
            cls._update_ca_status(session, existing_record["id"], CA_STATUS_PENDING)
            return existing_record["id"]
        
        # Handle orphaned pending rows - treat them as stale if they have no temporal_workflow_id
        if existing_record["status"] == CA_STATUS_PENDING and (existing_record["temporal_workflow_id"] is None or existing_record["temporal_workflow_id"] == ""):
            # Never actually queued, safe to retry
            cls._update_ca_status(session, existing_record["id"], CA_STATUS_PENDING, error_message="Previous pending state had no workflow ID, reset for retry")
            return existing_record["id"]

        # PENDING or PROCESSING → let it finish
        raise ValueError(f"Analysis already in progress with status: {existing_record['status']}")

    @classmethod
    def prepare_batch_for_analysis(
        cls,
        session: Session,
        conversation_ids: List[int],
        prompt_template_id: int
    ) -> Dict[int, Dict[str, Any]]:
        """
        Batch version of prepare_for_analysis.
        Ensures there are CA rows in PENDING state for multiple conversations.
        
        Args:
            session: SQLAlchemy session
            conversation_ids: List of conversation IDs to analyze
            prompt_template_id: ID of the prompt template to use
            
        Returns:
            Dictionary mapping conversation_id to details about its CA record
        """
        existing_map = cls._get_existing_analyses(session, conversation_ids, prompt_template_id)
        
        results = {}
        for conversation_id in conversation_ids:
            results[conversation_id] = cls._prepare_single_analysis(
                session, conversation_id, prompt_template_id, existing_map.get(conversation_id)
            )
        
        return results

    @classmethod
    def _get_existing_analyses(
        cls,
        session: Session, 
        conversation_ids: List[int], 
        prompt_template_id: int
    ) -> Dict[int, Dict[str, Any]]:
        """Get existing analysis records for the given conversation IDs."""
        placeholders = ', '.join([f':id{i}' for i in range(len(conversation_ids))])
        params = {f'id{i}': cid for i, cid in enumerate(conversation_ids)}
        params['pid'] = prompt_template_id
        
        query = f"""
        SELECT id, conversation_id, status
        FROM conversation_analyses
        WHERE conversation_id IN ({placeholders})
        AND prompt_template_id = :pid
        """
        
        existing_records = session.execute(text(query), params).mappings().all()
        return {rec["conversation_id"]: rec for rec in existing_records}

    @classmethod
    def _prepare_single_analysis(
        cls,
        session: Session,
        conversation_id: int,
        prompt_template_id: int,
        existing_record: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Prepare analysis for a single conversation."""
        now = datetime.utcnow()
        
        try:
            if existing_record is None:
                # No existing record, create new one
                result = session.execute(
                    text("""
                        INSERT INTO conversation_analyses
                        (conversation_id, prompt_template_id, status, retry_count, created_at, updated_at)
                        VALUES (:cid, :pid, :status, 0, :now, :now)
                        RETURNING id
                    """),
                    {
                        "cid": conversation_id, 
                        "pid": prompt_template_id,
                        "status": CA_STATUS_PENDING, 
                        "now": now
                    }
                )
                ca_id = result.scalar_one()
                return {
                    "ca_row_id": ca_id,
                    "status": "created",
                    "message": "New analysis record created",
                    "prompt_template_id_used": prompt_template_id
                }
            else:
                return cls._handle_existing_analysis(session, existing_record, prompt_template_id, now)
                
        except Exception as e:
            return {
                "ca_row_id": None,
                "status": "error",
                "message": f"Error preparing analysis: {str(e)}",
                "prompt_template_id_used": prompt_template_id
            }

    @classmethod
    def _handle_existing_analysis(
        cls,
        session: Session,
        record: Dict[str, Any],
        prompt_template_id: int,
        now: datetime
    ) -> Dict[str, Any]:
        """Handle existing analysis records based on their status."""
        if record["status"] == CA_STATUS_SUCCESS:
            return {
                "ca_row_id": record["id"],
                "status": "skipped",
                "message": "Analysis already completed successfully",
                "prompt_template_id_used": prompt_template_id
            }
        elif record["status"] in CA_RETRIABLE_STATUSES:
            # Reset for retry
            cls._update_ca_status(session, record["id"], CA_STATUS_PENDING)
            return {
                "ca_row_id": record["id"],
                "status": "retriggered",
                "message": f"Analysis retriggered from previous status: {record['status']}",
                "prompt_template_id_used": prompt_template_id
            }
        else:
            return {
                "ca_row_id": record["id"],
                "status": "skipped",
                "message": f"Analysis already in progress with status: {record['status']}",
                "prompt_template_id_used": prompt_template_id
            }

    @classmethod
    def update_status(
        cls,
        session: Session, 
        ca_id: int, 
        status: str, 
        error_message: Optional[str] = None
    ) -> None:
        """
        Update the status of a conversation analysis record.
        
        Args:
            session: SQLAlchemy session
            ca_id: ID of the conversation analysis record
            status: New status
            error_message: Optional error message
        """
        cls._update_ca_status(session, ca_id, status, error_message=error_message)

    @classmethod
    def update_temporal_workflow_id(
        cls,
        session: Session, 
        ca_id: int, 
        workflow_id: str
    ) -> None:
        """
        Update the temporal workflow ID for a conversation analysis record.
        
        Args:
            session: SQLAlchemy session
            ca_id: ID of the conversation analysis record
            workflow_id: Temporal workflow ID
        """
        cls._update_ca_status(session, ca_id, CA_STATUS_PROCESSING, temporal_workflow_id=workflow_id)

    @classmethod
    def get_by_conversation_and_prompt(
        cls,
        session: Session,
        conversation_id: int,
        prompt_template_id: int = None,
        eve_prompt_id: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get a conversation analysis record by conversation ID and prompt identifier.
        
        Args:
            session: SQLAlchemy session
            conversation_id: ID of the conversation
            prompt_template_id: (Legacy) Database prompt template ID
            eve_prompt_id: (New) Eve prompt ID
            
        Returns:
            Dictionary with record data or None if not found
        """
        if eve_prompt_id:
            # New Eve-based lookup
            sql = """
            SELECT *
            FROM conversation_analyses
            WHERE conversation_id = :cid
              AND eve_prompt_id = :eid
            LIMIT 1
            """
            
            row = session.execute(
                text(sql), 
                {"cid": conversation_id, "eid": eve_prompt_id}
            ).mappings().first()
        else:
            # Legacy template-based lookup
            sql = """
            SELECT *
            FROM conversation_analyses
            WHERE conversation_id = :cid
              AND prompt_template_id = :pid
            LIMIT 1
            """
            
            row = session.execute(
                text(sql), 
                {"cid": conversation_id, "pid": prompt_template_id}
            ).mappings().first()
        
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Utility helpers for analysis_passes
    # ------------------------------------------------------------------

    @classmethod
    def list_completed_template_ids_for_conversation(
        cls,
        session: Session,
        conversation_id: int,
    ) -> List[int]:
        """Return prompt_template_ids of analyses that are success or processing for a conversation."""
        sql = (
            "SELECT prompt_template_id FROM conversation_analyses "
            "WHERE conversation_id = :cid AND status IN ('success','processing')"
        )
        rows = session.execute(text(sql), {"cid": conversation_id}).fetchall()
        return [row[0] for row in rows]

    @classmethod
    def select_pending(
        cls,
        session: Session,
        limit: int
    ) -> List[Dict[str, Any]]:
        """
        Select pending conversation analyses to dispatch.
        
        Args:
            session: SQLAlchemy session
            limit: Maximum number of records to return
            
        Returns:
            List of dictionaries with conversation analysis details
        """
        sql = """
        SELECT 
            ca.id as ca_id,
            ca.conversation_id as convo_id,
            c.chat_id as chat_id,
            pt.name as prompt_name,
            pt.version as prompt_version,
            pt.category as prompt_category
        FROM 
            conversation_analyses ca
        JOIN
            conversations c ON ca.conversation_id = c.id
        JOIN
            prompt_templates pt ON ca.prompt_template_id = pt.id
        WHERE
            ca.status = 'pending'
        ORDER BY
            ca.created_at ASC
        LIMIT :limit
        """
        
        results = session.execute(
            text(sql),
            {"limit": limit}
        ).mappings().all()
        
        return [dict(r) for r in results]

    @classmethod
    def get_chat_analysis_summary(cls, session: Session, chat_id: int) -> Dict[str, int]:
        """
        Get analysis summary statistics for a chat.
        
        Returns counts of total conversations, analyzed, processing, queued, failed, and unqueued.
        """
        # Total conversations
        total_result = session.execute(
            text("SELECT COUNT(*) FROM conversations WHERE chat_id = :chat_id"),
            {"chat_id": chat_id}
        ).scalar() or 0
        
        # Get counts by status from conversation_analyses
        status_counts_sql = """
        SELECT ca.status, COUNT(DISTINCT ca.conversation_id) as count
        FROM conversation_analyses ca
        JOIN conversations c ON ca.conversation_id = c.id
        WHERE c.chat_id = :chat_id
        GROUP BY ca.status
        """
        
        status_results = session.execute(
            text(status_counts_sql),
            {"chat_id": chat_id}
        ).fetchall()
        
        # Initialize counts
        analyzed = 0
        processing = 0
        queued = 0
        failed = 0
        
        # Process status counts
        for row in status_results:
            status, count = row
            if status == 'success':
                analyzed = count
            elif status == 'processing':
                processing = count
            elif status == 'pending':
                queued = count
            elif status == 'failed':
                failed = count
        
        # Calculate unqueued (conversations without any analysis record)
        accounted_for = analyzed + processing + queued + failed
        unqueued = max(0, total_result - accounted_for)
        
        return {
            "total": total_result,
            "analyzed": analyzed,
            "processing": processing,
            "queued": queued,
            "failed": failed,
            "unqueued": unqueued
        }

    @classmethod
    def get_unanalyzed_conversations(cls, session: Session) -> List[Dict[str, Any]]:
        """Get all conversations that need analysis."""
        sql = """
            SELECT 
                c.id as conv_id,
                c.chat_id,
                ch.chat_name,
                -- Get participant names for this chat
                (
                    SELECT GROUP_CONCAT(co.name || ':' || co.id)
                    FROM chat_participants cp
                    JOIN contacts co ON cp.contact_id = co.id
                    WHERE cp.chat_id = c.chat_id
                ) as participant_mapping
            FROM conversations c
            JOIN chats ch ON c.chat_id = ch.id
            LEFT JOIN conversation_analyses ca ON 
                ca.conversation_id = c.id 
                AND ca.prompt_template_id = (
                    SELECT id FROM prompt_templates 
                    WHERE name = 'ConvoAll' AND version = 1
                )
            WHERE ca.id IS NULL OR ca.status NOT IN ('success', 'processing')
            ORDER BY c.chat_id, c.id
        """
        return cls.fetch_all(session, sql)

    @classmethod
    def get_messages_for_conversations(cls, session: Session, conv_ids: List[int]) -> Dict[str, Dict]:
        """Get all messages, attachments, and reactions for multiple conversations."""
        if not conv_ids:
            return {"messages": {}, "attachments": {}, "reactions": {}}
        
        # Use parameterized query with placeholders to prevent SQL injection
        placeholders = ', '.join([f':id{i}' for i in range(len(conv_ids))])
        params = {f'id{i}': cid for i, cid in enumerate(conv_ids)}
        
        # Load all messages in one query
        messages_sql = f"""
            SELECT 
                m.*,
                c.name as sender_name
            FROM messages m
            LEFT JOIN contacts c ON m.sender_id = c.id
            WHERE m.conversation_id IN ({placeholders})
            ORDER BY m.conversation_id, m.timestamp
        """
        all_messages = cls.fetch_all(session, messages_sql, params)
        
        # Load all attachments in one query
        attachments_sql = f"""
            SELECT a.*, m.conversation_id
            FROM attachments a
            JOIN messages m ON a.message_id = m.id
            WHERE m.conversation_id IN ({placeholders})
        """
        all_attachments = cls.fetch_all(session, attachments_sql, params)
        
        # Load all reactions in one query
        reactions_sql = f"""
            SELECT r.*, m.conversation_id, c.name as sender_name
            FROM reactions r
            JOIN messages m ON r.original_message_guid = m.guid
            LEFT JOIN contacts c ON r.sender_id = c.id
            WHERE m.conversation_id IN ({placeholders})
        """
        all_reactions = cls.fetch_all(session, reactions_sql, params)
        
        # Group data by conversation
        messages_by_conv = {}
        attachments_by_conv = {}
        reactions_by_conv = {}
        
        for msg in all_messages:
            messages_by_conv.setdefault(msg['conversation_id'], []).append(msg)
        
        for att in all_attachments:
            attachments_by_conv.setdefault(att['conversation_id'], []).append(att)
            
        for react in all_reactions:
            reactions_by_conv.setdefault(react['conversation_id'], []).append(react)
        
        return {
            "messages": messages_by_conv,
            "attachments": attachments_by_conv,
            "reactions": reactions_by_conv
        }
    
    @classmethod
    def count_completed_analyses_by_template(cls, session: Session, template_id: int, chat_id: Optional[int] = None) -> int:
        """Count completed analyses for a specific prompt template."""
        sql = """
            SELECT COUNT(DISTINCT conversation_id)
            FROM conversation_analyses
            WHERE prompt_template_id = :template_id 
              AND status = 'success'
        """
        params = {"template_id": template_id}
        
        if chat_id:
            sql += " AND conversation_id IN (SELECT id FROM conversations WHERE chat_id = :chat_id)"
            params["chat_id"] = chat_id
            
        return cls.fetch_scalar(session, sql, params) or 0 