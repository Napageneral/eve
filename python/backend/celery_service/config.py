"""
Celery configuration - supports multiple brokers with DLQ support
"""
import os
from kombu import Exchange, Queue
from backend.config import settings

# Consolidated settings constants
CHATSTATS_BROKER_URL = settings.broker_url
BROKER_TYPE = settings.broker_type

class CeleryConfig:
    # Broker URL from centralized config
    broker_url = CHATSTATS_BROKER_URL
    
    # Broker-specific optimizations
    if BROKER_TYPE == 'redis':
        # Redis-specific settings for reliability
        broker_transport_options = {
            'visibility_timeout': 3600,  # 1 hour before task returns to queue
            'fanout_prefix': True,
            'fanout_patterns': True,
            'priority_steps': list(range(10)),
            'queue_order_strategy': 'priority',
            'max_connections': int(os.getenv("CELERY_BROKER_MAX_CONNECTIONS", "2000")),
        }
        result_backend = None  # Disable result backend to avoid extra Redis clients
        redis_max_connections = int(os.getenv("CELERY_REDIS_MAX_CONNECTIONS", "1000"))  # increased for high concurrency
        redis_socket_keepalive = True
        redis_socket_keepalive_options = {
            1: 3,   # TCP_KEEPIDLE
            2: 3,   # TCP_KEEPINTVL
            3: 3,   # TCP_KEEPCNT
        }
        # Result persistence settings
        # No results stored, these settings are irrelevant but kept for clarity
        result_expires = 60
        result_persistent = False
    
    # Common settings for reliability
    event_queue_expires = 60
    worker_prefetch_multiplier = int(os.getenv("CELERY_PREFETCH", "4"))
    # Let CLI/env control concurrency; 0 = Celery auto (cores) for prefork. For gevent, override via CLI.
    worker_concurrency = int(os.getenv("CELERY_WORKER_CONCURRENCY", "0"))
    # Disable Celery software rate limits; use shared Redis limiter for global caps
    worker_disable_rate_limits = True
    worker_redirect_stdouts = True
    # Lower redirected stdout/stderr noise (e.g., LiteLLM prints). Default ERROR; override via env if needed.
    worker_redirect_stdouts_level = os.getenv('CELERY_REDIRECT_STDOUTS_LEVEL', 'ERROR')
    broker_pool_limit = int(os.getenv("CELERY_BROKER_POOL_LIMIT", "2000"))
    broker_heartbeat = 10
    broker_connection_retry_on_startup = True  # Suppress Celery 6.0 deprecation warning
    
    # Task execution settings
    task_serializer = 'json'
    accept_content = ['json']
    result_serializer = 'json'
    timezone = 'UTC'
    enable_utc = True
    
    # Task reliability settings
    task_acks_late = True  # Only ack after task completes successfully
    task_reject_on_worker_lost = True  # Requeue if worker dies
    task_track_started = True
    task_send_sent_event = True
    task_store_errors_even_if_ignored = True
    
    # Default retry settings (can be overridden per task)
    # Custom backoff schedule: 6 retries @ 20s, 19 retries @ 60s, 95+ retries @ 15min
    task_default_retry_delay = 20  # Base delay (custom schedule in BaseTaskWithDLQ)
    task_max_retries = 120  # Supports up to 24+ hours of retries for extreme network issues
    task_soft_time_limit = 300  # 5 minutes soft limit
    task_time_limit = 600  # 10 minutes hard limit

    # Per-task annotations (rate limits, etc.)
    # Note: Effective per worker instance. Use a shared Redis limiter for
    # multi-worker deployments to preserve a strict global cap.
    # Rate limiting handled via backend.infra.ratelimit (global Redis token bucket)
    task_annotations = {}
    
    # Task routing with DLQ support
    task_default_queue = 'chatstats-analysis'
    task_default_exchange = 'chatstats'
    task_default_routing_key = 'analysis.default'
    # Explicit queue declarations to avoid implicit creation quirks and make
    # `-Q <queue>` worker binding deterministic across brokers
    task_queues = (
        Queue('chatstats-analysis', Exchange('chatstats')),
        Queue('chatstats-embeddings', Exchange('chatstats')),
        Queue('chatstats-embeddings-index', Exchange('chatstats')),
        Queue('chatstats-db', Exchange('chatstats')),
        Queue('chatstats-bulk', Exchange('chatstats')),
        Queue('chatstats-report', Exchange('chatstats')),
        Queue('chatstats-display', Exchange('chatstats')),
        Queue('chatstats-events', Exchange('chatstats')),
        Queue('chatstats-commitments', Exchange('chatstats')),
        Queue('chatstats-commitments-sequential', Exchange('chatstats')),
        Queue('chatstats-dlq', Exchange('chatstats')),
    )
    
    # Queue definitions including DLQ
    if BROKER_TYPE == 'redis':
        # Define queues including DLQ
        task_routes = {
            'backend.celery_service.tasks.analyze_conversation.*': {'queue': 'chatstats-analysis'},
            'celery.ca.call_llm': {'queue': 'chatstats-analysis'},
            'celery.ca.persist': {'queue': 'chatstats-db'},
            'celery.embed_conversation': {'queue': 'chatstats-embeddings'},
            'celery.embed_analysis_batch': {'queue': 'chatstats-embeddings'},
            'celery.embed_analyses_for_conversation': {'queue': 'chatstats-embeddings'},
            'celery.embeddings.maybe_rebuild_faiss_index': {'queue': 'chatstats-embeddings-index'},
            'celery.db.historic_status_upsert': {'queue': 'chatstats-db'},
            'celery.db.historic_status_finalize': {'queue': 'chatstats-db'},
            'backend.celery_service.tasks.dlq.*': {'queue': 'chatstats-dlq'},
            'backend.celery_service.tasks.send_to_dlq': {'queue': 'chatstats-dlq'},
            'backend.celery_service.tasks.conversation_sealing.*': {'queue': 'chatstats-events'},
            'backend.celery_service.tasks.commitment_history.*': {'queue': 'chatstats-commitments'},
            'celery.wait_and_finalize_global_run': {'queue': 'chatstats-bulk'},
            'celery.check_and_seal_conversations': {'queue': 'chatstats-events'},
            'celery.handle_sealed_conversation': {'queue': 'chatstats-events'},
            'celery.handle_conversation_ready': {'queue': 'chatstats-analysis'},
            'celery.force_seal_chat': {'queue': 'chatstats-events'},
            'celery.analyze_historical_commitments': {'queue': 'chatstats-commitments'},
            'celery.process_historical_conversation': {'queue': 'chatstats-commitments'},
            # Sequential processing tasks - CRITICAL: Run worker with --concurrency=1
            # celery -A backend.celery_service.app worker -Q chatstats-commitments-sequential --concurrency=1
            'celery.initialize_historical_analysis': {'queue': 'chatstats-commitments-sequential'},
            'celery.process_single_historical_conversation': {'queue': 'chatstats-commitments-sequential'},
            'celery.ask_eve': {'queue': 'chatstats-report'},
        }
    
    # Beat schedule for periodic tasks
    beat_schedule = {
        'process-dlq-items': {
            'task': 'celery.process_dlq_items',
            'schedule': 900.0,  # Every 15 minutes
        },
        'check-sealed-conversations': {
            'task': 'celery.check_and_seal_conversations',
            'schedule': 60.0,  # Every minute
        },
        'coalesced-faiss-rebuild': {
            'task': 'celery.embeddings.maybe_rebuild_faiss_index',
            'schedule': 30.0,  # Every 30 seconds (coalesced via is_dirty)
        },
    }
    
    # Beat settings
    beat_scheduler = 'celery.beat:PersistentScheduler'
    beat_schedule_filename = 'celerybeat-schedule'
    beat_log_level = 'WARNING' 