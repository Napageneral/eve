from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional, Tuple
import struct
import os

from celery import shared_task
from sqlalchemy import text as sa_text

from backend.celery_service.tasks.base import BaseTaskWithDLQ
from backend.db.session_manager import new_session

logger = logging.getLogger(__name__)

# Global flag to avoid repeatedly attempting legacy batch paths that return 0 vectors
_LEGACY_BATCH_DISABLED = False

def _metrics_redis():
    try:
        url = os.getenv("CHATSTATS_METRICS_REDIS_URL") or os.getenv("REDIS_METRICS_URL") or os.getenv("CHATSTATS_REDIS_URL")
        if not url:
            return None
        import redis  # type: ignore
        return redis.Redis.from_url(url, decode_responses=True)
    except Exception:
        return None

def _run_incrby(run_id: Optional[str], field: str, n: int) -> None:
    if not run_id:
        return
    try:
        r = _metrics_redis()
        if not r:
            return
        r.incrby(f"emb:run:{run_id}:{field}", int(n))
    except Exception:
        pass


# -----------------------------
# Gemini embedding client helper
# -----------------------------

def _embed_batch(texts: List[str], task_type: str = "RETRIEVAL_DOCUMENT", output_dim: int = 768) -> List[List[float]]:
    """Return normalized embeddings (L2=1) for a batch of texts.

    Tries the new google.genai client first, then falls back to google.generativeai.
    """
    if not texts:
        return []

    # In-batch deduplication to reduce redundant API calls
    unique_texts: List[str] = []
    unique_positions: List[List[int]] = []  # positions per unique
    text_to_unique_idx: Dict[str, int] = {}
    for idx, t in enumerate(texts):
        key = t
        if key in text_to_unique_idx:
            u = text_to_unique_idx[key]
            unique_positions[u].append(idx)
        else:
            u = len(unique_texts)
            text_to_unique_idx[key] = u
            unique_texts.append(t)
            unique_positions.append([idx])

    api_key = None
    try:
        import os
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY")
    except Exception:
        api_key = None
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY/GOOGLE_GENERATIVE_AI_API_KEY for embeddings")

    # Prefer new client if available
    try:
        from google import genai  # type: ignore
        from google.genai import types as genai_types  # type: ignore

        client = genai.Client(api_key=api_key)
        cfg = genai_types.EmbedContentConfig(task_type=task_type, output_dimensionality=output_dim)
        res = client.models.embed_content(model="gemini-embedding-001", contents=unique_texts, config=cfg)
        embs = []
        for e in res.embeddings:
            vec = list(getattr(e, "values", []) or [])
            # Normalize for non-3072 dims
            norm = math.sqrt(sum((x * x) for x in vec)) or 1.0
            embs.append([x / norm for x in vec])
        # Re-expand to original length using unique positions mapping
        expanded: List[List[float]] = [[0.0] * len(embs[0]) for _ in range(len(texts))]
        for u, vec in enumerate(embs):
            for pos in unique_positions[u]:
                expanded[pos] = vec
        return expanded
    except Exception as e_new:
        logger.info("google.genai unavailable or failed, falling back to google-generativeai: %s", e_new)

    # Fallback to legacy google-generativeai with strict 1:1 output enforcement
    import google.generativeai as genai_legacy  # type: ignore
    genai_legacy.configure(api_key=api_key)
    model_code = "text-embedding-004"  # best available in legacy client

    def _extract_legacy_vectors(res_any: Any) -> List[List[float]]:
        vecs: List[List[float]] = []
        # Common shapes across versions
        if isinstance(res_any, dict) and "embedding" in res_any:
            arr = res_any["embedding"]
            if isinstance(arr, dict) and "values" in arr:
                arr = arr["values"]
            vecs.append(list(arr))
            return vecs
        if isinstance(res_any, dict) and "embeddings" in res_any:
            for e in res_any["embeddings"]:
                vals = e.get("values") if isinstance(e, dict) else getattr(e, "values", [])
                vecs.append(list(vals or []))
            return vecs
        # Best-effort attribute access
        try:
            tmp = res_any  # type: ignore[assignment]
            for e in getattr(tmp, "embeddings", []) or []:
                vecs.append(list(getattr(e, "values", []) or []))
            return vecs
        except Exception:
            return []

    # Attempt batch with raw list unless disabled/forced per-item
    vectors: List[List[float]] = []
    try:
        import os as _os
        force_per_item = _os.getenv("CHATSTATS_EMBED_FORCE_PERITEM", "0").lower() in ("1", "true", "yes")
    except Exception:
        force_per_item = False

    global _LEGACY_BATCH_DISABLED
    if not force_per_item and not _LEGACY_BATCH_DISABLED:
        try:
            res_any = genai_legacy.embed_content(model=model_code, content=unique_texts)
            vectors = _extract_legacy_vectors(res_any)
        except Exception as e1:
            logger.info("legacy embed_content(list) failed; will try dict-list: %s", e1)
            vectors = []

        # If count mismatch, try dict-list batch once
        if len(vectors) != len(unique_texts):
            try:
                batched = [{"content": t} for t in unique_texts]
                res_any = genai_legacy.embed_content(model=model_code, content=batched)
                vectors = _extract_legacy_vectors(res_any)
            except Exception as e2:
                logger.info("legacy embed_content(dict-list) failed; will try per-item: %s", e2)
                vectors = []

    # Final fallback: per-item to guarantee one vector per input
    if force_per_item or _LEGACY_BATCH_DISABLED or len(vectors) != len(unique_texts):
        if not force_per_item and not _LEGACY_BATCH_DISABLED:
            try:
                logger.warning("[embeddings] legacy batch unreliable; switching to per-item permanently | inputs=%s got=%s", len(unique_texts), len(vectors))
            except Exception:
                pass
            _LEGACY_BATCH_DISABLED = True
        # Concurrency for per-item calls on unique texts
        try:
            import os as _os
            per_item_threads = int(_os.getenv("CHATSTATS_EMBED_PERITEM_CONCURRENCY", "64"))
        except Exception:
            per_item_threads = 64
        try:
            logger.info("[embeddings] per-item fallback | unique=%s threads=%s", len(unique_texts), per_item_threads)
        except Exception:
            pass
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def _embed_one(t: str) -> List[float]:
            try:
                r = genai_legacy.embed_content(model=model_code, content=t)
            except Exception:
                r = genai_legacy.embed_content(model=model_code, content={"content": t})
            v_list = _extract_legacy_vectors(r)
            if not v_list and isinstance(r, dict) and "embedding" in r:
                arr = r["embedding"]
                if isinstance(arr, dict) and "values" in arr:
                    arr = arr["values"]
                v_list = [list(arr)]
            return list((v_list[0] if v_list else []))
        per_item_vecs: List[Optional[List[float]]] = [None] * len(unique_texts)
        with ThreadPoolExecutor(max_workers=max(1, per_item_threads)) as ex:
            fut_to_idx = {ex.submit(_embed_one, txt): i for i, txt in enumerate(unique_texts)}
            for fut in as_completed(fut_to_idx):
                i = fut_to_idx[fut]
                try:
                    per_item_vecs[i] = fut.result()
                except Exception:
                    per_item_vecs[i] = []
        vectors = [v or [] for v in per_item_vecs]

    # Helper: coerce arbitrary embedding shapes to a flat float list
    def _coerce_vector(vec_any: Any) -> List[float]:
        try:
            v = vec_any
            # Flatten nested lists (one or two levels defensively)
            for _ in range(2):
                if isinstance(v, list) and v and isinstance(v[0], list):
                    v = [x for sub in v for x in sub]
                else:
                    break
            outv: List[float] = []
            for x in (v or []):
                try:
                    outv.append(float(x))
                except Exception:
                    # Skip non-numeric entries
                    continue
            return outv
        except Exception:
            return []

    # Normalize unique vectors, then expand back to original positions
    coerced_unique: List[List[float]] = []
    for v in vectors:
        coerced = _coerce_vector(v)
        vv = list(coerced[:output_dim])
        if len(vv) < output_dim:
            vv = vv + [0.0] * (output_dim - len(vv))
        try:
            norm = math.sqrt(sum((x * x) for x in vv)) or 1.0
        except Exception:
            norm = 1.0
        coerced_unique.append([x / norm for x in vv])

    if not coerced_unique:
        coerced_unique = [[0.0] * output_dim for _ in range(len(unique_texts))]

    expanded: List[List[float]] = [[0.0] * output_dim for _ in range(len(texts))]
    for u, vec in enumerate(coerced_unique):
        for pos in unique_positions[u]:
            expanded[pos] = vec
    return expanded


