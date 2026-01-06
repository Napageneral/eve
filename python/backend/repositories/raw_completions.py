"""
Repository for raw LLM completion operations.
Handles all database operations related to raw LLM completions.
"""
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from .core.generic import GenericRepository
from .core.exceptions import RecordNotFoundError
import json

class RawCompletionRepository(GenericRepository):
    """Repository for raw LLM completion operations."""
    
    TABLE = "raw_completions"
    
    @classmethod
    def save_raw_completion(
        cls, 
        session: Session, 
        conversation_id: int,
        chat_id: int,
        model_name: str,
        prompt_template_id: int,
        compiled_prompt_text: str,
        raw_response_payload: Dict[str, Any]
    ) -> int:
        """Save raw LLM completion and return ID."""
        return cls.create(session, {
            "conversation_id": conversation_id,
            "chat_id": chat_id,
            "model_name": model_name,
            "prompt_template_id": prompt_template_id,
            "compiled_prompt_text": compiled_prompt_text,
            "raw_response_payload": json.dumps(raw_response_payload),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        })
    
    @classmethod
    def get_completions_for_conversation(cls, session: Session, conversation_id: int) -> List[Dict[str, Any]]:
        """Get all raw completions for a conversation."""
        sql = """
            SELECT 
                rc.*,
                pt.name as prompt_template_name,
                pt.version as prompt_template_version
            FROM raw_completions rc
            LEFT JOIN prompt_templates pt ON rc.prompt_template_id = pt.id
            WHERE rc.conversation_id = :conversation_id
            ORDER BY rc.created_at DESC
        """
        return cls.fetch_all(session, sql, {"conversation_id": conversation_id})
    
    @classmethod
    def get_completions_by_model(cls, session: Session, model_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent completions by model."""
        sql = """
            SELECT rc.*, pt.name as prompt_template_name
            FROM raw_completions rc
            LEFT JOIN prompt_templates pt ON rc.prompt_template_id = pt.id
            WHERE rc.model_name = :model_name
            ORDER BY rc.created_at DESC
            LIMIT :limit
        """
        return cls.fetch_all(session, sql, {"model_name": model_name, "limit": limit})
    
    @classmethod
    def get_completion_stats(cls, session: Session, hours: int = 24) -> Dict[str, Any]:
        """Get completion statistics for the last N hours."""
        since = datetime.utcnow() - timedelta(hours=hours)
        
        # Total completions
        total_sql = """
            SELECT COUNT(*) as total_count
            FROM raw_completions
            WHERE created_at >= :since
        """
        total_result = cls.fetch_one(session, total_sql, {"since": since})
        
        # Completions by model
        by_model_sql = """
            SELECT model_name, COUNT(*) as count
            FROM raw_completions
            WHERE created_at >= :since
            GROUP BY model_name
            ORDER BY count DESC
        """
        by_model = cls.fetch_all(session, by_model_sql, {"since": since})
        
        # Average response time (if available in payload)
        return {
            "total_completions": total_result["total_count"] if total_result else 0,
            "completions_by_model": by_model,
            "period_hours": hours
        }
    
    @classmethod
    def get_completion_with_details(cls, session: Session, completion_id: int) -> Optional[Dict[str, Any]]:
        """Get a completion with full details including prompt template info."""
        sql = """
            SELECT 
                rc.*,
                pt.name as prompt_template_name,
                pt.version as prompt_template_version,
                pt.category as prompt_template_category
            FROM raw_completions rc
            LEFT JOIN prompt_templates pt ON rc.prompt_template_id = pt.id
            WHERE rc.id = :completion_id
        """
        result = cls.fetch_one(session, sql, {"completion_id": completion_id})
        
        if result and result.get("raw_response_payload"):
            # Parse the JSON payload
            try:
                result["parsed_payload"] = json.loads(result["raw_response_payload"])
            except json.JSONDecodeError:
                result["parsed_payload"] = None
        
        return result
    
    @classmethod
    def get_completions_with_errors(cls, session: Session, limit: int = 50) -> List[Dict[str, Any]]:
        """Get completions that contain error information."""
        sql = """
            SELECT rc.*, pt.name as prompt_template_name
            FROM raw_completions rc
            LEFT JOIN prompt_templates pt ON rc.prompt_template_id = pt.id
            WHERE rc.raw_response_payload LIKE '%error%'
            OR rc.raw_response_payload LIKE '%Error%'
            ORDER BY rc.created_at DESC
            LIMIT :limit
        """
        return cls.fetch_all(session, sql, {"limit": limit})
    
    @classmethod
    def find_by_prompt_template(cls, session: Session, prompt_template_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Find completions by prompt template."""
        sql = """
            SELECT rc.*, pt.name as prompt_template_name
            FROM raw_completions rc
            LEFT JOIN prompt_templates pt ON rc.prompt_template_id = pt.id
            WHERE rc.prompt_template_id = :prompt_template_id
            ORDER BY rc.created_at DESC
            LIMIT :limit
        """
        return cls.fetch_all(session, sql, {"prompt_template_id": prompt_template_id, "limit": limit})
    
    @classmethod
    def delete_old_completions(cls, session: Session, days: int = 30) -> int:
        """Delete completions older than specified days."""
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        sql = """
            DELETE FROM raw_completions 
            WHERE created_at < :cutoff_date
        """
        return cls.execute(session, sql, {"cutoff_date": cutoff_date}) 