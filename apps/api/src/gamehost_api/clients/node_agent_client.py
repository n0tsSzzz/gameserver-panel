from typing import Any, Self

import httpx

from gamehost_api.db.models import Node


class NodeAgentClient:
    def __init__(self, node: Node, timeout_s: float = 10.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=node.endpoint_url.rstrip("/"),
            timeout=timeout_s,
            headers={"authorization": f"Bearer {node.api_key}"},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.aclose()

    async def tail_logs(self, container_id: str, n: int) -> list[str]:
        r = await self._client.get(f"/api/v1/containers/{container_id}/logs", params={"tail": n})
        r.raise_for_status()
        body = r.json()
        return list(body.get("lines", []))
