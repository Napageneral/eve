"""Parent-process watchdog for Electron-spawned backend.

Imported by ``backend.main`` *before* any heavy imports so that the backend
terminates if the parent Electron process dies (common on macOS when the user
quits the GUI).
"""

from __future__ import annotations

import logging
import os
import threading
import time

__all__ = ["start_parent_monitor"]


log = logging.getLogger(__name__)


def start_parent_monitor() -> None:  # noqa: D401 (simple function)
    """Spawn a daemon thread that exits this process when the parent PID dies."""

    parent_pid = int(os.environ.get("CHATSTATS_PARENT_PID", "0"))
    if parent_pid == 0:
        log.info("No parent PID specified – skipping parent monitoring")
        return

    # Make sure the parent exists before dedicating a thread to it.
    try:
        os.kill(parent_pid, 0)
    except (OSError, ProcessLookupError):
        log.warning("Parent process %s not found – skipping monitoring", parent_pid)
        return

    def _watch() -> None:
        while True:
            try:
                os.kill(parent_pid, 0)  # noqa: S40 – signal 0 == existence check
                time.sleep(5)
            except (OSError, ProcessLookupError):
                log.error("Parent process %s died – shutting down backend", parent_pid)
                os._exit(1)  # noqa: S225 – hard exit is intentional
            except Exception as exc:  # pragma: no cover – defensive
                log.warning("Error checking parent process: %s", exc)
                time.sleep(5)

    threading.Thread(target=_watch, name="parent-monitor", daemon=True).start()
    log.info("Started parent process monitor for PID %s", parent_pid) 