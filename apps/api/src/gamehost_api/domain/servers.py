import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.clients.node_agent_client import NodeAgentClient
from gamehost_api.core.config import get_settings
from gamehost_api.core.security import (
    create_logs_stream_token,
    decode_logs_stream_token,
)
from gamehost_api.db.models import Server, Task, User
from gamehost_api.domain.exceptions import (
    InvalidServerState,
    ServerNotFound,
    TemplateNotFound,
)
from gamehost_api.repositories.audit_log import AuditLogRepository
from gamehost_api.repositories.nodes import NodeRepository
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
    def __init__(
        self,
        session: AsyncSession,
        arq_pool: ArqPoolLike,
        redis: Any | None = None,
    ) -> None:
        self._s = session
        self._arq = arq_pool
        self._redis = redis
        self._servers = ServersRepository(session)
        self._tasks = TasksRepository(session)
        self._audit = AuditLogRepository(session)
        self._templates = TemplateRepository(session)
        self._nodes = NodeRepository(session)

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

    async def get_log_tail(self, server_id: uuid.UUID, tail: int, actor: User) -> list[str]:
        srv = await self.get_for(server_id, actor)
        if srv.container_id is None or srv.node_id is None:
            return []
        node = await self._nodes.get(srv.node_id)
        if node is None:
            return []
        timeout = get_settings().node_agent_timeout_s
        async with NodeAgentClient(node, timeout_s=timeout) as client:
            return await client.tail_logs(srv.container_id, tail)

    async def mint_log_token(self, server_id: uuid.UUID, actor: User) -> tuple[str, datetime]:
        srv = await self.get_for(server_id, actor)
        return create_logs_stream_token(server_id=srv.id)

    async def authorize_log_stream(self, server_id: uuid.UUID, token: str) -> str:
        try:
            claims = decode_logs_stream_token(token)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if claims["sub"] != str(server_id):
            raise HTTPException(status_code=401, detail="token_scope_mismatch")
        srv = await self._servers.get(server_id)
        if srv is None or srv.container_id is None:
            raise HTTPException(status_code=404, detail="server_not_provisioned")
        if self._redis is None:
            raise HTTPException(status_code=503, detail="redis_unavailable")
        return srv.container_id

    async def stream_logs_iter(self, container_id: str) -> AsyncIterator[bytes]:
        import contextlib

        if self._redis is None:
            raise RuntimeError("redis client not configured")
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(f"logs:{container_id}")
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if msg is None:
                    yield b": ping\n\n"
                    continue
                if msg.get("type") != "message":
                    continue
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                yield f"data: {data}\n\n".encode()
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
            with contextlib.suppress(Exception):
                await pubsub.aclose()
