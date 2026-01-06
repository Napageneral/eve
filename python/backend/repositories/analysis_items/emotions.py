"""Repository for emotion operations."""
from .analysis_items_base import AnalysisItemRepository

class EmotionsRepository(AnalysisItemRepository):
    """Repository for emotion operations."""
    
    TABLE = "emotions"
    ITEM_NAME_FIELD = "emotion_type" 