import uuid
from typing import Any

import pytest
import respx
from httpx import Response

from gamehost_api.repositories.backups import BackupsRepository
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from gamehost_worker.jobs import backup_server, restore_backup


async def _provision_server_state(
    sm: Any, server_id: uuid.UUID, container: str, node_id: uuid.UUID
) -> None:
    async with sm() as s:
        srv = await ServersRepository(s).get(server_id)
        assert srv is not None
        srv.container_id = container
        srv.node_id = node_id
        await s.commit()


async def test_backup_happy_path(
    ctx: dict[str, Any],
    fixtures: dict[str, Any],
    respx_mock_router: respx.MockRouter,
) -> None:
    sm = ctx["sm"]
    server_id = fixtures["server_id"]
    container = "deadbeef0001"
    await _provision_server_state(sm, server_id, container, fixtures["node_id"])

    # create backup row + task
    async with sm() as s:
        backup = await BackupsRepository(s).create_with_id(
            backup_id=uuid.uuid4(),
            server_id=server_id,
            s3_key=f"{server_id}/k.tar.gz",
            created_by=fixtures["user_id"],
        )
        task = await TasksRepository(s).create(
            server_id=server_id,
            kind="backup",
            payload={"backup_id": str(backup.id)},
        )
        await s.commit()

    respx_mock_router.post(f"http://node-1:8080/api/v1/containers/{container}/backup").mock(
        return_value=Response(200, json={"sizeBytes": 4096})
    )

    await backup_server(ctx, str(task.id))

    async with sm() as s:
        b = await BackupsRepository(s).get(backup.id)
        t = await TasksRepository(s).get(task.id)
    assert b is not None and b.status == "available" and b.size_bytes == 4096
    assert t is not None and t.status == "succeeded"


async def test_backup_failure_marks_failed(
    ctx: dict[str, Any],
    fixtures: dict[str, Any],
    respx_mock_router: respx.MockRouter,
) -> None:
    sm = ctx["sm"]
    server_id = fixtures["server_id"]
    container = "deadbeef0002"
    await _provision_server_state(sm, server_id, container, fixtures["node_id"])

    async with sm() as s:
        backup = await BackupsRepository(s).create_with_id(
            backup_id=uuid.uuid4(),
            server_id=server_id,
            s3_key=f"{server_id}/k2.tar.gz",
            created_by=fixtures["user_id"],
        )
        task = await TasksRepository(s).create(
            server_id=server_id,
            kind="backup",
            payload={"backup_id": str(backup.id)},
        )
        await s.commit()

    respx_mock_router.post(f"http://node-1:8080/api/v1/containers/{container}/backup").mock(
        return_value=Response(503, text="s3 down")
    )

    with pytest.raises(Exception):
        await backup_server(ctx, str(task.id))

    async with sm() as s:
        b = await BackupsRepository(s).get(backup.id)
        t = await TasksRepository(s).get(task.id)
    assert b is not None and b.status == "failed"
    assert t is not None and t.status == "failed"


async def test_restore_happy_path(
    ctx: dict[str, Any],
    fixtures: dict[str, Any],
    respx_mock_router: respx.MockRouter,
) -> None:
    sm = ctx["sm"]
    server_id = fixtures["server_id"]
    container = "deadbeef0003"
    # set server stopped + container set
    async with sm() as s:
        srv = await ServersRepository(s).get(server_id)
        assert srv is not None
        srv.container_id = container
        srv.node_id = fixtures["node_id"]
        srv.status = "stopped"
        await s.commit()

    async with sm() as s:
        backup = await BackupsRepository(s).create_with_id(
            backup_id=uuid.uuid4(),
            server_id=server_id,
            s3_key=f"{server_id}/r.tar.gz",
            created_by=fixtures["user_id"],
        )
        await BackupsRepository(s).mark_available(backup.id, 1024)
        task = await TasksRepository(s).create(
            server_id=server_id,
            kind="restore",
            payload={"backup_id": str(backup.id)},
        )
        await s.commit()

    respx_mock_router.post(f"http://node-1:8080/api/v1/containers/{container}/restore").mock(
        return_value=Response(200, json={"sizeBytes": 1024})
    )

    await restore_backup(ctx, str(task.id))

    async with sm() as s:
        t = await TasksRepository(s).get(task.id)
    assert t is not None and t.status == "succeeded"


async def test_restore_rejects_running_server(
    ctx: dict[str, Any],
    fixtures: dict[str, Any],
) -> None:
    sm = ctx["sm"]
    server_id = fixtures["server_id"]
    async with sm() as s:
        srv = await ServersRepository(s).get(server_id)
        assert srv is not None
        srv.container_id = "abc"
        srv.node_id = fixtures["node_id"]
        srv.status = "running"
        await s.commit()

    async with sm() as s:
        backup = await BackupsRepository(s).create_with_id(
            backup_id=uuid.uuid4(),
            server_id=server_id,
            s3_key=f"{server_id}/x.tar.gz",
            created_by=fixtures["user_id"],
        )
        await BackupsRepository(s).mark_available(backup.id, 1)
        task = await TasksRepository(s).create(
            server_id=server_id,
            kind="restore",
            payload={"backup_id": str(backup.id)},
        )
        await s.commit()

    with pytest.raises(Exception):
        await restore_backup(ctx, str(task.id))

    async with sm() as s:
        t = await TasksRepository(s).get(task.id)
    assert t is not None and t.status == "failed"
