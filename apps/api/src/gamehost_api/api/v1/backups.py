import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_current_user, get_session
from gamehost_api.db.models import User
from gamehost_api.domain.backups import BackupService
from gamehost_api.schemas.backups import (
    BackupCreateAcceptedOut,
    BackupOut,
    RestoreAcceptedOut,
)
from gamehost_api.tasks.arq_pool import ArqPoolLike

router = APIRouter()


def _service(request: Request, session: AsyncSession) -> BackupService:
    pool: ArqPoolLike = request.app.state.arq_pool
    return BackupService(session, pool)


@router.get("/servers/{server_id}/backups", response_model=list[BackupOut])
async def list_server_backups(
    server_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[BackupOut]:
    rows = await _service(request, session).list_for_server(user, server_id)
    return [BackupOut.model_validate(r) for r in rows]


@router.post(
    "/servers/{server_id}/backups",
    response_model=BackupCreateAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_server_backup(
    server_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BackupCreateAcceptedOut:
    backup, task = await _service(request, session).create_pending(user, server_id)
    return BackupCreateAcceptedOut(backup_id=backup.id, task_id=task.id)


@router.get("/backups/{backup_id}", response_model=BackupOut)
async def get_backup(
    backup_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> BackupOut:
    b = await _service(request, session).get_for(user, backup_id)
    return BackupOut.model_validate(b)


@router.post(
    "/backups/{backup_id}/restore",
    response_model=RestoreAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def restore_backup(
    backup_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RestoreAcceptedOut:
    task = await _service(request, session).request_restore(user, backup_id)
    return RestoreAcceptedOut(task_id=task.id)
