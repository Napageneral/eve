from enum import Enum
from typing import Dict, Optional
import time
import logging
from .client import AnthropicClient
# Batch job support deprecated – import lazily if still present
try:
    from .batches import create_batches  # type: ignore
except ImportError:  # Module removed during cleanup
    create_batches = None  # type: ignore
import json

POLLING_INTERVAL = 10  # seconds

logger = logging.getLogger(__name__)

class AnthropicModel(Enum):
    CLAUDE35 = "claude-3-5-sonnet-20240620"
    CLAUDE37 = "claude-3-7-sonnet-20250219"

    @classmethod
    def default(cls) -> "AnthropicModel":
        """Get default model (Claude 3.7)"""
        return cls.CLAUDE37

def create_message(
    prompt: str,
    model: AnthropicModel = None,
    max_tokens: int = 16000,
    temperature: Optional[float] = None,
    response_format: Optional[Dict] = None,
    client: Optional[AnthropicClient] = None
) -> Dict:
    """Create a message using Anthropic's API."""
    if client is None:
        client = AnthropicClient.get_default_client()
    
    if model is None:
        model = AnthropicModel.default()
    
    # Convert string model ID to AnthropicModel if needed
    if isinstance(model, str):
        # Remove anthropic/ prefix if present
        if model.startswith("anthropic/"):
            model_value = model.split("/")[1]
        else:
            model_value = model
            
        # Find matching AnthropicModel
        for anthropic_model in AnthropicModel:
            if anthropic_model.value == model_value:
                model = anthropic_model
                break
        else:
            # If no match found, use the default model
            print(f"[ANTHROPIC] Warning: No matching AnthropicModel for {model_value}, using default", flush=True)
            model = AnthropicModel.default()
    
    # Prepare the request parameters
    request_params = {
        "model": model.value,  # This will use the raw model name without the anthropic/ prefix
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens
    }
    
    # Add optional parameters if provided
    if temperature is not None:
        request_params["temperature"] = temperature
    
    # Handle response_format parameter
    if response_format is not None:
        print(f"[ANTHROPIC] Response format details: {response_format}", flush=True)
        
        # For simple text response format
        if response_format.get("type") == "text":
            # No special handling needed for text format
            print("[ANTHROPIC] Using text response format", flush=True)
        
        # For JSON response format
        elif response_format.get("type") == "json":
            print("[ANTHROPIC] Using JSON response format", flush=True)
            request_params["system"] = "You must respond using valid JSON."
        
        # For structured tool-based response format
        elif "schema" in response_format:
            print("[ANTHROPIC] Using schema-based response format", flush=True)
            request_params["system"] = (
                f"You must respond using the provided JSON schema. "
                f"Do not include any explanatory text, only output valid JSON."
            )
            
            # Check if we have all required fields for tool-based format
            if "name" in response_format and "description" in response_format and "schema" in response_format:
                request_params["tools"] = [
                    {
                        "name": response_format["name"],
                        "description": response_format["description"],
                        "input_schema": response_format["schema"]
                    }
                ]
                request_params["tool_choice"] = {"type": "tool", "name": response_format["name"]}
            else:
                print("[ANTHROPIC] WARNING: Incomplete tool definition in response_format. Missing name, description, or schema.", flush=True)
                print(f"[ANTHROPIC] Available keys: {list(response_format.keys())}", flush=True)
    
    # Debug request parameters
    # print("\n[ANTHROPIC] Request parameters:", flush=True)
    # print(json.dumps(request_params, indent=2), flush=True)
    
    try:
        # Make the API call
        response = client.client.messages.create(**request_params)
        
        # Debug response
        # print("\n[ANTHROPIC] API Response:", flush=True)
        # print(f"[ANTHROPIC] Response content type: {type(response.content)}", flush=True)
        # for block in response.content:
        #     print(f"[ANTHROPIC] Block type: {block.type}", flush=True)
        #     print(f"[ANTHROPIC] Block content: {block}", flush=True)
        
        # Initialize content as empty string
        content = ""
        
        # Extract content based on response type
        if response.content:
            if "tools" in request_params:
                # Handle tool-based response
                for block in response.content:
                    if block.type == "tool_use":
                        try:
                            # The content is already a dict in the input field
                            content = block.input
                            print(f"\n[ANTHROPIC] Tool use content: {content}", flush=True)
                            break
                        except Exception as e:
                            print(f"\n[ANTHROPIC] Failed to extract tool use content: {e}", flush=True)
                            content = {}
            else:
                # Handle regular text response
                content = response.content[0].text
                #print(f"\n[ANTHROPIC] Text content: {content}", flush=True)
        
        # Extract token usage data
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0),
            "output_tokens": getattr(response.usage, "output_tokens", 0),
            "total_tokens": getattr(response.usage, "total_tokens", 
                                  getattr(response.usage, "input_tokens", 0) + 
                                  getattr(response.usage, "output_tokens", 0))
        }
        
        return {
            "content": content,
            "model": model.value,
            "usage": usage
        }
    except Exception as e:
        print(f"[ANTHROPIC] Error calling API: {str(e)}", flush=True)
        import traceback
        print(f"[ANTHROPIC] Traceback: {traceback.format_exc()}", flush=True)
        
        # Return error with zero usage
        return {
            "content": "",
            "model": model.value if model else "unknown",
            "error": str(e),
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0
            }
        }

