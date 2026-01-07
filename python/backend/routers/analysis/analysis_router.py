# Consolidated imports
from backend.routers.common import (
    create_router, safe_endpoint, log_simple,
    HTTPException, BaseModel, text, db
)
from typing import Optional, List

router = create_router("/analysis", "Analysis Overview")

class InitializeAnalysisRequest(BaseModel):
    auth_token_for_metrics: Optional[str] = None
    top_n_chats: Optional[int] = 1  # Default: analyze top 1 chat
    time_range_days: Optional[int] = 365  # Default: last year
    start_immediately: Optional[bool] = True

@router.post("/initialize-historic-analysis")
@safe_endpoint
async def initialize_historic_analysis(request: InitializeAnalysisRequest):
    """One-time historic analysis for onboarding. Analyzes top N chats from the specified time range."""
    from backend.db.session_manager import new_session
    from sqlalchemy import text
    import asyncio
    from datetime import datetime, timedelta

    user_id = 1  # Single-user app mode

    # Check existing status
    with new_session() as session:
        row = session.execute(
            text("""
                SELECT status
                FROM historic_analysis_status
                WHERE user_id = :uid
            """),
            {"uid": user_id},
        ).fetchone()

        if row and row[0] == 'completed':
            return {"status": "already_completed", "message": "Historic analysis already completed"}
        if row and row[0] == 'running':
            return {"status": "already_running", "message": "Historic analysis is already in progress"}

    # Get top N chats by ranking (message volume in time range)
    top_n = request.top_n_chats or 1
    days = request.time_range_days or 365
    
    # Calculate since date
    since_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    with new_session() as session:
        sql = text("""
            SELECT c.id AS id
            FROM messages m
            JOIN chats c ON c.id = m.chat_id
            WHERE DATE(m.timestamp) >= :since
            GROUP BY c.id
            ORDER BY COUNT(m.id) DESC, MAX(m.timestamp) DESC
            LIMIT :limit
        """)
        rows = session.execute(sql, {"since": since_date, "limit": top_n}).fetchall()
        chat_ids = [int(r[0]) for r in rows]
    
    if not chat_ids:
        return {"status": "no_chats", "message": "No chats found in the specified time range"}
    
    # Start ranked analysis (single run for selected chats)
    from backend.celery_service.services import start_ranked_analysis
    result = await asyncio.to_thread(
        start_ranked_analysis,
        chat_ids,
        None,
        "ConvoAll",
        1,
        "conversation_analysis",
        request.auth_token_for_metrics,
    )

    # Determine initial totals from Redis snapshot (seeded by start_global_analysis)
    total = 0
    try:
        from backend.services.analysis.redis_counters import snapshot as _snap
        if result.run_id:
            snap = _snap(result.run_id)
            total = int(snap.get("total_convos", 0))
    except Exception:
        total = 0

    # Route status writes through DB queue to avoid multi-writer contention
    try:
        from backend.celery_service.tasks.db_control import (
            historic_status_upsert as _upsert,
        )
        if not result.run_id or total == 0:
            # Completed immediately
            _upsert.delay(user_id, result.run_id or "", total, "completed")
        else:
            _upsert.delay(user_id, result.run_id, total, "running")
    except Exception:
        # Fallback (best-effort) – keep original inline write to avoid breaking UX if Celery/task import fails
        with new_session() as session:
            if not result.run_id or total == 0:
                session.execute(text("""
                    INSERT INTO historic_analysis_status
                        (user_id, status, started_at, completed_at, run_id, total_conversations, analyzed_conversations, failed_conversations)
                    VALUES
                        (:uid, 'completed', :now, :now, :run_id, :total, 0, 0)
                    ON CONFLICT (user_id) DO UPDATE SET
                        status = 'completed',
                        started_at = :now,
                        completed_at = :now,
                        run_id = :run_id,
                        total_conversations = :total,
                        analyzed_conversations = 0,
                        failed_conversations = 0,
                        updated_at = CURRENT_TIMESTAMP
                """), {
                    "uid": user_id,
                    "now": datetime.utcnow(),
                    "run_id": result.run_id,
                    "total": total,
                })
            else:
                session.execute(text("""
                    INSERT INTO historic_analysis_status
                        (user_id, status, started_at, run_id, total_conversations)
                    VALUES
                        (:uid, 'running', :now, :run_id, :total)
                    ON CONFLICT (user_id) DO UPDATE SET
                        status = 'running',
                        started_at = :now,
                        run_id = :run_id,
                        total_conversations = :total,
                        updated_at = CURRENT_TIMESTAMP
                """), {
                    "uid": user_id,
                    "now": datetime.utcnow(),
                    "run_id": result.run_id,
                    "total": total,
                })
            session.commit()

    return {"status": "started", "run_id": result.run_id, "message": "Historic analysis started"}

