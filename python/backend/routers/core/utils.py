# Consolidated imports  
from backend.routers.common import create_router, safe_endpoint, log_simple, BaseModel
from backend.services.core.token import TokenService

router = create_router("/token", "Util")

class TokenCountRequest(BaseModel):
    text: str

@router.post("/count")
@safe_endpoint
async def get_token_count(request: TokenCountRequest):
    log_simple(f"Counting tokens for text of length {len(request.text)}")
    count = TokenService.count_tokens_with_fallback(request.text)
    return {"token_count": count} 