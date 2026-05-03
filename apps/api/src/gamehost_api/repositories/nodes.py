import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import Node


class NodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_(self) -> list[Node]:
        stmt = select(Node).order_by(Node.name.asc())
        return list((await self._s.execute(stmt)).scalars().all())

    async def get(self, node_id: uuid.UUID) -> Node | None:
        return await self._s.get(Node, node_id)

    async def get_by_name(self, name: str) -> Node | None:
        stmt = select(Node).where(Node.name == name)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        name: str,
        endpoint_url: str,
        api_key: str,
        capacity_cpu: Decimal,
        capacity_mem_mb: int,
    ) -> Node:
        node = Node(
            id=uuid.uuid4(),
            name=name,
            endpoint_url=endpoint_url,
            api_key=api_key,
            capacity_cpu=capacity_cpu,
            capacity_mem_mb=capacity_mem_mb,
        )
        self._s.add(node)
        await self._s.flush()
        return node

    async def update(self, node: Node, fields: dict[str, Any]) -> Node:
        for k, v in fields.items():
            setattr(node, k, v)
        await self._s.flush()
        return node

    async def delete(self, node: Node) -> None:
        await self._s.delete(node)
        await self._s.flush()
