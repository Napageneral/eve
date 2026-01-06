"""
Database cleanup and initialization utilities.

This module handles database initialization more robustly by:
- Cleaning up any existing database locks 
- Ensuring proper WAL mode configuration
- Handling file locking issues gracefully
"""

import os
import sqlite3
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def cleanup_database_files(db_path: str) -> None:
    """
    Clean up SQLite database auxiliary files that might cause locking issues.
    
    Args:
        db_path: Path to the main database file
    """
    db_path = Path(db_path)
    auxiliary_files = [
        db_path.with_suffix('.db-wal'),  # WAL file
        db_path.with_suffix('.db-shm'),  # Shared memory file
        db_path.with_suffix('.db-journal'),  # Journal file
    ]
    
    for aux_file in auxiliary_files:
        if aux_file.exists():
            try:
                aux_file.unlink()
                logger.info(f"Removed auxiliary database file: {aux_file}")
            except OSError as e:
                logger.warning(f"Could not remove {aux_file}: {e}")

def ensure_database_directory(db_path: str) -> None:
    """
    Ensure the database directory exists.
    
    Args:
        db_path: Path to the database file
    """
    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Ensured database directory exists: {db_dir}")

def initialize_database_safely(db_path: str, clean_start: bool = False) -> None:
    """
    Initialize database with proper WAL mode and cleanup if needed.
    
    Args:
        db_path: Path to the database file
        clean_start: Whether to clean up auxiliary files before initialization
    """
    logger.info(f"Initializing database safely at: {db_path}")
    
    # Ensure directory exists
    ensure_database_directory(db_path)
    
    # Clean up auxiliary files if requested
    if clean_start:
        logger.info("Performing clean start - removing auxiliary files")
        cleanup_database_files(db_path)
    
    # Test database connectivity and set up WAL mode
    try:
        with sqlite3.connect(db_path, timeout=10.0) as conn:
            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                busy_ms = int(os.getenv("CHATSTATS_SQLITE_BUSY_TIMEOUT_MS", "60000"))
            except Exception:
                busy_ms = 60000
            conn.execute(f"PRAGMA busy_timeout={busy_ms}")
            conn.execute("PRAGMA synchronous=NORMAL")  # Balance safety and performance
            conn.execute("PRAGMA cache_size=-64000")   # 64MB cache
            
            # Test basic functionality
            result = conn.execute("SELECT 1 as test").fetchone()
            if result and result[0] == 1:
                logger.info("Database connectivity test successful")
            else:
                raise Exception("Database connectivity test failed")
                
            conn.commit()
            
    except sqlite3.Error as e:
        logger.error(f"Database initialization failed: {e}")
        if clean_start:
            raise
        else:
            # Retry with clean start
            logger.info("Retrying with clean start...")
            initialize_database_safely(db_path, clean_start=True)

def check_database_health(db_path: str) -> dict:
    """
    Check database health and return status information.
    
    Args:
        db_path: Path to the database file
        
    Returns:
        Dictionary with health check results
    """
    result = {
        "database_exists": False,
        "writable": False,
        "wal_mode": False,
        "error": None
    }
    
    try:
        if not Path(db_path).exists():
            result["error"] = "Database file does not exist"
            return result
            
        result["database_exists"] = True
        
        with sqlite3.connect(db_path, timeout=5.0) as conn:
            # Check if writable
            conn.execute("SELECT 1").fetchone()
            result["writable"] = True
            
            # Check WAL mode
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()
            if journal_mode and journal_mode[0].upper() == 'WAL':
                result["wal_mode"] = True
                
    except sqlite3.Error as e:
        result["error"] = str(e)
        
    return result 