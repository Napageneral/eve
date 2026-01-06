from typing import Dict, Optional
import re
from openai import OpenAI
import json
from backend.services.llm.providers.openai.client import OpenAIClient

def extract_json_from_markdown(content: str) -> str:
    """Extract JSON content from markdown code blocks."""
    json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
    if json_match:
        return json_match.group(1)
    return content

def create_completion(
    prompt: str,
    model: str,  # Changed from LLMModel to str
    max_tokens: int = 4096,
    temperature: Optional[float] = None,
    response_format: Optional[dict] = None,
    client: Optional[OpenAIClient] = None
) -> Dict:
    """
    Create a single-turn text response via the new /v1/responses endpoint.
    If response_format is a JSON schema dict, we'll produce JSON adhering to that schema.
    """
    if client is None:
        client = OpenAIClient.get_default_client()

    # Convert the user's prompt to the new 'input' for the /v1/responses endpoint
    input_messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt}
    ]

    # Check if this is an OpenAI reasoning model
    is_reasoning_model = False
    # Exact model name checks for reasoning models
    if model in ["openai/o1", "openai/o1-preview", "openai/o1-mini"]:
        is_reasoning_model = True
    # Or look for pattern in the model name
    elif isinstance(model, str) and "o1" in model.lower():
        is_reasoning_model = True
        
    extra_params = {} if is_reasoning_model else {
        "temperature": temperature if (temperature is not None) else 0.7
    }

    # Build the payload for text output.
    text_payload = {}
    if response_format and isinstance(response_format, dict):
        # We assume response_format is a JSON schema dict
        text_payload["format"] = {
            "type": "json_schema",
            "name": "conversation_analysis",
            "schema": response_format,
            "strict": True
        }

    # The new endpoint uses max_output_tokens (instead of max_tokens)
    max_output_tokens = max_tokens if max_tokens else 4096

    # For OpenRouter, extract just the model ID without the provider prefix
    model_id = model.split("/")[1] if "/" in model else model

    try:
        # Call the new /v1/responses endpoint
        response = client.client.responses.create(
            model=model_id,
            input=input_messages,
            text=text_payload,
            max_output_tokens=max_output_tokens,
            **extra_params
        )

        # Extract token usage data in a standardized format
        usage = {}
        if hasattr(response, 'usage'):
            usage = {
                "input_tokens": getattr(response.usage, 'input_tokens', 0),
                "output_tokens": getattr(response.usage, 'output_tokens', 0),
                "total_tokens": getattr(response.usage, 'total_tokens', 0)
            }
            
            # Extract reasoning tokens for o1 models (optional)
            if hasattr(response.usage, 'output_tokens_details') and hasattr(response.usage.output_tokens_details, 'reasoning_tokens'):
                usage["reasoning_tokens"] = response.usage.output_tokens_details.reasoning_tokens

        # Check for refusals in the response object
        refusal = None
        
        # The refusal might be directly in the top-level response
        if hasattr(response, "refusal") and response.refusal:
            refusal = response.refusal
        
        # Or it might be in the output content
        elif hasattr(response, "output") and isinstance(response.output, list):
            for output_item in response.output:
                if hasattr(output_item, "content") and isinstance(output_item.content, list):
                    for content_item in output_item.content:
                        if hasattr(content_item, "type") and content_item.type == "refusal":
                            refusal = getattr(content_item, "refusal", "Refusal without message")

        # If we found a refusal, log it and return a valid but empty structure
        if refusal:
            conversation_id = "unknown"
            chat_id = "unknown"
            
            # Try to extract conversation_id and chat_id from the prompt
            conv_id_match = re.search(r'conversation_id["\']?\s*[:=]\s*["\']?(\d+)', prompt)
            chat_id_match = re.search(r'chat_id["\']?\s*[:=]\s*["\']?(\d+)', prompt)
            
            if conv_id_match:
                conversation_id = conv_id_match.group(1)
            if chat_id_match:
                chat_id = chat_id_match.group(1)
            
            print(f"⚠️ CONTENT MODERATION REFUSAL ⚠️")
            print(f"Model {model} refused to process conversation_id={conversation_id}, chat_id={chat_id}")
            print(f"Refusal message: {refusal}")
            
            # Return a valid default structure instead of failing
            return {
                "content": {
                    "summary": "This content could not be analyzed due to content policy restrictions.",
                    "entities": [],
                    "topics": [],
                    "emotions": [],
                    "humor": []
                },
                "model": model,
                "refusal": refusal,
                "usage": usage  # Include usage data even in refusal cases
            }

        # The text is in response.output_text
        raw_text = response.output_text or ""

        # If this is a JSON response, parse it
        if response_format and isinstance(response_format, dict):
            try:
                content = json.loads(raw_text)
            except json.JSONDecodeError as e:
                # Enhanced debugging information
                print("=" * 80)
                print(f"JSON PARSE ERROR in create_completion")
                print(f"Error: {str(e)}")
                print(f"Model: {model}")
                print("-" * 40)
                
                # Simply print the full response object
                print(f"FULL RESPONSE: {response}")
                print(f"Raw text response:\n{raw_text}")
                
                # Check if this might be a refusal not caught by our earlier checks
                refusal_strings = ["cannot assist", "policy", "content policy", "guidelines", "I'm sorry", "I apologize", "inappropriate"]
                possible_refusal = any(rs in raw_text for rs in refusal_strings)
                
                if possible_refusal:
                    print("⚠️ This appears to be an uncaught REFUSAL masquerading as malformed JSON!")
                    
                    # Try to extract conversation_id and chat_id from the prompt
                    conversation_id = "unknown"
                    chat_id = "unknown"
                    conv_id_match = re.search(r'conversation_id["\']?\s*[:=]\s*["\']?(\d+)', prompt)
                    chat_id_match = re.search(r'chat_id["\']?\s*[:=]\s*["\']?(\d+)', prompt)
                    
                    if conv_id_match:
                        conversation_id = conv_id_match.group(1)
                    if chat_id_match:
                        chat_id = chat_id_match.group(1)
                        
                    print(f"Likely refusal for conversation_id={conversation_id}, chat_id={chat_id}")
                
                # Try to extract any JSON-like content that might be embedded
                json_pattern = r'(\{.*\})'
                json_matches = re.findall(json_pattern, raw_text, re.DOTALL)
                if json_matches:
                    print("-" * 40)
                    print(f"Potential JSON content found, trying to parse...")
                    for i, match in enumerate(json_matches[:3]):  # Try first 3 matches
                        try:
                            test_json = json.loads(match)
                            print(f"Match {i+1} parsed successfully: {str(test_json)[:200]}...")
                            # If we found valid JSON with the right fields, use it
                            if all(key in test_json for key in ["summary", "entities", "topics", "emotions", "humor"]):
                                print(f"Found valid structure! Using this JSON.")
                                content = test_json
                                break
                        except Exception as nested_error:
                            print(f"Match {i+1} failed to parse: {str(nested_error)}")
                
                print("=" * 80)
                
                # If we didn't successfully extract JSON above, create a default structure
                if 'content' not in locals():
                    content = {
                        "summary": "Error parsing JSON response",
                        "entities": [],
                        "topics": [],
                        "emotions": [],
                        "humor": []
                    }
        else:
            # Just plain text
            content = raw_text

        return {
            "content": content,
            "model": model,
            "usage": usage
        }

    except Exception as e:
        print(f"Error in create_completion: {str(e)}")
        return {
            "content": "",
            "model": model,
            "error": str(e),
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0
            }
        }

def clean_unicode_text(text: str) -> str:
    """Clean up Unicode escape sequences in text."""
    if isinstance(text, str):
        # First decode any Unicode escape sequences
        text = text.encode('utf-8').decode('unicode-escape')
        
        # Replace common problematic sequences with simpler characters
        replacements = {
            '\u00e2\u0080\u0099': "'",  # Fancy apostrophe
            '\u00e2\u0080\u009c': '"',  # Fancy left quote
            '\u00e2\u0080\u009d': '"',  # Fancy right quote
            '\u00e2\u0080\u0098': "'",  # Fancy left single quote
            '\u00e2\u0080\u0093': "-",  # Em dash
            '\u00e2\u0080\u0094': "-",  # En dash
        }
        
        for old, new in replacements.items():
            text = text.replace(old, new)
            
        return text
    return text 