# -------- Ranked (Top N) historic analysis ------------------------------------
class RankedStartRequest(BaseModel):
    chat_ids: Optional[List[int]] = None
    limit: Optional[int] = 5
    since: Optional[str] = None  # YYYY-MM-DD
    auth_token_for_metrics: Optional[str] = None

@router.post("/ranked/start")
@safe_endpoint
async def start_ranked_historic_analysis(req: RankedStartRequest):
    """Start a historic analysis for the top N chats (or explicit chat_ids) as one run.

    Returns a run_id so the UI can subscribe to SSE progress with the same flow
    as the global run.
    """
    from backend.db.session_manager import new_session
    from sqlalchemy import text
    from backend.celery_service.services import start_ranked_analysis

    # Resolve chat_ids from ranking if not provided
    chat_ids: List[int] = []
    if req.chat_ids and len(req.chat_ids) > 0:
        chat_ids = [int(x) for x in req.chat_ids if x is not None]
    else:
        limit = int(req.limit or 5)
        since_clause = ":since" if req.since else "date('now','-365 day')"
        sql = text(f"""
            SELECT c.id AS id
            FROM messages m
            JOIN chats c ON c.id = m.chat_id
            WHERE DATE(m.timestamp) >= {since_clause}
            GROUP BY c.id
            ORDER BY COUNT(m.id) DESC, MAX(m.timestamp) DESC
            LIMIT :limit
        """)
        params = {"limit": limit}
        if req.since:
            params["since"] = req.since
        with new_session() as session:
            rows = session.execute(sql, params).fetchall()
            chat_ids = [int(r[0]) for r in rows]

    if not chat_ids:
        return {"success": True, "message": "No chats to analyze", "run_id": None}

    result = start_ranked_analysis(
        chat_ids=chat_ids,
        prompt_name="ConvoAll",
        prompt_version=1,
        prompt_category="conversation_analysis",
        auth_token=req.auth_token_for_metrics,
    )

    return {"success": bool(result.task_id), "message": result.message, "task_id": result.task_id, "run_id": result.run_id, "chat_count": len(chat_ids)}

@router.get("/historic-analysis-status")
@safe_endpoint
async def get_historic_analysis_status():
    """Check if historic analysis is complete for the current user."""
    from backend.db.session_manager import new_session
    from sqlalchemy import text

    user_id = 1  # Single-user app mode

    with new_session() as session:
        row = session.execute(text("""
            SELECT status, started_at, completed_at,
                   total_conversations, analyzed_conversations,
                   failed_conversations, run_id
            FROM historic_analysis_status
            WHERE user_id = :uid
        """), {"uid": user_id}).fetchone()

        if not row:
            return {"status": "not_started"}

        return {
            "status": row[0],
            "started_at": row[1],
            "completed_at": row[2],
            "total_conversations": row[3],
            "analyzed_conversations": row[4],
            "failed_conversations": row[5],
            "run_id": row[6],
        }

@router.get("/chats/{chat_id}/summary")
@safe_endpoint
async def get_chat_analysis_summary(chat_id: int):
    log_simple(f"Getting analysis summary for chat {chat_id}")
    
    from backend.db.session_manager import new_session
    from backend.repositories.conversation_analysis import ConversationAnalysisRepository
    
    with new_session() as session:
        return ConversationAnalysisRepository.get_chat_analysis_summary(session, chat_id)

@router.get("/overview")
@safe_endpoint
async def get_analysis_overview():
    """Stats overview endpoint deprecated - StatsRepository removed."""
    return {"message": "Stats overview endpoint deprecated - StatsRepository removed"}

# ---- Global run introspection & settlement ---------------------------------

@router.get("/global/snapshot")
@safe_endpoint
async def global_snapshot(run_id: str):
    from backend.services.analysis.redis_counters import snapshot as get_snapshot
    return get_snapshot(run_id)


@router.get("/global/items")
@safe_endpoint
async def global_items(run_id: str):
    from backend.services.analysis.redis_counters import items_by_state
    return items_by_state(run_id)


class SettleRunRequest(BaseModel):
    run_id: str
    force: bool = False


@router.post("/global/settle")
@safe_endpoint
async def settle_run(req: SettleRunRequest):
    import os
    if os.getenv("CHATSTATS_ALLOW_SETTLER", "1").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=403, detail="Settler disabled")
    from backend.services.analysis.redis_counters import force_finalize, snapshot as snap
    final = force_finalize(req.run_id, states=("pending", "processing"))
    # Publish a normalized run_complete for the UI
    try:
        from backend.services.core.event_bus import EventBus
        payload = {"run_id": req.run_id, **snap(req.run_id)}
        EventBus.publish("historic", "run_complete", payload, enrich=False)
    except Exception:
        pass
    return {"message": "Run settled", "snapshot": final}


@router.post("/global/reconcile")
@safe_endpoint
async def reconcile_run(run_id: str):
    from backend.services.analysis.redis_counters import reconcile as _recon
    snap = _recon(run_id)
    try:
        if snap.get("is_complete"):
            from backend.services.core.event_bus import EventBus
            EventBus.publish("historic", "run_complete", {"run_id": run_id, **snap}, enrich=False)
    except Exception:
        pass
    return snap


