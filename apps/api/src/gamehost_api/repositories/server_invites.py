import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import ServerInvite


class ServerInvitesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, invite_id: uuid.UUID) -> ServerInvite | None:
        return await self._s.get(ServerInvite, invite_id)

    async def get_by_token_hash(self, token_hash: str) -> ServerInvite | None:
        stmt = select(ServerInvite).where(ServerInvite.token_hash == token_hash)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def find_open(self, server_id: uuid.UUID, email: str) -> ServerInvite | None:
        stmt = select(ServerInvite).where(
            ServerInvite.server_id == server_id,
            ServerInvite.email == email,
            ServerInvite.accepted_at.is_(None),
            ServerInvite.revoked_at.is_(None),
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def list_open(self, server_id: uuid.UUID) -> list[ServerInvite]:
        stmt = (
            select(ServerInvite)
            .where(
                ServerInvite.server_id == server_id,
                ServerInvite.accepted_at.is_(None),
                ServerInvite.revoked_at.is_(None),
            )
            .order_by(ServerInvite.created_at.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def create(
        self,
        *,
        server_id: uuid.UUID,
        email: str,
        role: str,
        token_hash: str,
        ttl_days: int,
        created_by: uuid.UUID,
    ) -> ServerInvite:
        row = ServerInvite(
            id=uuid.uuid4(),
            server_id=server_id,
            email=email,
            role=role,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
            created_by=created_by,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def revoke(self, invite: ServerInvite) -> None:
        invite.revoked_at = datetime.now(UTC)
        await self._s.flush()

    async def accept(self, invite: ServerInvite, user_id: uuid.UUID) -> None:
        invite.accepted_at = datetime.now(UTC)
        invite.accepted_by = user_id
        await self._s.flush()
