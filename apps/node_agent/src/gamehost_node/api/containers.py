from fastapi import APIRouter, Depends, Query, Request, Response, status

from gamehost_node.core.auth import require_api_key
from gamehost_node.domain.containers import ContainerService
from gamehost_node.schemas.containers import (
    ContainerDetailOut,
    ContainerOut,
    CreateContainerIn,
    LogsTailOut,
)

router = APIRouter(dependencies=[Depends(require_api_key)])


def _service(request: Request) -> ContainerService:
    return ContainerService(
        request.app.state.docker_facade,
        getattr(request.app.state, "log_publisher", None),
    )


@router.post("", response_model=ContainerOut, status_code=status.HTTP_201_CREATED)
async def create_container(
    payload: CreateContainerIn, svc: ContainerService = Depends(_service)
) -> ContainerOut:
    return await svc.create(payload)


@router.get("/{container_id}", response_model=ContainerDetailOut)
async def get_container(
    container_id: str, svc: ContainerService = Depends(_service)
) -> ContainerDetailOut:
    return await svc.get(container_id)


@router.post("/{container_id}/start", response_model=ContainerOut)
async def start_container(
    container_id: str, svc: ContainerService = Depends(_service)
) -> ContainerOut:
    return await svc.start(container_id)


@router.post("/{container_id}/stop", response_model=ContainerOut)
async def stop_container(
    container_id: str, svc: ContainerService = Depends(_service)
) -> ContainerOut:
    return await svc.stop(container_id)


@router.post("/{container_id}/restart", response_model=ContainerOut)
async def restart_container(
    container_id: str, svc: ContainerService = Depends(_service)
) -> ContainerOut:
    return await svc.restart(container_id)


@router.delete("/{container_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_container(
    container_id: str, svc: ContainerService = Depends(_service)
) -> Response:
    await svc.delete(container_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{container_id}/logs", response_model=LogsTailOut)
async def tail_container_logs(
    container_id: str,
    tail: int = Query(default=200, ge=1, le=10000),
    svc: ContainerService = Depends(_service),
) -> LogsTailOut:
    lines = await svc.tail_logs(container_id, tail)
    return LogsTailOut(lines=lines)
