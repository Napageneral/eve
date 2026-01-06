from pydantic import BaseModel, Field
import uuid
from typing import Optional, List, Dict
from .conversation import LLMConfig # Reusing LLMConfig for overrides

class BulkAnalyzeIn(BaseModel):
    chat_id: int
    
    # LLM config override to be passed to child workflows
    llm_config_override: Optional[LLMConfig] = None 

    # Prompt selection to be passed to child workflows
    # These can be optional; if None, child workflow (ConversationAnalysisIn) defaults will be used.
    prompt_name: Optional[str] = None
    prompt_version: Optional[int] = None
    prompt_category: Optional[str] = None
    
    auth_token: Optional[str] = None # For parent workflow metrics
    idempotency_key: str # Removed default_factory, client must provide this.
    # conversation_ids_to_process: Optional[List[int]] = None # Keep if needed

class BulkAnalyzeOut(BaseModel):
    chat_id: int
    status: str 
    message: Optional[str] = None
    total_conversations_intended: int = 0
    total_conversations_processed: int = 0
    total_successful_analyses: int = 0
    total_failed_analyses: int = 0
    total_skipped_due_to_status: int = 0 # New field for analyses skipped due to existing status
    # Optionally, a list of individual results or failures
    # results_details: Optional[List[Dict]] = None 
    # error_details: Optional[List[Dict]] = None 