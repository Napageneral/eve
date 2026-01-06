"""Shared Pydantic models used across multiple router modules.

This module contains commonly used request/response models to avoid duplication
across router files. Models specific to a single router should remain in their
respective router files.
"""

from pydantic import BaseModel
from typing import Dict, Any, List, Optional


# ---------------------------------------------------------------------------
# Cost Estimation Models
# ---------------------------------------------------------------------------

class EstimateReportCostRequest(BaseModel):
    prompt_template_id: int
    placeholder_to_cs_id: Dict[str, int]
    model: Optional[str] = None
    avg_output_tokens: Optional[int] = 5000


class EstimateGenerationCostRequest(BaseModel):
    prompt_template_id: int
    placeholder_to_cs_id: Dict[str, int]
    model_prompt: Optional[str] = None
    model_display: Optional[str] = None
    avg_output_tokens_prompt: int = 5000
    avg_output_tokens_display: int = 10000


# ---------------------------------------------------------------------------
# Report Generation Models  
# ---------------------------------------------------------------------------

class GenerateReportRequest(BaseModel):
    prompt_template_id: int
    placeholder_to_cs_id: Dict[str, int]
    model: Optional[str] = "google/gemini-2.5-pro-preview-05-06"
    max_tokens: Optional[int] = 16000
    temperature: Optional[float] = 0.7
    chat_id: Optional[int] = None
    contact_id: Optional[int] = None
    title: Optional[str] = None


class GenerateTitleRequest(BaseModel):
    prompt_text: str


class AskEveRequest(BaseModel):
    question: str
    context_type: str
    context_id: int
    context_selection_id: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    chat_ids: Optional[List[int]] = None
    resolve_now: Optional[bool] = False


# ---------------------------------------------------------------------------
# Chat Models
# ---------------------------------------------------------------------------

class ChatBlockRequest(BaseModel):
    is_blocked: bool


# ---------------------------------------------------------------------------
# Analysis Models
# ---------------------------------------------------------------------------

class BulkAnalysisRequest(BaseModel):
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    prompt_name: Optional[str] = None
    prompt_version: Optional[int] = None
    prompt_category: Optional[str] = None
    auth_token_for_metrics: Optional[str] = None
    idempotency_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Common Response Models
# ---------------------------------------------------------------------------

class SuccessResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None 