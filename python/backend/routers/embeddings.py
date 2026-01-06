from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import math

from backend.routers.common import create_router, safe_endpoint, text, Session, Depends, get_db
from sqlalchemy import text as sa_text
import os
from backend.services.embeddings.faiss_index import query_topk
from array import array

router = create_router("/embeddings", tags=["embeddings"])


def _dot(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))


def _from_blob_to_vec(vb: bytes) -> List[float]:
    """Decode float32 little-endian blob to list of floats (robust across py versions)."""
    arr = array('f')
    try:
        arr.frombytes(vb)
        return list(arr)
    except Exception:
        try:
            # Older CPython exposed fromstring on array
            arr.fromstring(vb)  # type: ignore[attr-defined]
            return list(arr)
        except Exception:
            return []


@router.get("/search")
@safe_endpoint
def search_embeddings(
    q: str,
    scope: str = "messages",  # messages|analyses|artifacts|all|conversations
    user_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    k: int = 20,
    session: Session = Depends(get_db),
):
    # Embed the query using RETRIEVAL_QUERY
    try:
        from backend.celery_service.tasks.embeddings import _embed_batch
        qvec = _embed_batch([q], task_type="RETRIEVAL_QUERY", output_dim=768)[0]
    except Exception as e:
        return {"ok": False, "error": f"embed_failed: {e}"}

    # Try ANN first for speed (global index) – pull extra for grouping recall
    try:
        ann = query_topk(qvec, k=max(500, int(k) * 10))
    except Exception:
        ann = []

    # Candidate filter
    where = ["1=1", "model = 'gemini-embedding-001'", "dim = 768"]
    params: Dict[str, Any] = {}
    if user_id:
        where.append("user_id = :uid")
        params["uid"] = user_id
    if chat_id:
        where.append("(chat_id = :cid)")
        params["cid"] = chat_id
    if scope == "messages":
        where.append("source_type = 'conversation_raw'")
    elif scope == "analyses":
        where.append("source_type IN ('summary','topics','entities','emotions','humor')")
    elif scope == "artifacts":
        where.append("source_type IN ('artifact','artifact_section')")
    elif scope == "conversations":
        # Restrict to items that map cleanly to a conversation
        where.append("(conversation_id IS NOT NULL AND source_type IN ('conversation','summary','topics','entities','emotions','humor'))")

    # Prefer fetching the ANN candidate ids exactly; fall back to recents if ANN empty
    ann_ids = [rid for rid, _ in ann] if ann else []
    if ann_ids:
        id_ph = ", ".join([f":id{i}" for i in range(len(ann_ids))])
        params.update({f"id{i}": ann_ids[i] for i in range(len(ann_ids))})
        sql = text(
            f"""
            SELECT id, user_id, chat_id, conversation_id, message_id,
                   source_type, source_id, label, chunk_index, vector_blob
            FROM embeddings
            WHERE {' AND '.join(where)} AND id IN ({id_ph})
            """
        )
    else:
        sql = text(
            f"""
            SELECT id, user_id, chat_id, conversation_id, message_id,
                   source_type, source_id, label, chunk_index, vector_blob
            FROM embeddings
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC
            LIMIT 5000
            """
        )
    rows = session.execute(sql, params).mappings().all()

    results: List[Dict[str, Any]] = []
    score_override: Dict[int, float] = {rid: sc for rid, sc in ann}
    for r in rows:
        try:
            vb = r.get("vector_blob")
            if vb is None:
                continue
            vec = _from_blob_to_vec(vb)
            if not vec:
                continue
            score = score_override.get(int(r.get("id"))) if score_override else None
            if score is None:
                score = _dot(qvec, vec)
            results.append({
                "id": r.get("id"),
                "score": float(score),
                "source_type": r.get("source_type"),
                "source_id": r.get("source_id"),
                "label": r.get("label"),
                "chat_id": r.get("chat_id"),
                "conversation_id": r.get("conversation_id"),
                "message_id": r.get("message_id"),
            })
        except Exception:
            continue
    # Aggregate to conversations when requested
    if scope == "conversations":
        by_conv: Dict[str, Dict[str, Any]] = {}
        for item in results:
            cid_raw = item.get("conversation_id")
            cid = str(cid_raw or "")
            if not cid:
                continue
            rec = by_conv.get(cid)
            if not rec:
                by_conv[cid] = {
                    "id": int(cid) if cid.isdigit() else cid,
                    "conversation_id": int(cid) if cid.isdigit() else cid,
                    "chat_id": item.get("chat_id"),
                    "source_type": "conversation",
                    "score": float(item["score"]),
                    "hits": [{
                        "id": item["id"],
                        "score": float(item["score"]),
                        "source_type": item["source_type"],
                        "label": item.get("label"),
                    }],
                }
            else:
                rec["score"] = max(float(rec["score"]), float(item["score"]))
                hits = rec.setdefault("hits", [])
                if len(hits) < 8:
                    hits.append({
                        "id": item["id"],
                        "score": float(item["score"]),
                        "source_type": item["source_type"],
                        "label": item.get("label"),
                    })
        conv_results = list(by_conv.values())
        # Hydrate readable label using summary or chat title
        if conv_results:
            conv_ids = [c["conversation_id"] for c in conv_results]
            ph = ", ".join([f":c{i}" for i in range(len(conv_ids))])
            meta_sql = text(
                f"""
                SELECT c.id AS conversation_id, c.chat_id AS chat_id,
                       c.summary AS summary, cc.title AS chat_title
                FROM conversations c
                LEFT JOIN chatbot_chats cc ON cc.id = c.chat_id
                WHERE c.id IN ({ph})
                """
            )
            meta_rows = session.execute(meta_sql, {f"c{i}": conv_ids[i] for i in range(len(conv_ids))}).mappings().all()
            meta_map: Dict[str, Dict[str, Any]] = {str(r.get("conversation_id")): r for r in meta_rows}
            for r in conv_results:
                meta = meta_map.get(str(r["conversation_id"]))
                label = (meta.get("summary") if meta else None) or (meta.get("chat_title") if meta else None)
                r["label"] = label or f"Conversation {r['conversation_id']}"
        conv_results.sort(key=lambda x: x["score"], reverse=True)
        return {"ok": True, "results": conv_results[: max(1, min(200, int(k))) ]}

    # Default: return embedding rows
    results.sort(key=lambda x: x["score"], reverse=True)
    return {"ok": True, "results": results[: max(1, min(200, int(k))) ]}


