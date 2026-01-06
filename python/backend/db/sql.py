from typing import Any, Dict, List, Optional, TypeVar, Union
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
import time
import random

T = TypeVar('T')

def fetch_scalar(
    session: Session, 
    sql: str, 
    params: Optional[Dict[str, Any]] = None
) -> Optional[Any]:
    """
    Execute a text() statement and return the first column of the first row,
    or None if no row is found.
    """
    return session.execute(text(sql), params or {}).scalars().first()

def fetch_one(
    session: Session, 
    sql: str, 
    params: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Execute a text() statement and return the first row as a dictionary,
    or None if no row is found.
    """
    result = session.execute(text(sql), params or {})
    row = result.first()
    return dict(row._mapping) if row else None

def fetch_all(
    session: Session, 
    sql: str, 
    params: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Execute a text() statement and return all rows as a list of dictionaries.
    """
    result = session.execute(text(sql), params or {})
    return [dict(row._mapping) for row in result]

LOCK_STRINGS = ("database is locked", "database is busy", "db is locked")

def execute_write(
    session: Session,
    sql: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    max_retries: int = 6,
) -> int:
    """
    Execute a write operation (INSERT, UPDATE, DELETE) with retries on transient SQLite lock.
    Returns the number of rows affected.
    """
    for attempt in range(max_retries):
        try:
            result = session.execute(text(sql), params or {})
            return result.rowcount
        except OperationalError as e:
            msg = str(e).lower()
            if any(s in msg for s in LOCK_STRINGS):
                try:
                    session.rollback()
                except Exception:
                    pass
                sleep = min(0.8, 0.05 * (2 ** attempt)) + random.random() * 0.05
                time.sleep(sleep)
                continue
            raise

def execute_many(
    session: Session,
    sql: str,
    params_list: List[Dict[str, Any]],
    *,
    max_retries: int = 6,
) -> int:
    """
    Execute a write operation with multiple parameter sets with retries on lock.
    """
    if not params_list:
        return 0
    for attempt in range(max_retries):
        try:
            result = session.execute(text(sql), params_list)
            return result.rowcount
        except OperationalError as e:
            msg = str(e).lower()
            if any(s in msg for s in LOCK_STRINGS):
                try:
                    session.rollback()
                except Exception:
                    pass
                sleep = min(0.8, 0.05 * (2 ** attempt)) + random.random() * 0.05
                time.sleep(sleep)
                continue
            raise

def execute_write_and_return_id(
    session: Session,
    sql: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    max_retries: int = 6,
) -> Optional[int]:
    """
    Execute a write operation that returns an ID (typically INSERT ... RETURNING id)
    with retries on SQLite lock.
    """
    for attempt in range(max_retries):
        try:
            result = session.execute(text(sql), params or {})
            return result.scalar_one_or_none()
        except OperationalError as e:
            msg = str(e).lower()
            if any(s in msg for s in LOCK_STRINGS):
                try:
                    session.rollback()
                except Exception:
                    pass
                sleep = min(0.8, 0.05 * (2 ** attempt)) + random.random() * 0.05
                time.sleep(sleep)
                continue
            raise