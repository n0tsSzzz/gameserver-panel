import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, email: str, password_hash: str, role: str) -> User:
        user = User(
            id=uuid.uuid4(),
            email=email.strip(),
            password_hash=password_hash,
            role=role,
        )
        self._s.add(user)
        await self._s.flush()
        return user

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(func.lower(User.email) == email.strip().lower())
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._s.get(User, user_id)
