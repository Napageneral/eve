import os
import time
import logging
from typing import Optional, Set, Dict
from backend.etl.etl_contacts import find_live_address_books, extract_contacts, load_single_contact
from backend.etl.utils import normalize_phone_number
from .cache import CONTACT_MAP_CACHE
from backend.db.session_manager import db

logger = logging.getLogger(__name__)

# Track recent sync attempts to avoid redundancy
RECENT_SYNC_IDENTIFIERS: Set[str] = set()
SYNC_COOLDOWN_SECONDS = 60  # Don't re-sync same identifier within 1 minute

# Track AddressBook modification times
ADDRESSBOOK_MTIMES: Dict[str, float] = {}

def merge_duplicate_contacts(old_contact_id: int, new_contact_id: int) -> None:
    """Merge two contacts by updating all references from old to new."""
    logger.info(f"Merging duplicate contacts: {old_contact_id} -> {new_contact_id}")
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        
        # Update all messages
        cursor.execute(
            "UPDATE messages SET sender_id = ? WHERE sender_id = ?",
            (new_contact_id, old_contact_id)
        )
        messages_updated = cursor.rowcount
        
        # Update all reactions
        cursor.execute(
            "UPDATE reactions SET sender_id = ? WHERE sender_id = ?",
            (new_contact_id, old_contact_id)
        )
        reactions_updated = cursor.rowcount
        
        # Update chat participants - avoid duplicates
        cursor.execute(
            "DELETE FROM chat_participants WHERE contact_id = ? AND chat_id IN "
            "(SELECT chat_id FROM chat_participants WHERE contact_id = ?)",
            (old_contact_id, new_contact_id)
        )
        
        cursor.execute(
            "UPDATE chat_participants SET contact_id = ? WHERE contact_id = ?",
            (new_contact_id, old_contact_id)
        )
        participants_updated = cursor.rowcount
        
        # Move any identifiers from old contact (avoid duplicates)
        cursor.execute("""
            UPDATE contact_identifiers 
            SET contact_id = ? 
            WHERE contact_id = ? 
            AND identifier NOT IN (
                SELECT identifier FROM contact_identifiers WHERE contact_id = ?
            )
        """, (new_contact_id, old_contact_id, new_contact_id))
        
        # Delete remaining identifiers from old contact
        cursor.execute(
            "DELETE FROM contact_identifiers WHERE contact_id = ?",
            (old_contact_id,)
        )
        
        # Delete the old contact
        cursor.execute("DELETE FROM contacts WHERE id = ?", (old_contact_id,))
        
        logger.info(f"Merged contact {old_contact_id} into {new_contact_id}: "
                   f"{messages_updated} messages, {reactions_updated} reactions, "
                   f"{participants_updated} participants updated")

async def notify_contact_update(contact_id: int, update_type: str, data: dict):
    """Notify WebSocket clients about contact update."""
    try:
        # Import here to avoid circular imports
        from backend.routers.live_sync_router import notify_chat_update, notify_contact_update_global
        
        # Send global contact update notification
        await notify_contact_update_global(contact_id, update_type, data)
        
        # Get all chats this contact participates in
        with db.session_scope() as session:
            cursor = session.connection().connection.cursor()
            cursor.execute("""
                SELECT DISTINCT chat_id 
                FROM chat_participants 
                WHERE contact_id = ?
            """, (contact_id,))
            
            chat_ids = [row[0] for row in cursor.fetchall()]
            
            # Notify each chat about the contact update
            for chat_id in chat_ids:
                await notify_chat_update(chat_id, "contact_updated", {
                    "contact_id": contact_id,
                    "update_type": update_type,
                    **data
                })
                
        logger.info(f"Notified {len(chat_ids)} chats and global listeners about contact {contact_id} update")
        
    except Exception as e:
        logger.error(f"Failed to notify contact update: {e}")

