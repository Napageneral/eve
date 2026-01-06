import time
import logging
import functools
from typing import Any, Callable, Optional, Dict, List
from contextlib import contextmanager
from types import SimpleNamespace
from backend.db.session_manager import new_session


def timed(operation_name: Optional[str] = None):
    """Decorator to time function execution with standardized logging"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            op_name = operation_name or f"{func.__module__}.{func.__name__}"
            logger = logging.getLogger(func.__module__)
            
            start_time = time.time()
            logger.info(f"Starting {op_name}")
            
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                logger.info(f"Successfully completed {op_name} in {elapsed:.3f}s")
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                msg = str(e).lower()
                # Downgrade known/expected flow-control errors to WARNING (no traceback)
                if "rate limit" in msg or "too many requests" in msg or "resource_exhausted" in msg or e.__class__.__name__ in ("RateLimitError",):
                    logger.warning(f"{op_name} rate-limited after {elapsed:.3f}s: {e}")
                else:
                    logger.error(f"Failed {op_name} after {elapsed:.3f}s: {e}", exc_info=True)
                raise
        return wrapper
    return decorator


def with_session(commit: bool = True):
    """Decorator to handle database sessions automatically"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # If session is already provided, use it
            if 'session' in kwargs and kwargs['session'] is not None:
                return func(*args, **kwargs)
            
            # Otherwise create new session
            with new_session() as session:
                kwargs['session'] = session
                result = func(*args, **kwargs)
                if commit:
                    session.commit()
                return result
        return wrapper
    return decorator


class ServiceLoggerMixin:
    """Mixin for standardized logging in services"""
    
    @property
    def logger(self):
        if not hasattr(self, '_logger'):
            self._logger = logging.getLogger(self.__class__.__module__)
        return self._logger
    
    def _log_operation(self, operation: str, **details):
        """Standardized operation logging"""
        details_str = ', '.join(f"{k}={v}" for k, v in details.items())
        self.logger.info(f"[{self.__class__.__name__}] {operation}: {details_str}")
    
    def _log_debug(self, message: str, **details):
        """Standardized debug logging"""
        details_str = ', '.join(f"{k}={v}" for k, v in details.items()) if details else ""
        full_message = f"[{self.__class__.__name__}] {message}"
        if details_str:
            full_message += f" - {details_str}"
        self.logger.debug(full_message)
    
    def _log_error(self, operation: str, error: Exception, **details):
        """Standardized error logging"""
        details_str = ', '.join(f"{k}={v}" for k, v in details.items()) if details else ""
        self.logger.error(f"[{self.__class__.__name__}] {operation} failed: {error} - {details_str}", exc_info=True)


class EventPublisherMixin:
    """Mixin for event publishing"""
    
    def publish_event(self, scope: str, event_type: str, data: Dict[str, Any]):
        """Publish analysis event using the standard pattern"""
        try:
            from backend.services.conversations.analysis import ConversationAnalysisService
            ConversationAnalysisService.publish_analysis_event(scope, event_type, data)
        except Exception as e:
            logger = logging.getLogger(self.__class__.__module__)
            logger.error(f"Failed to publish event {event_type} to scope {scope}: {e}")


class BaseService(ServiceLoggerMixin, EventPublisherMixin):
    """Base class for all services with common functionality"""
    pass


# Utility functions for common patterns
def safe_json_parse(content: Any, default: Optional[Dict] = None) -> Dict:
    """Safely parse JSON content from LLM responses"""
    import json
    
    if default is None:
        default = {}
    
    if isinstance(content, dict):
        return content
    elif isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return default
    else:
        return default


def log_function_call(func_name: str, **kwargs):
    """Helper to log function calls with parameters"""
    logger = logging.getLogger(__name__)
    params = ', '.join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
    logger.debug(f"Calling {func_name}({params})")


def dict_to_obj(data_dict: Dict[str, Any]) -> SimpleNamespace:
    """Convert dictionary to object using SimpleNamespace for backward compatibility"""
    return SimpleNamespace(**data_dict)


def build_date_range(start_date: str = None, end_date: str = None, default_days: int = 365) -> Dict[str, str]:
    """
    Parse and build date range for queries with fallback defaults.
    
    Args:
        start_date: Start date in YYYY-MM-DD format (optional)
        end_date: End date in YYYY-MM-DD format (optional) 
        default_days: Default number of days to go back if start_date not provided
        
    Returns:
        Dict with 'start_date' and 'end_date' keys in YYYY-MM-DD format
    """
    from datetime import datetime, timedelta
    
    logger = logging.getLogger(__name__)
    
    # Parse end date
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            logger.debug(f"Parsed end_date: {end_dt}")
        except ValueError:
            end_dt = datetime.now()
            logger.warning(f"Failed to parse end_date '{end_date}', using current date: {end_dt}")
    else:
        end_dt = datetime.now()
        logger.debug(f"No end_date provided, using current date: {end_dt}")
    
    # Parse start date
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            logger.debug(f"Parsed start_date: {start_dt}")
        except ValueError:
            start_dt = end_dt - timedelta(days=default_days)
            logger.warning(f"Failed to parse start_date '{start_date}', using {default_days} days ago: {start_dt}")
    else:
        start_dt = end_dt - timedelta(days=default_days)
        logger.debug(f"No start_date provided, using {default_days} days ago: {start_dt}")
    
    return {
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d")
    }


def build_context_parameters(
    context_type: str,
    context_id: int,
    start_date: str = None,
    end_date: str = None,
    chat_ids: List[int] = None,
    default_days: int = 365
) -> Dict[str, Any]:
    """
    Build parameter values for context selection based on context type.
    
    Args:
        context_type: Type of context ('chat', 'contact', 'self')
        context_id: ID of the context entity
        start_date: Start date in YYYY-MM-DD format (optional)
        end_date: End date in YYYY-MM-DD format (optional)
        chat_ids: List of chat IDs for self analysis (required for 'self' type)
        default_days: Default number of days to go back
        
    Returns:
        Dict with parameter values for the context selection
    """
    logger = logging.getLogger(__name__)
    
    # Build base date range
    date_range = build_date_range(start_date, end_date, default_days)
    parameter_values = date_range.copy()
    
    # Add context-specific parameters
    if context_type == "chat":
        parameter_values["chat_id"] = context_id
        logger.debug(f"Chat context: chat_id={context_id}")
    elif context_type == "contact":
        parameter_values["contact_id"] = context_id
        parameter_values["n"] = 5  # Top N chats
        logger.debug(f"Contact context: contact_id={context_id}, n=5")
    elif context_type == "self":
        if not chat_ids:
            raise ValueError("chat_ids required for self analysis")
        parameter_values["chat_ids"] = chat_ids
        logger.debug(f"Self context: chat_ids={chat_ids}")
    else:
        raise ValueError(f"Invalid context_type: {context_type}")
    
    logger.debug(f"Built parameter values: {parameter_values}")
    return parameter_values


# Context manager for operation timing (alternative to decorator)
@contextmanager
def timed_operation(operation_name: str, logger: Optional[logging.Logger] = None):
    """Context manager for timing operations"""
    if logger is None:
        logger = logging.getLogger(__name__)
    
    start_time = time.time()
    logger.info(f"Starting {operation_name}")
    
    try:
        yield
        elapsed = time.time() - start_time
        logger.info(f"Successfully completed {operation_name} in {elapsed:.3f}s")
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Failed {operation_name} after {elapsed:.3f}s: {e}", exc_info=True)
        raise 