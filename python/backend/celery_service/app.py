"""
Celery app initialization - equivalent to Temporal client.
"""
import os
import sys

# Only monkey-patch when running inside Celery worker/beat processes.
# Backend API imports this module during startup health checks; patching there
# can interfere with the web server startup.
if (
    (os.getenv("CHATSTATS_IS_CELERY_WORKER") == "1" or any("celery" in arg for arg in sys.argv))
    and os.getenv("CHATSTATS_DISABLE_GEVENT_PATCH", "0") not in ("1", "true", "True")
):
    try:
        from gevent import monkey  # type: ignore
        monkey.patch_all()
    except Exception:
        # Safe to continue without gevent; only affects worker runtime
        pass
from celery import Celery
from .config import CeleryConfig
from celery.signals import after_setup_logger
import logging

# Use the same logging config/format as the FastAPI app so logs show up
# uniformly in Electron (stdout) and in the rotating file.
try:
    from backend.config.logging import configure_logging, LOG_FORMAT  # noqa: F401
except Exception:  # pragma: no cover – fallback if config import fails very early
    configure_logging = None  # type: ignore

# Lazy initialization flag
_celery_app = None
_initialized = False

@after_setup_logger.connect
def setup_loggers(logger, *args, **kwargs):
    """
    Set log levels for Celery and other noisy loggers after Celery has set up.
    """
    # Determine desired log level from settings/env; default to WARNING to cut IO
    desired_level = os.getenv('CELERY_LOG_LEVEL', os.getenv('CHATSTATS_LOG_LEVEL', 'WARNING')).upper()
    numeric_level = getattr(logging, desired_level, logging.WARNING)
    
    # Ensure Celery worker uses the same handler/format as the backend app
    try:
        if configure_logging:
            configure_logging(force=True)
    except Exception:
        # If configure_logging fails, continue with Celery's own logger
        pass

    # Set root level as well so module loggers at INFO are not suppressed
    logging.getLogger().setLevel(numeric_level)

    # Set the level for the Celery root logger
    logger.setLevel(numeric_level)
    
    # Set the level for other loggers - keep them at the same level (allow env override)
    litellm_level = os.getenv('CHATSTATS_LITELLM_LOG_LEVEL', None)
    if litellm_level:
        logging.getLogger('litellm').setLevel(getattr(logging, litellm_level.upper(), numeric_level))
    else:
        logging.getLogger('litellm').setLevel(numeric_level)
    # Quiet noisy client/network logs unless explicitly enabled
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    # Redirected stdout/stderr (e.g., LiteLLM banners) – allow env override
    redirected_level = os.getenv('CHATSTATS_REDIRECTED_LOG_LEVEL', os.getenv('CELERY_REDIRECT_STDOUTS_LEVEL', 'ERROR')).upper()
    logging.getLogger('celery.redirected').setLevel(getattr(logging, redirected_level, logging.ERROR))
    
    # Add Celery service specific logging
    logging.getLogger('backend.celery_service').setLevel(numeric_level)
    logging.getLogger('celery').setLevel(numeric_level)
    # Make sure our package logs (backend.*) are visible
    logging.getLogger('backend').setLevel(numeric_level)
    
    # Log that we've configured Celery logging
    logger.info(f"[CELERY LOGGING] Configured (level={desired_level})")


def get_celery_app() -> Celery:
    """Get the initialized Celery app instance (lazy initialization)."""
    global _celery_app, _initialized
    
    if _celery_app is None:
        # Initialize Celery app
        _celery_app = Celery('chatstats')
        _celery_app.config_from_object(CeleryConfig)
    
    # Only autodiscover tasks when actually needed (not during import)
    if not _initialized:
        # Initialize logger first to avoid reference errors
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            # 1) Explicitly import task modules so @shared_task definitions execute in every worker
            import importlib
            for module_name in (
                'backend.celery_service.tasks.analyze_conversation',
                'backend.celery_service.tasks.live_analysis',
                'backend.celery_service.tasks.generate_document_display',
            ):
                importlib.import_module(module_name)
                logger.debug("[CELERY INIT] Imported %s", module_name)

            # 2) Also import the tasks package (belt-and-suspenders; its __init__ re-exports modules)
            from . import tasks  # noqa: F401

            # 3) Autodiscover using the parent package; Celery appends '.tasks' by default
            _celery_app.autodiscover_tasks([
                'backend.celery_service',
            ])
            
            _initialized = True
            
            # Log registered tasks for debugging (after autodiscovery completes)
            registered_tasks = list(_celery_app.tasks.keys())
            celery_tasks = [t for t in registered_tasks if 'celery.' in t]
            
            logger.debug(f"[CELERY INIT] Total registered tasks: {len(registered_tasks)}")
            logger.debug(f"[CELERY INIT] Celery-namespaced tasks: {celery_tasks}")
            
            # Check specific commitment tasks
            commitment_tasks = [t for t in registered_tasks if 'commitment' in t.lower()]
            logger.debug(f"[CELERY INIT] Commitment-related tasks: {commitment_tasks}")
            
            # Check for historical analysis tasks specifically
            historical_tasks = [t for t in registered_tasks if 'historical' in t.lower()]
            logger.debug(f"[CELERY INIT] Historical analysis tasks: {historical_tasks}")
            
            # Verify key tasks are registered
            key_tasks = [
                'celery.analyze_historical_commitments',
                'celery.initialize_historical_analysis',
                'celery.process_single_historical_conversation',
                'celery.ca.call_llm',
                'celery.ca.persist',
            ]
            
            for task_name in key_tasks:
                is_registered = task_name in registered_tasks
                logger.debug(f"[CELERY INIT] {task_name}: {'✓ REGISTERED' if is_registered else '✗ MISSING'}")
            
        except Exception as e:
            logger.error(f"[CELERY INIT] Failed to initialize Celery tasks: {e}", exc_info=True)
    
    return _celery_app

# For backward compatibility, create a lazy property
class CeleryAppProxy:
    def __getattr__(self, name):
        return getattr(get_celery_app(), name)

# Create a proxy that acts like the celery app but initializes lazily
celery_app = CeleryAppProxy()

# Health check task for broker and worker connectivity
@get_celery_app().task(name='chatstats.ping')
def ping():
    """Simple ping task for health checking broker and workers."""
    return 'pong'

# Debug task for testing task execution
@get_celery_app().task(name='chatstats.debug_task', bind=True)
def debug_task(self):
    """Debug task to verify task execution and worker connectivity."""
    import sys
    print(f'[DEBUG TASK] Request: {self.request!r}', file=sys.stderr, flush=True)
    logging.getLogger(__name__).critical(f"[DEBUG TASK] Task executed successfully: {self.request!r}")
    return {
        "status": "success",
        "task_id": self.request.id,
        "worker": self.request.hostname,
        "message": "Debug task executed successfully"
    }

# Instance ID for task naming consistency (like Temporal's INSTANCE_ID)
CELERY_INSTANCE_ID = os.getenv('CHATSTATS_CELERY_INSTANCE_ID', 'default') 