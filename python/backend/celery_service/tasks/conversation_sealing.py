"""Celery tasks for conversation sealing and event handling"""
import logging
from celery import shared_task
from backend.celery_service.tasks.base import BaseTaskWithDLQ
from datetime import datetime, timezone, timedelta
from typing import List

from backend.etl.live_sync.conversation_tracker import conversation_tracker
from backend.etl.etl_conversations import etl_conversations
from backend.services.core.event_bus import event_bus
from backend.celery_service.analysis_passes import trigger_all_pending_passes, trigger_analysis_pass, get_batch_passes
from backend.db.session_manager import db

logger = logging.getLogger(__name__)

@shared_task(name='celery.check_and_seal_conversations', bind=True, base=BaseTaskWithDLQ)
def check_and_seal_conversations(self):
    """
    Periodic task to check for completed conversations and seal them
    """
    try:
        logger.info("Checking for conversations to seal...")
        sealed_chats = conversation_tracker.check_and_seal_conversations()
        
        if sealed_chats:
            logger.info(f"Sealed {len(sealed_chats)} conversations: {sealed_chats}")
        else:
            logger.debug("No conversations to seal")
            
        return {
            "sealed_count": len(sealed_chats),
            "sealed_chats": sealed_chats
        }
        
    except Exception as e:
        logger.error(f"Error in check_and_seal_conversations task: {e}", exc_info=True)
        raise

