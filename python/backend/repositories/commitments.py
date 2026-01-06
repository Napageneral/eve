import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from .core.generic import GenericRepository

logger = logging.getLogger(__name__)

class CommitmentRepository(GenericRepository):
    """Repository for all commitment database operations using raw SQL."""
    
    TABLE = "commitments"
    
    @classmethod
    def get_active_commitments(cls, session: Session, chat_id: Optional[int] = None) -> List[Dict]:
        """Get all active commitments (pending or monitoring_condition status)."""
        sql = """
            SELECT c.*, co.name as to_person_name
            FROM commitments c
            LEFT JOIN contacts co ON c.to_person_id = co.id
            WHERE c.status IN ('pending', 'monitoring_condition')
        """
        params = {}
        
        if chat_id:
            sql += " AND c.chat_id = :chat_id"
            params["chat_id"] = chat_id
            
        sql += " ORDER BY c.created_date DESC"
        
        rows = cls.fetch_all(session, sql, params)
        return [cls._format_commitment(row) for row in rows]
    
    @classmethod
    def get_all_active_commitments(cls, session: Session) -> List[Dict]:
        """Get all active commitments across all chats."""
        return cls.get_active_commitments(session, chat_id=None)
    
    @classmethod
    def get_all_commitments(
        cls, 
        session: Session, 
        include_inactive: bool = True,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        contact_id: Optional[int] = None
    ) -> List[Dict]:
        """Get all commitments with optional filtering."""
        sql = """
            SELECT c.*, co.name as to_person_name
            FROM commitments c
            LEFT JOIN contacts co ON c.to_person_id = co.id
            WHERE 1=1
        """
        params = {}
        
        if not include_inactive:
            sql += " AND c.status IN ('pending', 'monitoring_condition')"
        
        if start_date:
            sql += " AND c.created_date >= :start_date"
            params["start_date"] = start_date
            
        if end_date:
            sql += " AND c.created_date < :end_date"
            params["end_date"] = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
        
        if contact_id:
            sql += " AND c.to_person_id = :contact_id"
            params["contact_id"] = contact_id
            
        sql += " ORDER BY c.created_date DESC"
        
        rows = cls.fetch_all(session, sql, params)
        return [cls._format_commitment(row) for row in rows]
    
    @classmethod
    def get_commitment_by_id(cls, session: Session, commitment_id: str) -> Optional[Dict]:
        """Get a commitment by its ID."""
        sql = """
            SELECT c.*, co.name as to_person_name
            FROM commitments c
            LEFT JOIN contacts co ON c.to_person_id = co.id
            WHERE c.commitment_id = :commitment_id
        """
        row = cls.fetch_one(session, sql, {"commitment_id": commitment_id})
        return cls._format_commitment(row) if row else None
    
    @classmethod
    def add_commitment(cls, session: Session, commitment_data: Dict) -> int:
        """Add a new commitment to the database."""
        now = datetime.utcnow()
        data = {
            "commitment_id": commitment_data['id'],
            "conversation_id": commitment_data['conversation_id'],
            "chat_id": commitment_data['chat_id'],
            "contact_id": commitment_data.get('contact_id', 1),
            "to_person_id": cls._extract_person_id(commitment_data.get('to_person', 'person_1')),
            "commitment_text": commitment_data['commitment'],
            "context": commitment_data.get('context'),
            "due_date": cls._parse_date(commitment_data.get('due_date')),
            "due_specificity": commitment_data.get('due_specificity', 'none'),
            "status": 'pending',
            "priority": commitment_data.get('priority', 'medium'),
            "condition": commitment_data.get('condition'),
            "modifications": cls.dumps(commitment_data.get('modifications', [])),
            "reminder_data": cls.dumps(commitment_data.get('reminders')),
            "source_conversation_id": commitment_data.get('source_conversation_id', commitment_data['conversation_id']),
            "last_modified_conversation_id": commitment_data.get('last_modified_conversation_id', commitment_data['conversation_id']),
            # Ensure NOT NULL columns are populated
            "created_date": now,
            "created_at": now,
            "updated_at": now,
        }
        
        commitment_id = cls.create(session, data)
        logger.info(f"Added commitment {commitment_data['id']} to database")
        return commitment_id
    
    @classmethod
    def update_commitment(cls, session: Session, commitment_id: str, updates: Dict) -> bool:
        """Update an existing commitment."""
        # Get the internal ID first
        row = cls.fetch_one(session, "SELECT id FROM commitments WHERE commitment_id = :commitment_id", 
                           {"commitment_id": commitment_id})
        if not row:
            return False
            
        updates["updated_at"] = datetime.utcnow()
        cls.update(session, row["id"], updates)
        logger.info(f"Updated commitment {commitment_id}")
        return True
    
    @classmethod
    def remove_commitment(cls, session: Session, commitment_id: str, 
                         status: str = 'cancelled', resolution_method: str = 'user_action') -> Optional[Dict]:
        """Mark a commitment as completed/cancelled."""
        updates = {
            "status": status,
            "resolution_method": resolution_method,
            "updated_at": datetime.utcnow()
        }
        
        if status == 'completed':
            updates["completed_date"] = datetime.utcnow()
            
        if cls.update_commitment(session, commitment_id, updates):
            logger.info(f"Marked commitment {commitment_id} as {status}")
            return cls.get_commitment_by_id(session, commitment_id)
        return None
    
    @classmethod
    def find_commitment(cls, session: Session, commitment_id: str) -> Optional[Dict]:
        """Find a commitment by ID and return as dict."""
        return cls.get_commitment_by_id(session, commitment_id)
    
    @classmethod
    def get_commitments_by_status(cls, session: Session, status: str) -> List[Dict]:
        """Get commitments by status."""
        sql = """
            SELECT c.*, co.name as to_person_name
            FROM commitments c
            LEFT JOIN contacts co ON c.to_person_id = co.id
            WHERE c.status = :status
        """
        rows = cls.fetch_all(session, sql, {"status": status})
        return [cls._format_commitment(row) for row in rows]
    
    @classmethod
    def get_commitments_by_due_date(cls, session: Session, due_date: str) -> List[Dict]:
        """Get commitments by due date."""
        try:
            date_obj = date.fromisoformat(due_date)
            sql = """
                SELECT c.*, co.name as to_person_name
                FROM commitments c
                LEFT JOIN contacts co ON c.to_person_id = co.id
                WHERE c.due_date = :due_date
            """
            rows = cls.fetch_all(session, sql, {"due_date": date_obj})
            return [cls._format_commitment(row) for row in rows]
        except ValueError:
            return []
    
    @classmethod
    def get_recent_inactive_commitments(
        cls, session: Session, chat_id: int, days: int = 7
    ) -> List[Dict]:
        """Get recently completed/cancelled commitments for a chat within the specified days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        sql = """
            SELECT c.*, co.name as to_person_name
            FROM commitments c
            LEFT JOIN contacts co ON c.to_person_id = co.id
            WHERE c.chat_id = :chat_id
              AND c.status IN ('completed', 'cancelled')
              AND c.updated_at >= :cutoff
            ORDER BY c.updated_at DESC
        """
        rows = cls.fetch_all(session, sql, {
            "chat_id": chat_id,
            "cutoff": cutoff
        })
        return [cls._format_commitment(row) for row in rows]
    
    @classmethod
    def _format_commitment(cls, row: Dict) -> Dict:
        """Convert a database row to dict format for compatibility."""
        if not row:
            return {}
        
        # Safely normalize datetime/date fields which may already be strings from the driver
        def _iso_or_str(value, append_z: bool = False):
            if not value:
                return None
            if isinstance(value, str):
                return value
            try:
                return value.isoformat() + ('Z' if append_z else '')
            except AttributeError:
                return str(value)

        created_date = _iso_or_str(row.get('created_date'), append_z=True)
        due_date = _iso_or_str(row.get('due_date'), append_z=False)
        completed_date = _iso_or_str(row.get('completed_date'), append_z=True)
        updated_at = _iso_or_str(row.get('updated_at'), append_z=True)

        return {
            'id': row['commitment_id'],
            'commitment': row['commitment_text'],
            'description': row['commitment_text'],  # alias used by encoding service
            'to_person': f"person_{row['to_person_id']}",
            'to_person_name': row.get('to_person_name') or f"Contact {row['to_person_id']}",
            'conversation_id': row['conversation_id'],
            'chat_id': row['chat_id'],
            'contact_id': row['contact_id'],
            'created_date': created_date,
            'due_date': due_date,
            'due_specificity': row.get('due_specificity'),
            'context': row.get('context'),
            'status': row.get('status'),
            'priority': row.get('priority'),
            'condition': row.get('condition'),
            'reminders': cls.parse_json(row.get('reminder_data')) if row.get('reminder_data') else None,
            'modifications': cls.parse_json(row.get('modifications')) if row.get('modifications') else [],
            'completed_date': completed_date,
            'updated_at': updated_at,
            'resolution_method': row.get('resolution_method'),
            'source_conversation_id': row.get('source_conversation_id'),
            'last_modified_conversation_id': row.get('last_modified_conversation_id')
        }
    
    @staticmethod
    def _extract_person_id(person_str: str) -> int:
        """Extract person ID from string like 'person_123'."""
        if person_str.startswith('person_'):
            try:
                return int(person_str.split('_')[1])
            except (ValueError, IndexError):
                pass
        return 1  # Default fallback
    
    @staticmethod
    def _parse_date(date_str: Optional[str]) -> Optional[date]:
        """Parse ISO date string to date object."""
        if not date_str:
            return None
        try:
            return date.fromisoformat(date_str)
        except (ValueError, AttributeError):
            return None 