"""infra.redis – central Redis connection helper.

Provides a single get_redis() function that returns a shared redis.Redis client
backed by a connection pool.
"""

from __future__ import annotations

import os
import redis
import logging
import sys
from functools import lru_cache

from backend.config import settings

# Prefer explicit env overrides to avoid drift across processes; fallback to app settings
CHATSTATS_BROKER_URL = os.getenv("CHATSTATS_BROKER_URL") or settings.broker_url
METRICS_REDIS_URL = (
    os.getenv("CHATSTATS_METRICS_REDIS_URL")
    or os.getenv("CHATSTATS_REDIS_URL")
    or CHATSTATS_BROKER_URL
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _create_pool() -> redis.ConnectionPool:
    """Lazily create and cache a Redis connection pool."""
    keepalive_kwargs = {
        "max_connections": int(os.getenv("CHATSTATS_REDIS_POOL_MAX", "5000")),
        "socket_keepalive": True,
        "health_check_interval": 30,
    }
    if sys.platform.startswith("linux"):
        keepalive_kwargs["socket_keepalive_options"] = {1: 3, 2: 3, 3: 3}
    else:
        logger.debug("Skipping socket_keepalive_options on non-Linux platform")

    pool = redis.ConnectionPool.from_url(METRICS_REDIS_URL, **keepalive_kwargs)
    logger.info("Initialized Redis connection pool -> %s", METRICS_REDIS_URL)
    return pool


def get_redis() -> redis.Redis:  # noqa: D401
    """Return a Redis client using the shared pool."""
    return redis.Redis(connection_pool=_create_pool()) 