"""Repository for entity operations."""
from .analysis_items_base import AnalysisItemRepository

class EntitiesRepository(AnalysisItemRepository):
    """Repository for entity operations."""
    
    TABLE = "entities"
    ITEM_NAME_FIELD = "entity_name"
    EXTRA_FIELDS = ["entity_type"]  # Entities have an extra entity_type field 