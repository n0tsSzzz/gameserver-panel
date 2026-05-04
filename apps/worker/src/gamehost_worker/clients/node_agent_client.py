import asyncio
from typing import Any, Self

import httpx

from gamehost_api.db.models import Node


class NodeAgentHTTPError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"node-agent HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class ContainerMissingOnAgent(Exception):
    """node-agent returned 404 for the container."""


class NodeAgentClient:
    def __init__(self, node: Node, timeout_s: float = 10.0, max_retries: int = 3) -> None:
        self._client = httpx.AsyncClient(
            base_url=node.endpoint_url.rstrip("/"),
            timeout=timeout_s,
            headers={"authorization": f"Bearer {node.api_key}"},
        )
        self._max_retries = max_retries

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.aclose()

    async def _retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = await self._client.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            if response.status_code >= 500 and attempt < self._max_retries - 1:
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            return response
        assert last_exc is not None
        raise last_exc

    async def create_container(self, spec: dict[str, Any]) -> dict[str, Any]:
        r = await self._retry("POST", "/api/v1/containers", json=spec)
        if r.status_code != 201:
            raise NodeAgentHTTPError(r.status_code, r.text)
        return dict(r.json())

    async def get_container(self, container_id: str) -> dict[str, Any]:
        r = await self._retry("GET", f"/api/v1/containers/{container_id}")
        if r.status_code == 404:
            raise ContainerMissingOnAgent(container_id)
        if r.status_code != 200:
            raise NodeAgentHTTPError(r.status_code, r.text)
        return dict(r.json())

    async def lifecycle(self, container_id: str, action: str) -> dict[str, Any]:
        r = await self._retry("POST", f"/api/v1/containers/{container_id}/{action}")
        if r.status_code == 404:
            raise ContainerMissingOnAgent(container_id)
        if r.status_code != 200:
            raise NodeAgentHTTPError(r.status_code, r.text)
        return dict(r.json())

    async def backup(self, *, container_id: str, volume_name: str, s3_key: str) -> dict[str, Any]:
        r = await self._retry(
            "POST",
            f"/api/v1/containers/{container_id}/backup",
            json={"volumeName": volume_name, "s3Key": s3_key},
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0),
        )
        if r.status_code != 200:
            raise NodeAgentHTTPError(r.status_code, r.text)
        return dict(r.json())

    async def restore(self, *, container_id: str, volume_name: str, s3_key: str) -> dict[str, Any]:
        r = await self._retry(
            "POST",
            f"/api/v1/containers/{container_id}/restore",
            json={"volumeName": volume_name, "s3Key": s3_key},
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0),
        )
        if r.status_code != 200:
            raise NodeAgentHTTPError(r.status_code, r.text)
        return dict(r.json())

    async def delete_container(self, container_id: str) -> None:
        r = await self._retry("DELETE", f"/api/v1/containers/{container_id}")
        if r.status_code == 404:
            raise ContainerMissingOnAgent(container_id)
        if r.status_code != 204:
            raise NodeAgentHTTPError(r.status_code, r.text)
