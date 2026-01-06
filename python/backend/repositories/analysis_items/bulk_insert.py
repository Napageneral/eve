from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = ["bulk_insert_items"]


def bulk_insert_items(
    session: Session,
    *,
    table: str,
    rows: List[Dict[str, Any]],
) -> int:
    """Generic helper to **bulk INSERT raw rows**.

    Parameters
    ----------
    session : Session
        Active SQLAlchemy session.
    table : str
        Destination table name (already validated by caller).
    rows : list[dict[str, Any]]
        Prepared parameter dictionaries. Each key must match a column name.

    Returns
    -------
    int
        Number of rows inserted (0 if *rows* is empty).
    """
    if not rows:
        return 0

    col_list = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in col_list)
    column_names = ", ".join(col_list)

    session.execute(
        text(f"INSERT INTO {table} ({column_names}) VALUES ({placeholders})"),
        rows,
    )
    return len(rows) 