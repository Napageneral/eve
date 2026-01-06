"""
Conversation analysis service - business logic for conversation analysis operations
"""
from backend.db.session_manager import new_session
from backend.repositories.conversation_analysis import ConversationAnalysisRepository
from backend.celery_service import constants
from backend.services.core.utils import BaseService, timed, with_session
from backend.services.core.constants import DefaultLLMConfigs, ANALYSIS_SCOPE_TEMPLATE
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


class ConversationAnalysisService(BaseService):
    """Business logic service for conversation analysis operations."""
    
    @staticmethod
    @timed("fetch_and_encode_all_conversations")
    @with_session(commit=False)
    def fetch_and_encode_all_conversations(session=None) -> Dict[int, Dict[str, Any]]:
        """
        Fetch and encode ALL unanalyzed conversations across ALL chats using Eve service.
        Returns: {conversation_id: {"encoded_text": str, "chat_id": int}}
        """
        import requests
        
        # Step 1: Get all unanalyzed conversations using repository
        conversations_to_encode = ConversationAnalysisRepository.get_unanalyzed_conversations(session)
        logger.info(f"Found {len(conversations_to_encode)} conversations to encode")
        
        if not conversations_to_encode:
            logger.info("No conversations to encode")
            return {}
        
        # Step 2: Batch load ALL messages, attachments, reactions using repository
        conv_ids = [row['conv_id'] for row in conversations_to_encode]
        message_data = ConversationAnalysisRepository.get_messages_for_conversations(session, conv_ids)
        
        messages_by_conv = message_data["messages"]
        attachments_by_conv = message_data["attachments"]
        reactions_by_conv = message_data["reactions"]
        
        logger.info(f"Loaded messages for {len(messages_by_conv)} conversations")
        
        # Step 3: Encode each conversation with proper chat context
        encoded_texts = {}
        current_chat_id = None
        chat_participant_names = {}
        
        for conv_row in conversations_to_encode:
            # Update participant name cache when we switch chats
            if conv_row['chat_id'] != current_chat_id:
                current_chat_id = conv_row['chat_id']
                # Parse participant mapping
                chat_participant_names = {}
                if conv_row.get('participant_mapping'):
                    for mapping in conv_row['participant_mapping'].split(','):
                        if ':' in mapping:
                            name, contact_id = mapping.rsplit(':', 1)
                            chat_participant_names[int(contact_id)] = name
                logger.info(f"Switched to chat {current_chat_id} with {len(chat_participant_names)} participants")
            
            # Build conversation dict
            conv_messages = messages_by_conv.get(conv_row['conv_id'], [])
            conv_attachments = attachments_by_conv.get(conv_row['conv_id'], [])
            conv_reactions = reactions_by_conv.get(conv_row['conv_id'], [])
            
            # Create minimal conversation data for encoding
            conv_dict = {
                "id": conv_row['conv_id'],
                "chat_id": conv_row['chat_id'],
                "messages": []
            }
            
            # Pre-index attachments and reactions for O(1) lookup per message
            from collections import defaultdict
            attachments_by_msg = defaultdict(list)
            for att in conv_attachments:
                attachments_by_msg[att['message_id']].append({
                    "id": att['id'],
                    "mime_type": att['mime_type'],
                    "file_name": att['file_name'],
                    "is_sticker": bool(att.get('is_sticker')), 
                    "guid": att.get("guid"), 
                    "uti": att.get("uti"),
                })

            reactions_by_guid = defaultdict(list)
            for react in conv_reactions:
                reactions_by_guid[react['original_message_guid']].append({
                    "reaction_type": react['reaction_type'],
                    "sender_id": react['sender_id'],
                    "sender_name": react.get('sender_name'),
                    "is_from_me": bool(react.get("is_from_me")),
                })

            # Process messages with proper names from chat context
            for msg in conv_messages:
                # Use chat-specific name if available, fallback to contact name
                sender_name = chat_participant_names.get(msg['sender_id'], msg.get('sender_name')) or "Unknown"
                
                # Build proper lists using pre-indexed maps
                msg_attachments = attachments_by_msg.get(msg['id'], [])
                msg_reactions = reactions_by_guid.get(msg['guid'], [])
                
                conv_dict["messages"].append({
                    "id": msg['id'],
                    "content": msg['content'],
                    "timestamp": msg['timestamp'],
                    "sender_id": msg['sender_id'],
                    "sender_name": sender_name,
                    "is_from_me": msg['is_from_me'],
                    "message_type": msg['message_type'],
                    "guid": msg['guid'],
                    "attachments": msg_attachments,
                    "reactions": msg_reactions
                })
            
            # Encode using Eve service
            try:
                resp = requests.post(
                    'http://127.0.0.1:3031/engine/encode',
                    json={'conversation_id': conv_row['conv_id'], 'chat_id': conv_row['chat_id']},
                    timeout=30
                )
                resp.raise_for_status()
                data = resp.json()
                encoded_text = data.get('encoded_text', '')
                if encoded_text:
                    encoded_texts[conv_row['conv_id']] = {"encoded_text": encoded_text, "chat_id": conv_row['chat_id']}
            except Exception as e:
                logger.error(f"Failed to encode conversation {conv_row['conv_id']}: {e}")
                # Skip this conversation if encoding fails
            
            if len(encoded_texts) % 100 == 0:
                logger.info(f"Encoded {len(encoded_texts)} conversations...")
        
        # Log memory usage
        # Approximate payload bytes via encoded text lengths for a realistic count
        total_size = sum(len(v.get("encoded_text", "")) for v in encoded_texts.values())
        logger.debug(f"Total encoded text size: {total_size / 1024 / 1024:.2f} MB")
        
        return encoded_texts

    # ---------------------------------------------------------------------
    # Phase 4: Delegate event publishing to central EventBus
    # ---------------------------------------------------------------------
    @staticmethod
    def publish_analysis_event(chat_id: str | int, event_type: str, data: dict | None = None):
        """Publish analysis events via central EventBus with normalized scopes.

        Scopes:
        - chat-specific → "chat:{chat_id}"
        - global        → handled by callers via EventBus.publish("global", ...)
        """
        from backend.services.core.event_bus import EventBus
        chat_scope = f"chat:{chat_id}"
        EventBus.publish(chat_scope, event_type, data or {})

    @staticmethod
    def publish_commitment_event(scope: str | int, event_type: str, data: dict | None = None):
        """Publish commitment events via EventBus (refactored)."""
        from backend.services.core.event_bus import EventBus
        if isinstance(scope, int):
            scope_str = f"commitments:{scope}"
        elif scope == "global":
            scope_str = "commitments:global"
        elif str(scope).startswith("commitments:"):
            scope_str = str(scope)
        else:
            scope_str = str(scope)
        EventBus.publish(scope_str, event_type, data or {})

    # ---------------------------------------------------------------------
    # Phase 5: Migrate save_analysis_results and _process_commitment_analysis
    # ---------------------------------------------------------------------
    @staticmethod
    def save_analysis_results(
        llm_response_content_str: str,
        conversation_id: int,
        chat_id: int,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        model_name: str,
        prompt_template_db_id: int | None,  # Now optional (None for Eve prompts)
        conversation_analysis_row_id: int,
        compiled_prompt_for_llm: str | None = None,
        eve_prompt_id: str | None = None,  # Track Eve prompt ID
    ) -> Dict[str, Any]:
        """Save conversation analysis results to database (migrated from AnalysisService)."""
        from backend.db.session_manager import new_session
        from backend.repositories.analysis_results import AnalysisResultsRepository
        from datetime import datetime
        import json
        import logging as _logging

        _logger = _logging.getLogger(__name__)
        _logger.info(
            f"[CA] Saving results for convo_id={conversation_id} chat_id={chat_id} prompt_id={prompt_template_db_id}"
        )

        # Defensive check
        if isinstance(llm_response_content_str, dict):
            # Expected for structured response_format; not a warning-worthy event
            _logger.debug(f"Received dict instead of string for convo {conversation_id}, converting to JSON")
            llm_response_content_str = json.dumps(llm_response_content_str, ensure_ascii=False)

        raw_completion_id = None  # raw completions disabled after refactor

        # Parse JSON response
        try:
            analysis_data = AnalysisResultsRepository.parse_llm_json_response(llm_response_content_str)
        except json.JSONDecodeError as e:
            _logger.error(f"[CA] Could not parse JSON for convo {conversation_id}: {e}")
            raise ValueError(f"Failed to parse LLM JSON response for convo {conversation_id}: {e}")

        with new_session() as session:
            # Save analysis results (raw completion persistence removed)
            counts = AnalysisResultsRepository.save_analysis_results(
                session,
                conversation_id,
                chat_id,
                analysis_data,
                conversation_analysis_row_id,
                raw_completion_id,
                eve_prompt_id=eve_prompt_id,  # Track Eve prompt for this analysis
            )

            session.commit()
            _logger.info(f"[CA] Saved analysis for convo_id={conversation_id} (eve_prompt={eve_prompt_id}) counts={counts}")

            # Process commitments if this was a commitment extraction pass
            # Note: Commitments feature is paused, prompts now managed by Eve
            # This check is deprecated but kept for future reactivation
            if prompt_template_db_id and eve_prompt_id and "commitment" in eve_prompt_id.lower():
                _logger.info(
                    f"[COMMITMENT] Processing commitment analysis for conversation {conversation_id}"
                )
                from backend.services.commitments.commitments import CommitmentService
                CommitmentService.process_conversation_analysis(
                    session=session,
                    conversation_id=conversation_id,
                    chat_id=chat_id,
                    analysis_data=analysis_data,
                    prompt_template={"name": eve_prompt_id},  # Minimal prompt info for commitments
                )

            # Publish completion event
            ConversationAnalysisService.publish_analysis_event(chat_id, "analysis_saved", {
                "conversation_id": conversation_id,
                **counts,
            })

        return {
            "analysis_id": raw_completion_id,
            "conversation_analysis_id": conversation_analysis_row_id,
            "status": "saved_and_ca_updated",
            "conversation_id": conversation_id,
            **counts,
        }

    # NOTE: Commitment-specific post-processing now lives on CommitmentService.

    @staticmethod
    @timed("ensure_conversation_analysis_record")
    @with_session(commit=True)
    def ensure_conversation_analysis_record(
        conversation_id: int,
        chat_id: int,
        prompt_name_in: Optional[str] = None,
        prompt_version_in: Optional[int] = None,
        prompt_category_in: Optional[str] = None,
        session=None
    ) -> Dict[str, Any]:
        """
        Ensures a ConversationAnalysis record exists and is ready for processing.
        """
        logger.debug(f"Ensuring CA record for convo_id: {conversation_id}, chat_id: {chat_id}")
        
        # Resolve prompt details to defaults if not provided
        resolved_prompt_name = prompt_name_in or constants.CA_DEFAULT_PROMPT_NAME
        resolved_prompt_version = prompt_version_in or constants.CA_DEFAULT_PROMPT_VERSION
        resolved_prompt_category = prompt_category_in or constants.CA_DEFAULT_PROMPT_CATEGORY
        
        try:
            # Get prompt_template_id first
            prompt_template_id = ConversationAnalysisRepository.get_prompt_template_id(
                session,
                resolved_prompt_name,
                resolved_prompt_version,
                resolved_prompt_category
            )
            if not prompt_template_id:
                raise ValueError(f"Prompt template not found: {resolved_prompt_category}/{resolved_prompt_name} v{resolved_prompt_version}")
            
            # Call prepare_for_analysis
            ca_id = ConversationAnalysisRepository.prepare_for_analysis(
                session,
                conversation_id,
                prompt_template_id
            )
            
            return {
                "ca_row_id": ca_id,
                "status": constants.CA_STATUS_PENDING,
                "message": f"CA record {ca_id} is ready for processing.",
                "prompt_template_id_used": prompt_template_id
            }
            
        except ValueError as e:
            logger.error(f"Error ensuring CA record for convo_id {conversation_id}: {str(e)}")
            return {
                "ca_row_id": None,
                "status": "error_prepare_failed",
                "message": str(e),
                "prompt_template_id_used": None
            }

    @staticmethod
    @timed("update_ca_status")
    @with_session(commit=True)
    def update_ca_status(ca_id: int, status: str, error_message: Optional[str] = None, session=None):
        """
        Update the status of a conversation analysis record.
        """
        logger.debug(f"Updating CA record {ca_id} to status '{status}'")
        
        # Get chat_id before updating
        ca_record = ConversationAnalysisRepository.get_by_id(session, ca_id)
        if not ca_record:
            logger.error(f"CA record {ca_id} not found")
            return
            
        # Get chat_id from conversation
        chat_id = ConversationAnalysisRepository.get_conversation_chat_id(
            session, ca_record["conversation_id"]
        )
        
        if chat_id:
            # Update status
            ConversationAnalysisRepository.update_status(session, ca_id, status, error_message)
            
            # Prepare event data
            event_data = {
                "ca_id": ca_id,
                "conversation_id": ca_record["conversation_id"],
                "status": status,
                "error_message": error_message
            }
            
            # Publish to the normalized chat scope and also to the global scope
            ConversationAnalysisService.publish_analysis_event(chat_id, f"analysis_{status}", event_data)
            from backend.services.core.event_bus import EventBus
            EventBus.publish("global", f"analysis_{status}", event_data)
            
            status_message = f"Successfully updated CA record {ca_id} to status '{status}'."
            if status == constants.CA_STATUS_SUCCESS:
                status_message += " Analysis completed successfully."
            elif status == constants.CA_STATUS_FAILED:
                status_message += f" Analysis failed: {error_message or 'Unknown error'}"
            
            logger.debug(f"{status_message} Published {status} event for chat {chat_id} and global channel")
        else:
            logger.error(f"Could not find chat_id for conversation {ca_record['conversation_id']}")

    @staticmethod
    @timed("mark_ca_with_task_id")
    @with_session(commit=True)
    def mark_ca_with_task_id(ca_id: int, task_id: str, session=None):
        """
        Mark a conversation analysis with a Celery task ID.
        """
        logger.debug(f"Marking CA record {ca_id} with Celery Task ID: {task_id}")
        
        ConversationAnalysisRepository.update_temporal_workflow_id(session, ca_id, task_id)
        logger.debug(f"Successfully marked CA record {ca_id} with task ID.")

    @staticmethod
    @timed("list_pending_ca")
    @with_session(commit=False)
    def list_pending_ca(limit: int, session=None) -> List[Dict[str, Any]]:
        """
        List pending conversation analyses.
        """
        logger.debug(f"Listing up to {limit} pending conversation analyses.")
        
        tasks_to_dispatch = ConversationAnalysisRepository.select_pending(session, limit)
        logger.debug(f"Found {len(tasks_to_dispatch)} pending analysis tasks to dispatch.")
        return tasks_to_dispatch

    @staticmethod
    @timed("load_conversation")
    @with_session(commit=False)
    def load_conversation(conversation_id: int, chat_id: int, session=None) -> dict:
        """
        Loads a single conversation with all its details.
        Moved from activities/load_conversation.py
        """
        logger.debug(f"Loading single conversation data for ID: {conversation_id}, Chat ID: {chat_id}")
        
        from backend.repositories.conversations import ConversationRepository
        
        convo_data = ConversationRepository.load_single_conversation_by_id(session, conversation_id, chat_id)
        
        if not convo_data:
            raise ValueError(f"Conversation {conversation_id} not found in chat {chat_id}")
        
        if not convo_data.get("messages"):
            logger.warning(f"Conversation {conversation_id} has no messages. Proceeding with empty message list.")
        
        logger.debug(f"Successfully loaded conversation {conversation_id} with {len(convo_data.get('messages', []))} messages.")
        return convo_data

    @staticmethod
    @timed("list_conversation_ids")
    @with_session(commit=False)
    def list_conversation_ids(chat_id: int, session=None) -> List[int]:
        """
        Get list of conversation IDs for a chat.
        Moved from activities/bulk_load.py
        """
        logger.debug(f"Loading conversation IDs for chat {chat_id}")
        
        from sqlalchemy import text
        
        result = session.execute(
            text("SELECT id FROM conversations WHERE chat_id = :chat_id ORDER BY id"),
            {"chat_id": chat_id}
        ).fetchall()
        
        conversation_ids = [row[0] for row in result]
        logger.debug(f"Found {len(conversation_ids)} conversations in chat {chat_id}")
        return conversation_ids

    @staticmethod
    @timed("fetch_and_encode_batch")
    @with_session(commit=False)
    def fetch_and_encode_batch(chat_id: int, conversation_ids: List[int], session=None) -> List[Tuple[int, str]]:
        """
        Fetch and encode a batch of conversations.
        Moved from activities/bulk_load.py
        Returns list of (conversation_id, encoded_text) tuples.
        """
        logger.debug(f"Fetching and encoding batch of {len(conversation_ids)} conversations for chat {chat_id}")
        
        from backend.repositories.conversations import ConversationRepository
        # NOTE: Encoding migrated to Eve service
        import requests
        from typing import Tuple
        
        results = []
        for conv_id in conversation_ids:
            try:
                # Use Eve encoding service
                resp = requests.post(
                    'http://127.0.0.1:3031/engine/encode',
                    json={'conversation_id': conv_id, 'chat_id': chat_id},
                    timeout=30
                )
                if resp.ok:
                    data = resp.json()
                    encoded_text = data.get('encoded_text', '')
                    results.append((conv_id, encoded_text))
                else:
                    # Encoding failed - append empty
                    results.append((conv_id, ""))
                    
            except Exception as e:
                logger.warning(f"Failed to encode conversation {conv_id}: {e}")
                results.append((conv_id, ""))
        
        logger.debug(f"Successfully encoded {len(results)} conversations")
        return results 


__all__ = ["ConversationAnalysisService"] 