"""Centralised application configuration for the Eve backend.

This package replaces the legacy ``config.py`` + ``config_environment.py`` cluster
by exposing a single ``settings`` object (lazy-cached) and convenience constants
for backwards-compatibility.  All runtime code should preferentially import
``backend.config.settings`` or use the re-exported helpers::

    from backend.config import settings, configure_logging  # new-style

Legacy code that still does ``from backend import config`` will continue to work
because we expose the old constant names (``DB_PATH``/``APP_DIR``/etc.).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from functools import lru_cache
from typing import Final

# ---------------------------------------------------------------------------
# Pydantic compatibility layer ------------------------------------------------
# ---------------------------------------------------------------------------
# Pydantic v2 relocated ``BaseSettings`` to the *pydantic-settings* package.
# To keep a single code-path we attempt the old location first and fall back
# to the new one so the code works as-is on both major versions.

try:
    # Pydantic < 2.0
    from pydantic import BaseSettings, Field  # type: ignore
except ImportError:  # pragma: no cover – runtime path for Pydantic ≥ 2.0
    from pydantic import Field  # unchanged
    from pydantic_settings import BaseSettings  # type: ignore

# ---------------------------------------------------------------------------
# Pydantic-powered Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables (``EVE_*``)."""

    # --- generic -----------------------------------------------------------
    debug: bool = Field(False, description="Enable debug/auto-reload mode")
    log_level: str = Field("INFO", description="Root log level e.g. INFO | DEBUG")

    # --- paths -------------------------------------------------------------
    if sys.platform == "darwin":
        _default_app_dir: Final[Path] = Path.home() / "Library" / "Application Support" / "Eve"
    elif sys.platform.startswith("win"):
        _default_app_dir = Path(os.getenv("APPDATA", Path.home())) / "Eve"
    else:  # Linux and others
        _default_app_dir = Path.home() / ".local" / "share" / "Eve"

    app_dir: Path = Field(_default_app_dir, description="Root application data directory")
    # If not explicitly set, we derive it from app_dir in get_settings() to keep
    # env overrides (EVE_APP_DIR) working consistently across Pydantic v1/v2.
    db_path: Path | None = Field(None, description="SQLite DB location (defaults to app_dir/eve.db)")

    # --- broker ------------------------------------------------------------
    broker_type: str = Field("redis", description="redis | lavinmq | rabbitmq (future)")
    redis_url: str = Field("redis://localhost:6379/0", description="Redis connection string")
    
    # --- eve context engine ------------------------------------------------
    eve_http_url: str = Field("http://localhost:3032", description="Eve ODU HTTP server URL")

    # ---------------------------------------------------------------------
    # Derived helpers ------------------------------------------------------
    # ---------------------------------------------------------------------

    @property
    def broker_url(self) -> str:
        """Return the connection URL for the active broker implementation."""
        if self.broker_type == "redis":
            return self.redis_url
        # Add additional broker mappings here as they are supported.
        return self.redis_url

    # ------------------------------------------------------------------
    class Config:
        env_prefix = "EVE_"  # environment variables like EVE_DEBUG=1
        env_file = ".env"
        case_sensitive = False


# ---------------------------------------------------------------------------
# Public helpers ------------------------------------------------------------
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:  # noqa: D401 (simple function)
    """Instantiate and cache a *validated* Settings object.

    We also make sure the application directory exists so that downstream code
    can immediately write log files, databases, etc. without extra checks.
    """

    # Backwards-compat: if callers still set CHATSTATS_* vars, honor them unless
    # an explicit EVE_* override is present.
    if not os.getenv("EVE_APP_DIR") and os.getenv("CHATSTATS_APP_DIR"):
        os.environ["EVE_APP_DIR"] = os.environ["CHATSTATS_APP_DIR"]
    if not os.getenv("EVE_DB_PATH") and os.getenv("CHATSTATS_DB_PATH"):
        os.environ["EVE_DB_PATH"] = os.environ["CHATSTATS_DB_PATH"]

    s = Settings()  # env-driven, will raise `ValidationError` if invalid
    s.app_dir.mkdir(parents=True, exist_ok=True)
    if s.db_path is None:
        s.db_path = s.app_dir / "eve.db"
    return s


settings: Settings = get_settings()

# ---------------------------------------------------------------------------
# Backwards-compatibility shims --------------------------------------------
# ---------------------------------------------------------------------------

# Old constant names that scattered code might still import. They *mirror* the
# values held in the canonical Settings instance so everything stays in sync.

DEBUG: bool = settings.debug
LOG_LEVEL: str = settings.log_level.upper()
APP_DIR: Path = settings.app_dir
DB_PATH: Path = settings.db_path

# Pass-through to the new logging configurator so callers can simply do
# ``from backend.config import configure_logging``
from .logging import configure_logging, LOG_FORMAT  # noqa: E402  pylint: disable=C0413

__all__ = [
    "Settings",
    "settings",
    "get_settings",
    "configure_logging",
    # legacy constants
    "DEBUG",
    "LOG_LEVEL",
    "APP_DIR",
    "DB_PATH",
    # misc helpers
    "LOG_FORMAT",
]