def _upsert_embeddings(rows: List[Dict[str, Any]]):
    """Insert/replace embeddings with raw SQL upsert."""
    if not rows:
        return 0
    try:
        logger.debug("[embeddings] Upsert begin | rows=%s first=%s", len(rows), {
            "source_type": rows[0].get("source_type") if rows else None,
            "conversation_id": rows[0].get("conversation_id") if rows else None,
            "chat_id": rows[0].get("chat_id") if rows else None,
        })
    except Exception:
        pass
    sql = sa_text(
        """
        INSERT INTO embeddings (
            user_id, chat_id, conversation_id, message_id,
            source_type, source_id, label, chunk_index,
            model, dim, vector_blob, text_hash,
            created_at, updated_at
        ) VALUES (
            :user_id, :chat_id, :conversation_id, :message_id,
            :source_type, :source_id, :label, :chunk_index,
            :model, :dim, :vector_blob, :text_hash,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT(user_id, source_type, source_id, chunk_index, model, dim, text_hash)
        DO UPDATE SET vector_blob = excluded.vector_blob, updated_at = CURRENT_TIMESTAMP
        """
    )
    with new_session() as s:
        for r in rows:
            s.execute(sql, r)
    try:
        logger.debug("[embeddings] Upsert done | rows=%s", len(rows))
    except Exception:
        pass
    return len(rows)


