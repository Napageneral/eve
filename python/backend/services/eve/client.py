"""
Eve Context Engine HTTP Client

HTTP client for calling the Eve Context Engine from Python backend/Celery workers.
Eve compiles prompts with context and returns ready-to-use LLM prompts.
"""
import httpx
import logging
from typing import Dict, Any, Optional
from backend.config import settings

logger = logging.getLogger(__name__)


class EveClient:
    """HTTP client for Eve Context Engine"""
    
    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize Eve client.
        
        Args:
            base_url: Eve HTTP server URL (defaults to settings.eve_http_url or http://localhost:3031)
        """
        self.base_url = base_url or getattr(settings, 'eve_http_url', 'http://localhost:3031')
        self.client = httpx.Client(timeout=30.0)
        logger.debug(f"[EveClient] Initialized with base_url={self.base_url}")
    
    def execute_prompt(
        self,
        prompt_id: str,
        source_chat: int,
        vars: Optional[Dict[str, Any]] = None,
        budget_tokens: int = 200000,
    ) -> Dict[str, Any]:
        """
        Execute a prompt via Eve Context Engine.
        
        Args:
            prompt_id: Eve prompt ID (e.g., "convo-all-v1")
            source_chat: Source chat ID for context retrieval
            vars: Template variables to pass to prompt
            budget_tokens: Maximum tokens for context budget
        
        Returns:
            {
                "visiblePrompt": str,  # Compiled prompt ready for LLM
                "hiddenParts": [{"name": str, "text": str}],  # Context parts
                "ledger": {"totalTokens": int, "items": [...]},  # Context ledger
                "execution": {"mode": str, "resultType": str, "resultTitle": str | None}
            }
        
        Raises:
            ValueError: If Eve execution fails
        """
        payload = {
            "promptId": prompt_id,
            "sourceChat": source_chat,
            "vars": vars or {},
            "budgetTokens": budget_tokens,
        }
        
        try:
            logger.debug(
                f"[EveClient] Executing prompt {prompt_id} (chat={source_chat}, vars_keys={list((vars or {}).keys())}, budget={budget_tokens})"
            )
            
            response = self.client.post(
                f"{self.base_url}/engine/execute",
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            
            # Log success
            visible_prompt = result.get("visiblePrompt", "")
            logger.info(f"[EveClient] ✅ Compiled {prompt_id}: {len(visible_prompt)} chars")
            
            return result
            
        except httpx.HTTPStatusError as e:
            error_detail = e.response.text if e.response else str(e)
            logger.error(
                f"[EveClient] HTTP {e.response.status_code if e.response else 'error'} executing {prompt_id}: {error_detail}"
            )
            raise ValueError(f"Failed to execute prompt {prompt_id}: HTTP {e.response.status_code if e.response else 'error'}") from e
        except httpx.RequestError as e:
            logger.error(f"[EveClient] Request failed for {prompt_id}: {e}")
            raise ValueError(f"Failed to connect to Eve server at {self.base_url}: {e}") from e
        except Exception as e:
            logger.error(f"[EveClient] Unexpected error executing {prompt_id}: {e}", exc_info=True)
            raise ValueError(f"Unexpected error executing prompt {prompt_id}: {e}") from e

    def encode_conversation(self, conversation_id: int, chat_id: int) -> Dict[str, Any]:
        """Encode a conversation via Eve Context Engine (/engine/encode)."""
        payload = {"conversation_id": int(conversation_id), "chat_id": int(chat_id)}
        try:
            response = self.client.post(f"{self.base_url}/engine/encode", json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            error_detail = e.response.text if e.response else str(e)
            logger.error(
                "[EveClient] HTTP %s encoding convo=%s chat=%s: %s",
                e.response.status_code if e.response else "error",
                conversation_id,
                chat_id,
                error_detail,
            )
            raise ValueError(
                f"Failed to encode conversation {conversation_id}: HTTP {e.response.status_code if e.response else 'error'}"
            ) from e
        except httpx.RequestError as e:
            logger.error("[EveClient] Request failed for encode convo=%s chat=%s: %s", conversation_id, chat_id, e)
            raise ValueError(f"Failed to connect to Eve server at {self.base_url}: {e}") from e
        except Exception as e:
            logger.error("[EveClient] Unexpected error encoding convo=%s chat=%s: %s", conversation_id, chat_id, e, exc_info=True)
            raise ValueError(f"Unexpected error encoding conversation {conversation_id}: {e}") from e
    
    def __del__(self):
        """Cleanup HTTP client on destruction"""
        try:
            self.client.close()
        except Exception:
            pass


# Singleton instance for reuse across service calls
_eve_client_instance: Optional[EveClient] = None


def get_eve_client() -> EveClient:
    """Get or create singleton Eve client instance"""
    global _eve_client_instance
    if _eve_client_instance is None:
        _eve_client_instance = EveClient()
    return _eve_client_instance

