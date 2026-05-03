import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import Server


class ServersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        name: str,
        template_id: uuid.UUID,
        env_overrides: dict[str, Any],
        resources: dict[str, Any],
    ) -> Server:
        s = Server(
            id=uuid.uuid4(),
            owner_id=owner_id,
            name=name,
            template_id=template_id,
            env_overrides=env_overrides,
            resources=resources,
        )
        self._s.add(s)
        await self._s.flush()
        return s

    async def get(self, server_id: uuid.UUID) -> Server | None:
        return await self._s.get(Server, server_id)

    async def list_for_owner(self, owner_id: uuid.UUID) -> list[Server]:
        stmt = select(Server).where(Server.owner_id == owner_id).order_by(Server.created_at.desc())
        return list((await self._s.execute(stmt)).scalars().all())

    async def list_all(self) -> list[Server]:
        stmt = select(Server).order_by(Server.created_at.desc())
        return list((await self._s.execute(stmt)).scalars().all())

    async def set_status(self, server_id: uuid.UUID, status: str) -> Server | None:
        srv = await self.get(server_id)
        if srv is None:
            return None
        srv.status = status
        srv.updated_at = datetime.now(UTC)
        await self._s.flush()
        return srv

    async def update_fields(self, server: Server, fields: dict[str, Any]) -> Server:
        for k, v in fields.items():
            setattr(server, k, v)
        server.updated_at = datetime.now(UTC)
        await self._s.flush()
        return server

    async def set_provisioned(
        self,
        server_id: uuid.UUID,
        *,
        node_id: uuid.UUID,
        container_id: str,
        host: str,
        port: int | None,
        status: str,
    ) -> Server | None:
        srv = await self.get(server_id)
        if srv is None:
            return None
        srv.node_id = node_id
        srv.container_id = container_id
        srv.host = host
        srv.port = port
        srv.status = status
        srv.updated_at = datetime.now(UTC)
        await self._s.flush()
        return srv

    async def delete_row(self, server: Server) -> None:
        await self._s.delete(server)
        await self._s.flush()
