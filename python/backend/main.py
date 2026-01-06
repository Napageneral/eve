"""Unified backend entrypoint – holds watchdog, app factory, and CLI runner."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Parent-PID watchdog (keep this *first*)
# ---------------------------------------------------------------------------

from backend.startup.monitor import start_parent_monitor

start_parent_monitor()

# ---------------------------------------------------------------------------
# Standard library & third-party imports
# ---------------------------------------------------------------------------

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.config import configure_logging, settings

# Startup helpers
from backend.startup import database, celery_check, live_sync

# Configure logging as early as possible so startup logs go to stdout/file
configure_logging()

# ---------------------------------------------------------------------------
# Router imports
# ---------------------------------------------------------------------------

from backend.routers.analysis.analysis_router import router as analysis_overview_router
from backend.routers.analysis.bulk_operations import router as bulk_analysis_router
from backend.routers.analysis.single_operations import router as single_analysis_router
from backend.routers.analysis.progress_streaming import router as progress_router
from backend.routers.analysis.progress import router as progress_poll_router
from backend.routers.analysis.runtime_metrics import router as metrics_router

from backend.routers.admin.queue_monitoring import router as admin_queue_router

from backend.routers.chats.messages import router as chat_messages_router

from backend.routers.users.profile import router as auth_router
from backend.routers.commitments.operations import router as commitment_ops_router
from backend.routers.commitments.history_analysis import router as commitment_history_router
# Deleted: context_router (migrated to Eve service)
from backend.routers.system.database import router as db_router
from backend.routers.system.imports import router as import_router
from backend.routers.system.netprobe import router as netprobe_router
from backend.routers.notify_router import router as notify_router
from backend.routers.core.utils import router as util_router
from backend.routers.core.health import router as health_router
from backend.routers.system.live_sync import router as live_sync_router
from backend.routers.chatbot import router as chatbot_router
from backend.routers.chatbot.threads import router as chatbot_threads_router
from backend.routers.embeddings import router as embeddings_router

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:  # noqa: D401
    """Return a fully configured FastAPI application instance."""

    started_at = time.time()

    app = FastAPI(title="ChatStats Backend API", version="1.0.0")

    # ---------------- Middleware -----------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: lock down in prod
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------------- Routers --------------------------------------------
    router_specs = [
        (db_router, "/api", {}),
        (import_router, "/api", {}),
        (netprobe_router, "/api", {}),
        (notify_router, "/api", {}),
        (util_router, "/api", {}),
        (health_router, "/api", {}),
        (auth_router, "/api", {}),
        # Deleted: context_router (migrated to Eve service)
        (commitment_ops_router, "/api/commitments", {"tags": ["commitments"]}),
        (commitment_history_router, "/api/commitments", {"tags": ["commitments"]}),
        (live_sync_router, "/api", {}),
        (analysis_overview_router, "/api", {"tags": ["analysis"]}),
        (bulk_analysis_router, "/api/queue", {"tags": ["analysis"]}),
        (single_analysis_router, "/api/queue", {"tags": ["analysis"]}),
        (progress_router, "/api", {"tags": ["streaming"]}),
        (progress_poll_router, "/api", {"tags": ["analysis"]}),
        (metrics_router, "/api", {"tags": ["analysis"]}),
        (chat_messages_router, "/api", {"tags": ["chats"]}),
        (admin_queue_router, "/api/queue/admin", {"tags": ["admin"]}),
        (chatbot_router, "/api", {"tags": ["chatbot"]}),
        (chatbot_threads_router, "/api", {"tags": ["chatbot"]}),
        (embeddings_router, "/api", {"tags": ["embeddings"]}),
    ]

    # Optional: billing router (subscription status)
    try:
        from backend.routers.billing import router as billing_router
        router_specs.append((billing_router, "", {}))
    except Exception as e:
        logger.warning(f"Billing router not loaded: {e}")

    for rtr, prefix, kw in router_specs:
        app.include_router(rtr, prefix=prefix, **kw)

    logger.info("Registered %d API routers", len(router_specs))

    # ---------------- Startup block --------------------------------------
    @app.on_event("startup")
    async def _startup() -> None:  # noqa: D401
        await database.apply_migrations()
        await database.seed()
        celery_check.schedule()
        await live_sync.start()
        
        # Initialize global RPS limit for circuit breaker
        try:
            from backend.infra.redis import get_redis
            r = get_redis()
            if not r.exists("llm:global_rps"):
                r.setex("llm:global_rps", 90, 450)  # Start at 450 RPS
                logger.info("[STARTUP] Initialized global RPS limit to 450")
        except Exception as e:
            logger.error("[STARTUP] Failed to initialize RPS limit: %s", e)
        
        # Schedule FAISS index rebuild on startup (non-blocking)
        try:
            from backend.celery_service.tasks.embeddings import maybe_rebuild_faiss_index_task
            # Route FAISS rebuild to embeddings-index queue
            maybe_rebuild_faiss_index_task.apply_async(queue="chatstats-embeddings-index")
            logger.info("Scheduled FAISS index maybe-rebuild on startup")
        except Exception:
            logger.exception("Failed to schedule FAISS index rebuild")
        logger.info("Startup tasks done (%.3fs)", time.time() - started_at)

    # ---------------- Diagnostic routes ----------------------------------
    @app.get("/api/ws/health")
    async def websocket_health():
        return {
            "websocket_support": True,
            "endpoints": [
                "/api/ws/chat/{chat_id}/messages",
                "/api/ws/contact_updates",
            ],
        }

    # ---------------- Request logging middleware -------------------------
    @app.middleware("http")
    async def _log_requests(request: Request, call_next):  # type: ignore[override]
        start = time.time()
        response = await call_next(request)
        logger.info(
            "%s %s → %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            (time.time() - start) * 1000,
        )
        return response

    return app


# ---------------------------------------------------------------------------
# ASGI app export
# ---------------------------------------------------------------------------


app = create_app()

# ---------------------------------------------------------------------------
# CLI runner (uvicorn)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn  # local import to keep package deps optional

    configure_logging()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level=settings.log_level.lower(),
        reload=settings.debug,
    )
