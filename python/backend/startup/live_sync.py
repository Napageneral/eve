"""Live-sync initialisation + watcher startup."""

from __future__ import annotations

import asyncio
import logging

__all__ = ["start"]

log = logging.getLogger(__name__)


async def start() -> None:  # noqa: D401 – simple
    """Initialise watermarks and launch the WAL watcher."""

    try:
        # Use ERROR for the first couple breadcrumbs so they surface even if INFO is filtered
        log.error("[LIVE-SYNC] start() called – Initialising watermarks …")
        from backend.etl.live_sync.state import (
            initialize_watermark_if_missing,
            initialize_rowid_watermarks_if_missing,
            get_message_rowid_watermark,
            get_attachment_rowid_watermark,
            get_watermark,
        )
        from backend.etl.live_sync.wal import start_watcher

        await asyncio.to_thread(initialize_watermark_if_missing)
        await asyncio.to_thread(initialize_rowid_watermarks_if_missing)

        try:
            rowid = get_message_rowid_watermark()
            att_rowid = get_attachment_rowid_watermark()
            ts_ns = get_watermark()
            log.error("[LIVE-SYNC] Watermarks after init rowid=%s attachment_rowid=%s ts_ns=%s", rowid, att_rowid, ts_ns)
        except Exception:
            pass

        log.error("[LIVE-SYNC] Starting WAL watcher …")
        asyncio.create_task(start_watcher())
    except Exception as exc:  # pragma: no cover – best effort
        log.error("[LIVE-SYNC] Failed to start: %s", exc) 