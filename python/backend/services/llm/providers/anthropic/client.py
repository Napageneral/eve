from anthropic import Anthropic
from enum import Enum

ANTHROPIC_API_KEY = "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"

class AnthropicClient:
    """Manages interactions with Anthropic API."""
    
    def __init__(self, api_key: str = ANTHROPIC_API_KEY):
        self.client = Anthropic(api_key=api_key)
    
    @classmethod
    def get_default_client(cls):
        """Get a client instance with default API key."""
        return cls(ANTHROPIC_API_KEY)

