import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import Backup, Server, Task, User
from gamehost_api.domain.access import RANK, get_server_role_for
from gamehost_api.domain.exceptions import (
    BackupNotFound,
    BackupNotReady,
    Forbidden,
    RestoreNotAllowed,
    ServerNotFound,
)
from gamehost_api.repositories.audit_log import AuditLogRepository
from gamehost_api.repositories.backups import BackupsRepository
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from gamehost_api.tasks.arq_pool import ArqPoolLike


class BackupService:
    def __init__(self, session: AsyncSession, arq_pool: ArqPoolLike) -> None:
        self._s = session
        self._arq = arq_pool
        self._backups = BackupsRepository(session)
        self._tasks = TasksRepository(session)
        self._servers = ServersRepository(session)
        self._audit = AuditLogRepository(session)

    async def _require_server(self, server_id: uuid.UUID, actor: User, min_role: str) -> Server:
        srv = await self._servers.get(server_id)
        if srv is None:
            raise ServerNotFound(str(server_id))
        role = await get_server_role_for(self._s, server_id, actor)
        if role is None:
            raise ServerNotFound(str(server_id))
        if RANK[role] < RANK[min_role]:
            raise Forbidden(f"requires server role >= {min_role}")
        return srv

    async def list_for_server(self, actor: User, server_id: uuid.UUID) -> list[Backup]:
        await self._require_server(server_id, actor, "viewer")
        return await self._backups.list_for_server(server_id)

    async def create_pending(self, actor: User, server_id: uuid.UUID) -> tuple[Backup, Task]:
        srv = await self._require_server(server_id, actor, "operator")
        backup_id = uuid.uuid4()
        backup = await self._backups.create_with_id(
            backup_id=backup_id,
            server_id=srv.id,
            s3_key=f"{srv.id}/{backup_id}.tar.gz",
            created_by=actor.id,
        )
        task = await self._tasks.create(
            server_id=srv.id,
            kind="backup",
            payload={"backup_id": str(backup.id)},
        )
        await self._audit.record(
            actor_id=actor.id,
            action="backup.requested",
            target_type="backup",
            target_id=str(backup.id),
            meta={"server_id": str(srv.id), "task_id": str(task.id)},
        )
        await self._arq.enqueue_job("backup_server", str(task.id), _job_id=str(task.id))
        return backup, task

    async def get_for(self, actor: User, backup_id: uuid.UUID) -> Backup:
        b = await self._backups.get(backup_id)
        if b is None:
            raise BackupNotFound(str(backup_id))
        await self._require_server(b.server_id, actor, "viewer")
        return b

    async def request_restore(self, actor: User, backup_id: uuid.UUID) -> Task:
        b = await self._backups.get(backup_id)
        if b is None:
            raise BackupNotFound(str(backup_id))
        srv = await self._require_server(b.server_id, actor, "owner")
        if b.status != "available":
            raise BackupNotReady(f"backup status={b.status}")
        if srv.status != "stopped":
            raise RestoreNotAllowed(f"server status={srv.status}, must be 'stopped'")
        task = await self._tasks.create(
            server_id=srv.id,
            kind="restore",
            payload={"backup_id": str(b.id)},
        )
        await self._audit.record(
            actor_id=actor.id,
            action="backup.restore_requested",
            target_type="backup",
            target_id=str(b.id),
            meta={"server_id": str(srv.id), "task_id": str(task.id)},
        )
        await self._arq.enqueue_job("restore_backup", str(task.id), _job_id=str(task.id))
        return task
