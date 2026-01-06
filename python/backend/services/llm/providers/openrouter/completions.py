from backend.services.llm.providers.openrouter.client import get_openrouter_client
from backend.services.llm.providers.openrouter.models import get_model_ids, get_model_pricing
import logging
import openai # Import openai for exception handling
from typing import Optional, Dict
import requests
import re
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_json_from_response(content: str) -> str:
    """Extract JSON from markdown code blocks or raw text, falling back to original content if invalid."""
    # Try markdown code block first
    json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
    if json_match:
        return json_match.group(1)
    
    # Try finding JSON between braces
    json_start = content.find('{')
    json_end = content.rfind('}')
    if json_start >= 0 and json_end > json_start:
        potential_json = content[json_start:json_end+1]
        try:
            json.loads(potential_json)
            return potential_json
        except json.JSONDecodeError:
            pass
    
    return content

def get_generation_cost(generation_id: str) -> float:
    """Fetch the total cost from the generation metadata endpoint with authentication."""
    try:
        client = get_openrouter_client()
        headers = {"Authorization": f"Bearer {client.api_key}"}
        response = requests.get(f"https://openrouter.ai/api/v1/generation?id={generation_id}", headers=headers)
        response.raise_for_status()
        data = response.json()
        cost = data.get("data", {}).get("total_cost", 0.0)
        logger.info(f"Fetched cost for generation {generation_id}: ${cost:.6f}")
        return cost
    except Exception as e:
        logger.error(f"Failed to fetch cost for generation {generation_id}: {e}")
        return 0.0

def get_openrouter_completion(
    prompt: str, 
    model: str, 
    max_tokens: int = 65536,  # Increased from 8000 to match Gemini's capacity
    temperature: float = 1.0,
    response_format: Optional[Dict] = None
) -> dict:
    """Gets a chat completion from OpenRouter with accurate cost calculation."""
    available_models = get_model_ids()
    if not available_models: # Check if the model list is empty (fetch might have failed)
        logger.error("Cannot validate model: Model list is empty or fetch failed.")
        return {
            "content": "",
            "model": model,
            "error": "Failed to fetch model list from OpenRouter.",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "total_cost": 0.0},
            "cost": 0.0
        }

    if model not in available_models:
        logger.warning(f"Model '{model}' not found in available OpenRouter models.")
        # Log a few available models for reference
        available_sample = available_models[:5] if len(available_models) > 5 else available_models
        logger.warning(f"Available models sample: {available_sample} (total models: {len(available_models)})")
        raise ValueError(f"Model {model} not available on OpenRouter")

    try:
        client = get_openrouter_client()
        # Wrap the prompt in a messages array for chat completions
        messages = [{"role": "user", "content": prompt}]
        
        # Create request parameters for chat completions
        params = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        # Add response_format if provided, properly structured for chat completions
        if response_format:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "conversation_analysis",
                    "strict": True,
                    "schema": response_format
                }
            }
        
        # Call the OpenRouter chat completions API
        response = client.chat.completions.create(**params)
        
        # Extract content from the chat completion response
        content = response.choices[0].message.content.strip() if response.choices else ""
        
        # Extract and parse JSON if response_format is provided
        if response_format and isinstance(content, str):
            extracted_json = extract_json_from_response(content)
            try:
                content = json.loads(extracted_json)
                #logger.info(f"Successfully parsed JSON response for model {model}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from {model}: {e}, raw content: {content}...")
                content = extracted_json  # Keep raw string for debugging
        
        # Extract usage data safely
        usage = {
            "input_tokens": getattr(response.usage, "prompt_tokens", 0),
            "output_tokens": getattr(response.usage, "completion_tokens", 0),
            "total_tokens": getattr(response.usage, "total_tokens", 0)
        }
        
        # Extract generation ID for potential cost lookup
        generation_id = getattr(response, "id", None)
        
        # Calculate cost using cached pricing information
        cost = 0.0
        pricing = get_model_pricing(model)
        if pricing and "prompt" in pricing and "completion" in pricing:
            prompt_price = float(pricing.get("prompt", 0))
            completion_price = float(pricing.get("completion", 0))
            cost = (usage["input_tokens"] * prompt_price) + (usage["output_tokens"] * completion_price)
            #logger.info(f"Cost calculated for {model}: ${cost:.6f} (Input: {usage['input_tokens']} tokens @ ${prompt_price}/token, Output: {usage['output_tokens']} tokens @ ${completion_price}/token)")
        else:
            logger.warning(f"No pricing info for {model} in cache. Attempting to fetch from metadata endpoint.")
            if generation_id:
                cost = get_generation_cost(generation_id)
            else:
                logger.warning(f"No generation ID available to fetch cost for {model}.")
        
        # Include the cost in the usage dictionary
        usage["total_cost"] = cost
        
        return {
            "content": content,
            "model": model,
            "usage": usage,  # Now includes total_cost
            "cost": cost,  # Keep for backward compatibility
            "generation_id": generation_id
        }
    except openai.APIError as e:
        logger.error(f"OpenRouter API error for model {model}: {e}")
        return {
            "content": "",
            "model": model,
            "error": f"OpenRouter API Error: {e}",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "total_cost": 0.0},
            "cost": 0.0
        }
    except Exception as e:
        logger.exception(f"An unexpected error occurred during OpenRouter completion for model {model}: {e}") # Log full traceback
        return {
            "content": "",
            "model": model,
            "error": f"An unexpected error occurred: {e}",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "total_cost": 0.0},
            "cost": 0.0
        }