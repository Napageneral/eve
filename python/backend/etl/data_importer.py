import os
import logging
from datetime import datetime
from typing import Optional
from time import time

from backend.etl.etl_contacts import etl_contacts, etl_live_contacts
from backend.etl.etl_chats import etl_chats
from backend.etl.etl_conversations import (
    etl_conversations,                      # Incremental or “from-scratch” forward logic
    etl_conversations_fresh_split_compare   # Our “backup import” approach
)
from backend.etl.etl_messages import etl_messages
from backend.etl.etl_attachments import etl_attachments
from backend.etl.iphone_backup import get_sms_db_path, get_address_book_db_path
from backend.db.session_manager import db
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Concurrency guard – prevents duplicate live imports from being triggered
# simultaneously by multiple HTTP requests.  We use a simple module-level
# threading.Lock so that *any* second caller in the same Python process will
# bail out immediately instead of re-running the entire ETL.
# ---------------------------------------------------------------------------
from threading import Lock

_live_import_lock = Lock()

def _db_is_empty() -> bool:
    """Return True if this is a blank/first-run DB (no chats/messages/conversations)."""
    with db.session_scope() as session:
        return bool(session.execute(text("""
            SELECT 
              (SELECT COUNT(*) FROM messages)=0
              AND (SELECT COUNT(*) FROM conversations)=0
              AND (SELECT COUNT(*) FROM chats)=0
        """)).scalar())

def get_live_chat_db_path() -> str:
    override = os.getenv("EVE_SOURCE_CHAT_DB") or os.getenv("CHATSTATS_SOURCE_CHAT_DB")
    if override:
        return os.path.expanduser(override)
    home = os.path.expanduser('~')
    return os.path.join(home, 'Library', 'Messages', 'chat.db')

def import_backup_data(backup_path: str):
    """
    Imports from a backup that may include older/interleaved messages.
    We then do a 'fresh split & compare' to keep existing conversation IDs 
    whenever intervals match exactly, and only replace intervals that changed.
    """
    t0 = time()
    sms_db_path = get_sms_db_path(backup_path)
    address_book_db_path = get_address_book_db_path(backup_path)
    if not sms_db_path or not address_book_db_path:
        logger.error("Unable to locate backup databases")
        return
    
    print("\nBACKUP IMPORT")
    print("=" * 50)
    
    t = time()
    etl_contacts(address_book_db_path)
    print(f"[Backup] Contacts ETL:      {round(time() - t, 2):>6}s")
    
    t = time()
    etl_chats(sms_db_path)
    print(f"[Backup] Chats ETL:         {round(time() - t, 2):>6}s")
    
    t = time()
    etl_messages(sms_db_path)
    print(f"[Backup] Messages ETL:      {round(time() - t, 2):>6}s")

    # Ensure helpful indexes before conversation processing
    t = time()
    ensure_import_indexes()
    print(f"[Backup] Ensured indexes:   {round(time() - t, 2):>6}s")
    
    t = time()
    etl_attachments(sms_db_path)
    print(f"[Backup] Attachments ETL:   {round(time() - t, 2):>6}s")
    
    # Instead of calling the incremental 'etl_conversations', we do a 
    # "fresh split & compare" for each chat so we only replace intervals that truly changed.
    t = time()
    etl_conversations_fresh_split_compare()
    print(f"[Backup] Conversations (fresh split) ETL: {round(time() - t, 2):>6}s")
    
    total_time = round(time() - t0, 2)
    print("-" * 50)
    print(f"Total Backup Import Time:   {total_time:>6}s")
    print("=" * 50)

def ensure_import_indexes():
    """Create indexes that accelerate frequent ETL read paths if missing.
    Uses raw SQL via our session manager.
    """
    with db.session_scope() as session:
        session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_guid ON messages(guid)"))
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_id, timestamp)"))
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id)"))
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_conversations_chat_end ON conversations(chat_id, end_time DESC)"))
        session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_reactions_guid ON reactions(guid)"))
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_reactions_original_guid ON reactions(original_message_guid)"))
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_contact_ident ON contact_identifiers(identifier)"))
        # Guardrail: enforce uniqueness on (identifier, type) once duplicates are gone
        dup = session.execute(text(
            """
            SELECT 1
            FROM contact_identifiers
            GROUP BY identifier, type
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )).fetchone()
        if not dup:
            session.execute(text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_contact_identifiers_identifier_type
                ON contact_identifiers(identifier, type)
                """
            ))
        else:
            logger.warning("Duplicate contact identifiers exist; skipping unique index creation until cleanup")

