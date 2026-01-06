"""
Constants used across the application.
This centralizes magic constants to avoid duplication and make them easier to update.
"""
from datetime import timedelta

from backend.services.llm.model_constants import Models

# Conversation Analysis Constants (Legacy database names kept for tracking)
CA_DEFAULT_PROMPT_NAME = "ConvoAll"
CA_DEFAULT_PROMPT_CATEGORY = "conversation_analysis"
CA_DEFAULT_PROMPT_VERSION = 1

# Eve Prompt IDs (actual prompt content loaded from Eve)
CA_DEFAULT_PROMPT_ID = "convo-all-v1"
COMMITMENT_EXTRACTION_PROMPT_ID = "commitment-extraction-live-v2"
COMMITMENT_RECONCILIATION_PROMPT_ID = "commitment-reconciliation-v1"

# Analysis Status Constants
CA_STATUS_PENDING = "pending"
CA_STATUS_PROCESSING = "processing"
CA_STATUS_SUCCESS = "success"
CA_STATUS_FAILED = "failed"
CA_STATUS_FAILED_TO_QUEUE = "failed_to_queue"
CA_STATUS_SKIPPED = "skipped"
CA_STATUS_STALE = "stale"  # New status for stale processing records

# Retriable Status Constants
CA_RETRIABLE_STATUSES = [
    CA_STATUS_FAILED,
    CA_STATUS_FAILED_TO_QUEUE,
    CA_STATUS_SKIPPED
]

# Non-Retriable Status Constants
CA_NON_RETRIABLE_STATUSES = [CA_STATUS_SUCCESS, CA_STATUS_PENDING, CA_STATUS_PROCESSING]

# Cost Precision
COST_DECIMAL_PLACES = 6

# Conversation Analysis Result Batch Size (for optimization)
CA_BATCH_SIZE = 50

# How long before we consider a PROCESSING workflow to be stale
STALE_AFTER = timedelta(hours=1)

# Report Generation Constants
REPORT_DEFAULT_MODEL = Models.GEMINI_2_5_PRO
REPORT_DEFAULT_MAX_TOKENS = 65000
REPORT_DEFAULT_TEMPERATURE = 0.7

# Display Generation Constants
DISPLAY_DEFAULT_MODEL = Models.CLAUDE_4_SONNET
DISPLAY_DEFAULT_MAX_TOKENS = 16000
DISPLAY_DEFAULT_TEMPERATURE = 0.7

# Queue names
ANALYSIS_QUEUE = "chatstats-analysis"
BULK_QUEUE = "chatstats-bulk"
DLQ_QUEUE = "chatstats-dlq"
REPORT_QUEUE = "chatstats-report"
DISPLAY_QUEUE = "chatstats-display" 