import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import docker
from docker.errors import APIError, NotFound

from gamehost_node.schemas.containers import (
    ContainerOut,
    ContainerStatsOut,
    ContainerStatus,
    CreateContainerIn,
)


def _normalize_status(state: str, exit_code: int | None) -> ContainerStatus:
    if state in ("running", "restarting", "paused"):
        return "running"
    if state == "created":
        return "pending"
    if state in ("exited", "dead"):
        if exit_code is None or exit_code == 0:
            return "stopped"
        return "failed"
    return "pending"


def _to_out(attrs: dict[str, Any]) -> ContainerOut:
    state = attrs.get("State", {})
    created_raw = attrs.get("Created", "")
    try:
        created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError:
        created_at = datetime.now(UTC)
    return ContainerOut(
        id=attrs.get("Id", "")[:12],
        name=attrs.get("Name", "").lstrip("/"),
        status=_normalize_status(state.get("Status", "created"), state.get("ExitCode")),
        image=(attrs.get("Config") or {}).get("Image", ""),
        created_at=created_at,
    )


def _to_stats(snapshot: dict[str, Any]) -> ContainerStatsOut:
    cpu = snapshot.get("cpu_stats", {})
    pre = snapshot.get("precpu_stats", {})
    cpu_total = cpu.get("cpu_usage", {}).get("total_usage", 0)
    pre_total = pre.get("cpu_usage", {}).get("total_usage", 0)
    sys_total = cpu.get("system_cpu_usage", 0)
    sys_pre = pre.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage") or [1])
    cpu_delta = cpu_total - pre_total
    sys_delta = sys_total - sys_pre
    cpu_percent = (cpu_delta / sys_delta * online * 100.0) if sys_delta > 0 else 0.0
    mem = snapshot.get("memory_stats", {})
    mem_usage = mem.get("usage", 0) - mem.get("stats", {}).get("cache", 0)
    return ContainerStatsOut(
        cpu_percent=round(max(0.0, cpu_percent), 2),
        mem_usage_mb=round(max(0.0, mem_usage) / 1024 / 1024, 2),
        mem_limit_mb=round(mem.get("limit", 0) / 1024 / 1024, 2),
    )


class DockerFacade:
    def __init__(self, base_url: str | None = None) -> None:
        self._client = docker.DockerClient(base_url=base_url) if base_url else docker.from_env()

    def _build_create_kwargs(self, spec: CreateContainerIn) -> dict[str, Any]:
        port_bindings: dict[str, list[dict[str, str]]] = {}
        for p in spec.ports:
            key = f"{p.container_port}/{p.protocol}"
            port_bindings.setdefault(key, []).append({"HostPort": str(p.host_port)})
        volumes: dict[str, dict[str, str]] = {
            v.name: {"bind": v.mount_path, "mode": "ro" if v.read_only else "rw"}
            for v in spec.volumes
        }
        kwargs: dict[str, Any] = {
            "image": spec.image,
            "name": spec.name,
            "environment": spec.env,
            "detach": True,
            "ports": port_bindings or None,
            "volumes": volumes or None,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only": spec.read_only_root,
            "nano_cpus": int(spec.resources.cpu_cores * 1_000_000_000),
            "mem_limit": f"{spec.resources.mem_mb}m",
            "restart_policy": {"Name": "unless-stopped"},
            "labels": {"gamehost.managed": "true", "gamehost.name": spec.name},
        }
        if spec.read_only_root:
            kwargs["tmpfs"] = {"/tmp": ""}
        if spec.network:
            kwargs["network"] = spec.network
        return kwargs

    async def create_and_start(self, spec: CreateContainerIn) -> ContainerOut:
        kwargs = self._build_create_kwargs(spec)
        container = await asyncio.to_thread(self._client.containers.run, **kwargs)
        await asyncio.to_thread(container.reload)
        return _to_out(container.attrs)

    async def _get(self, container_id: str) -> Any:
        return await asyncio.to_thread(self._client.containers.get, container_id)

    async def start(self, container_id: str) -> None:
        c = await self._get(container_id)
        await asyncio.to_thread(c.start)

    async def stop(self, container_id: str, *, timeout_s: int = 30) -> None:
        c = await self._get(container_id)
        await asyncio.to_thread(c.stop, timeout=timeout_s)

    async def restart(self, container_id: str) -> None:
        c = await self._get(container_id)
        await asyncio.to_thread(c.restart, timeout=30)

    async def remove(self, container_id: str) -> None:
        c = await self._get(container_id)
        await asyncio.to_thread(c.remove, force=True, v=False)

    async def inspect(self, container_id: str) -> ContainerOut:
        c = await self._get(container_id)
        await asyncio.to_thread(c.reload)
        return _to_out(c.attrs)

    async def stats(self, container_id: str) -> ContainerStatsOut:
        c = await self._get(container_id)
        snapshot = await asyncio.to_thread(c.stats, stream=False)
        return _to_stats(snapshot)

    async def stream_logs(self, container_id: str) -> AsyncIterator[str]:
        c = await self._get(container_id)
        it = await asyncio.to_thread(c.logs, stream=True, follow=True, timestamps=False)
        try:
            while True:
                chunk = await asyncio.to_thread(next, it, None)
                if chunk is None:
                    break
                if isinstance(chunk, bytes):
                    yield chunk.decode("utf-8", errors="replace")
                else:
                    yield str(chunk)
        finally:
            close = getattr(it, "close", None)
            if close is not None:
                close()


__all__ = ["APIError", "DockerFacade", "NotFound"]
