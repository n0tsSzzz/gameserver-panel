import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt

from gamehost_api.core.config import get_settings

_ALGORITHM = "HS256"


def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_cost,
        parallelism=s.argon2_parallelism,
    )


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher().verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(*, user_id: uuid.UUID, email: str, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise ValueError("invalid_or_expired_token") from exc
    if claims.get("type") != "access":
        raise ValueError("wrong_token_type")
    return claims
