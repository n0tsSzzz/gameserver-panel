import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.core.security import hash_password
from gamehost_api.db.models import User

DEFAULT_PASSWORD = "hunter22hunter22"
_DEFAULT_HASH: str | None = None


def default_password_hash() -> str:
    global _DEFAULT_HASH
    if _DEFAULT_HASH is None:
        _DEFAULT_HASH = hash_password(DEFAULT_PASSWORD)
    return _DEFAULT_HASH


async def make_user(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str | None = None,
    role: str = "user",
    is_active: bool = True,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email.lower(),
        password_hash=password_hash or default_password_hash(),
        role=role,
        is_active=is_active,
    )
    session.add(user)
    await session.commit()
    return user
