import sqlite3
import os
import logging
from typing import List, Dict, Tuple, Set, Optional
from datetime import datetime, timezone, timedelta
from backend.db.session_manager import db
from backend.etl.etl_messages import transform_message, _convert_apple_timestamp
from backend.etl.utils import normalize_phone_number
from .cache import get_contact_map, get_message_guid_to_id_map, get_chat_map, get_reaction_guids, CONTACT_MAP_CACHE, MESSAGE_GUID_TO_ID_CACHE, REACTION_GUID_CACHE, get_chat_participants_map, CHAT_DB_ROWID_TO_PARTICIPANTS_CACHE, CHAT_MAP_CACHE
from .sync_contacts import sync_contact_from_addressbook
from sqlalchemy import text

logger = logging.getLogger("backend.etl.live_sync")

def create_basic_contact(cursor, identifier: str) -> int:
    """
    Create a basic contact when no AddressBook match is found using the provided cursor.
    Avoids nested sessions to prevent SQLite write locks.
    
    Args:
        cursor: SQLite cursor bound to the current transactional session
        identifier: The contact identifier (phone number or email)
    
    Returns:
        contact_id of the newly created or reused contact
    """
    # Reuse existing contact if identifier already known
    cursor.execute(
        """
        SELECT c.id
        FROM contact_identifiers ci
        JOIN contacts c ON c.id = ci.contact_id
        WHERE ci.identifier = ?
        """,
        (identifier,),
    )
    row = cursor.fetchone()
    if row:
        contact_id = int(row[0])
        cache_key = identifier.lower() if '@' in identifier else normalize_phone_number(identifier)
        CONTACT_MAP_CACHE[cache_key] = contact_id
        logger.debug(f"Re-used existing contact {contact_id} for {identifier}")
        return contact_id

    # Create contact with identifier as name
    cursor.execute(
        "INSERT INTO contacts (name, data_source) VALUES (?, ?)",
        (identifier, 'live_sync_unknown_sender')
    )
    contact_id = cursor.lastrowid

    # Add contact identifier
    identifier_type = 'Email' if '@' in identifier else 'Phone'
    cursor.execute(
        """
        INSERT INTO contact_identifiers 
        (contact_id, identifier, type, is_primary)
        VALUES (?, ?, ?, ?)
        """,
        (contact_id, identifier, identifier_type, True),
    )

    # Update cache
    cache_key = identifier.lower() if '@' in identifier else normalize_phone_number(identifier)
    CONTACT_MAP_CACHE[cache_key] = contact_id

    logger.debug(f"Created basic contact with ID {contact_id} for unknown sender: {identifier}")
    return contact_id

