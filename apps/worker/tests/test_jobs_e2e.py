from typing import Any

import respx
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from gamehost_worker.jobs import delete, provision, start, stop


async def test_provision_succeeds_sets_running(
    ctx: dict[str, Any], fixtures: dict[str, Any], respx_mock_router: respx.MockRouter
) -> None:
    respx_mock_router.post("http://node-1:8080/api/v1/containers").mock(
        return_value=Response(
            201,
            json={
                "id": "abcdef123456",
                "name": f"gh-{fixtures['server_id']}",
                "status": "running",
                "image": "busybox:latest",
                "createdAt": "2026-05-03T00:00:00+00:00",
            },
        )
    )
    respx_mock_router.get("http://node-1:8080/api/v1/containers/abcdef123456").mock(
        return_value=Response(
            200,
            json={
                "id": "abcdef123456",
                "name": f"gh-{fixtures['server_id']}",
                "status": "running",
                "image": "busybox:latest",
                "createdAt": "2026-05-03T00:00:00+00:00",
                "stats": {"cpuPercent": 0.0, "memUsageMb": 0.0, "memLimitMb": 64.0},
            },
        )
    )

    await provision(ctx, str(fixtures["task_id"]))

    sm = ctx["sm"]
    async with sm() as s:
        srv = await ServersRepository(s).get(fixtures["server_id"])
        task = await TasksRepository(s).get(fixtures["task_id"])
    assert srv is not None
    assert srv.status == "running"
    assert srv.container_id == "abcdef123456"
    assert srv.host == "node-1"
    assert task is not None
    assert task.status == "succeeded"


async def test_provision_no_capacity_marks_failed(
    ctx: dict[str, Any], session: AsyncSession, fixtures: dict[str, Any]
) -> None:
    # bump server resources beyond node capacity
    srv = await ServersRepository(session).get(fixtures["server_id"])
    assert srv is not None
    srv.resources = {"cpuCores": 100.0, "memMb": 1_000_000}
    await session.commit()

    try:
        await provision(ctx, str(fixtures["task_id"]))
    except Exception:
        pass

    sm = ctx["sm"]
    async with sm() as s:
        srv = await ServersRepository(s).get(fixtures["server_id"])
        task = await TasksRepository(s).get(fixtures["task_id"])
    assert srv is not None
    assert srv.status == "failed"
    assert task is not None
    assert task.status == "failed"


async def test_lifecycle_start_then_stop(
    ctx: dict[str, Any],
    session: AsyncSession,
    fixtures: dict[str, Any],
    respx_mock_router: respx.MockRouter,
) -> None:
    # Make server look provisioned & stopped
    srv = await ServersRepository(session).get(fixtures["server_id"])
    assert srv is not None
    srv.status = "stopped"
    srv.container_id = "abcdef123456"
    srv.node_id = fixtures["node_id"]
    await session.commit()

    # create start task
    start_task = await TasksRepository(session).create(server_id=srv.id, kind="start")
    await session.commit()

    respx_mock_router.post("http://node-1:8080/api/v1/containers/abcdef123456/start").mock(
        return_value=Response(
            200,
            json={
                "id": "abcdef123456",
                "name": f"gh-{srv.id}",
                "status": "running",
                "image": "busybox:latest",
                "createdAt": "2026-05-03T00:00:00+00:00",
            },
        )
    )

    await start(ctx, str(start_task.id))

    sm = ctx["sm"]
    async with sm() as s:
        srv2 = await ServersRepository(s).get(fixtures["server_id"])
    assert srv2 is not None
    assert srv2.status == "running"

    stop_task = await TasksRepository(session).create(server_id=srv.id, kind="stop")
    await session.commit()

    respx_mock_router.post("http://node-1:8080/api/v1/containers/abcdef123456/stop").mock(
        return_value=Response(
            200,
            json={
                "id": "abcdef123456",
                "name": f"gh-{srv.id}",
                "status": "stopped",
                "image": "busybox:latest",
                "createdAt": "2026-05-03T00:00:00+00:00",
            },
        )
    )

    await stop(ctx, str(stop_task.id))
    async with sm() as s:
        srv3 = await ServersRepository(s).get(fixtures["server_id"])
    assert srv3 is not None
    assert srv3.status == "stopped"


async def test_delete_removes_row(
    ctx: dict[str, Any],
    session: AsyncSession,
    fixtures: dict[str, Any],
    respx_mock_router: respx.MockRouter,
) -> None:
    srv = await ServersRepository(session).get(fixtures["server_id"])
    assert srv is not None
    srv.status = "running"
    srv.container_id = "abcdef123456"
    srv.node_id = fixtures["node_id"]
    await session.commit()

    delete_task = await TasksRepository(session).create(server_id=srv.id, kind="delete")
    await session.commit()

    respx_mock_router.delete("http://node-1:8080/api/v1/containers/abcdef123456").mock(
        return_value=Response(204)
    )

    await delete(ctx, str(delete_task.id))

    sm = ctx["sm"]
    async with sm() as s:
        srv2 = await ServersRepository(s).get(fixtures["server_id"])
    assert srv2 is None


async def test_delete_with_already_missing_container_succeeds(
    ctx: dict[str, Any],
    session: AsyncSession,
    fixtures: dict[str, Any],
    respx_mock_router: respx.MockRouter,
) -> None:
    srv = await ServersRepository(session).get(fixtures["server_id"])
    assert srv is not None
    srv.status = "running"
    srv.container_id = "abcdef123456"
    srv.node_id = fixtures["node_id"]
    await session.commit()
    delete_task = await TasksRepository(session).create(server_id=srv.id, kind="delete")
    await session.commit()

    respx_mock_router.delete("http://node-1:8080/api/v1/containers/abcdef123456").mock(
        return_value=Response(404, json={"code": "container_not_found"})
    )

    await delete(ctx, str(delete_task.id))

    sm = ctx["sm"]
    async with sm() as s:
        srv2 = await ServersRepository(s).get(fixtures["server_id"])
        task = await TasksRepository(s).get(delete_task.id)
    assert srv2 is None
    assert task is not None
    assert task.status == "succeeded"