@router.get("/global/runs")
@safe_endpoint
async def list_global_runs():
    from backend.services.analysis.redis_counters import list_snapshots
    return {"runs": list_snapshots()}


@router.get("/global/db-latch")
@safe_endpoint
async def global_db_latch(run_id: str):
    from sqlalchemy import text
    from backend.db.session_manager import new_session
    with new_session() as s:
        s.execute(text(
            """
            CREATE TABLE IF NOT EXISTS analysis_run_latch (
              run_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              started_at REAL,
              completed_at REAL
            );
            """
        ))
        row = s.execute(
            text("SELECT run_id, status, completed_at FROM analysis_run_latch WHERE run_id=:rid"),
            {"rid": run_id},
        ).fetchone()
        if not row:
            return {"run_id": run_id, "exists": False, "status": "unknown"}
        return {"run_id": row[0], "exists": True, "status": row[1], "completed_at": row[2]}

@router.get("/estimate-global-cost")
@safe_endpoint
async def estimate_global_analysis_cost():
    log_simple("Estimating global analysis cost")
    
    from backend.repositories.conversations import ConversationRepository
    # NOTE: Encoding migrated to Eve service - use HTTP endpoint for estimates
    import requests
    from backend.services.core.token import TokenService
    from backend.services.llm.models import get_pricing_for_model
    from backend.repositories.chats import ChatRepository
    
    with db.session_scope() as session:
        # Get chats with unanalyzed conversations
        unanalyzed_chats = ChatRepository.get_unanalyzed_chats(session)
        
        if not unanalyzed_chats:
            return {
                "estimated_cost": 0.0,
                "total_conversations": 0,
                "conversations_by_chat": {},
                "total_chats": 0
            }
        
        # Model configuration
        model_name = "gemini-2.0-flash"
        input_prompt_tokens = 250
        avg_output_tokens = 550
        
        # Get pricing
        pricing = get_pricing_for_model(model_name)
        if not pricing:
            raise HTTPException(status_code=500, detail=f"Pricing not found for model {model_name}")
        
        input_price_per_token = pricing.get("input", 0.0)
        output_price_per_token = pricing.get("output", 0.0)
        
        # Process each chat
        total_cost = 0.0
        total_conversations = 0
        conversations_by_chat = {}
        
        for chat_row in unanalyzed_chats:
            chat_id = chat_row["chat_id"]
            chat_name = chat_row["chat_name"] or chat_row["chat_identifier"]
            
            # Get unanalyzed conversation IDs
            conv_ids = session.execute(text("""
                SELECT c.id
                FROM conversations c
                LEFT JOIN conversation_analyses ca ON ca.conversation_id = c.id
                WHERE c.chat_id = :chat_id 
                AND (ca.id IS NULL OR ca.status NOT IN ('success', 'processing'))
            """), {"chat_id": chat_id}).fetchall()
            
            if not conv_ids:
                continue
            
            # Calculate tokens for conversations
            total_chat_tokens = 0
            chat_conversation_count = len(conv_ids)
            
            for conv_row in conv_ids:
                conv_id = conv_row[0]
                try:
                    # Use Eve encoding service via HTTP
                    base_url = getattr(settings, "eve_http_url", "http://127.0.0.1:3031").rstrip("/")
                    resp = requests.post(
                        f"{base_url}/engine/encode",
                        json={'conversation_id': conv_id, 'chat_id': chat_id},
                        timeout=10
                    )
                    if resp.ok:
                        data = resp.json()
                        conversation_text = data.get('encoded_text', '')
                        total_chat_tokens += data.get('token_count', 1000)  # Eve returns exact count
                    else:
                        total_chat_tokens += 1000  # Fallback if Eve unavailable
                        
                except Exception:
                    total_chat_tokens += 1000  # Fallback
            
            # Calculate cost for this chat
            total_input_tokens = (input_prompt_tokens * chat_conversation_count) + total_chat_tokens
            total_output_tokens = avg_output_tokens * chat_conversation_count
            
            chat_cost = (total_input_tokens * input_price_per_token) + (total_output_tokens * output_price_per_token)
            
            total_cost += chat_cost
            total_conversations += chat_conversation_count
            
            conversations_by_chat[str(chat_id)] = {
                "chat_name": chat_name,
                "conversation_count": chat_conversation_count,
                "estimated_cost": chat_cost,
                "token_count": total_chat_tokens,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens
            }
        
        log_simple(f"Cost estimation complete: ${total_cost:.4f} for {total_conversations} conversations")
        
        return {
            "estimated_cost": total_cost,
            "total_conversations": total_conversations,
            "total_chats": len(conversations_by_chat),
            "conversations_by_chat": conversations_by_chat,
            "model": model_name,
            "avg_output_tokens": avg_output_tokens
        }