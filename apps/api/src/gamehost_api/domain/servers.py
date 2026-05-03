import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import Server, Task, User
from gamehost_api.domain.exceptions import (
    InvalidServerState,
    ServerNotFound,
    TemplateNotFound,
)
from gamehost_api.repositories.audit_log import AuditLogRepository
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from gamehost_api.repositories.templates import TemplateRepository
from gamehost_api.schemas.servers import ServerCreateIn, ServerPatchIn
from gamehost_api.tasks.arq_pool import ArqPoolLike

_LIFECYCLE_ALLOWED: dict[str, set[str]] = {
    "start": {"stopped", "failed"},
    "stop": {"running"},
    "restart": {"running"},
}


def _authorize(server: Server, user: User) -> None:
    if server.owner_id != user.id and user.role != "admin":
        raise ServerNotFound(str(server.id))


class ServerService:
    def __init__(self, session: AsyncSession, arq_pool: ArqPoolLike) -> None:
        self._s = session
        self._arq = arq_pool
        self._servers = ServersRepository(session)
        self._tasks = TasksRepository(session)
        self._audit = AuditLogRepository(session)
        self._templates = TemplateRepository(session)

    async def list_for(self, user: User) -> list[Server]:
        if user.role == "admin":
            return await self._servers.list_all()
        return await self._servers.list_for_owner(user.id)

    async def get_for(self, server_id: uuid.UUID, user: User) -> Server:
        srv = await self._servers.get(server_id)
        if srv is None:
            raise ServerNotFound(str(server_id))
        _authorize(srv, user)
        return srv

    async def create(self, payload: ServerCreateIn, owner: User) -> tuple[Server, Task]:
        template = await self._templates.get(payload.template_id)
        if template is None:
            raise TemplateNotFound(str(payload.template_id))
        resources_dict = (
            payload.resources.model_dump(by_alias=True)
            if payload.resources is not None
            else dict(template.min_resources)
        )
        if not resources_dict:
            resources_dict = {"cpuCores": 1.0, "memMb": 1024}
        try:
            server = await self._servers.create(
                owner_id=owner.id,
                name=payload.name,
                template_id=payload.template_id,
                env_overrides=payload.env_overrides,
                resources=resources_dict,
            )
        except IntegrityError as exc:
            raise InvalidServerState("server name already taken") from exc
        task = await self._tasks.create(server_id=server.id, kind="provision")
        await self._audit.record(
            actor_id=owner.id,
            action="server.created",
            target_type="server",
            target_id=str(server.id),
            meta={"template_id": str(payload.template_id), "task_id": str(task.id)},
        )
        await self._enqueue("provision", task.id)
        return server, task

    async def request_action(
        self, server_id: uuid.UUID, kind: str, actor: User
    ) -> tuple[Server, Task]:
        srv = await self.get_for(server_id, actor)
        allowed = _LIFECYCLE_ALLOWED.get(kind)
        if allowed is None:
            raise InvalidServerState(f"unknown action {kind}")
        if srv.status not in allowed:
            raise InvalidServerState(f"cannot {kind} from status={srv.status}")
        task = await self._tasks.create(server_id=srv.id, kind=kind)
        await self._audit.record(
            actor_id=actor.id,
            action=f"server.{kind}_requested",
            target_type="server",
            target_id=str(srv.id),
            meta={"task_id": str(task.id)},
        )
        await self._enqueue(kind, task.id)
        return srv, task

    async def patch(self, server_id: uuid.UUID, payload: ServerPatchIn, actor: User) -> Server:
        srv = await self.get_for(server_id, actor)
        if srv.status != "stopped":
            raise InvalidServerState("can only patch stopped server")
        fields: dict[str, object] = {}
        if payload.env_overrides is not None:
            fields["env_overrides"] = payload.env_overrides
        if payload.resources is not None:
            fields["resources"] = payload.resources.model_dump(by_alias=True)
        if not fields:
            return srv
        return await self._servers.update_fields(srv, fields)

    async def delete(self, server_id: uuid.UUID, actor: User) -> tuple[Server, Task]:
        srv = await self.get_for(server_id, actor)
        if srv.status == "deleting":
            raise InvalidServerState("server is already being deleted")
        await self._servers.set_status(srv.id, "deleting")
        task = await self._tasks.create(server_id=srv.id, kind="delete")
        await self._audit.record(
            actor_id=actor.id,
            action="server.delete_requested",
            target_type="server",
            target_id=str(srv.id),
            meta={"task_id": str(task.id)},
        )
        await self._enqueue("delete", task.id)
        return srv, task

    async def _enqueue(self, kind: str, task_id: uuid.UUID) -> None:
        await self._arq.enqueue_job(kind, str(task_id), _job_id=str(task_id))
