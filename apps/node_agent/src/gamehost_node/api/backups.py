from fastapi import APIRouter, Depends, Request

from gamehost_node.core.auth import require_api_key
from gamehost_node.domain.backups import BackupOps
from gamehost_node.schemas.backups import BackupRequestIn, BackupResultOut

router = APIRouter(dependencies=[Depends(require_api_key)])


def _ops(request: Request) -> BackupOps:
    return BackupOps(request.app.state.docker_facade)


@router.post("/{container_id}/backup", response_model=BackupResultOut)
async def create_backup(
    container_id: str,
    payload: BackupRequestIn,
    ops: BackupOps = Depends(_ops),
) -> BackupResultOut:
    size = await ops.run_backup(volume_name=payload.volume_name, s3_key=payload.s3_key)
    return BackupResultOut(size_bytes=size)


@router.post("/{container_id}/restore", response_model=BackupResultOut)
async def restore_backup(
    container_id: str,
    payload: BackupRequestIn,
    ops: BackupOps = Depends(_ops),
) -> BackupResultOut:
    size = await ops.run_restore(volume_name=payload.volume_name, s3_key=payload.s3_key)
    return BackupResultOut(size_bytes=size)
