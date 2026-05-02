import hmac

from fastapi import Header, HTTPException, status

from gamehost_node.core.config import get_settings


async def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer")
    presented = authorization.split(" ", 1)[1].strip()
    expected = get_settings().api_key.get_secret_value()
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_api_key")
