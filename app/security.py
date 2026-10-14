import secrets

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    api_key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    """No-op until API_KEY is set; then every request must carry a matching X-API-Key header."""
    if not settings.api_key:
        return
    if not api_key or not secrets.compare_digest(api_key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