def create_chat_if_missing(cursor, chat_identifier: str, is_group: bool = False) -> int:
    """
    Create a new chat entry if it doesn't exist in our database.
    Updates the CHAT_MAP_CACHE with the new chat_id.
    
    Args:
        cursor: SQLite cursor to use for queries
        chat_identifier: The unique identifier for the chat
        is_group: Whether this is a group chat
        
    Returns:
        int: The chat ID (newly created or existing)
    """
    # First check if it's in the cache
    chat_id = CHAT_MAP_CACHE.get(chat_identifier)
    if chat_id:
        return chat_id
    
    # Double-check database directly
    cursor.execute("SELECT id FROM chats WHERE chat_identifier = ?", (chat_identifier,))
    result = cursor.fetchone()
    if result:
        chat_id = result[0]
        # Update cache
        CHAT_MAP_CACHE[chat_identifier] = chat_id
        return chat_id
    
    # Create a new chat
    try:
        now = datetime.now()
        cursor.execute(
            """INSERT INTO chats 
               (chat_identifier, created_date, last_message_date, is_group, service_name, total_messages)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chat_identifier, now, now, is_group, "iMessage", 0)
        )
        chat_id = cursor.lastrowid
        
        # Update cache
        CHAT_MAP_CACHE[chat_identifier] = chat_id
        
        logger.debug(f"Created new chat with identifier: '{chat_identifier}', assigned id: {chat_id}")
        return chat_id
    except Exception as e:
        logger.error(f"Failed to create new chat for identifier '{chat_identifier}': {e}")
        return None

def ensure_chat_participants(cursor, chat_id: int, chat_identifier: str, sender_id: int = None):
    """
    Ensure all participants for a chat are properly linked in the chat_participants table.
    Also updates the chat name for individual chats.
    """
    # Parse the chat identifier to get all participant identifiers
    participant_identifiers = chat_identifier.split(',') if chat_identifier else []
    
    # Get existing participants for this chat
    cursor.execute(
        "SELECT contact_id FROM chat_participants WHERE chat_id = ?",
        (chat_id,)
    )
    existing_participant_ids = {row[0] for row in cursor.fetchall()}
    
    # Ensure each participant is linked
    participants_added = 0
    contact_names = []
    
    for identifier in participant_identifiers:
        if not identifier:
            continue
            
        # Look up the contact
        cache_key = identifier.lower() if '@' in identifier else normalize_phone_number(identifier)
        contact_id = CONTACT_MAP_CACHE.get(cache_key)
        
        if contact_id and contact_id not in existing_participant_ids:
            # Add to chat_participants
            cursor.execute(
                "INSERT OR IGNORE INTO chat_participants (chat_id, contact_id) VALUES (?, ?)",
                (chat_id, contact_id)
            )
            participants_added += 1
            existing_participant_ids.add(contact_id)
        
        # Get contact name for chat naming
        if contact_id:
            cursor.execute("SELECT name FROM contacts WHERE id = ?", (contact_id,))
            result = cursor.fetchone()
            if result and result[0]:
                contact_names.append(result[0])
    
    # Also ensure the sender is a participant (if provided)
    if sender_id and sender_id not in existing_participant_ids:
        cursor.execute(
            "INSERT OR IGNORE INTO chat_participants (chat_id, contact_id) VALUES (?, ?)",
            (chat_id, sender_id)
        )
        participants_added += 1
    
    # Update chat name for individual chats
    if len(participant_identifiers) == 1 and contact_names:
        # This is an individual chat, update the name
        cursor.execute(
            "UPDATE chats SET chat_name = ? WHERE id = ? AND (chat_name IS NULL OR chat_name = chat_identifier)",
            (contact_names[0], chat_id)
        )
    
    if participants_added > 0:
        logger.debug(f"Added {participants_added} participants to chat {chat_id}")

def ensure_conversation_for_message_raw(cursor, chat_id: int, message_timestamp: datetime) -> int:
    """
    Conversation management using the same raw cursor to avoid session/cursor mixing.
    Creates or extends a conversation within a 90-minute window and returns its id.
    """
    cutoff = message_timestamp - timedelta(minutes=90)
    logger.debug(f"[CONV-DEBUG] Looking for conversation in chat {chat_id} since {cutoff}")

    cursor.execute(
        """
        SELECT id, message_count
        FROM conversations
        WHERE chat_id = ? AND end_time >= ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (chat_id, cutoff),
    )
    row = cursor.fetchone()
    if row:
        conv_id, current_count = row
        cursor.execute(
            """
            UPDATE conversations
            SET end_time = ?, message_count = ?
            WHERE id = ?
            """,
            (message_timestamp, (current_count or 0) + 1, conv_id),
        )
        logger.debug(f"[CONV-DEBUG] Extended conversation {conv_id} in chat {chat_id}, new count: {(current_count or 0) + 1}")
        return int(conv_id)
    # Create a new conversation
    cursor.execute(
        """
        INSERT INTO conversations (chat_id, start_time, end_time, message_count, summary)
        VALUES (?, ?, ?, 1, '')
        """,
        (chat_id, message_timestamp, message_timestamp),
    )
    conv_id = cursor.lastrowid
    logger.debug(f"[CONV-DEBUG] Created new conversation {conv_id} in chat {chat_id}")
    return int(conv_id)

