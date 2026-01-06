from pydantic import BaseModel, Field
import uuid
from typing import Optional

class LLMConfig(BaseModel):
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None

class ConversationAnalysisIn(BaseModel):
    conversation_id: int
    chat_id: int
    conversation_analysis_row_id: Optional[int] = None
    auth_token: Optional[str] = None
    encoded_conversation_text: Optional[str] = None
    
    llm_config_override: Optional[LLMConfig] = None
    
    prompt_name: Optional[str] = None
    prompt_version: Optional[int] = None
    prompt_category: Optional[str] = None

    idempotency_key: str

class ConversationAnalysisOut(BaseModel):
    success: bool
    message: Optional[str] = None
    analysis_id: Optional[int] = None
    conversation_analysis_row_id: Optional[int] = None 