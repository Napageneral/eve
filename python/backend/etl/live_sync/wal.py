import asyncio
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Set, List, Dict

from .state import get_watermark, set_watermark, initialize_watermark_if_missing
from .state import get_message_rowid_watermark, set_message_rowid_watermark
from .state import get_attachment_rowid_watermark, set_attachment_rowid_watermark
from .extractors import fetch_new_messages, fetch_new_attachments, get_live_chat_db_path
from .sync_messages import sync_messages
from .sync_attachments import sync_attachments
from .sync_contacts import incremental_contact_sync
from .conversation_tracker import conversation_tracker
from .timing import timed

logger = logging.getLogger(__name__)

# Constants
DEBOUNCE_MS = 50
LIVE_DB = get_live_chat_db_path()
APPLE_UNIX_EPOCH_DIFF_NS = 978307200 * 1_000_000_000

async def poll_for_changes(queue: asyncio.Queue, db_path: str, wal_path: str, polling_interval_s: float):
    """
    Periodically polls the database and WAL/SHM files for modifications.
    """
    last_db_mtime = 0.0
    last_wal_mtime = 0.0
    last_shm_mtime = 0.0

    try:
        last_db_mtime = os.path.getmtime(db_path)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"[Polling] Could not get initial mtime for {db_path}: {e}", exc_info=True)

    try:
        if os.path.exists(wal_path):
            last_wal_mtime = os.path.getmtime(wal_path)
        shm_path = db_path + "-shm"
        if os.path.exists(shm_path):
            last_shm_mtime = os.path.getmtime(shm_path)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"[Polling] Could not get initial mtime for {wal_path} or {db_path}-shm: {e}", exc_info=True)

    while True:
        await asyncio.sleep(polling_interval_s)

        current_db_mtime = 0.0
        current_wal_mtime = 0.0
        current_shm_mtime = 0.0
        changed = False

        try:
            current_db_mtime = os.path.getmtime(db_path)
            db_changed = current_db_mtime != last_db_mtime
            if db_changed:
                changed = True
            old_db_mtime = last_db_mtime
            last_db_mtime = current_db_mtime
        except FileNotFoundError:
            if last_db_mtime != 0.0:
                changed = True
            last_db_mtime = 0.0
        except Exception as e:
            logger.warning(f"[Polling] Error stating main DB {db_path}: {e}", exc_info=True)

        try:
            wal_exists_now = os.path.exists(wal_path)
            if wal_exists_now:
                current_wal_mtime = os.path.getmtime(wal_path)
                wal_changed = current_wal_mtime != last_wal_mtime
                if wal_changed:
                    changed = True
                elif last_wal_mtime == 0.0 and current_wal_mtime != 0.0:  # newly created
                    changed = True
                old_wal_mtime = last_wal_mtime
                last_wal_mtime = current_wal_mtime
            elif last_wal_mtime != 0.0:
                changed = True
                last_wal_mtime = 0.0
        except FileNotFoundError:
            if last_wal_mtime != 0.0:
                changed = True
            last_wal_mtime = 0.0
        except Exception as e:
            logger.warning(f"[Polling] Error stating WAL file {wal_path}: {e}", exc_info=True)

        # Also check the -shm file; on some macOS builds this moves even when WAL mtime doesn't
        try:
            shm_path = db_path + "-shm"
            shm_exists_now = os.path.exists(shm_path)
            if shm_exists_now:
                current_shm_mtime = os.path.getmtime(shm_path)
                shm_changed = current_shm_mtime != last_shm_mtime
                if shm_changed:
                    changed = True
                elif last_shm_mtime == 0.0 and current_shm_mtime != 0.0:
                    changed = True
                old_shm_mtime = last_shm_mtime
                last_shm_mtime = current_shm_mtime
            elif last_shm_mtime != 0.0:
                changed = True
                last_shm_mtime = 0.0
        except FileNotFoundError:
            if last_shm_mtime != 0.0:
                changed = True
            last_shm_mtime = 0.0
        except Exception as e:
            logger.warning(f"[Polling] Error stating SHM file {db_path}-shm: {e}", exc_info=True)

        if changed:
            # Debug-level breadcrumb only
            try:
                logger.debug(
                    "[LiveSync] FS change detected db_mtime=%s→%s wal_mtime=%s→%s shm_mtime=%s→%s",
                    f"{locals().get('old_db_mtime', 0.0):.3f}",
                    f"{locals().get('current_db_mtime', 0.0):.3f}",
                    f"{locals().get('old_wal_mtime', 0.0):.3f}",
                    f"{locals().get('current_wal_mtime', 0.0):.3f}",
                    f"{locals().get('last_shm_mtime', 0.0):.3f}",
                    f"{locals().get('current_shm_mtime', 0.0):.3f}",
                )
            except Exception:
                pass
            queue.put_nowait(time.time())

