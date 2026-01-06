#!/usr/bin/env python3
"""
Script to fix missing chat_participants entries for existing chats.
This ensures all chats are properly linked to their participants.
"""

import sys
import os
import logging

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from backend.db.session_manager import db
from backend.etl.utils import normalize_phone_number
from backend.etl.live_sync.sync_contacts import update_chat_names_for_contact

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_chat_participants():
    """Fix missing chat_participants entries by analyzing chat_identifiers and messages."""
    logger.info("Starting chat participants fix...")
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        
        # Get all chats
        cursor.execute("""
            SELECT id, chat_identifier, chat_name, is_group
            FROM chats
            ORDER BY id
        """)
        all_chats = cursor.fetchall()
        
        # Get contact map
        cursor.execute("""
            SELECT ci.identifier, c.id, c.name
            FROM contacts c
            JOIN contact_identifiers ci ON c.id = ci.contact_id
        """)
        contact_map = {}
        contact_names = {}
        for identifier, contact_id, name in cursor.fetchall():
            normalized = identifier.lower() if '@' in identifier else normalize_phone_number(identifier)
            contact_map[normalized] = contact_id
            contact_names[contact_id] = name
        
        fixed_count = 0
        chat_names_updated = 0
        
        for chat_id, chat_identifier, chat_name, is_group in all_chats:
            # Get existing participants
            cursor.execute(
                "SELECT contact_id FROM chat_participants WHERE chat_id = ?",
                (chat_id,)
            )
            existing_participants = {row[0] for row in cursor.fetchall()}
            
            # Parse chat identifier to get expected participants
            expected_participants = set()
            if chat_identifier:
                for identifier in chat_identifier.split(','):
                    if identifier:
                        contact_id = contact_map.get(identifier)
                        if contact_id:
                            expected_participants.add(contact_id)
            
            # Also check messages for additional participants
            cursor.execute("""
                SELECT DISTINCT sender_id 
                FROM messages 
                WHERE chat_id = ? AND sender_id IS NOT NULL
            """, (chat_id,))
            message_senders = {row[0] for row in cursor.fetchall()}
            expected_participants.update(message_senders)
            
            # Add missing participants
            missing_participants = expected_participants - existing_participants
            if missing_participants:
                logger.info(f"Chat {chat_id}: Adding {len(missing_participants)} missing participants")
                for contact_id in missing_participants:
                    cursor.execute(
                        "INSERT OR IGNORE INTO chat_participants (chat_id, contact_id) VALUES (?, ?)",
                        (chat_id, contact_id)
                    )
                    fixed_count += 1
            
            # Update chat name for individual chats
            if not is_group and len(expected_participants) == 1:
                participant_id = list(expected_participants)[0]
                participant_name = contact_names.get(participant_id)
                
                if participant_name and (not chat_name or chat_name == chat_identifier):
                    cursor.execute(
                        "UPDATE chats SET chat_name = ? WHERE id = ?",
                        (participant_name, chat_id)
                    )
                    chat_names_updated += 1
                    logger.info(f"Updated chat {chat_id} name to '{participant_name}'")
        
        logger.info(f"Fixed {fixed_count} missing chat_participants entries")
        logger.info(f"Updated {chat_names_updated} chat names")

def list_chats_without_participants():
    """List chats that have no participants linked."""
    logger.info("Finding chats without participants...")
    
    with db.session_scope() as session:
        cursor = session.connection().connection.cursor()
        
        cursor.execute("""
            SELECT c.id, c.chat_identifier, c.chat_name, c.is_group,
                   COUNT(cp.contact_id) as participant_count
            FROM chats c
            LEFT JOIN chat_participants cp ON c.id = cp.chat_id
            GROUP BY c.id
            HAVING participant_count = 0
            ORDER BY c.id
        """)
        
        orphaned_chats = cursor.fetchall()
        
        if orphaned_chats:
            logger.info(f"Found {len(orphaned_chats)} chats without participants:")
            for chat_id, identifier, name, is_group, _ in orphaned_chats:
                logger.info(f"  Chat {chat_id}: {name or identifier} (group: {bool(is_group)})")
        else:
            logger.info("All chats have participants linked!")
        
        return orphaned_chats

def main():
    """Run the chat participants fix."""
    logger.info("Chat Participants Fix Script")
    logger.info("=" * 50)
    
    # First, list chats without participants
    orphaned = list_chats_without_participants()
    
    if orphaned:
        logger.info("\nFixing missing participants...")
        fix_chat_participants()
        
        # Check again
        logger.info("\nVerifying fix...")
        remaining = list_chats_without_participants()
        
        if not remaining:
            logger.info("✅ All chats now have participants!")
        else:
            logger.warning(f"⚠️  Still {len(remaining)} chats without participants")
    else:
        logger.info("\n✅ No issues found - all chats have participants!")

if __name__ == "__main__":
    main() 