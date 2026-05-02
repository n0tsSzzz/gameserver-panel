import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_session, require_admin
from gamehost_api.domain.nodes import NodeService
from gamehost_api.schemas.nodes import NodeCreateIn, NodeCreateOut, NodeOut, NodePatchIn

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("", response_model=list[NodeOut])
async def list_nodes(session: AsyncSession = Depends(get_session)) -> list[NodeOut]:
    items = await NodeService(session).list_()
    return [NodeOut.model_validate(n) for n in items]


@router.post("", response_model=NodeCreateOut, status_code=status.HTTP_201_CREATED)
async def create_node(
    payload: NodeCreateIn, session: AsyncSession = Depends(get_session)
) -> NodeCreateOut:
    node, plain_key = await NodeService(session).create(payload)
    base = NodeOut.model_validate(node)
    return NodeCreateOut(**base.model_dump(), api_key=plain_key)


@router.patch("/{node_id}", response_model=NodeOut)
async def patch_node(
    node_id: uuid.UUID,
    payload: NodePatchIn,
    session: AsyncSession = Depends(get_session),
) -> NodeOut:
    node = await NodeService(session).update(node_id, payload)
    return NodeOut.model_validate(node)


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(node_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Response:
    await NodeService(session).delete(node_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
