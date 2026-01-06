"""Generic repository patterns to eliminate CRUD boilerplate."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .base import BaseRepository
from .exceptions import RecordNotFoundError, DatabaseError


class GenericRepository(BaseRepository):
    """Base class for simple CRUD repositories.
    
    Subclasses just need to set TABLE and optionally ID_COL.
    
    Example:
        class ContactRepository(GenericRepository):
            TABLE = "contacts"
    """
    
    TABLE: str = ""  # Must be set by subclasses
    ID_COL: str = "id"  # Override if using different ID column
    
    @classmethod
    def get_by_id(cls, session: Session, record_id: Any) -> Optional[Dict[str, Any]]:
        """Get record by ID."""
        return super().get_by_id(session, cls.TABLE, record_id)
    
    @classmethod  
    def get_all(cls, session: Session, order_by: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all records."""
        order_by = order_by or cls.ID_COL
        return super().get_all(session, cls.TABLE, order_by)
    
    @classmethod
    def create(cls, session: Session, data: Dict[str, Any]) -> Optional[int]:
        """Create record and return ID."""
        return super().insert_returning_id(session, cls.TABLE, data)
    
    @classmethod
    def update(cls, session: Session, record_id: Any, data: Dict[str, Any]) -> int:
        """Update record by ID."""
        return super().update_by_id(session, cls.TABLE, record_id, data)
    
    @classmethod
    def delete(cls, session: Session, record_id: Any) -> int:
        """Delete record by ID."""
        sql = f"DELETE FROM {cls.TABLE} WHERE {cls.ID_COL} = :id"
        return cls.execute(session, sql, {"id": record_id})
    
    @classmethod
    def exists(cls, session: Session, **conditions: Any) -> bool:
        """Check if record exists."""
        return super().exists(session, cls.TABLE, **conditions)
    
    @classmethod
    def count(cls, session: Session, **conditions: Any) -> int:
        """Count records."""
        return super().count(session, cls.TABLE, **conditions)
    
    @classmethod
    def get_by_id_or_raise(cls, session: Session, record_id: Any) -> Dict[str, Any]:
        """Get record by ID or raise RecordNotFoundError."""
        result = cls.get_by_id(session, record_id)
        if not result:
            raise RecordNotFoundError(f"{cls.TABLE} record {record_id} not found")
        return result
    
    @classmethod
    def create_many(cls, session: Session, records: List[Dict[str, Any]]) -> List[int]:
        """Batch insert multiple records."""
        if not records:
            return []
        
        try:
            ids = []
            for record in records:
                record_id = cls.create(session, record)
                if record_id:
                    ids.append(record_id)
            return ids
        except Exception as e:
            raise DatabaseError(f"Failed to create multiple {cls.TABLE} records: {str(e)}")
    
    @classmethod
    def get_recent(cls, session: Session, hours: int = 24) -> List[Dict[str, Any]]:
        """Get records from the last N hours (requires created_at column)."""
        since = datetime.utcnow() - timedelta(hours=hours)
        sql = f"SELECT * FROM {cls.TABLE} WHERE created_at >= :since ORDER BY created_at DESC"
        return cls.fetch_all(session, sql, {"since": since})
    
    @classmethod
    def soft_delete(cls, session: Session, record_id: Any) -> int:
        """Mark record as deleted without removing from database (requires deleted_at and is_deleted columns)."""
        try:
            return cls.update(session, record_id, {
                "deleted_at": datetime.utcnow(),
                "is_deleted": True
            })
        except Exception as e:
            raise DatabaseError(f"Failed to soft delete {cls.TABLE} record {record_id}: {str(e)}")
    
    @classmethod
    def find_by_field(cls, session: Session, field: str, value: Any) -> List[Dict[str, Any]]:
        """Find multiple records by a field value."""
        sql = f"SELECT * FROM {cls.TABLE} WHERE {field} = :value"
        return cls.fetch_all(session, sql, {"value": value})


class NamedEntityMixin:
    """Mixin for entities with name fields."""
    
    NAME_COL: str = "name"  # Override if using different name column
    
    @classmethod
    def get_by_name(cls, session: Session, name: str) -> Optional[Dict[str, Any]]:
        """Get record by name."""
        return cls.get_by_field(session, cls.TABLE, cls.NAME_COL, name)
    
    @classmethod
    def search_by_name(cls, session: Session, search_term: str) -> List[Dict[str, Any]]:
        """Search records by name (case-insensitive partial match)."""
        sql = f"SELECT * FROM {cls.TABLE} WHERE {cls.NAME_COL} LIKE :search_term ORDER BY {cls.NAME_COL}"
        return cls.fetch_all(session, sql, {"search_term": f"%{search_term}%"})
    
    @classmethod
    def get_name(cls, session: Session, record_id: Any) -> Optional[str]:
        """Get just the name field for a record."""
        sql = f"SELECT {cls.NAME_COL} FROM {cls.TABLE} WHERE id = :id"
        return cls.fetch_scalar(session, sql, {"id": record_id})


class GenericNamedRepository(GenericRepository, NamedEntityMixin):
    """Convenience class combining GenericRepository + NamedEntityMixin."""
    pass 