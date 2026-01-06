# Core service constants - extracted from various services to reduce token usage

# Reaction mappings (from encoding_service.py)
REACTION_EMOJIS = {
    2000: '❤️',  # Love
    2001: '👍',  # Like
    2002: '👎',  # Dislike
    2003: '😂',  # Laugh
    2004: '‼️',  # Emphasis
    2005: '❓',  # Question
}

# Context window configuration (from encoding_service.py)
DEFAULT_CONTEXT_WINDOW = 30  # Target total messages across all conversations
MIN_PREVIOUS_CONVERSATIONS = 1  # Always include at least this many previous conversations
LOOKBACK_DAYS = 7

# Stream scopes and event patterns
STREAM_SCOPE_TEMPLATE = "commitments:{chat_id}"
ANALYSIS_SCOPE_TEMPLATE = "analysis:{chat_id}"

# Common model configurations
class DefaultModels:
    """Default models for different operations - centralized to avoid hardcoding"""
    FAST_MODEL = "gemini/gemini-2.0-flash"
    FALLBACK_MODEL = "openai/gpt-4o-mini"
    REPORT_MODEL = "gemini/gemini-2.5-pro"
    DISPLAY_MODEL = "claude/claude-3-5-sonnet"
    ANALYSIS_MODEL = "gemini/gemini-2.0-flash"

# Common LLM configurations
class DefaultLLMConfigs:
    """Standard LLM configurations for different operations"""
    EXTRACTION = {
        "model_name": DefaultModels.FAST_MODEL,
        "temperature": 0.3,
        "max_tokens": 4000,
    }
    
    RECONCILIATION = {
        "model_name": DefaultModels.FAST_MODEL,
        "temperature": 0.4,
        "max_tokens": 4000,
    }
    
    TITLE_GENERATION = {
        "model_name": DefaultModels.FALLBACK_MODEL,
        "temperature": 0.4,
        "max_tokens": 100,
    }
    
    REPORT_GENERATION = {
        "model_name": DefaultModels.REPORT_MODEL,
        "temperature": 0.7,
        "max_tokens": 65000,
    }

# Prompt categories and names (integrates with existing /prompts structure)
class PromptCategories:
    CONVERSATION_ANALYSIS = "conversation_analysis"
    REPORT_GENERATION = "report_generation"
    DISPLAY_GENERATION = "display_generation"
    TITLE_GENERATION = "title_generation"

# Common prompt names (matches existing prompt directories)
class PromptNames:
    COMMITMENT_EXTRACTION = "CommitmentExtractionLive"
    COMMITMENT_RECONCILIATION = "CommitmentReconciliation"
    CONVO_ALL = "ConvoAll"
    DISPLAY_GENERATION = "DisplayGeneration"
    TITLE_GENERATION = "TitleGeneration"

# Log formatting constants
class LogFormats:
    LLM_CALL_PREFIX = "[LLM-CALL]"
    COMMIT_PREFIX = "[COMMIT]"
    REPORT_PREFIX = "[REPORT]"
    
# File size and content limits
MAX_PROMPT_PREVIEW_LENGTH = 500
MAX_RESPONSE_PREVIEW_LENGTH = 500
MAX_TOKEN_COUNT_DISPLAY = 200000

# Default timeouts and retries
DEFAULT_LLM_TIMEOUT = 300  # 5 minutes
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_DELAY = 1  # seconds 

from backend.services.llm.model_constants import Models


# ------------------------------------------------------------------
# Task default LLM settings (Phase 2 refactor)
# ------------------------------------------------------------------
class TaskDefaults:
    """Centralised default LLM configs used by celery tasks after refactor."""

    # Conversation Analysis
    CA_MODEL = Models.GEMINI_2_0_FLASH
    CA_TEMPERATURE = 0.3
    CA_MAX_TOKENS = 10_000

    # Report Generation
    REPORT_MODEL = Models.GEMINI_2_5_PRO
    REPORT_TEMPERATURE = 0.7
    REPORT_MAX_TOKENS = 65_000

    # Display Generation
    DISPLAY_MODEL = Models.CLAUDE_4_SONNET
    DISPLAY_TEMPERATURE = 0.7
    DISPLAY_MAX_TOKENS = 16_000

    # Ask Eve
    ASK_EVE_MODEL = Models.GEMINI_2_5_PRO
    ASK_EVE_TEMPERATURE = 1.0
    ASK_EVE_MAX_TOKENS = 8_000 