async def periodic_contact_sync():
    """
    Background task to sync contacts every 60 seconds.
    Runs immediately on startup, then continues with regular intervals.
    """
    logger.debug("[LiveSync] Starting periodic contact sync task")
    
    # Run immediately on startup
    try:
        synced_count = await asyncio.to_thread(incremental_contact_sync)
        if synced_count > 0:
            logger.debug(f"[LiveSync] Initial contact sync: {synced_count} contacts updated")
    except Exception as e:
        logger.error(f"[LiveSync] Initial contact sync failed: {e}", exc_info=True)
    
    # Continue with regular periodic syncs
    while True:
        await asyncio.sleep(60)
        
        try:
            synced_count = await asyncio.to_thread(incremental_contact_sync)
            if synced_count > 0:
                logger.debug(f"[LiveSync] Periodic contact sync: {synced_count} contacts updated")
        except Exception as e:
            logger.error(f"[LiveSync] Periodic contact sync failed: {e}", exc_info=True)

async def check_expired_conversations_startup():
    """
    Non-blocking startup task to check for expired conversations.
    This handles the edge case where conversations expired while the app was closed.
    """
    logger.debug("[LiveSync] Starting background check for expired conversations…")
    try:
        sealed_count = await asyncio.to_thread(
            conversation_tracker.check_and_seal_conversations
        )
        if sealed_count:
            logger.debug(f"[LiveSync] Sealed {len(sealed_count)} expired conversations on startup")
        
        # Cleanup stale checks
        try:
            cleanup_count = await asyncio.to_thread(
                conversation_tracker.cleanup_stale_checks, 24
            )
            if cleanup_count > 0:
                logger.debug(f"[LiveSync] Cleaned up {cleanup_count} stale conversation checks on startup")
        except Exception as cleanup_error:
            logger.error(f"[LiveSync] Error during startup cleanup: {cleanedup_error}", exc_info=True)
            
    except Exception as e:
        logger.error(f"[LiveSync] Error in background expired conversation check: {e}", exc_info=True)

