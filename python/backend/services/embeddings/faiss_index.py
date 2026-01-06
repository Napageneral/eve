from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Tuple, Optional

import logging

import faiss  # type: ignore

from sqlalchemy import text as sa_text

from backend.db.session_manager import new_session

logger = logging.getLogger(__name__)


# In-memory single-partition registry (global, process-local)
_INDEX: Optional[faiss.Index] = None
_IDMAP: List[int] = []
_METRIC: int = faiss.METRIC_INNER_PRODUCT
_DIM: int = 768
_MODEL: str = "gemini-embedding-001"


def _index_dir() -> Path:
    # Reuse application support dir similar to DB
    base = Path.home() / "Library" / "Application Support" / "ChatStats"
    d = base / "faiss"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _paths() -> Tuple[Path, Path]:
    d = _index_dir()
    return d / "embeddings_all_768_ip.faiss", d / "embeddings_all_768_ip.ids.json"


def _meta_path() -> Path:
    return _index_dir() / "embeddings_meta.json"


def _load_meta() -> Tuple[int, str]:
    p = _meta_path()
    if not p.exists():
        return (0, "")
    try:
        data = json.loads(p.read_text())
        return (int(data.get("count") or 0), str(data.get("max_updated_at") or ""))
    except Exception:
        return (0, "")


def _save_meta(count: int, max_updated_at: str) -> None:
    p = _meta_path()
    try:
        p.write_text(json.dumps({"count": int(count), "max_updated_at": str(max_updated_at or "")}))
    except Exception:
        logger.debug("[faiss] failed to write meta", exc_info=True)


def _current_stats() -> Tuple[int, str]:
    with new_session() as s:
        row = s.execute(sa_text("SELECT COUNT(1) as c, COALESCE(MAX(updated_at),'') as m FROM embeddings WHERE model = :m AND dim = :d"), {"m": _MODEL, "d": _DIM}).first()
    c = int(row[0]) if row and row[0] is not None else 0
    m = str(row[1] or "") if row else ""
    return (c, m)


def is_dirty() -> bool:
    bc, bm = _load_meta()
    cc, cm = _current_stats()
    return cc != bc or str(cm) != str(bm)


def _lock_path() -> Path:
    return _index_dir() / "faiss_build.lock"


def _acquire_lock() -> bool:
    try:
        p = _lock_path()
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def _release_lock() -> None:
    try:
        p = _lock_path()
        if p.exists():
            p.unlink()
    except Exception:
        pass


def build_index_from_db() -> Tuple[int, int]:
    """Build a FAISS IP index from all embeddings (model=_MODEL, dim=_DIM). Returns (num_vectors, dim)."""
    global _INDEX, _IDMAP
    # Single-flight guard to avoid concurrent FAISS builds (can segfault)
    if not _acquire_lock():
        logger.warning("[faiss] build skipped; another build in progress")
        return (0, _DIM)
    with new_session() as s:
        rows = s.execute(sa_text(
            """
            SELECT id, vector_blob FROM embeddings WHERE model = :m AND dim = :d
            ORDER BY updated_at ASC
            """
        ), {"m": _MODEL, "d": _DIM}).mappings().all()

    if not rows:
        logger.info("[faiss] no embeddings found; skipping build")
        return (0, _DIM)

    # Prepare vectors and id map
    import numpy as np
    idmap: List[int] = []
    vecs: List[List[float]] = []
    for r in rows:
        try:
            vb = r.get("vector_blob")
            if vb is None:
                continue
            import numpy as np
            arr = np.frombuffer(vb, dtype="<f4")
            if arr.size <= 0:
                continue
            idmap.append(int(r.get("id")))
            vecs.append(arr.tolist())
        except Exception:
            continue
    if not vecs:
        _release_lock()
        return (0, _DIM)

    # Coerce to fixed dimension with pad/truncate for safety
    xb = np.zeros((len(vecs), _DIM), dtype="float32")
    for i, v in enumerate(vecs):
        if not v:
            continue
        if len(v) >= _DIM:
            xb[i, :] = np.array(v[:_DIM], dtype="float32")
        else:
            tmp = np.zeros((_DIM,), dtype="float32")
            tmp[: len(v)] = np.array(v, dtype="float32")
            xb[i, :] = tmp
    d = xb.shape[1]

    # HNSW(IP) if available; fallback to FlatIP
    try:
        index = faiss.IndexHNSWFlat(d, 32, _METRIC)  # m=32
        index.hnsw.efConstruction = 200
        index.hnsw.efSearch = 64
    except Exception:
        index = faiss.IndexFlatIP(d)

    try:
        index.add(xb)
    except Exception as e:
        logger.error("[faiss] index.add failed: %s", e, exc_info=True)
        _release_lock()
        raise

    # Persist
    idx_path, ids_path = _paths()
    try:
        faiss.write_index(index, str(idx_path))
        with open(ids_path, "w") as f:
            json.dump(idmap, f)
    except Exception as e:
        logger.error("[faiss] failed to write index: %s", e, exc_info=True)

    # Replace in-memory and update meta
    _assign_index(index, idmap)
    logger.info("[faiss] built index: %d vectors, dim=%d", len(idmap), d)
    try:
        cc, cm = _current_stats()
        _save_meta(cc, cm)
    except Exception:
        logger.debug("[faiss] failed to save meta", exc_info=True)
    _release_lock()
    return (len(idmap), d)


def _assign_index(index: faiss.Index, idmap: List[int]):
    global _INDEX, _IDMAP
    _INDEX = index
    _IDMAP = idmap


def load_index_from_disk() -> bool:
    idx_path, ids_path = _paths()
    if not idx_path.exists() or not ids_path.exists():
        return False
    try:
        index = faiss.read_index(str(idx_path))
        with open(ids_path, "r") as f:
            idmap = json.load(f)
        _assign_index(index, list(map(int, idmap)))
        logger.info("[faiss] loaded index from disk: %d ids", len(_IDMAP))
        return True
    except Exception as e:
        logger.error("[faiss] load failed: %s", e, exc_info=True)
        return False


def ensure_index() -> bool:
    if _INDEX is not None:
        return True
    if load_index_from_disk():
        return True
    try:
        n, _ = build_index_from_db()
        return n > 0
    except Exception:
        return False


def query_topk(vec: List[float], k: int = 50) -> List[Tuple[int, float]]:
    """Return list of (embedding_row_id, score) by ANN; falls back to empty if index missing."""
    import numpy as np
    if not ensure_index():
        return []
    if _INDEX is None or not _IDMAP:
        return []
    xq = np.array([vec], dtype="float32")
    D, I = _INDEX.search(xq, max(1, int(k)))
    ids_scores: List[Tuple[int, float]] = []
    if I is not None and D is not None:
        for idx, score in zip(I[0], D[0]):
            if int(idx) >= 0 and int(idx) < len(_IDMAP):
                ids_scores.append((_IDMAP[int(idx)], float(score)))
    return ids_scores


