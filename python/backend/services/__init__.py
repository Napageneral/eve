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

from __future__ import annotations

from typing import Any

# IMPORTANT:
# Do NOT import heavy services at module import-time. Many lightweight modules
# (like live-sync) import `backend.services.*` submodules; importing Celery/LLM
# machinery here would make the CLI data plane depend on the compute plane.
#
# We keep backwards-compatibility via *lazy* attribute resolution.

__all__ = [
    "ConversationAnalysisService",
    "ConversationAnalysisWorkflow",
    "BulkAnalysisWorkflowService",
    "ReportPromptService",
    "prompt",
]


def __getattr__(name: str) -> Any:  # pragma: no cover
    if name == "ConversationAnalysisService":
        from .conversations.analysis import ConversationAnalysisService
        return ConversationAnalysisService
    if name == "ConversationAnalysisWorkflow":
        from .conversations.analysis_workflow import ConversationAnalysisWorkflow
        return ConversationAnalysisWorkflow
    if name == "BulkAnalysisWorkflowService":
        from .conversations.bulk_workflow import BulkAnalysisWorkflowService
        return BulkAnalysisWorkflowService
    if name == "ReportPromptService":
        from .reports.prompt import ReportPromptService
        return ReportPromptService
    if name == "prompt":
        from .prompt import prompt
        return prompt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(list(globals().keys()) + __all__)
