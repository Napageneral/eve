import openai
import os
import logging # Optional: for logging

logger = logging.getLogger(__name__)

# It's good practice to load API keys from environment variables or a secure config system.
# Ensure OPENROUTER_API_KEY is set in the environment where your Temporal worker runs.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

_openrouter_client_instance = None

def get_openrouter_client() -> openai.OpenAI:
    """
    Retrieves or creates an instance of the OpenAI client configured for OpenRouter.
    Raises ValueError if the API key is not set.
    """
    global _openrouter_client_instance
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY environment variable not set.")
        raise ValueError("OPENROUTER_API_KEY environment variable not set.")
    
    if _openrouter_client_instance is None:
        logger.debug("Initializing OpenAI client for OpenRouter.")
        _openrouter_client_instance = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
    return _openrouter_client_instance

# You can also define a type for easier type hinting if needed elsewhere, though direct use of openai.OpenAI is fine.
# OpenAIClientType = openai.OpenAI 