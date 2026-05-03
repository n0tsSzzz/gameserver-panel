import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_current_user, get_session
from gamehost_api.db.models import User
from gamehost_api.domain.servers import ServerService
from gamehost_api.schemas.servers import (
    AcceptedOut,
    ServerCreateIn,
    ServerOut,
    ServerPatchIn,
)
from gamehost_api.tasks.arq_pool import ArqPoolLike

router = APIRouter()


def _service(request: Request, session: AsyncSession) -> ServerService:
    pool: ArqPoolLike = request.app.state.arq_pool
    return ServerService(session, pool)


@router.get("", response_model=list[ServerOut])
async def list_servers(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ServerOut]:
    items = await _service(request, session).list_for(user)
    return [ServerOut.model_validate(s) for s in items]


@router.post("", response_model=AcceptedOut, status_code=status.HTTP_202_ACCEPTED)
async def create_server(
    payload: ServerCreateIn,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AcceptedOut:
    server, task = await _service(request, session).create(payload, user)
    return AcceptedOut(server_id=server.id, task_id=task.id)


@router.get("/{server_id}", response_model=ServerOut)
async def get_server(
    server_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ServerOut:
    srv = await _service(request, session).get_for(server_id, user)
    return ServerOut.model_validate(srv)


@router.post(
    "/{server_id}/start",
    response_model=AcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_server(
    server_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AcceptedOut:
    server, task = await _service(request, session).request_action(server_id, "start", user)
    return AcceptedOut(server_id=server.id, task_id=task.id)


@router.post(
    "/{server_id}/stop",
    response_model=AcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_server(
    server_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AcceptedOut:
    server, task = await _service(request, session).request_action(server_id, "stop", user)
    return AcceptedOut(server_id=server.id, task_id=task.id)


@router.post(
    "/{server_id}/restart",
    response_model=AcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restart_server(
    server_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AcceptedOut:
    server, task = await _service(request, session).request_action(server_id, "restart", user)
    return AcceptedOut(server_id=server.id, task_id=task.id)


@router.patch("/{server_id}", response_model=ServerOut)
async def patch_server(
    server_id: uuid.UUID,
    payload: ServerPatchIn,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ServerOut:
    srv = await _service(request, session).patch(server_id, payload, user)
    return ServerOut.model_validate(srv)


@router.delete("/{server_id}", response_model=AcceptedOut, status_code=status.HTTP_202_ACCEPTED)
async def delete_server(
    server_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AcceptedOut:
    server, task = await _service(request, session).delete(server_id, user)
    return AcceptedOut(server_id=server.id, task_id=task.id)
