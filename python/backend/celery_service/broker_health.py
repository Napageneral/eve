"""
Broker health checking utilities.
"""
from kombu import Connection
from kombu.exceptions import OperationalError
import logging
from backend.config import settings

CHATSTATS_BROKER_URL = settings.broker_url

logger = logging.getLogger(__name__)

def broker_is_alive(url: str = None, timeout: int = 3) -> bool:
    """
    Check if the message broker is reachable and responding.
    
    Args:
        url: Broker URL (defaults to centralized config)
        timeout: Connection timeout in seconds
    
    Returns:
        True if broker is alive, False otherwise
    """
    broker_url = url or CHATSTATS_BROKER_URL
    try:
        with Connection(broker_url, connect_timeout=timeout) as conn:
            conn.connect()
        return True
    except OperationalError as e:
        logger.warning(f"Broker health check failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during broker health check: {e}")
        return False 