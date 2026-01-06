from __future__ import annotations

"""Common utilities for building FastAPI routers.

This module centralises boilerplate that is currently repeated across
all router modules:

* `safe_endpoint` — a decorator that wraps endpoint functions in a generic
  try/except/HTTPException block with proper error logging.  Importantly this
  decorator **does not swallow** `HTTPException` raised by the handler itself;
  it only converts *unexpected* exceptions into a generic 500 response and
  logs them using the standard application logger.
* `create_router` — a very small convenience wrapper around
  `fastapi.APIRouter` that enforces a consistent signature (`prefix`, `tags`)
  while still allowing caller-supplied keyword arguments.
* `to_dict` — utility for converting database objects to dictionaries with field selection
* `log_simple` — simplified logging helper
* Re-exported common imports for convenience

Usage example (inside any router module):

```python
from backend.routers.common import (
    create_router, safe_endpoint, to_dict, log_simple,
    HTTPException, Query, Depends, BaseModel, text, Session
)

router = create_router("/chats", "Chats – data")

@router.get("/{chat_id}")
@safe_endpoint
async def get_chat(chat_id: int):
    log_simple(f"Getting chat {chat_id}")
    # ... logic ...
```

Keeping all shared helpers in one place allows us to remove ~8–10 lines of
boilerplate per endpoint and roughly 6–8 import lines per router module.
"""

from functools import wraps
import asyncio
import logging
from typing import Any, Awaitable, Callable, Iterable, List, Union, Dict, Optional

# Re-export commonly used imports for convenience
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from backend.db.session_manager import db, get_db

__all__ = [
    "create_router",
    "safe_endpoint", 
    "to_dict",
    "log_simple",
    "error_response",
    # Re-exported for convenience
    "APIRouter",
    "HTTPException", 
    "Query",
    "Depends",
    "BaseModel",
    "text",
    "Session",
    "db",
    "get_db",
]

# Type helpers
TFunc = Callable[..., Union[Awaitable[Any], Any]]


def _wrap_sync(func: TFunc) -> TFunc:  # type: ignore[override]
    """Return a sync wrapper with unified error handling."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any):  # type: ignore[override]
        try:
            return func(*args, **kwargs)
        except HTTPException:
            # Let intentionally-raised HTTP errors bubble up unchanged.
            raise
        except Exception as exc:  # noqa: BLE001  (broad OK for generic handler)
            logging.exception("Unhandled error in %s: %s", func.__name__, exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

    return wrapper  # type: ignore[return-value]


def _wrap_async(func: TFunc) -> TFunc:  # type: ignore[override]
    """Return an async wrapper with unified error handling."""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any):  # type: ignore[override]
        try:
            return await func(*args, **kwargs)  # type: ignore[misc]
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logging.exception("Unhandled error in %s: %s", func.__name__, exc)
            raise HTTPException(status_code=500, detail="Internal server error") from exc

    return wrapper  # type: ignore[return-value]


def safe_endpoint(func: TFunc) -> TFunc:  # type: ignore[override]
    """Decorator that converts uncaught exceptions into HTTP 500 responses.

    It logs any unexpected exception with `logging.exception` so we keep the
    full traceback while returning a generic error to the client.
    """

    if asyncio.iscoroutinefunction(func):
        return _wrap_async(func)
    return _wrap_sync(func)


# ---------------------------------------------------------------------------
# Router factory helper
# ---------------------------------------------------------------------------

def create_router(prefix: str, tags: Union[str, Iterable[str]], **kwargs: Any) -> APIRouter:
    """Factory that standardises `APIRouter` instantiation.

    Parameters
    ----------
    prefix:
        The URL prefix for the router, e.g. "/chats".
    tags:
        Either a single tag or an iterable of tags to group the endpoints in
        the OpenAPI schema / documentation UI.
    **kwargs:
        Any extra keyword arguments accepted by `fastapi.APIRouter`.
    """

    if isinstance(tags, str):
        tags = [tags]
    else:
        tags = list(tags)  # Ensure we pass a list to FastAPI for JSON serialisation.

    return APIRouter(prefix=prefix, tags=tags, **kwargs)


# ---------------------------------------------------------------------------
# Response construction utilities
# ---------------------------------------------------------------------------

def to_dict(obj: Any, fields: List[str], transforms: Optional[Dict[str, Callable]] = None) -> Dict[str, Any]:
    """Convert database object to dictionary with field selection and transforms.
    
    Parameters
    ----------
    obj:
        Database object or any object with attributes
    fields:
        List of field names to include in the result
    transforms:
        Optional dict mapping field names to transform functions
        
    Examples
    --------
    >>> to_dict(report, ['id', 'title', 'created_at'], 
    ...         {'created_at': lambda obj: obj.created_at.isoformat()})
    {'id': 1, 'title': 'My Report', 'created_at': '2024-01-01T00:00:00'}
    """
    result = {}
    transforms = transforms or {}
    
    for field in fields:
        if field in transforms:
            result[field] = transforms[field](obj)
        else:
            result[field] = getattr(obj, field, None)
    
    return result


# ---------------------------------------------------------------------------
# Logging utilities  
# ---------------------------------------------------------------------------

def log_simple(message: str, level: str = "info") -> None:
    """Simplified logging helper that removes verbosity.
    
    Parameters
    ----------
    message:
        Log message to write
    level:
        Log level: 'info', 'warning', 'error', 'debug'
    """
    logger = logging.getLogger("backend.routers.system.live_sync")
    getattr(logger, level.lower())(message)


# ---------------------------------------------------------------------------
# Error handling utilities
# ---------------------------------------------------------------------------

def error_response(status_code: int, detail: str) -> None:
    """Standard error response helper."""
    raise HTTPException(status_code=status_code, detail=detail) 