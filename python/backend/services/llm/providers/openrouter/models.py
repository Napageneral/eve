import requests
from cachetools import TTLCache
import logging
from typing import Dict, Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_CACHE = TTLCache(maxsize=1, ttl=3600)  # Cache for 1 hour

def get_openrouter_models() -> Dict[str, Dict]:
    """Fetches the list of available models from OpenRouter API and caches it."""
    if "models" not in MODEL_CACHE:
        try:
            response = requests.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
            models = response.json().get("data", [])
            # Store as dictionary with model_id as key for efficient lookups
            MODEL_CACHE["models"] = {model["id"]: model for model in models}
            logger.info(f"Successfully fetched and cached {len(models)} models from OpenRouter.")
            
            # Log a sample model with pricing for debugging
            if models:
                sample_model = models[0]
                sample_id = sample_model["id"]
                sample_pricing = sample_model.get("pricing", {})
                logger.info(f"Sample model pricing - {sample_id}: prompt=${sample_pricing.get('prompt', 'N/A')}/token, completion=${sample_pricing.get('completion', 'N/A')}/token")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch models from OpenRouter: {e}")
            # Return empty dict on failure to avoid breaking downstream consumers
            return {}
    return MODEL_CACHE["models"]

def get_model_ids():
    """Returns a list of model IDs available on OpenRouter."""
    models = get_openrouter_models()
    return list(models.keys())

def get_model_pricing(model_id: str) -> Optional[Dict[str, float]]:
    """Get pricing information for a specific model."""
    models = get_openrouter_models()
    model_data = models.get(model_id)
    if model_data:
        pricing = model_data.get("pricing", {})
        if not pricing or "prompt" not in pricing or "completion" not in pricing:
            logger.warning(f"Model {model_id} found but missing complete pricing information: {pricing}")
        return pricing
    logger.warning(f"Model {model_id} not found in cached models")
    return None

# Example usage (optional - can be removed or kept for testing)
if __name__ == "__main__":
    try:
        model_ids = get_model_ids()
        if model_ids:
            print(f"Available OpenRouter Model IDs ({len(model_ids)}):")
            # Print first 10 models for brevity
            for model_id in model_ids[:10]:
                print(f"- {model_id}")
                # Also print pricing if available
                pricing = get_model_pricing(model_id)
                if pricing:
                    print(f"  Prompt: ${pricing.get('prompt', 0)}/token, Completion: ${pricing.get('completion', 0)}/token")
            if len(model_ids) > 10:
                print("... and more.")
        else:
            print("No models retrieved or cache is empty after fetch attempt.")
    except Exception as e:
        print(f"An error occurred: {e}") 