def import_live_data(
    since_date: Optional[datetime] = None,
    race_mode: bool = False,
    *,
    include_contacts: bool = True,
):
    """
    For the live DB import:
      - If no since_date, we do a "from-scratch" forward logic (extract all).
      - If since_date is provided, we do the incremental approach, only new messages.
    """
    # --------------------------------------------------
    # Acquire non-blocking lock – if we fail we know an import
    # is already in progress and we simply log & exit early.
    # --------------------------------------------------
    if not _live_import_lock.acquire(blocking=False):
        logger.warning("Live import already running – duplicate trigger ignored")
        return

    try:
        t0 = time()
        sms_db_path = get_live_chat_db_path()
        if not os.path.exists(sms_db_path):
            logger.error(f"Messages database not found at {sms_db_path}")
            return
        
        print("\nLIVE IMPORT")
        print("=" * 50)
        if since_date:
            print(f"Importing data since: {since_date.isoformat()}")
        else:
            print("Importing all live data (no since_date).")

        # Auto-enable race mode for very first import on an empty DB
        if since_date is None and not race_mode and _db_is_empty():
            race_mode = True

        if include_contacts:
            t = time()
            etl_live_contacts()
            print(f"[Live] Contacts ETL:      {round(time() - t, 2):>6}s")
        else:
            print("[Live] Contacts ETL:      (skipped)")

        t = time()
        etl_chats(sms_db_path)
        print(f"[Live] Chats ETL:         {round(time() - t, 2):>6}s")

        if race_mode:
            print("[Live] *** RACE MODE ON (unsafe PRAGMAs + dropping indexes) ***")
            drop_import_indexes()

        t = time()
        etl_messages(sms_db_path, since_date, race_mode=race_mode)
        print(f"[Live] Messages ETL:      {round(time() - t, 2):>6}s")

        # Ensure indexes *before* conversations so they can leverage (chat_id, timestamp)
        t = time()
        ensure_import_indexes()
        print(f"[Live] Ensured indexes:   {round(time() - t, 2):>6}s")

        t = time()
        etl_attachments(sms_db_path, since_date)
        print(f"[Live] Attachments ETL:   {round(time() - t, 2):>6}s")

        # Use the normal "etl_conversations" incremental approach for live data
        # (or from-scratch if no since_date).
        t = time()
        etl_conversations(since_date=since_date, race_mode=race_mode)
        print(f"[Live] Conversations ETL: {round(time() - t, 2):>6}s")

        if race_mode:
            # PRAGMAs back to safe settings
            restore_safe_pragmas()
            print("[Live] *** RACE MODE OFF → WAL/NORMAL restored ***")

        total_time = round(time() - t0, 2)
        print("-" * 50)
        print(f"Total Live Import Time:   {total_time:>6}s")
        print("=" * 50)

    finally:
        # Always release the lock so future imports can run
        try:
            _live_import_lock.release()
        except Exception:
            pass

def drop_import_indexes():
    """Drop write-heavy indexes before bulk load (we recreate them after)."""
    with db.session_scope() as session:
        session.execute(text("DROP INDEX IF EXISTS idx_messages_guid"))
        session.execute(text("DROP INDEX IF EXISTS idx_messages_chat_ts"))
        session.execute(text("DROP INDEX IF EXISTS idx_messages_conversation"))
        session.execute(text("DROP INDEX IF EXISTS idx_conversations_chat_end"))
        session.execute(text("DROP INDEX IF EXISTS idx_reactions_guid"))
        session.execute(text("DROP INDEX IF EXISTS idx_reactions_original_guid"))
        session.execute(text("DROP INDEX IF EXISTS idx_contact_ident"))

def restore_safe_pragmas():
    """Return DB to fast & durable settings after race mode."""
    with db.session_scope() as session:
        session.execute(text("PRAGMA foreign_keys=ON"))
        session.execute(text("PRAGMA journal_mode=WAL"))
        session.execute(text("PRAGMA synchronous=NORMAL"))
        session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
