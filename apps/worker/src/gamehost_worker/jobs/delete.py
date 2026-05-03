from typing import Any

import structlog

from gamehost_api.repositories.audit_log import AuditLogRepository
from gamehost_api.repositories.nodes import NodeRepository
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from gamehost_worker.clients.node_agent_client import (
    ContainerMissingOnAgent,
    NodeAgentClient,
)
from gamehost_worker.jobs._common import task_uuid


async def delete(ctx: dict[str, Any], task_id_str: str) -> None:
    log = structlog.get_logger().bind(job="delete", task_id=task_id_str)
    sm = ctx["sm"]
    timeout = ctx["node_agent_timeout_s"]
    task_id = task_uuid(task_id_str)

    server_id = None
    container_id = None
    node_id = None
    async with sm() as s:
        await TasksRepository(s).mark_running(task_id)
        t = await TasksRepository(s).get(task_id)
        if t is None or t.server_id is None:
            await s.commit()
            raise RuntimeError("task missing")
        server_id = t.server_id
        srv = await ServersRepository(s).get(server_id)
        if srv is None:
            await TasksRepository(s).mark_succeeded(task_id)
            await s.commit()
            return
        container_id = srv.container_id
        node_id = srv.node_id
        await s.commit()

    try:
        if container_id and node_id:
            async with sm() as s:
                node = await NodeRepository(s).get(node_id)
            if node is not None:
                try:
                    async with NodeAgentClient(node, timeout_s=timeout) as client:
                        await client.delete_container(container_id)
                except ContainerMissingOnAgent:
                    log.info("container_already_gone")

        async with sm() as s:
            srv = await ServersRepository(s).get(server_id)
            if srv is not None:
                await ServersRepository(s).delete_row(srv)
            await AuditLogRepository(s).record(
                action="server.deleted",
                target_type="server",
                target_id=str(server_id),
                meta={"container_id": container_id},
            )
            await TasksRepository(s).mark_succeeded(task_id)
            await s.commit()
        log.info("delete_succeeded")
    except Exception as exc:
        log.exception("delete_failed")
        async with sm() as s:
            await TasksRepository(s).mark_failed(task_id, str(exc))
            await s.commit()
        raise
