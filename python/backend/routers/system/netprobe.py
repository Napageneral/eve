# app/backend/routers/system/netprobe.py
from fastapi import APIRouter, Request
import time

router = APIRouter(tags=["system"])

@router.post("/_netprobe/upload")
async def netprobe_upload(req: Request):
    """
    Read and discard request body to let the client measure uplink speed.
    Returns server-side receive time (informational).
    """
    t0 = time.perf_counter()
    await req.body()  # discard
    elapsed_s = time.perf_counter() - t0
    return {"elapsed_s": elapsed_s}

