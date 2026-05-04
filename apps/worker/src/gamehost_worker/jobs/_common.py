import uuid
from typing import Any
from urllib.parse import urlparse

from gamehost_api.db.models import GameTemplate, Node, Server


def host_from_endpoint(endpoint_url: str) -> str:
    parsed = urlparse(endpoint_url)
    return parsed.hostname or endpoint_url


def build_create_spec(server: Server, template: GameTemplate) -> dict[str, Any]:
    env = {**dict(template.default_env), **dict(server.env_overrides)}
    ports = [
        {"containerPort": p["container"], "hostPort": 0, "protocol": p.get("protocol", "tcp")}
        for p in template.default_ports
    ]
    res = server.resources or template.min_resources or {"cpuCores": 1.0, "memMb": 1024}
    return {
        "name": f"gh-{server.id}",
        "image": template.docker_image,
        "env": {k: str(v) for k, v in env.items()},
        "ports": ports,
        "volumes": [{"name": f"gh-{server.id}-data", "mountPath": "/data", "readOnly": False}],
        "resources": {
            "cpuCores": float(res.get("cpuCores", 1.0)),
            "memMb": int(res.get("memMb", 1024)),
        },
        "readOnlyRoot": True,
    }


def first_host_port(container_info: dict[str, Any]) -> int | None:
    """Best-effort: node-agent may extend ContainerOut with NetworkSettings later.
    For now, return None — the host port resolution lives in stage 5+ once we
    extend node-agent to surface mapped ports."""
    ports = container_info.get("ports") or []
    if isinstance(ports, list) and ports:
        first = ports[0]
        if isinstance(first, dict):
            host = first.get("hostPort")
            if isinstance(host, int) and host > 0:
                return host
    return None


def task_uuid(task_id_str: str) -> uuid.UUID:
    return uuid.UUID(task_id_str)


__all__ = [
    "GameTemplate",
    "Node",
    "Server",
    "build_create_spec",
    "first_host_port",
    "host_from_endpoint",
    "task_uuid",
]
