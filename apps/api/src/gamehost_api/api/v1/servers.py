import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_current_user, get_session
from gamehost_api.db.models import User
from gamehost_api.domain.servers import ServerService
from gamehost_api.schemas.base import CamelModel
from gamehost_api.schemas.servers import (
    AcceptedOut,
    ServerCreateIn,
    ServerOut,
    ServerPatchIn,
)
from gamehost_api.tasks.arq_pool import ArqPoolLike

router = APIRouter()


class LogsTailOut(CamelModel):
    lines: list[str]


class StreamTokenOut(CamelModel):
    token: str
    expires_at: datetime


def _service(request: Request, session: AsyncSession) -> ServerService:
    pool: ArqPoolLike = request.app.state.arq_pool
    redis = getattr(request.app.state, "redis", None)
    return ServerService(session, pool, redis=redis)


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


@router.get("/{server_id}/logs", response_model=LogsTailOut)
async def get_server_logs_tail(
    server_id: uuid.UUID,
    request: Request,
    tail: int = Query(default=200, ge=1, le=10000),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LogsTailOut:
    lines = await _service(request, session).get_log_tail(server_id, tail, user)
    return LogsTailOut(lines=lines)


@router.post("/{server_id}/logs/stream-token", response_model=StreamTokenOut)
async def issue_log_stream_token(
    server_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamTokenOut:
    token, exp = await _service(request, session).mint_log_token(server_id, user)
    return StreamTokenOut(token=token, expires_at=exp)


@router.get("/{server_id}/logs/stream")
async def stream_server_logs(
    server_id: uuid.UUID,
    t: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    svc = _service(request, session)
    container_id = await svc.authorize_log_stream(server_id, t)
    return StreamingResponse(svc.stream_logs_iter(container_id), media_type="text/event-stream")