def _hash_text(txt: str) -> str:
    import hashlib
    return hashlib.sha256(txt.encode("utf-8")).hexdigest()


def _to_float32le_blob(vec: List[float], dim: int) -> bytes:
    vv = list(vec[:dim])
    if len(vv) < dim:
        vv = vv + [0.0] * (dim - len(vv))
    try:
        return struct.pack("<" + ("f" * dim), *vv)
    except Exception:
        out = bytearray()
        for x in vv:
            out += struct.pack("<f", float(x))
        return bytes(out)


def _encoded_conversation_for_chat(chat_id: str) -> Tuple[str, Optional[str]]:
    """Best-effort encoding of the conversation: role: text lines."""
    with new_session() as s:
        msgs = s.execute(sa_text(
            """
            SELECT role, parts FROM chatbot_messages_v2 WHERE chat_id = :cid ORDER BY created_at ASC
            """
        ), {"cid": chat_id}).mappings().all()
        title_row = s.execute(sa_text("SELECT title FROM chatbot_chats WHERE id = :cid"), {"cid": chat_id}).first()
        title = (title_row[0] if title_row else None)
    lines: List[str] = []
    for m in msgs:
        role = str(m.get("role") or "")
        parts_raw = m.get("parts")
        try:
            parts = json.loads(parts_raw) if isinstance(parts_raw, str) else parts_raw
        except Exception:
            parts = parts_raw
        texts = []
        if isinstance(parts, list):
            for p in parts:
                if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str):
                    texts.append(p.get("text"))
        joined = " ".join(texts).strip()
        if joined:
            lines.append(f"{role}: {joined}")
    return ("\n".join(lines), str(title) if title else None)


