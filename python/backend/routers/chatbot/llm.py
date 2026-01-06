from __future__ import annotations

from typing import Any, Dict, List, AsyncGenerator

from fastapi.responses import StreamingResponse

from backend.routers.common import create_router, safe_endpoint, log_simple

router = create_router("/chatbot/llm", tags=["chatbot"])


@router.post("/generate")
@safe_endpoint
def llm_generate(body: Dict[str, Any]):
    from backend.services.llm import LLMService
    import os

    system = (body or {}).get("system") or ""
    messages = (body or {}).get("messages") or []
    default_model = os.getenv("CHATBOT_DEFAULT_MODEL", "gpt-5")
    model = (body or {}).get("model") or default_model

    def parts_to_text(parts: Any) -> str:
        try:
            return "".join(p.get("text", "") for p in (parts or []) if isinstance(p, dict))
        except Exception:
            return str(parts or "")

    prompt_lines: List[str] = []
    if system:
        prompt_lines.append(system)
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant", "system"):
            continue
        if role == "system":
            prompt_lines.append(parts_to_text(m.get("parts")))
            continue
        prefix = "User:" if role == "user" else "Assistant:"
        prompt_lines.append(f"{prefix} {parts_to_text(m.get('parts'))}")

    prompt_text = "\n\n".join(s for s in prompt_lines if s)
    log_simple(f"[chatbot.llm] prompt_preview={prompt_text[:200].replace(chr(10),' ')}")

    resp = LLMService.call_llm(
        prompt_str=prompt_text,
        llm_config_dict={"model_name": model, "temperature": 0.7, "max_tokens": 2048},
    )
    text_out = resp.get("content") if isinstance(resp, dict) else str(resp)
    return {"text": text_out or ""}


@router.post("/generate-stream")
@safe_endpoint
async def llm_generate_stream(body: Dict[str, Any]) -> StreamingResponse:
    from backend.services.llm import LLMService
    import os, asyncio

    system = (body or {}).get("system") or ""
    messages = (body or {}).get("messages") or []
    default_model = os.getenv("CHATBOT_DEFAULT_MODEL", "gpt-5")
    model = (body or {}).get("model") or default_model

    def parts_to_text(parts: Any) -> str:
        try:
            return "".join(p.get("text", "") for p in (parts or []) if isinstance(p, dict))
        except Exception:
            return str(parts or "")

    prompt_lines: List[str] = []
    if system:
        prompt_lines.append(system)
    for m in messages:
        role = (m or {}).get("role")
        if role not in ("user", "assistant", "system"):
            continue
        parts = m.get("parts") or m.get("content")
        if role == "system":
            prompt_lines.append(parts_to_text(parts))
            continue
        prefix = "User:" if role == "user" else "Assistant:"
        prompt_lines.append(f"{prefix} {parts_to_text(parts)}")

    prompt_text = "\n\n".join(s for s in prompt_lines if s)
    resp = LLMService.call_llm(
        prompt_str=prompt_text,
        llm_config_dict={"model_name": model, "temperature": 0.7, "max_tokens": 2048},
    )
    full_text = resp.get("content") if isinstance(resp, dict) else str(resp)
    if not isinstance(full_text, str):
        full_text = str(full_text or "")

    async def gen() -> AsyncGenerator[bytes, None]:
        for word in full_text.split():
            yield (word + " ").encode("utf-8")
            await asyncio.sleep(0)

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


