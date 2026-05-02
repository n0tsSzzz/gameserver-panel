import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from gamehost_node.docker_facade import DockerFacade
from gamehost_node.schemas.containers import ContainerOut, ContainerStatsOut

API_KEY = "test-node-key-123"


@pytest.fixture(scope="session", autouse=True)
def _api_key_env() -> Iterator[None]:
    os.environ["NODE_AGENT_API_KEY"] = API_KEY
    yield


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NODE_AGENT_API_KEY", API_KEY)
    from gamehost_node.core.config import get_settings

    get_settings.cache_clear()


def _default_container_out(name: str = "smoke", cid: str = "abc123def456") -> ContainerOut:
    return ContainerOut(
        id=cid[:12],
        name=name,
        status="running",
        image="busybox:latest",
        created_at=datetime.now(UTC),
    )


def _default_stats() -> ContainerStatsOut:
    return ContainerStatsOut(cpu_percent=1.5, mem_usage_mb=10.0, mem_limit_mb=64.0)


@pytest.fixture
def mock_facade() -> MagicMock:
    f = MagicMock(spec=DockerFacade)
    f.create_and_start = AsyncMock(return_value=_default_container_out())
    f.start = AsyncMock(return_value=None)
    f.stop = AsyncMock(return_value=None)
    f.restart = AsyncMock(return_value=None)
    f.remove = AsyncMock(return_value=None)
    f.inspect = AsyncMock(return_value=_default_container_out())
    f.stats = AsyncMock(return_value=_default_stats())

    async def _empty_stream() -> AsyncIterator[str]:
        for line in ["hello\n", "world\n"]:
            yield line

    f.stream_logs = MagicMock(return_value=_empty_stream())
    return f


@pytest_asyncio.fixture
async def client(mock_facade: MagicMock) -> AsyncIterator[AsyncClient]:
    from gamehost_node.main import app

    app.state.docker_facade = mock_facade
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        async with app.router.lifespan_context(app):
            yield c


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"authorization": f"Bearer {API_KEY}"}
