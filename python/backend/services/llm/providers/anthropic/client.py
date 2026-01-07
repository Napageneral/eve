from anthropic import Anthropic
from enum import Enum
import os

def _get_key(explicit: str | None = None) -> str:
    key = (explicit or os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise ValueError("Missing ANTHROPIC_API_KEY")
    return key

class AnthropicClient:
    """Manages interactions with Anthropic API."""
    
    def __init__(self, api_key: str | None = None):
        self.client = Anthropic(api_key=_get_key(api_key))
    
    @classmethod
    def get_default_client(cls):
        """Get a client instance with default API key."""
        return cls(None)

