from __future__ import annotations

from celery import shared_task
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
import time
import random
import logging
from backend.db.session_manager import new_session

logger = logging.getLogger(__name__)


@shared_task(name="celery.db.historic_status_upsert", queue="chatstats-db")
def historic_status_upsert(user_id: int, run_id: str, total_conversations: int, status: str) -> None:
    sql = """
    INSERT INTO historic_analysis_status (user_id, status, started_at, run_id, total_conversations)
    VALUES (:user_id, :status, CURRENT_TIMESTAMP, :run_id, :total_conversations)
    ON CONFLICT (user_id) DO UPDATE SET
      status = excluded.status,
      started_at = CURRENT_TIMESTAMP,
      run_id = excluded.run_id,
      total_conversations = excluded.total_conversations,
      updated_at = CURRENT_TIMESTAMP
    """
    with new_session() as s:
        for attempt in range(6):
            try:
                s.execute(
                    text(sql),
                    {
                        "user_id": int(user_id),
                        "status": str(status),
                        "run_id": str(run_id),
                        "total_conversations": int(total_conversations),
                    },
                )
                s.commit()
                return
            except OperationalError as e:
                msg = str(e).lower()
                if "database is locked" in msg or "database is busy" in msg:
                    backoff = min(0.5, 0.05 * (2 ** attempt)) + random.random() * 0.05
                    time.sleep(backoff)
                    try:
                        s.rollback()
                    except Exception:
                        pass
                    continue
                raise


@shared_task(name="celery.db.historic_status_finalize", queue="chatstats-db", ignore_result=True, max_retries=0, autoretry_for=())
def historic_status_finalize(run_id: str, success: int, failed: int) -> None:
    """Finalize historic analysis status after all tasks complete.
    
    This is called ONCE per run_id, making it the correct place to spawn ExecutionAgents.
    """
    sql = text(
        """
        UPDATE historic_analysis_status
        SET status='completed',
            completed_at = CURRENT_TIMESTAMP,
            analyzed_conversations = :ok,
            failed_conversations = :fail,
            updated_at = CURRENT_TIMESTAMP
        WHERE run_id = :rid
        """
    )
    with new_session() as s:
        for attempt in range(6):
            try:
                s.execute(sql, {"ok": int(success), "fail": int(failed), "rid": str(run_id)})
                s.commit()
                break  # Success - exit retry loop
            except OperationalError as e:
                msg = str(e).lower()
                if "database is locked" in msg or "database is busy" in msg:
                    backoff = min(0.5, 0.05 * (2 ** attempt)) + random.random() * 0.05
                    time.sleep(backoff)
                    try:
                        s.rollback()
                    except Exception:
                        pass
                    continue
                raise
    
    # Auto-spawn ExecutionAgents for post-analysis work (Intentions + Overall analysis)
    # THIS IS THE RIGHT PLACE - called ONCE per run_id after ALL conversations complete
    try:
        import requests
        eve_port = 3032  # Eve ODU server port
        spawn_url = f"http://127.0.0.1:{eve_port}/agents/spawn-post-analysis"
        logger.info("[FINALIZE] All conversations complete for run_id=%s - spawning ExecutionAgents", run_id)
        logger.info("[FINALIZE] Analyzed: %d successful, %d failed", success, failed)
        
        response = requests.post(spawn_url, json={}, timeout=10)
        
        if response.ok:
            result = response.json()
            agent_count = len(result.get('agents', []))
            logger.info("[FINALIZE] ✅ Successfully spawned %d ExecutionAgents", agent_count)
            if agent_count > 0:
                logger.info("[FINALIZE] Agents: %s", result.get('agents'))
        else:
            error_text = response.text()[:200] if response.text else 'unknown'
            logger.warning("[FINALIZE] Failed to spawn ExecutionAgents: HTTP %d - %s", response.status_code, error_text)
    except Exception as spawn_err:
        # Don't fail the finalization if ExecutionAgent spawn fails
        logger.warning("[FINALIZE] ExecutionAgent spawn error (non-critical): %s", spawn_err, exc_info=True)


