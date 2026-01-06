"""
Model management using LiteLLM
"""
import litellm
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

def get_available_models() -> List[str]:
    """Get list of available models from LiteLLM"""
    try:
        # LiteLLM tracks available models internally
        models = litellm.model_list
        return sorted(models) if models else []
    except Exception as e:
        logger.error(f"Failed to get model list: {e}")
        # Return a default list of known models
        return [
            "gpt-4o",
            "gpt-4o-mini", 
            "o1",
            "o1-mini",
            "claude-3-7-sonnet-20250219",
            "claude-4-sonnet",
            "gemini-2.0-flash",
            "gemini-2.5-pro-preview-05-06"
        ]

def get_model_info(model: str) -> Optional[Dict]:
    """Get model information including costs"""
    try:
        info = litellm.get_model_info(model)
        return info
    except Exception as e:
        logger.warning(f"Failed to get info for model {model}: {e}")
        return None

def validate_model(model: str) -> bool:
    """Check if a model is supported"""
    try:
        # Try to get model info - if it succeeds, model is valid
        info = litellm.get_model_info(model)
        return info is not None
    except:
        return False

def get_model_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost for a model"""
    try:
        return litellm.completion_cost(
            model=model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens
        )
    except Exception as e:
        logger.warning(f"Failed to calculate cost for {model}: {e}")
        return 0.0

def get_pricing_for_model(model: str) -> Optional[Dict]:
    """Get pricing information for a model.
    
    Returns dict with 'input' and 'output' keys containing price per token,
    or None if pricing info is unavailable.
    """
    try:
        # Get model info from LiteLLM
        info = litellm.get_model_info(model)
        if not info:
            logger.warning(f"No model info available for {model}")
            return {"input": 0.0, "output": 0.0}
        
        # LiteLLM returns pricing in various formats depending on the model
        # Try to extract input/output pricing per token
        input_cost_per_token = info.get("input_cost_per_token", 0.0)
        output_cost_per_token = info.get("output_cost_per_token", 0.0)
        
        # Fallback: some models use different keys
        if input_cost_per_token == 0.0:
            input_cost_per_token = info.get("input_cost_per_million_tokens", 0.0) / 1_000_000
        if output_cost_per_token == 0.0:
            output_cost_per_token = info.get("output_cost_per_million_tokens", 0.0) / 1_000_000
        
        return {
            "input": input_cost_per_token,
            "output": output_cost_per_token
        }
    except Exception as e:
        logger.warning(f"Failed to get pricing for model {model}: {e}")
        return {"input": 0.0, "output": 0.0} 