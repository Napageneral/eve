import tiktoken
from typing import Optional
from backend.services.llm.models import get_pricing_for_model
from backend.services.llm.providers.google.gemini import count_tokens_gemini
import logging

logger = logging.getLogger(__name__)

# Initialize tokenizer once
_tokenizer = tiktoken.get_encoding("o200k_base")

class TokenService:
    """Centralized token counting and pricing utilities."""
    
    @staticmethod
    def num_tokens(text: str) -> int:
        """Count tokens using tiktoken."""
        if not text:
            return 0
        return len(_tokenizer.encode(text))
    
    @staticmethod 
    def count_tokens_with_fallback(text: str, model: Optional[str] = None) -> int:
        """Count tokens with Gemini API fallback to local counting."""
        try:
            if model and "gemini" in model.lower():
                return count_tokens_gemini(text, model)
        except Exception as e:
            logger.debug(f"Gemini token counting failed, using local: {e}")
        
        return TokenService.num_tokens(text)
    
    @staticmethod
    def calculate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
        """Calculate cost based on token counts and model pricing."""
        pricing = get_pricing_for_model(model)
        
        if hasattr(pricing, "input_cost_per_1k_tokens"):
            input_price = pricing.input_cost_per_1k_tokens / 1000.0
            output_price = pricing.output_cost_per_1k_tokens / 1000.0
        else:
            input_price = float(pricing.get("input", 0.0))
            output_price = float(pricing.get("output", 0.0))
        
        return (input_tokens * input_price) + (output_tokens * output_price) 