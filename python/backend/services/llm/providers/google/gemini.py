import os
import re
import requests
import json
import time
import random
import hashlib
from typing import Dict, Optional, Any, List

# In-memory token count cache with TTL
TOKEN_COUNT_CACHE = {}
TOKEN_COUNT_CACHE_TTL = 600  # 10 minutes in seconds

# Maximum token limits for Gemini models
GEMINI_MAX_TOTAL = 1_048_576  # Hard limit for input + output tokens
GEMINI_MAX_OUT = 65_536       # Hard limit for output tokens

def extract_json_from_markdown(content: str) -> str:
    """
    Helper function to parse JSON from a markdown code block if present.
    Also tries to extract JSON without markdown formatting if needed.
    """
    # First try to find JSON in a markdown code block
    json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
    if json_match:
        return json_match.group(1)
    
    # If no markdown block, try to find JSON between curly braces
    json_start = content.find('{')
    json_end = content.rfind('}')
    if json_start >= 0 and json_end > json_start:
        potential_json = content[json_start:json_end+1]
        try:
            # Validate it's actually JSON
            json.loads(potential_json)
            return potential_json
        except json.JSONDecodeError:
            pass
    
    return content


def simplify_schema(schema: Dict[str, Any], defs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Recursively convert a Pydantic JSON schema into the simplified Gemini schema.
    
    - Removes keys: "$defs", "title", "description", "additionalProperties"
    - Converts type strings to upper-case (e.g. "string" -> "STRING")
    - Resolves $ref references if possible.
    """
    if defs is None and "$defs" in schema:
        defs = schema["$defs"]

    simplified = {}
    for key, value in schema.items():
        if key in {"$defs", "title", "description", "additionalProperties"}:
            continue

        if key == "type" and isinstance(value, str):
            simplified[key] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            new_props = {}
            for prop_name, prop_schema in value.items():
                # Handle a $ref if present.
                if isinstance(prop_schema, dict) and "$ref" in prop_schema:
                    ref_val = prop_schema["$ref"]
                    # Expecting a ref in the format "#/$defs/SomeKey"
                    if ref_val.startswith("#/$defs/") and defs:
                        ref_key = ref_val[len("#/$defs/"):]
                        if ref_key in defs:
                            new_props[prop_name] = simplify_schema(defs[ref_key], defs)
                        else:
                            new_props[prop_name] = {}
                    else:
                        new_props[prop_name] = simplify_schema(prop_schema, defs)
                elif isinstance(prop_schema, dict):
                    new_props[prop_name] = simplify_schema(prop_schema, defs)
                else:
                    new_props[prop_name] = prop_schema
            simplified[key] = new_props
        elif isinstance(value, dict):
            simplified[key] = simplify_schema(value, defs)
        elif isinstance(value, list):
            simplified_list = []
            for item in value:
                if isinstance(item, dict):
                    simplified_list.append(simplify_schema(item, defs))
                else:
                    simplified_list.append(item)
            simplified[key] = simplified_list
        else:
            simplified[key] = value
    return simplified


def try_parse_json(content: str) -> Any:
    """
    Try to parse a string as JSON, returning the parsed object if successful.
    If parsing fails, return the original string.
    """
    if not content or not isinstance(content, str):
        return content
        
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content


def create_gemini_completion(
    prompt: str,
    model: str,
    max_tokens: int = 65000,
    temperature: float = 0.7,
    response_format: Optional[dict] = None,  # Now expecting a dict for JSON schema
) -> Dict:
    """
    Creates a completion against Google's Gemini model using raw HTTP.
    
    If response_format is provided as a JSON schema dict, this function will convert
    it to Gemini's format (uppercasing types) and pass it as 'response_schema' in 
    generationConfig so Gemini returns structured JSON matching that schema.
    """
    # 1) Get your Google Generative AI key from environment
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
    if not api_key:
        raise ValueError("No GEMINI_API_KEY found in environment.")

    # 2) Build the endpoint URL with API key as query parameter.
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    model_name = model
    # Handle OpenRouter style model ids
    if "/" in model:
        provider, model_id = model.split("/", 1)
        if provider == "google":
            model_name = model_id
    
    endpoint = f"{base_url}/models/{model_name}:generateContent?key={api_key}"

    # 3) Prepare headers.
    headers = {
        "Content-Type": "application/json"
    }

    # 4) Build payload.
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "topP": 0.95,
            "topK": 40
        }
    }

    # Determine if the model supports parsing (all modern Gemini models do)
    supports_parse = True
    # If structured output is desired, supply the proper schema and MIME type.
    if response_format and supports_parse:
        payload["generationConfig"]["response_mime_type"] = "application/json"
        
        # Convert the schema to Gemini format (uppercase types, etc.)
        gemini_schema = simplify_schema(response_format)
        
        # Add property ordering if needed to preserve order of top-level keys
        if "properties" in gemini_schema and isinstance(gemini_schema["properties"], dict):
            top_level_keys = list(gemini_schema["properties"].keys())
            if len(top_level_keys) > 0:
                gemini_schema["propertyOrdering"] = top_level_keys
        
        payload["generationConfig"]["response_schema"] = gemini_schema

    # Add safety settings to disable all filters
    payload["safetySettings"] = [
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}
    ]

    # 5) Make the HTTP request with retry logic for 502 errors
    max_retries = 3
    retry_delay_base = 2  # Base delay in seconds
    
    for retry_count in range(max_retries):
        try:
            if retry_count > 0:
                # Add jitter to avoid thundering herd problem
                jitter = random.uniform(0, 1)
                retry_delay = (retry_delay_base ** retry_count) + jitter
                time.sleep(retry_delay)
                
            # Increase timeout for large responses
            response = requests.post(endpoint, headers=headers, json=payload, timeout=300)
            
            #print(f"[GEMINI] Response Status Code: {response.status_code}", flush=True)
            # print(f"[GEMINI] Response: {response.json()}", flush=True)

            if response.status_code == 502:
                if retry_count == max_retries - 1:
                    response.raise_for_status()
                continue
            elif response.status_code != 200:
                response.raise_for_status()
                
            # If we get here, the request was successful
            break
            
        except requests.exceptions.RequestException as e:
            if retry_count < max_retries - 1 and "502 Server Error: Bad Gateway" in str(e):
                continue
            else:
                # Return a response with error and empty usage
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
    else:
        # This executes if the for loop completes without a break
        return {
            "content": "",
            "model": model,
            "error": "All retry attempts to Gemini API failed with 502 errors",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0
            }
        }

    # 6) Parse the response.
    try:
        result_json = response.json()
    except Exception as e:
        print(f"[GEMINI] Failed to parse response JSON: {str(e)}", flush=True)
        return {
            "content": "",
            "model": model,
            "error": f"Failed to parse response JSON: {str(e)}",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0
            }
        }

    # Process candidates.
    #print(f"[GEMINI] Result JSON: {result_json}", flush=True)
    candidates = result_json.get("candidates", [])
    if not candidates:
        return {
            "content": "",
            "model": model,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0
            }
        }

    # Extract the text content.
    #print(f"[GEMINI] Candidates: {candidates}", flush=True)
    result_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")

    # If structured output was expected, parse the response as JSON
    if response_format:
        # For models that don't support structured outputs, we'll try to extract JSON from the text
        if not supports_parse:
            # Extract JSON from markdown or raw text
            result_content = extract_json_from_markdown(result_content)
        
        # Try to parse the content as JSON
        try:
            parsed_content = json.loads(result_content)
            result_content = parsed_content  # Use the parsed JSON object directly
        except json.JSONDecodeError:
            pass
        
    # Extract standardized token usage data
    usage_metadata = result_json.get("usageMetadata", {})
    usage_dict = {
        "input_tokens": usage_metadata.get("promptTokenCount", 0),
        "output_tokens": usage_metadata.get("candidatesTokenCount", 0),
        "total_tokens": usage_metadata.get("totalTokenCount", 0)
    }
    
    # 7) Return the final response.
    final_response = {
        "content": result_content,
        "model": model,
        "usage": usage_dict
    }
    
    return final_response


def create_gemini_multimodal_completion(
    prompt: str,
    model: str,
    max_tokens: int = 65000,
    temperature: float = 0.7,
    response_modalities: List[str] = ["Text", "Image"],
) -> Dict:
    """
    Creates a completion against Google's Gemini model with multimodal output support.
    
    This function is specifically designed for models like gemini-2.0-flash-exp that
    can generate both text and images in a single response.
    
    Args:
        prompt: The text prompt for generation
        model: The model ID string (e.g., "google/gemini-2.0-flash-exp")
        max_tokens: Maximum tokens to generate
        temperature: Temperature for generation
        response_modalities: List of modalities to include in response (e.g., ["Text", "Image"])
        
    Returns:
        A dictionary containing the response with both text content and image URLs
    """
    # 1) Get your Google Generative AI key from environment
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
    if not api_key:
        raise ValueError("No GEMINI_API_KEY found in environment.")

    # 2) Build the endpoint URL with API key as query parameter.
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    model_name = model
    # Handle OpenRouter style model ids
    if "/" in model:
        provider, model_id = model.split("/", 1)
        if provider == "google":
            model_name = model_id
    
    endpoint = f"{base_url}/models/{model_name}:generateContent?key={api_key}"

    # 3) Prepare headers.
    headers = {
        "Content-Type": "application/json"
    }

    # 4) Build payload.
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "topP": 0.95,
            "topK": 40,
            "responseModalities": response_modalities  # This is the key difference for multimodal
        }
    }

    # Add safety settings to disable all filters
    payload["safetySettings"] = [
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}
    ]

    print(f"[GEMINI] Endpoint: {endpoint}", flush=True)
    print(f"[GEMINI] Headers: {headers}", flush=True)
    payload_to_print = {k: v for k, v in payload.items() if k != 'contents'}
    print(f"[GEMINI] Payload (excluding contents): {payload_to_print}", flush=True)

    # 5) Make the HTTP request.
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=60)  # Longer timeout for image gen
        if response.status_code != 200:
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        import traceback
        return {
            "text_content": "",
            "image_urls": [],
            "image_data": [],
            "model": model,
            "error": str(e),
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0
            }
        }

    # 6) Parse the response.
    try:
        result_json = response.json()
    except Exception as e:
        return {
            "text_content": "",
            "image_urls": [],
            "image_data": [],
            "model": model,
            "error": f"Failed to parse response JSON: {str(e)}",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0
            }
        }

    # Process candidates.
    candidates = result_json.get("candidates", [])
    if not candidates:
        return {
            "text_content": "",
            "image_urls": [],
            "image_data": [],
            "model": model,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0
            }
        }

    # Extract the content parts (text and images)
    content_parts = candidates[0].get("content", {}).get("parts", [])
    
    # Process the parts to extract text and images
    text_content = []
    image_urls = []
    image_data = []
    
    for part in content_parts:
        if "text" in part:
            text_content.append(part["text"])
        
        if "inlineData" in part:
            mime_type = part["inlineData"].get("mimeType", "")
            if mime_type.startswith("image/"):
                # For base64 encoded images
                image_b64 = part["inlineData"].get("data", "")
                image_data.append({
                    "mime_type": mime_type,
                    "data": image_b64
                })
        
        if "fileData" in part:
            # For file URLs
            file_uri = part["fileData"].get("fileUri", "")
            if file_uri:
                image_urls.append(file_uri)

    # Extract standardized token usage data
    usage_metadata = result_json.get("usageMetadata", {})
    usage_dict = {
        "input_tokens": usage_metadata.get("promptTokenCount", 0),
        "output_tokens": usage_metadata.get("candidatesTokenCount", 0),
        "total_tokens": usage_metadata.get("totalTokenCount", 0)
    }

    # 7) Return the final response.
    final_response = {
        "text_content": "\n".join(text_content),
        "image_urls": image_urls,
        "image_data": image_data,
        "model": model,
        "usage": usage_dict
    }
    return final_response


def count_tokens_gemini(content: str, model: str = "google/gemini-2.5-pro-preview-05-06") -> int:
    """
    Count tokens for a text string using Google's countTokens API.
    Uses in-memory caching with 10-minute TTL to avoid redundant API calls.
    
    Args:
        content: Text content to count tokens for
        model: Model ID to use for token counting
        
    Returns:
        Integer count of tokens
    """
    # Generate cache key using content hash
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    cache_key = f"{model}:{content_hash}"
    
    # Check cache first
    current_time = time.time()
    print(f"[GEMINI] Checking cache for key: {cache_key[:20]}...")
    
    if cache_key in TOKEN_COUNT_CACHE:
        cache_entry = TOKEN_COUNT_CACHE[cache_key]
        if current_time - cache_entry['timestamp'] < TOKEN_COUNT_CACHE_TTL:
            print(f"[GEMINI] Cache hit, returning token_count: {cache_entry['token_count']}")
            return cache_entry['token_count']
    
    # Clean up expired cache entries
    expired_keys = [k for k, v in TOKEN_COUNT_CACHE.items() 
                   if current_time - v['timestamp'] > TOKEN_COUNT_CACHE_TTL]
    for key in expired_keys:
        del TOKEN_COUNT_CACHE[key]
    
    # Handle OpenRouter style model ids
    model_name = model
    if "/" in model:
        provider, model_id = model.split("/", 1)
        if provider == "google":
            model_name = model_id
    
    print(f"[GEMINI] Cache miss, counting tokens for content length: {len(content)} chars")
    
    # Prepare the API request
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY")
    if not api_key:
        print("[GEMINI] Error: No GEMINI_API_KEY found in environment")
        raise ValueError("No GEMINI_API_KEY found in environment.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:countTokens?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {
                "parts": [{"text": content}]
            }
        ]
    }
    
    print(f"[GEMINI] Sending request to API endpoint: {url.split('?')[0]}")
    
    # Call the API
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"[GEMINI] Response status code: {response.status_code}")
        
        response.raise_for_status()
        result = response.json()
        print(f"[GEMINI] Response JSON: {result}")
        
        token_count = result.get("totalTokens", 0)
        
        # Cache the result
        TOKEN_COUNT_CACHE[cache_key] = {
            'token_count': token_count,
            'timestamp': current_time
        }
        
        print(f"[GEMINI] Cached token_count: {token_count}")
        return token_count
    except Exception as e:
        print(f"[GEMINI] API error: {str(e)}")
        # Make a rough estimate as fallback (4 chars per token)
        estimated_count = len(content) // 4
        print(f"[GEMINI] Fallback to estimated token_count: {estimated_count}")
        return estimated_count