@shared_task(bind=True, name="celery.embed_messages_for_chat", base=BaseTaskWithDLQ, ignore_result=True)
def embed_messages_for_chat_task(self, chat_id: str, user_id: Optional[str] = None):
    # Build encoded convo from DB (all time)
    text, title = _encoded_conversation_for_chat(chat_id)
    if not text.strip():
        logger.info("[embeddings] chat %s empty, skipping", chat_id)
        return {"ok": True, "count": 0}

    vec = _embed_batch([text], task_type="RETRIEVAL_DOCUMENT", output_dim=768)[0]
    row = {
        "user_id": user_id or "",
        "chat_id": chat_id,
        "conversation_id": None,
        "message_id": None,
        "source_type": "conversation_raw",
        "source_id": str(chat_id),
        "label": title or None,
        "chunk_index": 0,
        "model": "gemini-embedding-001",
        "dim": 768,
        "vector_blob": _to_float32le_blob(vec, 768),
        "text_hash": _hash_text(text),
    }
    n = _upsert_embeddings([row])
    # Mark dirty via meta comparison (periodic task will rebuild)
    return {"ok": True, "count": n}


@shared_task(bind=True, name="celery.embed_analyses_for_conversation", base=BaseTaskWithDLQ, ignore_result=True)
def embed_analyses_for_conversation_task(self, conversation_id: int, chat_id: int, run_id: Optional[str] = None, user_id: Optional[str] = None):
    """Embed analysis-derived items for a single conversation using normalized tables."""
    try:
        logger.debug("[embeddings] per-convo start | convo=%s chat=%s", conversation_id, chat_id)
    except Exception:
        pass
    # 1) Fetch sources from normalized schema
    sql_summ = sa_text(
        "SELECT id AS conversation_id, summary FROM conversations WHERE id = :cid"
    )
    sql_topics = sa_text(
        "SELECT conversation_id, title AS text FROM topics WHERE conversation_id = :cid"
    )
    sql_entities = sa_text(
        "SELECT conversation_id, title AS text FROM entities WHERE conversation_id = :cid"
    )
    sql_emotions = sa_text(
        "SELECT conversation_id, emotion_type AS text FROM emotions WHERE conversation_id = :cid"
    )
    sql_humor = sa_text(
        "SELECT conversation_id, snippet AS text FROM humor_items WHERE conversation_id = :cid"
    )

    with new_session() as s:
        summ_rows = s.execute(sql_summ, {"cid": int(conversation_id)}).mappings().all()
        topic_rows = s.execute(sql_topics, {"cid": int(conversation_id)}).mappings().all()
        entity_rows = s.execute(sql_entities, {"cid": int(conversation_id)}).mappings().all()
        emotion_rows = s.execute(sql_emotions, {"cid": int(conversation_id)}).mappings().all()
        humor_rows = s.execute(sql_humor, {"cid": int(conversation_id)}).mappings().all()

    texts: List[str] = []
    meta: List[Tuple[str, int, str]] = []  # (label, conversation_id, text)

    for r in summ_rows:
        t = (r.get("summary") or "").strip()
        if t:
            texts.append(t)
            meta.append(("summary", int(conversation_id), t))
    for r in topic_rows:
        t = (r.get("text") or "").strip()
        if t:
            texts.append(t)
            meta.append(("topics", int(conversation_id), t))
    for r in entity_rows:
        t = (r.get("text") or "").strip()
        if t:
            texts.append(t)
            meta.append(("entities", int(conversation_id), t))
    for r in emotion_rows:
        t = (r.get("text") or "").strip()
        if t:
            texts.append(t)
            meta.append(("emotions", int(conversation_id), t))
    for r in humor_rows:
        t = (r.get("text") or "").strip()
        if t:
            texts.append(t)
            meta.append(("humor", int(conversation_id), t))

    if not texts:
        logger.debug("[embeddings] per-convo no texts | convo=%s chat=%s", conversation_id, chat_id)
        return {"ok": True, "count": 0}

    try:
        _run_incrby(run_id, "total", len(texts))
        vecs = _embed_batch(texts, task_type="RETRIEVAL_DOCUMENT", output_dim=768)
    except Exception as e:
        logger.error("[embeddings] per-convo embed _embed_batch failed: %s | convo=%s chat=%s", e, conversation_id, chat_id, exc_info=True)
        _run_incrby(run_id, "failed", len(texts))
        raise

    out: List[Dict[str, Any]] = []
    for (label, conv_id, text_val), vec in zip(meta, vecs):
        out.append({
            "user_id": user_id or "",
            "chat_id": str(chat_id),
            "conversation_id": str(conv_id),
            "message_id": None,
            "source_type": label,
            "source_id": f"{conv_id}:{label}:{_hash_text(text_val)[:8]}",
            "label": label,
            "chunk_index": 0,
            "model": "gemini-embedding-001",
            "dim": 768,
            "vector_blob": _to_float32le_blob(vec, 768),
            "text_hash": _hash_text(text_val),
        })
    n = _upsert_embeddings(out)
    _run_incrby(run_id, "completed", n)
    try:
        logger.debug("[embeddings] per-convo done | convo=%s chat=%s count=%s", conversation_id, chat_id, n)
    except Exception:
        pass
    return {"ok": True, "count": n}