def sync_messages(new_rows: List[Dict]) -> Tuple[int, Dict[int, int]]:
    """
    Process new messages from chat.db into our database.
    
    Args:
        new_rows: List of raw message rows from chat.db
        
    Returns:
        Tuple containing:
        - Number of imported messages
        - Dict mapping chat_id to number of new messages
    """
    # Only emit on actual candidates; skip empty batches entirely
    if not new_rows:
        return 0, {}
    logger.debug("[LiveSync] sync_messages start", extra={"row_count": len(new_rows)})
    
    if not new_rows:
        logger.info("No new messages to process")
        return 0, {}

    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout = 5000")
        except Exception:
            pass
        
        # Get caches
        chat_map_cache = get_chat_map(cursor, force_refresh=False)
        message_cache_map = get_message_guid_to_id_map(cursor, force_refresh=False)
        reaction_guid_cache = get_reaction_guids(cursor, force_refresh=False)
        contact_map_cache_global = get_contact_map(cursor, force_refresh=False)
        chat_participants_map = get_chat_participants_map(force_refresh=False)

        # Get or create "Me" contact
        cursor.execute("SELECT id FROM contacts WHERE is_me = 1")
        user_id_row = cursor.fetchone()
        user_id = user_id_row[0] if user_id_row else None
        
        if not user_id:
            cursor.execute("INSERT INTO contacts (name, is_me, data_source) VALUES (?, ?, ?)", 
                         ("Me", True, "system_user"))
            user_id = cursor.lastrowid
            logger.debug(f"Created 'Me' contact with ID {user_id}")
        
        messages_to_insert = []
        prepared_guids: list[str] = []
        reactions_to_insert = []
        chat_counts = {}
        imported_count = 0
        conversation_ids_affected = set()

        # Process each message
        for i, row in enumerate(new_rows):
            logger.debug(
                "[LiveSync] processing raw row",
                extra={
                    "row_index": i,
                    "chat_id": row.get("chat_id"),
                    "guid": row.get("guid"),
                    "service": row.get("service"),
                    "is_from_me": row.get("is_from_me"),
                }
            )

            # Handle missing chat_participants
            if not row.get('chat_participants') and 'chat_id' in row:
                chat_id_from_row = row.get('chat_id')
                if chat_id_from_row in chat_participants_map:
                    row['chat_participants'] = chat_participants_map[chat_id_from_row]
            
            if not row.get('chat_participants'):
                sender_handle_str = row.get('sender_identifier')
                row['chat_participants'] = sender_handle_str if sender_handle_str else ""
            
            # Transform message
            transformed = transform_message(row)
            
            # Get or create chat
            chat_id = chat_map_cache.get(transformed['chat_identifier'])
            if not chat_id and transformed['chat_identifier']:
                is_group = len(row.get('chat_participants', '').split(',')) > 1
                chat_id = create_chat_if_missing(cursor, transformed['chat_identifier'], is_group)
                if chat_id:
                    ensure_chat_participants(cursor, chat_id, transformed['chat_identifier'])
            
            if not chat_id:
                logger.debug(
                    "[LiveSync] Chat not found: %s", transformed['chat_identifier']
                )
                continue
            
            # Get sender
            sender_id = None
            if transformed['is_from_me']:
                sender_id = user_id
            else:
                sender_identifier = transformed.get('sender_identifier')
                if sender_identifier:
                    cache_key = sender_identifier.lower() if '@' in sender_identifier else normalize_phone_number(sender_identifier)
                    sender_id = CONTACT_MAP_CACHE.get(cache_key)
                    
                    if not sender_id:
                        sender_id = sync_contact_from_addressbook(sender_identifier)
                        if not sender_id:
                            sender_id = create_basic_contact(cursor, sender_identifier)
                
                if not sender_id:
                    logger.debug(f"Failed to resolve sender for '{sender_identifier}'")
                    continue
            
            message_guid = transformed.get('guid')
            if not message_guid:
                logger.debug("Message without GUID, skipping")
                continue

            # Handle reactions vs regular messages
            if transformed['message_type'] != 0 and transformed['message_type'] is not None:
                if message_guid not in reaction_guid_cache:
                    reactions_to_insert.append((
                        transformed['associated_message_guid'],
                        transformed['message_type'],
                        sender_id,
                        transformed['timestamp'],
                        chat_id,
                        message_guid
                    ))
            else:
                if message_guid not in message_cache_map:
                    # Ensure conversation exists and get its ID (raw SQL via the same cursor)
                    conv_id = ensure_conversation_for_message_raw(cursor, chat_id, transformed['timestamp'])
                    conversation_ids_affected.add(conv_id)
                    
                    messages_to_insert.append((
                        chat_id,
                        sender_id,
                        transformed['content'],
                        transformed['timestamp'],
                        transformed['is_from_me'],
                        transformed['message_type'],
                        transformed['service_name'],
                        message_guid,
                        conv_id  # Add conversation_id to insert
                    ))
                    prepared_guids.append(message_guid)
                    chat_counts[chat_id] = chat_counts.get(chat_id, 0) + 1
                    imported_count += 1
                    
                    ensure_chat_participants(cursor, chat_id, transformed['chat_identifier'], sender_id)
                else:
                    logger.debug("[LiveSync] duplicate message skipped", extra={"guid": message_guid})

        # Insert messages with conversation_id
        if messages_to_insert:
            logger.debug(
                "[LiveSync] inserting messages",
                extra={
                    "insert_count": len(messages_to_insert),
                    "chat_ids": list(chat_counts.keys()),
                    "first_guid": prepared_guids[0] if prepared_guids else None,
                },
            )
            cursor.executemany("""
                INSERT INTO messages (chat_id, sender_id, content, timestamp, 
                                    is_from_me, message_type, service_name, guid, conversation_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, messages_to_insert)
            
            # Update message cache
            guids = [msg[-2] for msg in messages_to_insert]  # guid is second to last now
            placeholders = ','.join('?' for _ in guids)
            cursor.execute(f"SELECT guid, id FROM messages WHERE guid IN ({placeholders})", guids)
            for guid, db_id in cursor.fetchall():
                MESSAGE_GUID_TO_ID_CACHE[guid] = db_id

        # Insert reactions
        if reactions_to_insert:
            cursor.executemany("""
                INSERT INTO reactions (original_message_guid, reaction_type, 
                                     sender_id, timestamp, chat_id, guid)
                VALUES (?, ?, ?, ?, ?, ?)
            """, reactions_to_insert)
            for reaction in reactions_to_insert:
                REACTION_GUID_CACHE.add(reaction[-1])

        # Update chat message counts
        if chat_counts:
            cursor.executemany(
                "UPDATE chats SET total_messages = total_messages + ? WHERE id = ?",
                [(count, chat_id) for chat_id, count in chat_counts.items()]
            )
        
        # ─── NEW: push freshly-inserted messages to in-process hub ────────────
        if messages_to_insert:
            from backend.services.core.chat_message_hub import message_hub

            # guids are second to last in insertion tuples
            new_guids = [row[-2] for row in messages_to_insert]
            if new_guids:
                placeholders = ",".join(["?" for _ in new_guids])
                # Use raw SQL per user preference; fetch fresh rows with PKs
                cursor.execute(
                    f"SELECT * FROM messages WHERE guid IN ({placeholders})",
                    new_guids,
                )
                columns = [desc[0] for desc in cursor.description]
                fetched = cursor.fetchall()

                by_chat: Dict[int, list[dict]] = {}
                for row in fetched:
                    row_map = {col: val for col, val in zip(columns, row)}
                    # Serialize message for API
                    ts = row_map.get("timestamp")
                    api_msg = {
                        "id": row_map.get("id"),
                        "chat_id": row_map.get("chat_id"),
                        "sender_id": row_map.get("sender_id"),
                        "sender_name": None,
                        "content": row_map.get("content"),
                        "timestamp": (ts.isoformat() if hasattr(ts, "isoformat") else str(ts)),
                        "is_from_me": bool(row_map.get("is_from_me")),
                        "message_type": row_map.get("message_type"),
                        "service_name": row_map.get("service_name"),
                        "guid": row_map.get("guid"),
                        "associated_message_guid": row_map.get("associated_message_guid"),
                        "reply_to_guid": row_map.get("reply_to_guid"),
                        "reaction_count": row_map.get("reaction_count") or 0,
                        "attachments": [],
                        "conversation_id": row_map.get("conversation_id"),
                    }
                    by_chat.setdefault(int(api_msg["chat_id"]), []).append(api_msg)

                total_payload = 0
                for chat_id, payload in by_chat.items():
                    total_payload += len(payload)
                    try:
                        message_hub.publish(chat_id, payload)
                    except Exception:
                        logger.exception("Failed to publish messages to in-process hub for chat %s", chat_id)
                logger.info("[LiveSync] published to hub: chats=%d messages=%d", len(by_chat), total_payload)
        
        # Commit all database operations before triggering analysis
        session.commit()
        
        # Trigger live analysis via Celery task (optional compute plane).
        # Default OFF for CLI-first / local-only mode.
        # IMPORTANT: don't fall back to legacy CHATSTATS_* env vars here.
        # If a user has old env vars set in their shell, we don't want Eve's
        # CLI/watch to silently start dispatching Celery tasks.
        trigger_compute = os.getenv("EVE_ENABLE_COMPUTE_PLANE", "0").lower() in ("1", "true", "yes", "on")
        if trigger_compute and chat_counts and imported_count > 0:
            try:
                # Import and trigger Celery task directly
                from backend.celery_service.tasks.live_analysis import handle_new_messages_synced_task
                
                logger.debug(
                    "[LiveSync] triggering live analysis",
                    extra={
                        "imported_count": imported_count,
                        "chat_ids": list(chat_counts.keys()),
                        "conversation_ids": list(conversation_ids_affected),
                    },
                )
                task_result = handle_new_messages_synced_task.delay(
                    chat_counts=chat_counts,
                    conversation_ids=list(conversation_ids_affected),
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
                
                logger.debug(f"[LiveSync] Live analysis task dispatched: task_id={task_result.id}")
                
            except Exception as e:
                logger.error(f"[LiveSync] Failed to trigger live analysis task: {e}", exc_info=True)
        
        if imported_count > 0:
            logger.debug(
                "[LiveSync] sync_messages completed",
                extra={
                    "imported_count": imported_count,
                    "chat_counts": chat_counts,
                    "conversation_ids": list(conversation_ids_affected),
                },
            )
        return imported_count, chat_counts 

def get_last_message_timestamp(chat_id: int) -> Optional[datetime]:
    """Get the timestamp of the most recent message for a chat"""
    try:
        with db.session_scope() as session:
            result = session.execute(
                text("""
                    SELECT MAX(timestamp) as last_message_timestamp
                    FROM messages
                    WHERE chat_id = :chat_id
                """),
                {'chat_id': chat_id}
            ).fetchone()
            
            return result[0] if result and result[0] else None
    except Exception as e:
        logger.error(f"Error getting last message timestamp for chat {chat_id}: {e}", exc_info=True)
        return None 