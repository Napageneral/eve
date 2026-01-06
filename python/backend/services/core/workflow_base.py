import logging
import time
import functools
from typing import Any, Callable

from .utils import ServiceLoggerMixin
from .utils import BaseService

__all__ = ["WorkflowBase", "workflow_run"]


def workflow_run(func: Callable) -> Callable:
    """Decorator that wraps a workflow's run() method with start/finish logging & timing."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = logging.getLogger(func.__module__)
        start = time.time()
        logger.info("[WORKFLOW] %s starting", func.__qualname__)
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start
            logger.info("[WORKFLOW] %s completed in %.2fs", func.__qualname__, duration)
            return result
        except Exception as exc:
            duration = time.time() - start
            logger.error("[WORKFLOW] %s failed after %.2fs: %s", func.__qualname__, duration, exc, exc_info=True)
            raise

    return wrapper


class WorkflowBase(BaseService):
    """Lightweight base class offering a common pattern for workflow services.

    Subclasses should implement a **@staticmethod** or **@classmethod** named *run* that
    orchestrates the business logic.  The optional :func:`workflow_run` decorator can be
    applied to *run* for automatic timing & error logging::

        class MyWorkflow(WorkflowBase):
            @staticmethod
            @workflow_run
            def run(param_a: int, param_b: str):
                # business logic here
                return {"status": "ok"}
    """

    # No additional behaviour needed for now – the mixin just provides logging helpers.
    pass 