@shared_task(bind=True, name="celery.embed_artifacts_for_user", base=BaseTaskWithDLQ, ignore_result=True)
def embed_artifacts_for_user_task(self, user_id: str):
    with new_session() as s:
        docs = s.execute(sa_text(
            """
            SELECT id, title, content, origin_chat_id FROM chatbot_documents WHERE user_id = :uid
            ORDER BY created_at DESC
            """
        ), {"uid": user_id}).mappings().all()
    if not docs:
        return {"ok": True, "count": 0}

    texts: List[str] = []
    meta: List[Tuple[str, Optional[str], Optional[str]]] = []  # (doc_id, title, chat_id)
    for d in docs:
        title = str(d.get("title") or "").strip()
        body = str(d.get("content") or "").strip()
        full = (f"# {title}\n{body}").strip()
        if not full:
            continue
        texts.append(full)
        meta.append((str(d.get("id")), title or None, str(d.get("origin_chat_id") or "") or None))

    vecs = _embed_batch(texts, task_type="RETRIEVAL_DOCUMENT", output_dim=768)
    out: List[Dict[str, Any]] = []
    for (doc_id, title, chat_id), vec, text_val in zip(meta, vecs, texts):
        out.append({
            "user_id": user_id or "",
            "chat_id": chat_id,
            "conversation_id": None,
            "message_id": None,
            "source_type": "artifact",
            "source_id": doc_id,
            "label": title,
            "chunk_index": 0,
            "model": "gemini-embedding-001",
            "dim": 768,
            "vector_blob": _to_float32le_blob(vec, 768),
            "text_hash": _hash_text(text_val),
        })
    n = _upsert_embeddings(out)
    # Mark dirty; periodic task handles coalesced rebuild
    return {"ok": True, "count": n}


@shared_task(bind=True, name="celery.embeddings.maybe_rebuild_faiss_index", base=BaseTaskWithDLQ, ignore_result=True)
def maybe_rebuild_faiss_index_task(self):
    from backend.services.embeddings.faiss_index import is_dirty, build_index_from_db
    if not is_dirty():
        return {"ok": True, "rebuilt": False}
    n, d = build_index_from_db()
    return {"ok": True, "rebuilt": True, "vectors": n, "dim": d}



# ---------------------------------------------------------------------------
# New: Conversation-level embedding using the same encoded text as analysis
# ---------------------------------------------------------------------------

@shared_task(bind=True, name="celery.embed_conversation", base=BaseTaskWithDLQ, ignore_result=True)
def embed_conversation_task(self, conversation_id: int, chat_id: int, encoded_text: str, user_id: Optional[str] = None):
    """Embed a single conversation using the encoded text used for analysis."""
    try:
        text_val = (encoded_text or "").strip()
        logger.info("[embeddings] embed_conversation_task start | convo=%s chat=%s len=%s", conversation_id, chat_id, len(text_val))
        if not text_val:
            return {"ok": True, "count": 0}
        vec = _embed_batch([text_val], task_type="RETRIEVAL_DOCUMENT", output_dim=768)[0]
        h = _hash_text(text_val)
        row = {
            "user_id": user_id or "",
            "chat_id": str(chat_id),
            "conversation_id": str(conversation_id),
            "message_id": None,
            "source_type": "conversation",
            "source_id": str(conversation_id),
            "label": None,
            "chunk_index": 0,
            "model": "gemini-embedding-001",
            "dim": 768,
            "vector_blob": _to_float32le_blob(vec, 768),
            "text_hash": h,
        }
        n = _upsert_embeddings([row])
        logger.info("[embeddings] embed_conversation_task done | convo=%s chat=%s count=%s hash=%s", conversation_id, chat_id, n, h[:8])
        return {"ok": True, "count": n}
    except Exception as e:
        logger.error("[embeddings] embed_conversation_task failed: %s", e, exc_info=True)
        raise


