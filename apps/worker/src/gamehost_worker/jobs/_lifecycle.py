from typing import Any

import structlog

from gamehost_api.repositories.audit_log import AuditLogRepository
from gamehost_api.repositories.nodes import NodeRepository
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from gamehost_worker.clients.node_agent_client import NodeAgentClient
from gamehost_worker.jobs._common import task_uuid

_TARGET_STATUS: dict[str, str] = {
    "start": "running",
    "stop": "stopped",
    "restart": "running",
}


async def run_lifecycle(ctx: dict[str, Any], task_id_str: str, action: str) -> None:
    log = structlog.get_logger().bind(job=action, task_id=task_id_str)
    sm = ctx["sm"]
    timeout = ctx["node_agent_timeout_s"]
    task_id = task_uuid(task_id_str)

    server_id = None
    container_id = None
    node = None
    async with sm() as s:
        await TasksRepository(s).mark_running(task_id)
        t = await TasksRepository(s).get(task_id)
        if t is None or t.server_id is None:
            await s.commit()
            raise RuntimeError("task missing")
        server_id = t.server_id
        srv = await ServersRepository(s).get(server_id)
        if srv is None or srv.container_id is None or srv.node_id is None:
            await TasksRepository(s).mark_failed(task_id, "server has no container/node")
            await s.commit()
            raise RuntimeError("server not provisioned")
        container_id = srv.container_id
        node = await NodeRepository(s).get(srv.node_id)
        await s.commit()

    if node is None:
        async with sm() as s:
            await TasksRepository(s).mark_failed(task_id, "node missing")
            await s.commit()
        raise RuntimeError("node missing")

    try:
        async with NodeAgentClient(node, timeout_s=timeout) as client:
            await client.lifecycle(container_id, action)

        async with sm() as s:
            await ServersRepository(s).set_status(server_id, _TARGET_STATUS[action])
            await AuditLogRepository(s).record(
                action=f"server.{action}_completed",
                target_type="server",
                target_id=str(server_id),
                meta={"container_id": container_id},
            )
            await TasksRepository(s).mark_succeeded(task_id)
            await s.commit()
        log.info("lifecycle_succeeded")
    except Exception as exc:
        log.exception("lifecycle_failed")
        async with sm() as s:
            await TasksRepository(s).mark_failed(task_id, str(exc))
            await s.commit()
        raise
