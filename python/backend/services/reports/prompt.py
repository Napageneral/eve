"""
Report prompt service - handle prompt templates and placeholder resolution

NOTE: Prompt template loading is DEPRECATED - all prompts now managed by Eve.
This module is kept for context selection resolution only.
"""
from typing import Any, Dict
import logging
import re
import json

from backend.services.core.utils import BaseService, timed, with_session
from backend.services.core.token import TokenService

logger = logging.getLogger(__name__)


class ReportPromptService(BaseService):
    """Load prompt templates & resolve placeholders."""

    @staticmethod
    @timed("load_prompt_template")
    @with_session(commit=False)
    def load_prompt_template(prompt_template_id: int, session=None) -> Dict[str, Any]:
        """
        DEPRECATED: All prompts now managed by Eve.
        This method is kept for backward compatibility but should not be called.
        """
        logger.error(
            "load_prompt_template() called but prompts are now managed by Eve. "
            "Use Eve context packs instead."
        )
        raise ValueError("Prompt templates deprecated - all prompts now in Eve context packs")

    # ------------------------------------------------------------------
    # Placeholder substitution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def substitute_placeholders(prompt_text: str, resolved_content: Dict[str, str]) -> str:
        final_prompt = prompt_text
        for placeholder_name, content in resolved_content.items():
            placeholder = f"{{{{{{{placeholder_name}}}}}}}"
            if placeholder in final_prompt:
                final_prompt = final_prompt.replace(placeholder, content)
            else:
                logger.warning("Placeholder %s not found", placeholder)
        remaining = re.findall(r"\{{3}([^}]+)\}{3}", final_prompt)
        if remaining:
            logger.warning("Unresolved placeholders: %s", remaining)
        return final_prompt

    # ------------------------------------------------------------------
    # Context-selection resolution
    # ------------------------------------------------------------------

    # NOTE: Legacy report system - not used (reports feature removed)
    # resolve_context_selections() removed during Eve migration
    # Context retrieval now handled by Eve service
    
    @staticmethod
    @timed("resolve_context_selections")
    @with_session(commit=True)
    def resolve_context_selections(
        placeholder_to_cs_id: Dict[str, int], session=None
    ) -> Dict[str, str]:
        # NOTE: Deleted modules - function kept for reference only
        # from backend.repositories.contexts import ContextRepository
        # from backend.services.context.retrieval.index import RETRIEVAL_FUNCTIONS
        raise NotImplementedError("Reports feature removed, context system migrated to Eve")

        resolved: Dict[str, str] = {}
        logger.debug("[resolve_cs] start placeholders=%s", list(placeholder_to_cs_id.keys()))
        for placeholder, cs_id in placeholder_to_cs_id.items():
            logger.debug("[resolve_cs] ph=%s cs_id=%s", placeholder, cs_id)
            cs_row = ContextRepository.get_context_selection_with_definition(session, cs_id)
            if not cs_row:
                raise ValueError(f"ContextSelection ID={cs_id} not found")

            # Re-use cached content when present
            if cs_row["resolved_content"]:
                resolved[placeholder] = cs_row["resolved_content"]
                continue

            retrieval_fn = RETRIEVAL_FUNCTIONS.get(cs_row["retrieval_function_ref"])
            if not retrieval_fn:
                raise ValueError(f"No retrieval function for {cs_row['retrieval_function_ref']}")

            params = json.loads(cs_row["parameter_values"]) if cs_row["parameter_values"] else {}
            logger.debug("[resolve_cs] retrieval=%s params=%s", cs_row["retrieval_function_ref"], params)
            content = retrieval_fn(params)
            token_count = TokenService.count_tokens_with_fallback(content)
            ContextRepository.update_context_selection_content(session, cs_id, content, token_count)
            resolved[placeholder] = content
            try:
                logger.debug("[resolve_cs] content_len=%s tokens=%s", len(content or ""), token_count)
            except Exception:
                logger.debug("[resolve_cs] content_len=? tokens=%s", token_count)
        return resolved


__all__ = ["ReportPromptService"] 