# ---------------------------------------------------------------------------
# New: Batch embed analysis results for multiple conversations (efficient)
# ---------------------------------------------------------------------------

@shared_task(bind=True, name="celery.embed_analysis_batch", base=BaseTaskWithDLQ, ignore_result=True)
def embed_analysis_batch_task(self, conversation_ids: List[int], chat_id: int, user_id: Optional[str] = None):
    """Batch embed analysis results for the latest successful analysis per conversation."""
    try:
        ids = [int(x) for x in (conversation_ids or []) if x is not None]
        if not ids:
            return {"ok": True, "count": 0}
        logger.info("[embeddings] embed_analysis_batch_task start | chat=%s conv_ids=%s (count=%s)", chat_id, ids[:10], len(ids))

        # 1) Determine which conversations have a successful analysis
        placeholders = ", ".join([f":c{i}" for i in range(len(ids))])
        params = {f"c{i}": ids[i] for i in range(len(ids))}
        sql_success = sa_text(
            f"""
            SELECT conversation_id
            FROM conversation_analyses
            WHERE conversation_id IN ({placeholders}) AND status = 'success'
            GROUP BY conversation_id
            """
        )
        with new_session() as s:
            ok_rows = s.execute(sql_success, params).fetchall()
        conv_ok = {int(r[0]) for r in ok_rows}
        if not conv_ok:
            logger.error("[embeddings] embed_analysis_batch_task no successful analyses | chat=%s", chat_id)
            return {"ok": True, "count": 0}

        # 2) Fetch summary from conversations table
        placeholders_ok = ", ".join([f":ok{i}" for i, _ in enumerate(conv_ok)])
        params_ok = {f"ok{i}": cid for i, cid in enumerate(conv_ok)}
        sql_summ = sa_text(
            f"""
            SELECT id AS conversation_id, summary
            FROM conversations
            WHERE id IN ({placeholders_ok})
            """
        )
        # 3) Fetch dimension items from normalized tables
        sql_topics = sa_text(
            f"SELECT conversation_id, title AS text FROM topics WHERE conversation_id IN ({placeholders_ok})"
        )
        sql_entities = sa_text(
            f"SELECT conversation_id, title AS text FROM entities WHERE conversation_id IN ({placeholders_ok})"
        )
        sql_emotions = sa_text(
            f"SELECT conversation_id, emotion_type AS text FROM emotions WHERE conversation_id IN ({placeholders_ok})"
        )
        sql_humor = sa_text(
            f"SELECT conversation_id, snippet AS text FROM humor_items WHERE conversation_id IN ({placeholders_ok})"
        )

        with new_session() as s:
            summ_rows = s.execute(sql_summ, params_ok).mappings().all()
            topic_rows = s.execute(sql_topics, params_ok).mappings().all()
            entity_rows = s.execute(sql_entities, params_ok).mappings().all()
            emotion_rows = s.execute(sql_emotions, params_ok).mappings().all()
            humor_rows = s.execute(sql_humor, params_ok).mappings().all()
        try:
            logger.error(
                "[embeddings] batch sources | chat=%s ok_convos=%s summaries=%s topics=%s entities=%s emotions=%s humor=%s",
                chat_id, len(conv_ok), len(summ_rows), len(topic_rows), len(entity_rows), len(emotion_rows), len(humor_rows)
            )
        except Exception:
            pass

        texts: List[str] = []
        meta: List[Tuple[str, int, str]] = []  # (label, conversation_id, text)
        # Summaries
        for r in summ_rows:
            conv_id = int(r.get("conversation_id"))
            stext = (r.get("summary") or "").strip()
            if stext:
                texts.append(stext)
                meta.append(("summary", conv_id, stext))
        # Topics
        for r in topic_rows:
            conv_id = int(r.get("conversation_id"))
            t = (r.get("text") or "").strip()
            if t:
                texts.append(t)
                meta.append(("topics", conv_id, t))
        # Entities
        for r in entity_rows:
            conv_id = int(r.get("conversation_id"))
            t = (r.get("text") or "").strip()
            if t:
                texts.append(t)
                meta.append(("entities", conv_id, t))
        # Emotions
        for r in emotion_rows:
            conv_id = int(r.get("conversation_id"))
            t = (r.get("text") or "").strip()
            if t:
                texts.append(t)
                meta.append(("emotions", conv_id, t))
        # Humor
        for r in humor_rows:
            conv_id = int(r.get("conversation_id"))
            t = (r.get("text") or "").strip()
            if t:
                texts.append(t)
                meta.append(("humor", conv_id, t))

        if not texts:
            logger.error("[embeddings] embed_analysis_batch_task no texts | chat=%s", chat_id)
            return {"ok": True, "count": 0}

        try:
            sample = [(m[0], m[1], len(m[2])) for m in meta[:5]]
            label_counts: Dict[str, int] = {}
            for (label, _cid, _t) in meta:
                label_counts[label] = label_counts.get(label, 0) + 1
            logger.error(
                "[embeddings] embed_analysis_batch_task prepared | chat=%s texts=%s meta=%s sample=%s counts=%s",
                chat_id, len(texts), len(meta), sample, label_counts
            )
        except Exception:
            pass

        # Chunk large batches to keep provider/API and DB happy
        try:
            import os as _os
            chunk_size = int(_os.getenv("CHATSTATS_EMBED_BATCH_SIZE", "256"))
        except Exception:
            chunk_size = 256

        total_upserted = 0
        for i in range(0, len(texts), max(1, chunk_size)):
            chunk_texts = texts[i:i + chunk_size]
            chunk_meta = meta[i:i + chunk_size]
            try:
                logger.error(
                    "[embeddings] _embed_batch(chunk) | chat=%s i=%s size=%s", chat_id, i, len(chunk_texts)
                )
                chunk_vecs = _embed_batch(chunk_texts, task_type="RETRIEVAL_DOCUMENT", output_dim=768)
            except Exception as e:
                logger.error("[embeddings] _embed_batch(chunk) failed: %s", e, exc_info=True)
                continue

            out: List[Dict[str, Any]] = []
            for (label, conv_id, text_val), vec in zip(chunk_meta, chunk_vecs):
                out.append({
                    "user_id": user_id or "",
                    "chat_id": str(chat_id),
                    "conversation_id": str(conv_id),
                    "message_id": None,
                    "source_type": label,
                    "source_id": f"{conv_id}:{label}:{_hash_text(text_val)[:8]}",
                    "label": label,
                    "chunk_index": 0,
                    "model": "gemini-embedding-001",
                    "dim": 768,
                    "vector_blob": _to_float32le_blob(vec, 768),
                    "text_hash": _hash_text(text_val),
                })
            if not out:
                logger.error("[embeddings] upsert(chunk) skipped; empty out | chat=%s i=%s", chat_id, i)
                continue
            up = _upsert_embeddings(out)
            total_upserted += up
            logger.error("[embeddings] upsert(chunk) done | chat=%s i=%s rows=%s total=%s", chat_id, i, up, total_upserted)

        logger.error("[embeddings] embed_analysis_batch_task done | chat=%s total_count=%s", chat_id, total_upserted)
        return {"ok": True, "count": total_upserted}
    except Exception as e:
        logger.error("[embeddings] embed_analysis_batch_task failed: %s", e, exc_info=True)
        raise

