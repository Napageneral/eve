"""
Services package - modular business logic layer

All services are now organized by domain:
- core/: shared utilities, decorators, LLM helpers
- encoding/: conversation and commitment encoding
- conversations/: analysis services and workflows  
- commitments/: commitment management
- reports/: report generation and display
- ask_eve/: dynamic prompt generation
- catalog/, context/, publish/: domain-specific services
- infra/: infrastructure and monitoring
"""

# Re-export key services for backward compatibility
from .conversations.analysis import ConversationAnalysisService
from .conversations.analysis_workflow import ConversationAnalysisWorkflow  
from .conversations.bulk_workflow import BulkAnalysisWorkflowService
from .reports.prompt import ReportPromptService
from .prompt import prompt

__all__ = [
    "ConversationAnalysisService",
    "ConversationAnalysisWorkflow", 
    "BulkAnalysisWorkflowService",
    "ReportPromptService",
    "prompt",
]