async def watch():
    """
    Main watcher function that monitors chat.db for changes and triggers synchronization.
    """
    # Gate live sync while a bulk/global analysis is running to avoid SQLite writer contention
    # We perform two checks repeatedly until clear:
    # 1) Fast env flag
    # 2) Database latch (historic_analysis_status)
    async def _is_global_analysis_running() -> bool:
        try:
            if os.getenv("CHATSTATS_BULK_ANALYSIS_RUNNING", "0").lower() in ("1", "true", "yes", "on"):
                return True
        except Exception:
            pass
        try:
            from backend.db.session_manager import new_session
            from sqlalchemy import text
            with new_session() as s:
                row = s.execute(
                    text(
                        """
                        SELECT 1
                        FROM sqlite_master
                        WHERE type='table' AND name='historic_analysis_status'
                        """
                    )
                ).fetchone()
                if not row:
                    return False
                run = s.execute(
                    text("SELECT status FROM historic_analysis_status WHERE user_id = :uid LIMIT 1"),
                    {"uid": 1},
                ).fetchone()
                return bool(run and (run[0] or "").lower() == "running")
        except Exception:
            return False

    # Wait until no active global analysis before starting watcher tasks
    try:
        waited = 0
        while await _is_global_analysis_running():
            if waited % 30 == 0:
                logger.debug("[LiveSync] Waiting for global analysis to finish before starting live sync…")
            await asyncio.sleep(1.0)
            waited += 1
    except Exception:
        # If waiting fails, proceed to start and rely on per-batch gate below
        pass
    change_queue: asyncio.Queue = asyncio.Queue()
    
    wal_file_path = LIVE_DB + "-wal"
    polling_interval_s = 0.05

    # Start background tasks
    poller_task = asyncio.create_task(poll_for_changes(change_queue, LIVE_DB, wal_file_path, polling_interval_s))
    contact_sync_task = asyncio.create_task(periodic_contact_sync())
    startup_check_task = asyncio.create_task(check_expired_conversations_startup())
    
    # Initialize watermarks
    current_watermark_ns = get_watermark() or 0
    current_message_rowid = get_message_rowid_watermark()
    current_attachment_rowid = get_attachment_rowid_watermark()
    
    logger.debug(f"Starting watcher with message ROWID watermark: {current_message_rowid}, attachment ROWID watermark: {current_attachment_rowid}")
    
    last_sync_time = 0.0
    
    try:
        while True:
            _ = await change_queue.get()
            logger.debug("[LiveSync] BATCH dequeued (debounce=%dms)", DEBOUNCE_MS)

            # Re-check analysis latch periodically to avoid contention mid-run
            try:
                from backend.db.session_manager import new_session
                from sqlalchemy import text
                with new_session() as s:
                    row = s.execute(
                        text("SELECT status FROM historic_analysis_status WHERE user_id=:uid LIMIT 1"),
                        {"uid": 1},
                    ).fetchone()
                    if row and (row[0] or "").lower() == "running":
                        logger.debug("[LiveSync] Skipping batch due to running historic/global analysis")
                        continue
            except Exception:
                # Best-effort only; if check fails, proceed
                pass
            
            now = time.time()
            if now - last_sync_time < DEBOUNCE_MS / 1000:
                continue
            
            await asyncio.sleep(DEBOUNCE_MS / 1000 + 0.025)
            last_sync_time = time.time()
            
            timings: Dict[str, float] = {}
            batch_imported = 0
            batch_chats = 0
            
            try:
                # Fetch new messages and attachments
                with timed("fetch_messages_by_rowid", timings):
                    new_messages, new_message_rowid = fetch_new_messages(current_message_rowid, current_watermark_ns)
                    logger.debug(
                        "[LiveSync] FETCH messages result count=%d max_rowid=%s delta=%s",
                        len(new_messages),
                        new_message_rowid,
                        (new_message_rowid - (current_message_rowid or 0)) if isinstance(new_message_rowid, int) else "n/a",
                    )
                with timed("fetch_attachments_by_rowid", timings):
                    new_attachments, new_attachment_rowid = fetch_new_attachments(current_attachment_rowid)
                    logger.debug(
                        "[LiveSync] FETCH attachments result count=%d max_rowid=%s delta=%s",
                        len(new_attachments),
                        new_attachment_rowid,
                        (new_attachment_rowid - (current_attachment_rowid or 0)) if isinstance(new_attachment_rowid, int) else "n/a",
                    )
                
                if not new_messages and not new_attachments:
                    continue
                
                # Process messages
                if new_messages:
                    with timed("sync_messages", timings):
                        imported_count, chat_counts = sync_messages(new_messages)
                    batch_imported = imported_count
                    batch_chats = len(chat_counts or {})
                    
                    prev_rowid = current_message_rowid
                    current_message_rowid = new_message_rowid
                    set_message_rowid_watermark(current_message_rowid)
                    logger.debug("[LiveSync] WATERMARK commit rowid %s→%s", prev_rowid, current_message_rowid)
                    
                    # Update conversation tracking
                    if imported_count > 0:
                        with timed("update_conversation_tracking", timings):
                            try:
                                chat_latest_timestamps = {}
                                
                                for msg in new_messages:
                                    chat_id = msg.get('chat_id')
                                    if not chat_id:
                                        continue
                                        
                                    if isinstance(msg.get('date'), (int, float)):
                                        apple_ns = msg['date']
                                        unix_ns = apple_ns + APPLE_UNIX_EPOCH_DIFF_NS
                                        msg_timestamp = datetime.fromtimestamp(unix_ns / 1_000_000_000, tz=timezone.utc)
                                        
                                        if chat_id not in chat_latest_timestamps or msg_timestamp > chat_latest_timestamps[chat_id]:
                                            chat_latest_timestamps[chat_id] = msg_timestamp
                                
                                if chat_latest_timestamps:
                                    await asyncio.to_thread(
                                        conversation_tracker.batch_update_last_messages,
                                        chat_latest_timestamps
                                    )
                            except Exception as e:
                                logger.error(f"Error updating conversation tracking: {e}", exc_info=True)
                    
                    # Update timestamp watermark
                    if imported_count > 0:
                        valid_message_dates_ns = [m['date'] for m in new_messages if isinstance(m.get('date'), int)]
                        if valid_message_dates_ns:
                            new_watermark_ns_candidate = max(valid_message_dates_ns)
                            with timed("set_timestamp_watermark", timings):
                                prev_ts = current_watermark_ns
                                set_watermark(new_watermark_ns_candidate)
                                current_watermark_ns = new_watermark_ns_candidate
                                logger.debug("[LiveSync] WATERMARK commit timestamp_ns %s→%s", prev_ts, current_watermark_ns)
                
                # Process attachments
                if new_attachments:
                    with timed("sync_attachments", timings):
                        _ = sync_attachments(new_attachments)
                    
                    current_attachment_rowid = new_attachment_rowid
                    set_attachment_rowid_watermark(current_attachment_rowid)
                
                # Single high-signal success log per batch with ingestion timing
                if batch_imported > 0:
                    total_ms = int(round(sum(timings.values()) * 1000))
                    ingest_ms = int(round((timings.get("sync_messages", 0.0)) * 1000))
                    logger.info(
                        "[LiveSync] ingest ok: imported=%d chats=%d ingest_ms=%d total_ms=%d",
                        batch_imported,
                        batch_chats,
                        ingest_ms,
                        total_ms,
                    )

            except Exception as e:
                logger.error(f"Error in live sync processing loop: {e}", exc_info=True)
    
    except Exception as e:
        logger.error(f"Main processing loop error: {e}", exc_info=True)
    finally:
        # Clean up tasks
        tasks_to_cleanup = [
            ("poller", poller_task),
            ("contact_sync", contact_sync_task),
            ("startup_check", startup_check_task)
        ]
        
        for task_name, task in tasks_to_cleanup:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug(f"[{task_name.title()}] Task cancelled as expected")
                except Exception as e:
                    logger.error(f"[{task_name.title()}] Error during task shutdown: {e}", exc_info=True)

async def start_watcher():
    """Helper function to start the watcher."""
    logger.debug("[LIVE-SYNC] start_watcher() called - live sync is starting!")
    try:
        await watch()
    except Exception as e:
        logger.error(f"Failed to start watcher: {e}", exc_info=True) 