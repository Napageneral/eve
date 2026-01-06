"""Base repository with common raw-SQL helpers.

This module centralizes small utility wrappers around backend.db.sql so that
all other repositories can inherit and benefit from consistent, concise
helpers.  Absolutely no ORM querying should be performed here – raw SQL only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from backend.db.sql import (
    fetch_one as _fetch_one,
    fetch_all as _fetch_all,
    fetch_scalar as _fetch_scalar,
    execute_write as _execute_write,
)

logger = logging.getLogger(__name__)


class BaseRepository:
    """Common database helpers for repository subclasses.

    All methods are static so they can be used directly from child classes
    without instantiation.  Only raw SQL is used underneath, delegating to the
    utility functions in ``backend.db.sql``.
    """

    # ------------------------------------------------------------------
    # Thin wrappers around backend.db.sql helpers
    # ------------------------------------------------------------------

    @staticmethod
    def fetch_one(
        session: Session, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Return the first row (as ``dict``) or ``None``."""
        return _fetch_one(session, sql, params)

    @staticmethod
    def fetch_all(
        session: Session, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Return *all* rows (as list of ``dict``)."""
        return _fetch_all(session, sql, params)

    @staticmethod
    def fetch_scalar(
        session: Session, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Return a single scalar value (first column of first row)."""
        return _fetch_scalar(session, sql, params)

    @staticmethod
    def execute(
        session: Session, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> int:
        """Execute an INSERT/UPDATE/DELETE and return affected-row count."""
        return _execute_write(session, sql, params)

    # ------------------------------------------------------------------
    # Common CRUD operations
    # ------------------------------------------------------------------

    @staticmethod
    def get_by_id(session: Session, table: str, record_id: Any) -> Optional[Dict[str, Any]]:
        """Get a single record by ID."""
        sql = f"SELECT * FROM {table} WHERE id = :id"
        return BaseRepository.fetch_one(session, sql, {"id": record_id})

    @staticmethod
    def get_by_field(
        session: Session, table: str, field: str, value: Any
    ) -> Optional[Dict[str, Any]]:
        """Get a single record by any field."""
        sql = f"SELECT * FROM {table} WHERE {field} = :value LIMIT 1"
        return BaseRepository.fetch_one(session, sql, {"value": value})

    @staticmethod
    def get_all(session: Session, table: str, order_by: str = "id") -> List[Dict[str, Any]]:
        """Get all records from a table."""
        sql = f"SELECT * FROM {table} ORDER BY {order_by}"
        return BaseRepository.fetch_all(session, sql)

    @staticmethod
    def insert_returning_id(
        session: Session, table: str, data: Dict[str, Any]
    ) -> Optional[int]:
        """Insert a record and return its ID."""
        if not data:
            raise ValueError("Data dictionary cannot be empty")
        
        columns = ", ".join(data.keys())
        placeholders = ", ".join(f":{key}" for key in data.keys())
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING id"
        
        result = BaseRepository.fetch_one(session, sql, data)
        return result["id"] if result else None

    @staticmethod
    def update_by_id(
        session: Session, table: str, record_id: Any, data: Dict[str, Any]
    ) -> int:
        """Update a record by ID and return affected row count."""
        if not data:
            raise ValueError("Data dictionary cannot be empty")
        
        set_clauses = ", ".join(f"{col} = :{col}" for col in data.keys())
        sql = f"UPDATE {table} SET {set_clauses} WHERE id = :id"
        
        params = data.copy()
        params["id"] = record_id
        return BaseRepository.execute(session, sql, params)

    # ------------------------------------------------------------------
    # Insert-or-update convenience (aka *upsert*)
    # ------------------------------------------------------------------

    @staticmethod
    def upsert(
        session: Session,
        table: str,
        matching_cols: Dict[str, Any],
        data: Dict[str, Any],
    ) -> int:
        """Simple *upsert* helper using two SQL statements (SELECT → UPDATE/INSERT).

        Because SQLite (and even Postgres in some versions) require explicit
        `ON CONFLICT` clauses that vary by table, we implement a generic
        *upsert* pattern in Python:

        1. Perform a lightweight ``SELECT id`` to see if a row with
           *matching_cols* exists.
        2. If found, call :py:meth:`update_by_id` with the supplied *data* and
           return the existing ``id``.
        3. Otherwise, insert a new record that merges *matching_cols* and *data*
           and return the newly created ``id``.
        """

        if not matching_cols:
            raise ValueError("matching_cols cannot be empty for upsert()")

        # 1) Look for existing row
        where_clause = " AND ".join(f"{col} = :{col}" for col in matching_cols)
        select_sql = f"SELECT id FROM {table} WHERE {where_clause} LIMIT 1"

        existing_row = BaseRepository.fetch_one(session, select_sql, matching_cols)

        if existing_row:
            # UPDATE path – do *not* modify matching columns
            BaseRepository.update_by_id(session, table, existing_row["id"], data)
            return existing_row["id"]

        # INSERT path – merge dictionaries so matching columns are included
        insert_data = {**matching_cols, **data}
        return BaseRepository.insert_returning_id(session, table, insert_data)

    # ------------------------------------------------------------------
    # Convenience utilities
    # ------------------------------------------------------------------

    @staticmethod
    def exists(session: Session, table: str, **conditions: Any) -> bool:
        """Generic *exists* helper using simple equality conditions.

        Example::

            BaseRepository.exists(session, "reports", id=123)
        """
        if not conditions:
            raise ValueError("At least one condition must be supplied")

        where_clauses = " AND ".join(f"{col} = :{col}" for col in conditions)
        sql = f"SELECT 1 FROM {table} WHERE {where_clauses} LIMIT 1"
        return BaseRepository.fetch_scalar(session, sql, conditions) is not None

    @staticmethod
    def count(session: Session, table: str, **conditions: Any) -> int:
        """Count records matching conditions."""
        if conditions:
            where_clauses = " AND ".join(f"{col} = :{col}" for col in conditions)
            sql = f"SELECT COUNT(*) FROM {table} WHERE {where_clauses}"
            return BaseRepository.fetch_scalar(session, sql, conditions)
        else:
            sql = f"SELECT COUNT(*) FROM {table}"
            return BaseRepository.fetch_scalar(session, sql)

    # ------------------------------------------------------------------
    # Parsing helpers (datetime / JSON)
    # ------------------------------------------------------------------

    @staticmethod
    def parse_datetime(dt_value: Any) -> Optional[datetime]:
        """Best-effort ISO8601 → ``datetime`` parser (UTC-aware when possible)."""
        if isinstance(dt_value, datetime):
            return dt_value
        if isinstance(dt_value, str):
            try:
                # Allow trailing ``Z`` to indicate UTC
                return datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
            except ValueError:
                logger.debug("Unable to parse datetime: %s", dt_value)
        return None

    @staticmethod
    def parse_json(json_value: Any) -> Union[Dict[str, Any], List[Any], Any]:
        """Best-effort JSON deserialization.

        If the input is a string, the function attempts ``json.loads`` and
        returns the resulting Python object.  When parsing fails, or for
        non-string inputs, the value is returned unchanged so that callers can
        decide how to handle it.
        """
        if isinstance(json_value, str):
            try:
                return json.loads(json_value)
            except json.JSONDecodeError:
                logger.debug("Unable to parse JSON value: %s", json_value[:100])
        return json_value

    @staticmethod
    def dumps(data: Any) -> str:
        """Serialize Python object to JSON string."""
        return json.dumps(data) if data else "{}"

    @staticmethod
    def convert_timestamp(dt_obj: Any) -> int:
        """Convert datetime to millisecond timestamp."""
        if not dt_obj:
            return 0
        try:
            if isinstance(dt_obj, str):
                dt_obj = BaseRepository.parse_datetime(dt_obj)
            if isinstance(dt_obj, datetime):
                return int(dt_obj.timestamp() * 1000)
        except (ValueError, AttributeError):
            pass
        return 0 