"""Repository for topic operations."""
from .analysis_items_base import AnalysisItemRepository

class TopicsRepository(AnalysisItemRepository):
    """Repository for topic operations."""
    
    TABLE = "topics"
    ITEM_NAME_FIELD = "topic_name" 