def sync_contact_from_addressbook(identifier: str) -> Optional[int]:
    """
    Attempt to find and sync a specific contact from AddressBook databases.
    Returns the contact_id if found and synced, None otherwise.
    
    Args:
        identifier: The contact identifier (phone number or email) to search for
        
    Returns:
        contact_id if found and synced, None otherwise
    """
    # Check cooldown
    if identifier in RECENT_SYNC_IDENTIFIERS:
        logger.debug(f"Skipping sync for {identifier} - in cooldown period")
        return None
    
    logger.debug(f"Searching for contact with identifier: {identifier}")
    
    # Search all AddressBook databases
    for db_path in find_live_address_books():
        try:
            contacts = extract_contacts(db_path, is_live=True)
            
            # Normalize the target identifier for robust comparison
            norm_target = identifier.lower() if '@' in identifier else normalize_phone_number(identifier)
            from backend.etl.etl_contacts import transform_contact

            # Look for matching contact (compare normalized identifiers)
            for contact in contacts:
                transformed = transform_contact(contact, 'live_addressbook')
                if not transformed:
                    continue
                if transformed['identifier'] != norm_target:
                    continue
                logger.info(f"Found contact {contact.get('first_name', '')} {contact.get('last_name', '')} for identifier {identifier}")
                
                # Use the updated load function that handles merging and updates
                contact_id = load_single_contact_with_updates(transformed)
                if contact_id:
                    RECENT_SYNC_IDENTIFIERS.add(identifier)
                    
                    # Clean up old entries periodically
                    if len(RECENT_SYNC_IDENTIFIERS) > 1000:
                        RECENT_SYNC_IDENTIFIERS.clear()
                    
                    return contact_id
                else:
                    logger.warning(f"Failed to load contact for identifier {identifier}")
                        
        except Exception as e:
            logger.error(f"Error searching AddressBook database {db_path}: {e}")
            continue
    
    # Add to recent attempts even if not found to prevent repeated searches
    RECENT_SYNC_IDENTIFIERS.add(identifier)
    logger.debug(f"Contact not found in any AddressBook for identifier: {identifier}")
    return None

def check_addressbooks_modified() -> bool:
    """
    Check if any AddressBook databases have been modified since last check.
    
    Returns:
        True if any AddressBook has been modified, False otherwise
    """
    modified = False
    
    for db_path in find_live_address_books():
        try:
            current_mtime = os.path.getmtime(db_path)
            last_mtime = ADDRESSBOOK_MTIMES.get(db_path, 0)
            
            if current_mtime > last_mtime:
                logger.debug(f"AddressBook database modified: {db_path}")
                modified = True
                ADDRESSBOOK_MTIMES[db_path] = current_mtime
        except Exception as e:
            logger.error(f"Error checking modification time for {db_path}: {e}")
            continue
    
    return modified

