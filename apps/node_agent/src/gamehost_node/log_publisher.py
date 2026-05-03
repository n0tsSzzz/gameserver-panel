import asyncio
import contextlib
from typing import Any

import structlog

from gamehost_node.docker_facade import DockerFacade


class LogPublisher:
    """Streams docker logs of managed containers to Redis pub/sub.

    Publishers are spawned per container_id. The class is designed to be a
    long-lived singleton on app.state and used through start/stop/shutdown.
    """

    def __init__(self, redis_client: Any, facade: DockerFacade) -> None:
        self._redis = redis_client
        self._facade = facade
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._log = structlog.get_logger("log_publisher")

    async def start(self, container_id: str) -> None:
        existing = self._tasks.get(container_id)
        if existing is not None and not existing.done():
            return
        self._tasks[container_id] = asyncio.create_task(
            self._run(container_id), name=f"log-publisher-{container_id[:12]}"
        )

    async def stop(self, container_id: str) -> None:
        task = self._tasks.pop(container_id, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def shutdown(self) -> None:
        for cid in list(self._tasks):
            await self.stop(cid)
        close = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                result = close()
                if asyncio.iscoroutine(result):
                    await result

    async def _run(self, container_id: str) -> None:
        channel = f"logs:{container_id}"
        try:
            async for line in self._facade.stream_logs(container_id):
                payload = line.rstrip("\n")
                if payload:
                    await self._redis.publish(channel, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log.exception("log_publisher_failed", container_id=container_id)
