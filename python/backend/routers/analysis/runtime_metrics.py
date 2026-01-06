from __future__ import annotations

import asyncio
import json
from fastapi.responses import StreamingResponse

from backend.routers.common import create_router, safe_endpoint
from backend.routers.sse_utils import encode_sse_event
import os
try:
    from backend.services.metrics.runtime_metrics import RuntimeMetrics
except Exception:
    class RuntimeMetrics:  # type: ignore
        @staticmethod
        def snapshot():
            return {}

METRICS_ENABLED = os.getenv("CHATSTATS_METRICS_ENABLED", "0").lower() in ("1", "true", "yes")

router = create_router("/analysis/metrics", "Analysis Metrics")

@router.get("/snapshot")
@safe_endpoint
async def get_snapshot():
    if not METRICS_ENABLED:
        return {"enabled": False}
    return RuntimeMetrics.snapshot()

@router.get("/stream")
async def stream_metrics():
    async def gen():
        while True:
            if METRICS_ENABLED:
                snap = RuntimeMetrics.snapshot()
                yield encode_sse_event(event="metrics", data=json.dumps(snap))
            else:
                yield encode_sse_event(event="metrics", data=json.dumps({"enabled": False}))
            await asyncio.sleep(2.5)
    return StreamingResponse(gen(), media_type="text/event-stream")

@router.get("/bottlenecks")
@safe_endpoint  
async def get_bottleneck_metrics():
    """Bottleneck metrics disabled by default; set CHATSTATS_METRICS_ENABLED=1 to re-enable."""
    if not METRICS_ENABLED:
        return {"enabled": False}
    from backend.services.metrics.bottleneck_metrics import BottleneckMetrics
    return BottleneckMetrics.get_snapshot()
