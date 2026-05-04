import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import ServerMember


class ServerMembersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, server_id: uuid.UUID, user_id: uuid.UUID) -> ServerMember | None:
        stmt = select(ServerMember).where(
            ServerMember.server_id == server_id, ServerMember.user_id == user_id
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def list_for_server(self, server_id: uuid.UUID) -> list[ServerMember]:
        stmt = (
            select(ServerMember)
            .where(ServerMember.server_id == server_id)
            .order_by(ServerMember.created_at.asc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def create(
        self,
        *,
        server_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str,
        invited_by: uuid.UUID | None,
    ) -> ServerMember:
        m = ServerMember(server_id=server_id, user_id=user_id, role=role, invited_by=invited_by)
        self._s.add(m)
        await self._s.flush()
        return m

    async def delete(self, member: ServerMember) -> None:
        await self._s.delete(member)
        await self._s.flush()