def incremental_contact_sync() -> int:
    """
    Perform incremental sync of all AddressBooks.
    Only processes databases that have been modified since last sync.
    Handles contact updates and merging properly.
    
    Returns:
        Number of new/updated contacts synchronized
    """
    if not check_addressbooks_modified():
        logger.debug("No AddressBook databases have been modified")
        return 0
    
    logger.info("Starting incremental contact sync")
    
    # Clear cache to force refresh
    CONTACT_MAP_CACHE.clear()
    
    # Track updates for notification
    updated_contacts = []
    
    try:
        # Process each AddressBook database
        total_synced = 0
        
        for db_path in find_live_address_books():
            try:
                contacts = extract_contacts(db_path, is_live=True)
                logger.info(f"Extracted {len(contacts)} contacts from {db_path}")
                
                for contact in contacts:
                    try:
                        # Transform contact data
                        from backend.etl.etl_contacts import transform_contact
                        transformed = transform_contact(contact, 'live_addressbook')
                        if not transformed:
                            continue
                        
                        # Handle contact update/merge using updated load_single_contact
                        contact_id = load_single_contact_with_updates(transformed)
                        if contact_id:
                            updated_contacts.append(contact_id)
                            total_synced += 1
                            
                    except Exception as e:
                        logger.error(f"Error processing contact {contact.get('first_name', '')} {contact.get('last_name', '')}: {e}")
                        continue
                        
            except Exception as e:
                logger.error(f"Error processing AddressBook database {db_path}: {e}")
                continue
        
        logger.info(f"Incremental contact sync completed: {total_synced} contacts updated")
        
        # Notify WebSocket clients about updates (run in background)
        if updated_contacts:
            import asyncio
            import threading
            
            try:
                # Schedule notifications for updated contacts
                if threading.current_thread() is threading.main_thread():
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            for contact_id in updated_contacts:
                                asyncio.create_task(notify_contact_update(
                                    contact_id, 
                                    "name_updated", 
                                    {"source": "incremental_sync"}
                                ))
                    except RuntimeError:
                        logger.debug("No event loop available for contact notifications")
                else:
                    logger.debug("Not in main thread, skipping contact notifications")
            except Exception as e:
                logger.error(f"Failed to schedule contact notifications: {e}")
                
        return total_synced
        
    except Exception as e:
        logger.error(f"Error during incremental contact sync: {e}")
        return 0

def load_single_contact_with_updates(transformed_contact: Dict) -> Optional[int]:
    """
    Load a single contact, handling name updates and merging properly.
    
    Args:
        transformed_contact: Already transformed contact data
        
    Returns:
        contact_id if successfully loaded/updated, None otherwise
    """
    try:
        with db.session_scope() as session:
            cursor = session.connection().connection.cursor()
            
            # First, check if we have a contact with this identifier
            cursor.execute("""
                SELECT c.id, c.name 
                FROM contacts c
                JOIN contact_identifiers ci ON c.id = ci.contact_id
                WHERE ci.identifier = ?
            """, (transformed_contact['identifier'],))
            
            existing = cursor.fetchone()
            
            if existing:
                contact_id, existing_name = existing
                
                # Check if the existing contact has no real name (just phone/email)
                needs_name_update = (
                    not existing_name or 
                    existing_name == transformed_contact['identifier'] or 
                    (existing_name.startswith('+') and 
                     existing_name.replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '').isdigit())
                )
                
                if needs_name_update:
                    # Update the name
                    cursor.execute(
                        "UPDATE contacts SET name = ? WHERE id = ?",
                        (transformed_contact['name'], contact_id)
                    )
                    
                    logger.info(f"Updated contact {contact_id} name from '{existing_name}' to '{transformed_contact['name']}'")
                    
                    # Update chat names for this contact
                    update_chat_names_for_contact(contact_id, transformed_contact['name'])
                    
                    # Update cache
                    cache_key = transformed_contact['identifier'].lower() if '@' in transformed_contact['identifier'] else transformed_contact['identifier']
                    CONTACT_MAP_CACHE[cache_key] = contact_id
                    
                    return contact_id
                
                # If names differ significantly, check for potential merge
                elif existing_name != transformed_contact['name']:
                    # Check if we also have a contact with the new name
                    cursor.execute(
                        "SELECT id FROM contacts WHERE name = ? AND id != ?",
                        (transformed_contact['name'], contact_id)
                    )
                    new_name_contact = cursor.fetchone()
                    
                    if new_name_contact:
                        # Merge: keep the one with the proper name
                        logger.info(f"Merging duplicate contacts: {contact_id} -> {new_name_contact[0]}")
                        merge_duplicate_contacts(contact_id, new_name_contact[0])
                        
                        # Update cache
                        cache_key = transformed_contact['identifier'].lower() if '@' in transformed_contact['identifier'] else transformed_contact['identifier']
                        CONTACT_MAP_CACHE[cache_key] = new_name_contact[0]
                        
                        return new_name_contact[0]
                    else:
                        # Just update the name
                        cursor.execute(
                            "UPDATE contacts SET name = ? WHERE id = ?",
                            (transformed_contact['name'], contact_id)
                        )
                        logger.info(f"Updated contact {contact_id} name from '{existing_name}' to '{transformed_contact['name']}'")
                        
                        # Update chat names for this contact
                        update_chat_names_for_contact(contact_id, transformed_contact['name'])
                        
                        return contact_id
                
                # Name is already correct
                return contact_id
                
            else:
                # No existing contact, create new one
                cursor.execute(
                    "INSERT INTO contacts (name, data_source) VALUES (?, ?)",
                    (transformed_contact['name'], transformed_contact['source'])
                )
                contact_id = cursor.lastrowid
                
                # Insert contact identifier
                cursor.execute("""
                    INSERT INTO contact_identifiers 
                    (contact_id, identifier, type, is_primary)
                    VALUES (?, ?, ?, ?)
                """, (
                    contact_id,
                    transformed_contact['identifier'],
                    transformed_contact['identifier_type'],
                    True
                ))
                
                # Update cache
                cache_key = transformed_contact['identifier'].lower() if '@' in transformed_contact['identifier'] else transformed_contact['identifier']
                CONTACT_MAP_CACHE[cache_key] = contact_id
                
                # Suppressed: too noisy during initial sync
                # logger.info(f"Created new contact with ID {contact_id} for {transformed_contact['name']} ({transformed_contact['identifier']})")
                logger.debug(f"Created new contact with ID {contact_id} for {transformed_contact['name']} ({transformed_contact['identifier']})")
                return contact_id
                
    except Exception as e:
        logger.error(f"Error loading single contact with updates: {e}", exc_info=True)
        return None

