from typing import Any

import structlog

from gamehost_api.domain.exceptions import NoCapacity
from gamehost_api.domain.node_selector import least_loaded
from gamehost_api.repositories.audit_log import AuditLogRepository
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from gamehost_api.repositories.templates import TemplateRepository
from gamehost_worker.clients.node_agent_client import NodeAgentClient
from gamehost_worker.jobs._common import (
    build_create_spec,
    first_host_port,
    host_from_endpoint,
    task_uuid,
)


async def provision(ctx: dict[str, Any], task_id_str: str) -> None:
    log = structlog.get_logger().bind(job="provision", task_id=task_id_str)
    sm = ctx["sm"]
    timeout = ctx["node_agent_timeout_s"]
    task_id = task_uuid(task_id_str)

    server_id = None
    async with sm() as s:
        await TasksRepository(s).mark_running(task_id)
        await s.commit()

    try:
        async with sm() as s:
            t = await TasksRepository(s).get(task_id)
            if t is None or t.server_id is None:
                raise RuntimeError("task or server missing")
            server_id = t.server_id
            srv = await ServersRepository(s).get(server_id)
            if srv is None:
                raise RuntimeError("server row missing")
            tpl = await TemplateRepository(s).get(srv.template_id)
            if tpl is None:
                raise RuntimeError("template row missing")
            await ServersRepository(s).set_status(server_id, "provisioning")
            node = await least_loaded(s, srv.resources)
            if node is None:
                raise NoCapacity()
            spec = build_create_spec(srv, tpl)
            await s.commit()

            async with NodeAgentClient(node, timeout_s=timeout) as client:
                created = await client.create_container(spec)
                inspected = await client.get_container(created["id"])

            await ServersRepository(s).set_provisioned(
                server_id,
                node_id=node.id,
                container_id=created["id"],
                host=host_from_endpoint(node.endpoint_url),
                port=first_host_port(inspected),
                status="running",
            )
            await AuditLogRepository(s).record(
                action="server.provisioned",
                target_type="server",
                target_id=str(server_id),
                meta={"node_id": str(node.id), "container_id": created["id"]},
            )
            await s.commit()

        async with sm() as s:
            await TasksRepository(s).mark_succeeded(task_id)
            await s.commit()
        log.info("provision_succeeded")
    except Exception as exc:
        log.exception("provision_failed")
        async with sm() as s:
            if server_id is not None:
                await ServersRepository(s).set_status(server_id, "failed")
            await TasksRepository(s).mark_failed(task_id, str(exc))
            await s.commit()
        raise