# Lightweight activity signal for non-blocking UI indexing toast
@router.get("/active")
@safe_endpoint
def embeddings_active(window_seconds: int = 10, run_id: str | None = None, session: Session = Depends(get_db)):
    try:
        ws = max(1, min(120, int(window_seconds)))
    except Exception:
        ws = 10
    # SQLite-compatible relative time window
    sql = sa_text(
        """
        SELECT COALESCE(MAX(updated_at), '' ) as last_ts,
               SUM(CASE WHEN updated_at >= datetime('now', :win) THEN 1 ELSE 0 END) as recent
        FROM embeddings
        """
    )
    row = session.execute(sql, {"win": f"-{ws} seconds"}).first()
    last_ts = str(row[0] or "") if row else ""
    recent = int(row[1] or 0) if row else 0
    payload: Dict[str, Any] = {"ok": True, "active": recent > 0, "recent": recent, "window_seconds": ws, "last_updated_at": last_ts}
    # Optional per-run counters from Redis if available
    if run_id:
        try:
            import redis  # type: ignore
            url = os.getenv("CHATSTATS_METRICS_REDIS_URL") or os.getenv("REDIS_METRICS_URL") or os.getenv("CHATSTATS_REDIS_URL")
            if url:
                r = redis.Redis.from_url(url, decode_responses=True)
                total = int(r.get(f"emb:run:{run_id}:total") or 0)
                completed = int(r.get(f"emb:run:{run_id}:completed") or 0)
                failed = int(r.get(f"emb:run:{run_id}:failed") or 0)
                payload.update({"run_id": run_id, "total": total, "completed": completed, "failed": failed})
        except Exception:
            pass
    return payload