@shared_task(name='celery.handle_sealed_conversation', bind=True, base=BaseTaskWithDLQ)
def handle_sealed_conversation(self, chat_id: int, sealed_at: str = None, **kwargs):
    """
    Handle a sealed conversation event by running ETL and triggering analysis
    
    Args:
        chat_id: ID of the chat that was sealed
        sealed_at: ISO timestamp when conversation was sealed
        **kwargs: Additional event data
    """
    try:
        logger.info(f"Handling sealed conversation for chat {chat_id}")
        
        # Parse sealed_at timestamp
        if sealed_at:
            sealed_datetime = datetime.fromisoformat(sealed_at.replace('Z', '+00:00'))
        else:
            sealed_datetime = datetime.now(timezone.utc)
        
        # Run ETL to create/update conversation records
        # Use a timeframe that goes back a bit to ensure we catch everything
        since_date = sealed_datetime - timedelta(hours=24)  # Look back 24 hours to be safe
        
        imported, updated, new_convo_ids = etl_conversations(
            chat_id=chat_id, 
            since_date=since_date
        )
        
        logger.info(f"ETL for chat {chat_id}: imported={imported}, updated={updated}, new_conversations={len(new_convo_ids)}")
        
        # Emit events for each new conversation that's ready for analysis
        for convo_id in new_convo_ids:
            event_bus.publish(
                f"{chat_id}",
                "conversation_ready_for_analysis",
                {
                    "conversation_id": convo_id,
                    "chat_id": chat_id,
                    "sealed_at": sealed_at,
                    "etl_completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            logger.info(
                f"Emitted conversation_ready_for_analysis event for conversation {convo_id}"
            )
        
        return {
            "chat_id": chat_id,
            "imported": imported,
            "updated": updated,
            "new_conversations": new_convo_ids,
            "events_emitted": len(new_convo_ids)
        }
        
    except Exception as e:
        logger.error(f"Error handling sealed conversation {chat_id}: {e}", exc_info=True)
        raise

@shared_task(name='celery.handle_conversation_ready', bind=True, base=BaseTaskWithDLQ)
def handle_conversation_ready(self, conversation_id: int, chat_id: int, **kwargs):
    """
    Handle a conversation ready for analysis event by triggering batch analysis passes
    
    Args:
        conversation_id: ID of the conversation ready for analysis
        chat_id: ID of the chat
        **kwargs: Additional event data
    """
    try:
        logger.info(f"Handling conversation ready for analysis: {conversation_id} (chat {chat_id})")
        
        # Analysis enabled for all chats
        logger.debug(f"Processing sealed conversation {conversation_id} for chat {chat_id}")
        
        # Trigger all batch analysis passes for sealed conversations
        batch_passes = get_batch_passes()
        triggered_passes = []
        
        for pass_name, config in batch_passes.items():
            logger.info(f"[ConversationSealing] Triggering batch pass '{pass_name}' for conversation {conversation_id}")
            task_id = trigger_analysis_pass(conversation_id, chat_id, pass_name)
            triggered_passes.append(pass_name)
            logger.info(f"[ConversationSealing] Triggered '{pass_name}' task {task_id} for conversation {conversation_id}")
        
        # Emit event for each triggered pass
        for pass_name in triggered_passes:
            event_bus.publish(
                f"{chat_id}",
                "analysis_pass_triggered",
                {
                "conversation_id": conversation_id,
                "chat_id": chat_id,
                "pass_name": pass_name,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        
        logger.info(f"Triggered {len(triggered_passes)} analysis passes for conversation {conversation_id}: {triggered_passes}")
        
        return {
            "conversation_id": conversation_id,
            "chat_id": chat_id,
            "passes_triggered": triggered_passes
        }
        
    except Exception as e:
        logger.error(f"Error handling conversation ready {conversation_id}: {e}", exc_info=True)
        raise

@shared_task(name='celery.force_seal_chat', bind=True, base=BaseTaskWithDLQ)
def force_seal_chat(self, chat_id: int):
    """
    Force seal a specific chat (useful for testing or manual intervention)
    
    Args:
        chat_id: ID of the chat to force seal
    """
    try:
        logger.info(f"Force sealing chat {chat_id}")
        
        success = conversation_tracker.force_seal_chat(chat_id)
        
        if success:
            logger.info(f"Successfully force sealed chat {chat_id}")
            return {"chat_id": chat_id, "sealed": True}
        else:
            logger.error(f"Failed to force seal chat {chat_id}")
            return {"chat_id": chat_id, "sealed": False}
            
    except Exception as e:
        logger.error(f"Error force sealing chat {chat_id}: {e}", exc_info=True)
        raise

# Event subscription handlers (will be set up during app startup)
def subscribe_to_conversation_events():
    """
    Subscribe to conversation-related events and dispatch to Celery tasks
    
    This should be called during application startup to set up event handlers
    """
    def handle_conversation_sealed(event_data):
        """Handle conversation_sealed events"""
        try:
            data = event_data.get('data', {})
            chat_id = data.get('chat_id')
            
            if chat_id:
                # Dispatch to Celery task
                handle_sealed_conversation.delay(
                    chat_id=chat_id,
                    sealed_at=data.get('sealed_at'),
                    last_message_at=data.get('last_message_at'),
                    gap_duration_minutes=data.get('gap_duration_minutes'),
                    forced=data.get('forced', False)
                )
                logger.debug(f"Dispatched sealed conversation handler for chat {chat_id}")
            else:
                logger.error(f"No chat_id in conversation_sealed event: {event_data}")
                
        except Exception as e:
            logger.error(f"Error handling conversation_sealed event: {e}", exc_info=True)
    
    def handle_conversation_ready_event(event_data):
        """Handle conversation_ready_for_analysis events"""
        try:
            data = event_data.get('data', {})
            conversation_id = data.get('conversation_id')
            chat_id = data.get('chat_id')
            
            if conversation_id and chat_id:
                # Dispatch to Celery task
                handle_conversation_ready.delay(
                    conversation_id=conversation_id,
                    chat_id=chat_id,
                    sealed_at=data.get('sealed_at'),
                    etl_completed_at=data.get('etl_completed_at')
                )
                logger.debug(f"Dispatched conversation ready handler for conversation {conversation_id}")
            else:
                logger.error(f"Missing conversation_id or chat_id in conversation_ready_for_analysis event: {event_data}")
                
        except Exception as e:
            logger.error(f"Error handling conversation_ready_for_analysis event: {e}", exc_info=True)
    
    # Subscribe to events
    try:
        event_bus.subscribe('conversation_sealed', handle_conversation_sealed)
        event_bus.subscribe('conversation_ready_for_analysis', handle_conversation_ready_event)
        logger.info("Subscribed to conversation events")
    except Exception as e:
        logger.error(f"Error subscribing to conversation events: {e}", exc_info=True) 