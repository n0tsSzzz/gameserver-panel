import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
        ip: str | None,
    ) -> RefreshToken:
        row = RefreshToken(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def revoke(self, row: RefreshToken) -> None:
        row.revoked_at = datetime.now(UTC)
        await self._s.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        result = await self._s.execute(stmt)
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount
