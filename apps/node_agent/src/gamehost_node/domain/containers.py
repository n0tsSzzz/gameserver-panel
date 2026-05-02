from collections.abc import AsyncIterator

import docker.errors
import requests.exceptions

from gamehost_node.docker_facade import DockerFacade
from gamehost_node.domain.exceptions import (
    ContainerNameTaken,
    ContainerNotFound,
    DockerUnavailable,
)
from gamehost_node.schemas.containers import (
    ContainerDetailOut,
    ContainerOut,
    CreateContainerIn,
)


class ContainerService:
    def __init__(self, facade: DockerFacade) -> None:
        self._f = facade

    async def create(self, payload: CreateContainerIn) -> ContainerOut:
        try:
            return await self._f.create_and_start(payload)
        except docker.errors.APIError as exc:
            if exc.status_code == 409 or "is already in use" in str(exc):
                raise ContainerNameTaken(payload.name) from exc
            raise DockerUnavailable(str(exc)) from exc
        except requests.exceptions.ConnectionError as exc:
            raise DockerUnavailable(str(exc)) from exc

    async def get(self, container_id: str) -> ContainerDetailOut:
        try:
            base = await self._f.inspect(container_id)
            stats = await self._f.stats(container_id)
        except docker.errors.NotFound as exc:
            raise ContainerNotFound(container_id) from exc
        except (docker.errors.APIError, requests.exceptions.ConnectionError) as exc:
            raise DockerUnavailable(str(exc)) from exc
        return ContainerDetailOut(**base.model_dump(), stats=stats)

    async def start(self, container_id: str) -> ContainerOut:
        try:
            await self._f.start(container_id)
            return await self._f.inspect(container_id)
        except docker.errors.NotFound as exc:
            raise ContainerNotFound(container_id) from exc
        except (docker.errors.APIError, requests.exceptions.ConnectionError) as exc:
            raise DockerUnavailable(str(exc)) from exc

    async def stop(self, container_id: str) -> ContainerOut:
        try:
            await self._f.stop(container_id)
            return await self._f.inspect(container_id)
        except docker.errors.NotFound as exc:
            raise ContainerNotFound(container_id) from exc
        except (docker.errors.APIError, requests.exceptions.ConnectionError) as exc:
            raise DockerUnavailable(str(exc)) from exc

    async def restart(self, container_id: str) -> ContainerOut:
        try:
            await self._f.restart(container_id)
            return await self._f.inspect(container_id)
        except docker.errors.NotFound as exc:
            raise ContainerNotFound(container_id) from exc
        except (docker.errors.APIError, requests.exceptions.ConnectionError) as exc:
            raise DockerUnavailable(str(exc)) from exc

    async def delete(self, container_id: str) -> None:
        try:
            await self._f.remove(container_id)
        except docker.errors.NotFound as exc:
            raise ContainerNotFound(container_id) from exc
        except (docker.errors.APIError, requests.exceptions.ConnectionError) as exc:
            raise DockerUnavailable(str(exc)) from exc

    async def stream_logs(self, container_id: str) -> AsyncIterator[str]:
        try:
            async for line in self._f.stream_logs(container_id):
                yield line
        except docker.errors.NotFound as exc:
            raise ContainerNotFound(container_id) from exc
        except (docker.errors.APIError, requests.exceptions.ConnectionError) as exc:
            raise DockerUnavailable(str(exc)) from exc
