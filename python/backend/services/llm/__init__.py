"""LiteLLM-based LLM integration for ChatStats"""
from .completions import get_completion
from .prompt import Prompt
from .config import configure_litellm
from .llm import LLMService, LLMConfigResolver, LLMError

__all__ = ['get_completion', 'Prompt', 'configure_litellm', 'LLMService', 'LLMConfigResolver', 'LLMError'] 