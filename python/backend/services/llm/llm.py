"""
Compatibility wrapper for LLM service.

This provides LLMService, LLMConfigResolver, and LLMError for backward compatibility
with existing code that used services/core/llm.py.

The actual LLM calls are handled by completions.py (from llm_lite).
"""

import logging
from typing import Dict, Any, Optional
from .completions import get_completion
from .models import get_model_info

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM-related errors"""
    pass


class LLMConfigResolver:
    """Resolves LLM configuration from multiple sources.
    
    Hierarchy: base_config → prompt_config → user_override
    """
    
    @staticmethod
    def resolve_config(
        base_config: Dict[str, Any],
        prompt_config: Optional[Dict[str, Any]] = None,
        user_override: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Merge config layers with proper precedence.
        
        Only non-None values from higher layers override lower layers.
        This prevents Pydantic defaults (None) from overriding base config.
        """
        config = {**(base_config or {})}
        if prompt_config:
            # Only update with non-None values
            config.update({k: v for k, v in prompt_config.items() if v is not None})
        if user_override:
            # Only update with non-None values
            config.update({k: v for k, v in user_override.items() if v is not None})
        return config


class LLMService:
    """Centralized LLM service wrapper."""
    
    @staticmethod
    def call_llm(
        prompt_str: str,
        llm_config_dict: Dict[str, Any],
        response_schema_dict: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Call LLM with unified interface.
        
        Returns:
            {
                "content": str,
                "usage": {
                    "input_tokens": int,
                    "output_tokens": int,
                    "total_cost": float
                }
            }
        """
        try:
            model_name = llm_config_dict.get("model_name", "gpt-4o-mini")
            temperature = llm_config_dict.get("temperature", 0.7)
            max_tokens = llm_config_dict.get("max_tokens", 4000)
            
            # VERBOSE: Log LLM call details for debugging
            has_schema = response_schema_dict is not None
            logger.info(
                f"[LLM] Calling {model_name} (temp={temperature}, max_tokens={max_tokens}, "
                f"has_schema={has_schema}, prompt_length={len(prompt_str)} chars)"
            )
            
            if has_schema:
                logger.debug(f"[LLM] Response schema provided: {list(response_schema_dict.keys()) if isinstance(response_schema_dict, dict) else type(response_schema_dict)}")
            else:
                logger.warning("⚠️ [LLM] No response schema provided - LLM will return free-form text!")
            
            # Build Prompt object
            from backend.services.llm.prompt import Prompt
            compiled_prompt = Prompt(
                prompt_text=prompt_str,
                model=model_name,
                temperature=temperature,
                response_format=response_schema_dict
            )
            
            # Call LiteLLM
            response = get_completion(
                compiled_prompt=compiled_prompt,
                max_tokens=max_tokens
            )
            
            # VERBOSE: Log response details
            content = response.get("content", "")
            content_length = len(content) if isinstance(content, str) else len(str(content))
            content_type = type(content).__name__
            logger.info(f"[LLM] Response received: {content_length} chars, type={content_type}")
            
            return response
            
        except Exception as e:
            logger.error(f"LLM call failed: {e}", exc_info=True)
            raise LLMError(f"LLM call failed: {e}")

