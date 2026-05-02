import asyncio

import pytest

from gamehost_node.docker_facade import DockerFacade
from gamehost_node.schemas.containers import CreateContainerIn, Resources


@pytest.mark.docker_real
async def test_busybox_lifecycle_against_real_daemon() -> None:
    facade = DockerFacade()
    name = "gh-stage3-smoke"
    spec = CreateContainerIn(
        name=name,
        image="busybox:latest",
        env={},
        ports=[],
        volumes=[],
        resources=Resources(cpu_cores=0.5, mem_mb=64),
        read_only_root=True,
    )
    # ensure clean slate
    try:
        await facade.remove(name)
    except Exception:
        pass

    out = await facade.create_and_start(spec)
    try:
        assert out.status in ("running", "stopped")
        await asyncio.sleep(0.2)
        info = await facade.inspect(name)
        assert info.name == name
        await facade.stop(name)
        info2 = await facade.inspect(name)
        assert info2.status in ("stopped", "failed")
    finally:
        await facade.remove(name)
