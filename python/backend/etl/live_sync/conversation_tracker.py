"""Track active conversations and detect when they're complete"""
import redis
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Set, List, Dict, Any
from backend.config import settings

REDIS_URL = settings.redis_url
from backend.services.core.event_bus import event_bus

logger = logging.getLogger(__name__)

class ConversationTracker:
    """Tracks active conversations and detects when they should be sealed"""
    
    def __init__(self):
        self.redis = redis.from_url(REDIS_URL)
        self.gap_threshold = timedelta(minutes=90)
    
    def update_last_message(self, chat_id: int, timestamp: datetime):
        """
        Update the last message time for a chat and schedule a sealing check
        
        Args:
            chat_id: ID of the chat
            timestamp: Timestamp of the last message
        """
        try:
            # PHASE 1 FIX: Ensure timestamp is timezone-aware
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
                logger.debug(f"Converted naive datetime to UTC for chat {chat_id}")
            
            # Store the last message time with expiration (2 hours to be safe)
            key = f"chat:last_msg:{chat_id}"
            self.redis.set(key, timestamp.isoformat(), ex=7200)
            
            # Schedule a check for conversation completion
            # Use a sorted set where score is the timestamp when we should check
            check_time = timestamp + self.gap_threshold
            # PHASE 1 FIX: Use consistent string formatting for Redis keys
            self.redis.zadd("conversation:check_queue", {str(chat_id): check_time.timestamp()})
            
            logger.debug(f"Updated last message for chat {chat_id} at {timestamp}, check scheduled for {check_time}")
            
        except Exception as e:
            logger.error(f"Failed to update last message for chat {chat_id}: {e}", exc_info=True)
    
    def batch_update_last_messages(self, chat_timestamps: Dict[int, datetime]) -> None:
        """
        Update last message times for multiple chats in a batch (more efficient for bulk updates)
        
        Args:
            chat_timestamps: Dict mapping chat_id to latest timestamp
        """
        if not chat_timestamps:
            return
            
        try:
            with self.redis.pipeline() as pipe:
                for chat_id, timestamp in chat_timestamps.items():
                    # PHASE 1 FIX: Ensure timestamp is timezone-aware
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                        logger.debug(f"Converted naive datetime to UTC for chat {chat_id} in batch")
                    
                    key = f"chat:last_msg:{chat_id}"
                    # Store timestamp with 2-hour expiration
                    pipe.set(key, timestamp.isoformat(), ex=7200)
                    
                    # Schedule check for conversation completion
                    check_time = timestamp + self.gap_threshold
                    # PHASE 1 FIX: Use consistent string formatting for Redis keys
                    pipe.zadd("conversation:check_queue", {str(chat_id): check_time.timestamp()})
                
                pipe.execute()
                
            logger.debug(f"Batch updated last message times for {len(chat_timestamps)} chats")
            
        except Exception as e:
            logger.error(f"Failed to batch update last message times: {e}", exc_info=True)
    
    def check_and_seal_conversations(self) -> List[int]:
        """
        Check for conversations that need sealing and emit events
        
        Returns:
            List of chat IDs that were sealed
        """
        sealed_chats = []
        
        try:
            now = datetime.now(timezone.utc)
            cutoff = now.timestamp()
            
            # Get all chats that should be checked (score <= current timestamp)
            to_check = self.redis.zrangebyscore("conversation:check_queue", 0, cutoff)
            
            for chat_id_bytes in to_check:
                chat_id = int(chat_id_bytes.decode())
                
                try:
                    # Check if there were new messages since scheduled check
                    last_msg_key = f"chat:last_msg:{chat_id}"
                    last_msg_str = self.redis.get(last_msg_key)
                    
                    if last_msg_str:
                        last_msg = datetime.fromisoformat(last_msg_str.decode())
                        
                        # PHASE 1 FIX: Make sure last_msg is timezone aware
                        if last_msg.tzinfo is None:
                            last_msg = last_msg.replace(tzinfo=timezone.utc)
                            logger.debug(f"Converted stored naive datetime to UTC for chat {chat_id}")
                        
                        time_since_last = now - last_msg
                        
                        if time_since_last >= self.gap_threshold:
                            # Conversation is complete - emit event
                            logger.info(f"Sealing conversation for chat {chat_id} after {time_since_last}")
                            
                            event_bus.publish(
                                f"{chat_id}",
                                "conversation_sealed",
                                {
                                "chat_id": chat_id,
                                "sealed_at": now.isoformat(),
                                "last_message_at": last_msg.isoformat(),
                                "gap_duration_minutes": time_since_last.total_seconds() / 60
                                }
                            )
                            
                            sealed_chats.append(chat_id)
                            
                            # PHASE 1 FIX: Use consistent string formatting for cleanup
                            self.redis.zrem("conversation:check_queue", str(chat_id))
                            self.redis.delete(last_msg_key)
                        else:
                            # Not enough time has passed yet
                            # DON'T reschedule here - update_last_message will handle it
                            # when new messages arrive. Just remove this stale entry.
                            # PHASE 1 FIX: Use consistent string formatting
                            self.redis.zrem("conversation:check_queue", str(chat_id))
                            logger.debug(f"Chat {chat_id} check was premature, removed stale entry")
                    else:
                        # No last message found, remove from queue
                        logger.warning(f"No last message found for chat {chat_id}, removing from queue")
                        # PHASE 1 FIX: Use consistent string formatting
                        self.redis.zrem("conversation:check_queue", str(chat_id))
                        
                except Exception as e:
                    logger.error(f"Error processing chat {chat_id} for sealing: {e}", exc_info=True)
                    
        except Exception as e:
            logger.error(f"Error in check_and_seal_conversations: {e}", exc_info=True)
        
        if sealed_chats:
            logger.info(f"Sealed {len(sealed_chats)} conversations: {sealed_chats}")
        
        return sealed_chats
    
    def get_active_chats(self) -> List[int]:
        """Get list of currently active chat IDs"""
        try:
            chat_keys = self.redis.keys("chat:last_msg:*")
            return [int(key.decode().split(":")[-1]) for key in chat_keys]
        except Exception as e:
            logger.error(f"Error getting active chats: {e}", exc_info=True)
            return []
    
    def get_last_message_time(self, chat_id: int) -> Optional[datetime]:
        """Get the last message time for a chat"""
        try:
            key = f"chat:last_msg:{chat_id}"
            last_msg_str = self.redis.get(key)
            if last_msg_str:
                last_msg = datetime.fromisoformat(last_msg_str.decode())
                # PHASE 1 FIX: Ensure returned datetime is timezone-aware
                if last_msg.tzinfo is None:
                    last_msg = last_msg.replace(tzinfo=timezone.utc)
                    logger.debug(f"Converted stored naive datetime to UTC for chat {chat_id}")
                return last_msg
            return None
        except Exception as e:
            logger.error(f"Error getting last message time for chat {chat_id}: {e}", exc_info=True)
            return None
    
    def force_seal_chat(self, chat_id: int) -> bool:
        """Force seal a specific chat conversation"""
        try:
            now = datetime.now(timezone.utc)
            
            event_bus.publish(
                f"{chat_id}",
                "conversation_sealed",
                {
                "chat_id": chat_id,
                "sealed_at": now.isoformat(),
                "forced": True
                }
            )
            
            # Clean up tracking
            # PHASE 1 FIX: Use consistent string formatting for Redis keys
            self.redis.zrem("conversation:check_queue", str(chat_id))
            self.redis.delete(f"chat:last_msg:{chat_id}")
            
            logger.info(f"Force sealed conversation for chat {chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error force sealing chat {chat_id}: {e}", exc_info=True)
            return False

    # PHASE 2 - Debug and Memory Management Methods
    def get_pending_checks(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get information about pending conversation checks
        
        Args:
            limit: Maximum number of pending checks to return
            
        Returns:
            List of dicts with chat_id, check_time, and time_until_check
        """
        try:
            pending = self.redis.zrange("conversation:check_queue", 0, limit - 1, withscores=True)
            results = []
            now = datetime.now(timezone.utc)
            
            for chat_id_bytes, score in pending:
                chat_id = int(chat_id_bytes.decode())
                check_time = datetime.fromtimestamp(score, tz=timezone.utc)
                time_until_check = (check_time - now).total_seconds()
                
                results.append({
                    "chat_id": chat_id,
                    "check_time": check_time.isoformat(),
                    "time_until_check_seconds": time_until_check,
                    "overdue": time_until_check < 0
                })
            
            return results
            
        except Exception as e:
            logger.error(f"Error getting pending checks: {e}", exc_info=True)
            return []
    
    def cleanup_stale_checks(self, older_than_hours: int = 24) -> int:
        """
        Remove checks scheduled more than N hours ago (memory leak prevention)
        
        Args:
            older_than_hours: Remove checks older than this many hours
            
        Returns:
            Number of stale checks removed
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
            removed = self.redis.zremrangebyscore("conversation:check_queue", 0, cutoff.timestamp())
            
            if removed > 0:
                logger.info(f"Cleaned up {removed} stale conversation checks older than {older_than_hours} hours")
            else:
                logger.debug(f"No stale conversation checks found (older than {older_than_hours} hours)")
                
            return removed
            
        except Exception as e:
            logger.error(f"Error cleaning up stale checks: {e}", exc_info=True)
            return 0
    
    def get_redis_memory_stats(self) -> Dict[str, Any]:
        """
        Get Redis memory usage statistics for conversation tracking
        
        Returns:
            Dict with memory usage statistics
        """
        try:
            stats = {}
            
            # Count keys
            last_msg_keys = self.redis.keys("chat:last_msg:*")
            stats["active_chats"] = len(last_msg_keys)
            
            # Count pending checks
            pending_count = self.redis.zcard("conversation:check_queue")
            stats["pending_checks"] = pending_count
            
            # Get Redis info if available
            try:
                redis_info = self.redis.info("memory")
                stats["redis_memory_used"] = redis_info.get("used_memory_human", "N/A")
                stats["redis_memory_peak"] = redis_info.get("used_memory_peak_human", "N/A")
            except Exception:
                stats["redis_memory_used"] = "N/A"
                stats["redis_memory_peak"] = "N/A"
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting Redis memory stats: {e}", exc_info=True)
            return {"error": str(e)}
    
    def force_cleanup_all(self) -> Dict[str, int]:
        """
        Emergency cleanup - remove all conversation tracking data
        WARNING: This will lose all conversation tracking state
        
        Returns:
            Dict with counts of removed items
        """
        try:
            logger.warning("FORCE CLEANUP: Removing all conversation tracking data")
            
            # Remove all last message keys
            last_msg_keys = self.redis.keys("chat:last_msg:*")
            last_msg_removed = 0
            if last_msg_keys:
                last_msg_removed = self.redis.delete(*last_msg_keys)
            
            # Clear the check queue
            queue_removed = self.redis.delete("conversation:check_queue")
            
            result = {
                "last_message_keys_removed": last_msg_removed,
                "check_queue_cleared": queue_removed
            }
            
            logger.warning(f"FORCE CLEANUP COMPLETED: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Error in force cleanup: {e}", exc_info=True)
            return {"error": str(e)}

# Global conversation tracker instance
conversation_tracker = ConversationTracker() 