import secrets
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import Node
from gamehost_api.domain.exceptions import NodeNameTaken, NodeNotFound
from gamehost_api.repositories.nodes import NodeRepository
from gamehost_api.schemas.nodes import NodeCreateIn, NodePatchIn


class NodeService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = NodeRepository(session)

    async def list_(self) -> list[Node]:
        return await self._repo.list_()

    async def create(self, payload: NodeCreateIn) -> tuple[Node, str]:
        if await self._repo.get_by_name(payload.name) is not None:
            raise NodeNameTaken(payload.name)
        plain = secrets.token_urlsafe(32)
        try:
            node = await self._repo.create(
                name=payload.name,
                endpoint_url=str(payload.endpoint_url),
                api_key=plain,
                capacity_cpu=payload.capacity_cpu,
                capacity_mem_mb=payload.capacity_mem_mb,
            )
        except IntegrityError as exc:
            raise NodeNameTaken(payload.name) from exc
        return node, plain

    async def update(self, node_id: uuid.UUID, payload: NodePatchIn) -> Node:
        node = await self._repo.get(node_id)
        if node is None:
            raise NodeNotFound(str(node_id))
        fields = payload.model_dump(exclude_unset=True)
        if "endpoint_url" in fields and fields["endpoint_url"] is not None:
            fields["endpoint_url"] = str(fields["endpoint_url"])
        return await self._repo.update(node, fields)

    async def delete(self, node_id: uuid.UUID) -> None:
        node = await self._repo.get(node_id)
        if node is None:
            raise NodeNotFound(str(node_id))
        await self._repo.delete(node)
