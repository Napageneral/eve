"""
Prompt service - DEPRECATED - all prompts now managed by Eve

This module is kept for backward compatibility but all methods raise deprecation errors.
"""
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from backend.services.core.utils import BaseService

logger = logging.getLogger(__name__)


class PromptService(BaseService):
    """Service layer for prompt template operations (DEPRECATED - use Eve)."""

    @staticmethod
    def create_prompt_template(
        session: Session,
        name: str,
        prompt_text: str,
        category: Optional[str] = None,
        placeholder_mapping: Optional[Dict[str, Any]] = None
    ):
        """DEPRECATED: All prompts now managed by Eve."""
        raise ValueError("Prompt templates deprecated - all prompts now in Eve context packs")

    @staticmethod
    def list_prompt_templates(session: Session) -> List[Dict[str, Any]]:
        """DEPRECATED: All prompts now managed by Eve."""
        raise ValueError("Prompt templates deprecated - all prompts now in Eve context packs")

    @staticmethod
    def get_prompt_template(session: Session, template_id: int) -> Optional[Dict[str, Any]]:
        """DEPRECATED: All prompts now managed by Eve."""
        raise ValueError("Prompt templates deprecated - all prompts now in Eve context packs")

    @staticmethod
    def delete_prompt_template(session: Session, template_id: int) -> bool:
        """DEPRECATED: All prompts now managed by Eve."""
        raise ValueError("Prompt templates deprecated - all prompts now in Eve context packs")


# Create instance for direct import
prompt = PromptService() 