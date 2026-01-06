"""Database startup helpers: migrations + seed.

These run synchronously in a threadpool so they do not block the FastAPI event
loop.  They are designed to be invoked from an ``async`` context like::

    await database.apply_migrations()
    await database.seed()
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from backend.config import DB_PATH

__all__ = ["apply_migrations", "seed"]

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alembic migrations ---------------------------------------------------------
# ---------------------------------------------------------------------------


async def apply_migrations() -> None:
    """Apply Alembic migrations in a background thread."""

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _apply_migrations_sync)


def _apply_migrations_sync() -> None:
    start = time.time()
    try:
        from alembic.config import Config  # local import – heavy
        from alembic import command

        base_dir: Path = Path(__file__).resolve().parent.parent  # backend/
        if getattr(sys, "frozen", False):  # PyInstaller bundle
            base_dir = Path(sys.executable).parent

        cfg = Config(str(base_dir / "alembic.ini"))
        cfg.set_main_option("script_location", str(base_dir / "alembic"))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{DB_PATH}")

        command.upgrade(cfg, "head")
        log.info("[MIGRATION] Completed in %.3fs", time.time() - start)
    except Exception as exc:
        # CRITICAL ERROR LOGGING - Make migration failures IMPOSSIBLE to miss
        elapsed = time.time() - start
        log.critical("=" * 80)
        log.critical("DATABASE MIGRATION FAILED - APPLICATION CANNOT START")
        log.critical("=" * 80)
        log.critical("Error: %s", str(exc))
        log.critical("Database path: %s", DB_PATH)
        log.critical("Time elapsed: %.3fs", elapsed)
        log.critical("=" * 80)
        
        # Also print to stderr to ensure Electron sees it
        error_msg = f"\n{'=' * 80}\nCRITICAL: DATABASE MIGRATION FAILED\n{exc}\n{'=' * 80}\n"
        print(error_msg, file=sys.stderr, flush=True)
        
        # Log the full traceback
        log.exception("[MIGRATION] Failed after %.3fs", elapsed)
        raise


# ---------------------------------------------------------------------------
# Database seeding -----------------------------------------------------------
# ---------------------------------------------------------------------------


async def seed() -> None:  # noqa: D401 – simple
    """Seed function - no longer needed.
    
    Context definitions and prompts are now managed by the Eve system.
    Historical data is preserved in the database.
    """
    log.info("[SEED] Skipped - Eve system manages prompts and contexts") 