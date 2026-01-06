"""
Prompt class - identical to existing one for compatibility
"""
from typing import Any, Dict, Optional

class Prompt:
    def __init__(
        self, 
        prompt_text: str, 
        model: str, 
        temperature: Optional[float] = 0.7, 
        response_format: Optional[Dict] = None,
        prompt_name: Optional[str] = None,
        prompt_version: Optional[int] = None
    ):
        self.prompt_text = prompt_text
        self.model = model
        self.temperature = temperature
        self.response_format = response_format
        self.prompt_name = prompt_name
        self.prompt_version = prompt_version

    def __str__(self):
        return f"""Model: {self.model}
        Prompt: {self.prompt_name} v{self.prompt_version}
        {self.prompt_text}""" 