def update_chat_names_for_contact(contact_id: int, new_name: str) -> None:
    """Update chat names for chats where this contact is the only other participant."""
    logger.info(f"Updating chat names for contact {contact_id} with new name '{new_name}'")
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        
        # Get contact identifiers for this contact
        cursor.execute("""
            SELECT identifier
            FROM contact_identifiers
            WHERE contact_id = ?
        """, (contact_id,))
        
        identifiers = [row[0] for row in cursor.fetchall()]
        
        # Find individual chats (not groups) where this contact is a participant
        cursor.execute("""
            SELECT DISTINCT c.id, c.chat_identifier, c.chat_name, c.is_group
            FROM chats c
            JOIN chat_participants cp ON cp.chat_id = c.id
            WHERE cp.contact_id = ? 
            AND c.is_group = 0
        """, (contact_id,))
        
        chats_to_update = cursor.fetchall()
        updated_count = 0
        
        for chat_id, chat_identifier, current_chat_name, is_group in chats_to_update:
            # For individual chats, update the chat_name to the contact's name
            # Only update if the current name is NULL, is the identifier itself, or is a phone number
            should_update = False
            
            if not current_chat_name or current_chat_name == chat_identifier:
                should_update = True
            else:
                # Check if current name is just a phone number (normalized or not)
                clean_name = current_chat_name.replace('+', '').replace('-', '').replace(' ', '').replace('(', '').replace(')', '')
                if clean_name.isdigit():
                    should_update = True
                # Also check if it matches any of the contact's identifiers
                elif current_chat_name in identifiers:
                    should_update = True
            
            if should_update:
                cursor.execute(
                    "UPDATE chats SET chat_name = ? WHERE id = ?",
                    (new_name, chat_id)
                )
                updated_count += 1
                logger.info(f"Updated chat {chat_id} name to '{new_name}'")
        
        if updated_count > 0:
            logger.info(f"Updated {updated_count} chat names for contact {contact_id}")

def reset_sync_cooldown():
    """Reset the sync cooldown cache. Useful for testing or forced refresh."""
    global RECENT_SYNC_IDENTIFIERS
    RECENT_SYNC_IDENTIFIERS.clear()
    logger.debug("Sync cooldown cache reset") 