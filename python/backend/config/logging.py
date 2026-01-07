"""Root logging configuration for the Eve backend.

This centralises *all* logger setup so that the rest of the codebase can
simply call::

    from backend.config.logging import configure_logging
    configure_logging()

or, equivalently::

    from backend.config import configure_logging

Repeated calls are safe – the function is idempotent unless ``force=True`` is
passed.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from . import settings  # circular-safe: this module is imported *after* settings
import json

# --- Module-scope, worker-safe filter -----------------
class LiteLLMSpamFilter(logging.Filter):
    """Drop the repetitive LiteLLM help/debug footers."""
    DROP = (
        "Give Feedback / Get Help: https://github.com/BerriAI/litellm",
        "LiteLLM.Info: If you need to debug this error, use `litellm._turn_on_debug()",
    )
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True  # never fail logging
        return not any(s in msg for s in self.DROP)

# Keep the old log format so existing parsing tools continue to work
LOG_FORMAT: Final[str] = (
    "%(asctime)s  %(levelname)-8s  [%(name)s]  %(filename)s:%(lineno)d  %(message)s"
)

# Simple ANSI colorizer for console output (no extra deps)
_ANSI_RESET = "\033[0m"
_COLORS = {
    "DEBUG": "\033[90m",      # bright black / grey
    "INFO": "\033[36m",       # cyan (backend default)
    "WARNING": "\033[33m",    # yellow
    "ERROR": "\033[31m",      # red
    "CRITICAL": "\033[35m",   # magenta
}


class ColorFormatter(logging.Formatter):
    def __init__(self, fmt: str, use_color: bool = True) -> None:
        super().__init__(fmt)
        self._use_color = use_color and sys.stdout.isatty()
        # Precompute default record keys so we can identify custom extras
        self._default_keys = set(logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None).__dict__.keys())

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # First, format the base message
        base_msg = super().format(record)

        # Append any custom extras (safe JSON) for visibility
        try:
            extras = {k: v for k, v in record.__dict__.items() if k not in self._default_keys}
            if extras:
                safe = {k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v)) for k, v in extras.items()}
                base_msg = f"{base_msg}  |  {json.dumps(safe, ensure_ascii=False)}"
        except Exception:
            pass

        if self._use_color:
            color = _COLORS.get(record.levelname, "")
            # Colorize the levelname and the logger name for quick scanning
            original_levelname = record.levelname
            original_name = record.name
            try:
                record.levelname = f"{color}{record.levelname}{_ANSI_RESET}"
                # Emphasize our package logs
                if original_name.startswith("backend"):
                    record.name = f"\033[36m{original_name}{_ANSI_RESET}"  # cyan
                msg = base_msg
            finally:
                record.levelname = original_levelname
                record.name = original_name
            return msg
        return base_msg


def _attach_handler(root: logging.Logger, handler: logging.Handler, *, replace: bool = False) -> None:
    """Utility to (optionally) replace an already-installed handler of same type."""
    if replace:
        for h in tuple(root.handlers):
            if isinstance(h, type(handler)):
                root.removeHandler(h)
    root.addHandler(handler)


def configure_logging(*, force: bool = False) -> None:  # noqa: D401 (simple function)
    """Initialise the root logger with console + rotating-file handlers.

    Parameters
    ----------
    force
        Remove any pre-existing handlers even if they weren't added by a
        previous call to ``configure_logging``.  Useful in reload contexts.
    """

    root = logging.getLogger()

    # Short-circuit if already configured
    if root.handlers and not force:
        return

    # ------------------------------------------------------------------
    # House-keeping -----------------------------------------------------
    # ------------------------------------------------------------------
    # Flush + drop all previous handlers when *force* is requested
    if force:
        for h in tuple(root.handlers):
            h.flush()
            root.removeHandler(h)

    root.setLevel(settings.log_level.upper())

    # ------------------------------------------------------------------
    # File handler ------------------------------------------------------
    # ------------------------------------------------------------------
    log_file: Path = settings.app_dir / "backend.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5_000_000,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    # ------------------------------------------------------------------
    # Console handler ---------------------------------------------------
    # ------------------------------------------------------------------
    # For CLI usage we keep JSON output on stdout and send logs to stderr.
    # The Electron app (ChatStats legacy) expects stdout. Allow env override.
    log_to_stderr = os.getenv("EVE_LOG_TO_STDERR", "0").lower() in ("1", "true", "yes", "on")
    console_handler = logging.StreamHandler(sys.stderr if log_to_stderr else sys.stdout)
    console_handler.setFormatter(ColorFormatter(LOG_FORMAT, use_color=True))

    # Attach handlers (replacing same-type ones when *force* is True)
    _attach_handler(root, file_handler, replace=force)
    _attach_handler(root, console_handler, replace=force)
    
    # Apply the spam filter only to LiteLLM-specific loggers.
    # IMPORTANT: do not attach to the root logger or celery loggers (breaks worker boot on spawn).
    for name in ("litellm", "LiteLLM"):
        logging.getLogger(name).addFilter(LiteLLMSpamFilter())

    # Duplicate ERROR-and-above to stderr so external supervisors (Electron) reliably capture them
    try:
        attach_stderr = (os.getenv("EVE_STDERR_ERRORS") or os.getenv("CHATSTATS_STDERR_ERRORS") or "1").lower() in ("1", "true", "yes")
    except Exception:
        attach_stderr = True
    if attach_stderr:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.ERROR)
        stderr_handler.setFormatter(ColorFormatter(LOG_FORMAT, use_color=True))
        _attach_handler(root, stderr_handler, replace=False)

    # Reduce noise: allow callers to control backend verbosity via env while leaving
    # the rest of the ecosystem at the configured root level.  The root level was
    # already set earlier to ``settings.log_level``; avoid clobbering it back to
    # WARNING so that backend.* INFO logs continue to flow to stdout (and through
    # the Electron bridge).
    backend_level = (os.getenv("EVE_BACKEND_LOG_LEVEL") or os.getenv("CHATSTATS_BACKEND_LOG_LEVEL") or settings.log_level).upper()
    logging.getLogger("backend").setLevel(getattr(logging, backend_level, logging.INFO))

    # Noisy third-party loggers → WARNING/ERROR
    for noisy in ("httpx", "httpcore", "celery.worker.consumer.mingle", "celery.apps.worker", "celery.worker.strategy", "celery.app.trace"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # LiteLLM often logs via multiple names; default to WARNING but allow override via env
    litellm_level = (os.getenv("EVE_LITELLM_LOG_LEVEL") or os.getenv("CHATSTATS_LITELLM_LOG_LEVEL") or "WARNING").upper()
    for name in ("litellm", "LiteLLM", "litellm.logging"):
        logging.getLogger(name).setLevel(getattr(logging, litellm_level, logging.WARNING))
    # Redirected stdout/stderr (e.g., LiteLLM banners) – allow env override
    redirected_level = (os.getenv("EVE_REDIRECTED_LOG_LEVEL") or os.getenv("CHATSTATS_REDIRECTED_LOG_LEVEL") or os.getenv("CELERY_REDIRECT_STDOUTS_LEVEL") or "ERROR").upper()
    logging.getLogger("celery.redirected").setLevel(getattr(logging, redirected_level, logging.ERROR))

    # Make uvicorn log through the root logger so everything follows the same
    # format.  Strip handlers and propagate. Default to WARNING to reduce noise.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []  # type: ignore[attr-defined]
        uv_logger.propagate = True
        # Reduce uvicorn verbosity unless explicitly overridden elsewhere
        uv_logger.setLevel(logging.WARNING)

    logging.getLogger("backend").info(
        "Logging configured | backend_level=%s | file=%s", backend_level, log_file
    )


__all__ = ["configure_logging", "LOG_FORMAT"]
