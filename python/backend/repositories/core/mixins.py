"""Mixins for common repository functionality."""
import json
import logging
from typing import Any, Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)

class JSONFieldMixin:
    """Mixin for repositories that handle JSON fields."""
    
    @staticmethod
    def safe_json_loads(data: Any, default: Any = None) -> Any:
        """Safely parse JSON using BaseRepository.parse_json."""
        from .base import BaseRepository
        parsed = BaseRepository.parse_json(data)
        return parsed if parsed is not None else default

    @classmethod
    def prepare_json_fields(cls, data: Dict[str, Any], json_fields: List[str]) -> Dict[str, Any]:
        """Prepare dictionary with JSON fields serialized."""
        result = data.copy()
        for field in json_fields:
            if field in result and result[field] is not None:
                if isinstance(result[field], (dict, list)):
                    result[field] = json.dumps(result[field])
        return result

class TimestampMixin:
    """Mixin for handling timestamp fields."""
    
    @staticmethod
    def add_timestamps(data: Dict[str, Any], update_only: bool = False) -> Dict[str, Any]:
        """Add created_at/updated_at timestamps."""
        now = datetime.utcnow()
        data["updated_at"] = now
        if not update_only:
            data["created_at"] = now
        return data

class LoggingMixin:
    """Minimal logging for production, verbose for debug."""
    
    @classmethod
    def log_operation(cls, operation: str, details: Dict[str, Any] = None, level: str = "debug"):
        """Centralized operation logging."""
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[{cls.__name__}] {operation}: {details or {}}")
        elif level == "info":
            logger.info(f"[{cls.__name__}] {operation}") 