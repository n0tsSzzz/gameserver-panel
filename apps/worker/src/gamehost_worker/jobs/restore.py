import uuid
from typing import Any

import structlog

from gamehost_api.repositories.audit_log import AuditLogRepository
from gamehost_api.repositories.backups import BackupsRepository
from gamehost_api.repositories.nodes import NodeRepository
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from gamehost_worker.clients.node_agent_client import NodeAgentClient
from gamehost_worker.jobs._common import task_uuid


async def restore_backup(ctx: dict[str, Any], task_id_str: str) -> None:
    log = structlog.get_logger().bind(job="restore", task_id=task_id_str)
    sm = ctx["sm"]
    timeout = ctx["node_agent_timeout_s"]
    task_id = task_uuid(task_id_str)

    backup_id: uuid.UUID | None = None
    server_id: uuid.UUID | None = None
    container_id: str | None = None
    node = None
    s3_key: str | None = None

    async with sm() as s:
        await TasksRepository(s).mark_running(task_id)
        t = await TasksRepository(s).get(task_id)
        if t is None:
            await s.commit()
            raise RuntimeError("task missing")
        payload = t.payload or {}
        backup_id = uuid.UUID(payload["backup_id"])
        server_id = t.server_id
        srv = await ServersRepository(s).get(server_id) if server_id else None
        backup = await BackupsRepository(s).get(backup_id)
        if srv is None or backup is None or srv.node_id is None or srv.container_id is None:
            await TasksRepository(s).mark_failed(task_id, "server/backup missing")
            await s.commit()
            raise RuntimeError("server/backup missing")
        if srv.status != "stopped":
            await TasksRepository(s).mark_failed(
                task_id, f"server status={srv.status}, must be stopped"
            )
            await s.commit()
            raise RuntimeError("restore_not_allowed")
        container_id = srv.container_id
        s3_key = backup.s3_key
        node = await NodeRepository(s).get(srv.node_id)
        await s.commit()

    if node is None:
        async with sm() as s:
            await TasksRepository(s).mark_failed(task_id, "node missing")
            await s.commit()
        raise RuntimeError("node missing")

    try:
        async with NodeAgentClient(node, timeout_s=timeout) as client:
            result = await client.restore(
                container_id=container_id,
                volume_name=f"gh-{server_id}-data",
                s3_key=s3_key,
            )
        size = int(result.get("sizeBytes", 0))
        async with sm() as s:
            await TasksRepository(s).mark_succeeded(task_id)
            await AuditLogRepository(s).record(
                action="backup.restored",
                target_type="backup",
                target_id=str(backup_id),
                meta={"server_id": str(server_id), "size_bytes": size},
            )
            await s.commit()
        log.info("restore_succeeded", size=size)
    except Exception as exc:
        log.exception("restore_failed")
        async with sm() as s:
            await TasksRepository(s).mark_failed(task_id, str(exc))
            await s.commit()
        raise