def process_batch_results(batch_id: str, client: AnthropicClient) -> Dict[str, Dict]:
    """Process results from a completed batch."""
    try:
        # Retrieve batch results using the correct API method
        results_iterator = client.client.beta.messages.batches.results(batch_id)
        results = {}
        
        # Process each result in the batch
        for result in results_iterator:
            custom_id = result.custom_id
            if result.result.type == "succeeded":
                message = result.result.message
                # Use custom_id as the conversation_id since metadata isn't available
                conversation_id = custom_id
                
                # Extract content from the message
                if message.content and len(message.content) > 0:
                    content = message.content[0].text
                    try:
                        parsed_content = json.loads(content)
                        
                        # Extract token usage data
                        usage = {
                            "input_tokens": getattr(message.usage, "input_tokens", 0),
                            "output_tokens": getattr(message.usage, "output_tokens", 0),
                            "total_tokens": getattr(message.usage, "total_tokens", 
                                                  getattr(message.usage, "input_tokens", 0) + 
                                                  getattr(message.usage, "output_tokens", 0))
                        }
                        
                        results[conversation_id] = {
                            "content": parsed_content,
                            "model": message.model,
                            "usage": usage
                        }
                        print(f"Successfully processed completion for {conversation_id}")
                    except json.JSONDecodeError:
                        print(f"Failed to parse JSON content for {conversation_id}")
                        print(f"Raw content: {content[:200]}...")
                else:
                    print(f"No content found in message for {conversation_id}")
            else:
                print(f"Request {custom_id} did not succeed. Result type: {result.result.type}")
                if result.result.type == "errored":
                    error = result.result.error
                    print(f"Error: {error}")
                    
                    # Add error result with zero usage
                    results[custom_id] = {
                        "content": "",
                        "model": "unknown",
                        "error": str(error),
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0
                        }
                    }
        
        return results
        
    except Exception as e:
        print(f"Error in process_batch_results: {str(e)}")
        print(f"Batch ID: {batch_id}")
        raise

def generate_batch_completions(
    items: Dict,
    model: AnthropicModel = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    response_format: Optional[Dict] = None,
    timeout: int = 1500,
    polling_interval: int = POLLING_INTERVAL,
    client: Optional[AnthropicClient] = None
) -> Dict[str, Dict]:
    """Generate batch completions via Anthropic batch API.

    NOTE: The dedicated batch job subsystem has been removed from ChatStats. If
    `create_batches` is unavailable this function will raise a
    `NotImplementedError` so that callers fail fast rather than crashing on an
    import error during module import time.
    """

    if create_batches is None:
        raise NotImplementedError("Batch completions are no longer supported – create_batches helper missing.")

    print(f"\n=== Starting batch completion for {len(items)} items ===")
    
    if client is None:
        client = AnthropicClient.get_default_client()
        
    if model is None:
        model = AnthropicModel.default()
    
    # Convert string model ID to AnthropicModel if needed
    if isinstance(model, str):
        # Remove anthropic/ prefix if present
        if model.startswith("anthropic/"):
            model_value = model.split("/")[1]
        else:
            model_value = model
            
        # Find matching AnthropicModel
        for anthropic_model in AnthropicModel:
            if anthropic_model.value == model_value:
                model = anthropic_model
                break
        else:
            # If no match found, use the default model
            print(f"[BATCH] Warning: No matching AnthropicModel for {model_value}, using default", flush=True)
            model = AnthropicModel.default()
    
    # Create batches
    print("Creating request batches...")
    batches = create_batches(items, request_builder=lambda cid, text: {
        "custom_id": str(cid),
        "params": {
            "model": model.value,
            "messages": [{"role": "user", "content": text}],
            **({"max_tokens": max_tokens} if max_tokens is not None else {}),
            **({"temperature": temperature} if temperature is not None else {}),
            # Add tool/schema if response format is provided
            **({
                "tools": [{
                    "name": response_format["name"],
                    "description": response_format["description"],
                    "input_schema": response_format["schema"]
                }],
                "tool_choice": {"type": "tool", "name": response_format["name"]}
            } if response_format is not None else {})
        }
    })

    
    # Submit batches
    print(f"\nSubmitting {len(batches)} batches...")
    batch_ids = []
    for batch in batches:
        try:
            batch_response = client.client.beta.messages.batches.create(requests=batch)
            batch_ids.append(batch_response.id)
            print(f"Batch {batch_response.id}: Submitted successfully")
        except Exception as e:
            print(f"Error submitting batch: {str(e)}")
            raise
    
    # Track results and progress
    results = {}
    completed_batches = set()
    failed_batches = set()
    start_time = time.time()
    
    # Monitor batches
    print("\n=== Starting batch monitoring ===")
    while len(completed_batches) + len(failed_batches) < len(batch_ids):
        current_time = time.time()
        
        # Status update
        total = len(batch_ids)
        completed = len(completed_batches)
        failed = len(failed_batches)
        pending = total - completed - failed
        elapsed = int(current_time - start_time)
        print(
            f"\nProgress: {completed}/{total} completed, {failed} failed, {pending} pending | "
            f"Elapsed: {elapsed}s"
        )
        
        # Check pending batches
        for batch_id in batch_ids:
            if batch_id in completed_batches or batch_id in failed_batches:
                continue
                
            batch = client.client.beta.messages.batches.retrieve(batch_id)
            print(f"\nBatch {batch_id} status: {batch.processing_status}")
            print(f"Request counts: {batch.request_counts}")
            
            if batch.processing_status == "ended":
                batch_results = process_batch_results(batch_id, client)
                results.update(batch_results)
                completed_batches.add(batch_id)
            elif batch.processing_status == "failed":
                print(f"Batch {batch_id} failed")
                failed_batches.add(batch_id)
        
        time.sleep(polling_interval)
    
    # Final status report
    print("\n=== Batch processing complete ===")
    print(f"Results retrieved: {len(results)}/{len(items)} items")
    
    if len(results) == 0:
        print("No results were successfully processed!")
        
    return results
