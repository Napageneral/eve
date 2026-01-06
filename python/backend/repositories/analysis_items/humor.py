"""Repository for humor operations."""
from .analysis_items_base import AnalysisItemRepository

class HumorRepository(AnalysisItemRepository):
    """Repository for humor item operations."""
    
    TABLE = "humor_items"
    ITEM_NAME_FIELD = "category"
    EXTRA_FIELDS = ["snippet"]  # Humor has an extra snippet field 