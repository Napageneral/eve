"""Repository for embeddings operations."""
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from .core.generic import GenericRepository
import logging

logger = logging.getLogger(__name__)


class EmbeddingsRepository(GenericRepository):
    """Repository for semantic search embeddings."""
    
    TABLE = "embeddings"
    
    # TODO: Find and migrate direct DB access from services
    # This is likely in services/embeddings/faiss_index.py or similar
    
    @classmethod
    def get_by_source(cls, session: Session, source_type: str, source_id: str) -> List[Dict[str, Any]]:
        """Get embeddings by source."""
        from backend.db.sql import fetch_all
        
        sql = """
            SELECT * FROM embeddings
            WHERE source_type = :source_type AND source_id = :source_id
        """
        return fetch_all(session, sql, {"source_type": source_type, "source_id": source_id})
    
    @classmethod
    def get_for_chat(cls, session: Session, chat_id: int) -> List[Dict[str, Any]]:
        """Get all embeddings for a chat."""
        from backend.db.sql import fetch_all
        
        sql = """
            SELECT * FROM embeddings
            WHERE source_type = 'conversation' 
            AND source_id IN (
                SELECT CAST(id AS TEXT) FROM conversations WHERE chat_id = :chat_id
            )
        """
        return fetch_all(session, sql, {"chat_id": chat_id})
    
    @classmethod
    def upsert_embedding(cls, session: Session, source_type: str, source_id: str, 
                        embedding: List[float], metadata: Optional[Dict] = None) -> None:
        """Insert or update an embedding."""
        from backend.db.sql import execute_write
        import json
        
        sql = """
            INSERT INTO embeddings (source_type, source_id, embedding, metadata, updated_at)
            VALUES (:source_type, :source_id, :embedding, :metadata, CURRENT_TIMESTAMP)
            ON CONFLICT (source_type, source_id) DO UPDATE SET
                embedding = :embedding,
                metadata = :metadata,
                updated_at = CURRENT_TIMESTAMP
        """
        execute_write(session, sql, {
            "source_type": source_type,
            "source_id": source_id,
            "embedding": json.dumps(embedding),
            "metadata": json.dumps(metadata) if metadata else None
        })

