from collections.abc import AsyncIterator, Iterator
from decimal import Decimal

import pytest
import pytest_asyncio
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, postgres_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    from gamehost_worker.core.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def respx_mock_router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest_asyncio.fixture
async def session(postgres_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def ctx(postgres_url: str) -> AsyncIterator[dict[str, object]]:
    engine = create_async_engine(postgres_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield {"engine": engine, "sm": sm, "node_agent_timeout_s": 5.0}
    await engine.dispose()


@pytest_asyncio.fixture
async def fixtures(session: AsyncSession) -> dict[str, object]:
    """Create user, template, node, server, provision-task. Returns dict of ids."""
    import uuid

    from gamehost_api.repositories.nodes import NodeRepository
    from gamehost_api.repositories.servers import ServersRepository
    from gamehost_api.repositories.tasks import TasksRepository
    from gamehost_api.repositories.templates import TemplateRepository
    from gamehost_api.repositories.users import UserRepository

    # truncate before each test
    await session.execute(
        text(
            "TRUNCATE backups, audit_log, tasks, servers, refresh_tokens, "
            "nodes, game_templates, users RESTART IDENTITY CASCADE"
        )
    )
    await session.commit()

    user = await UserRepository(session).create(email="u@x.test", password_hash="x", role="user")
    tpl = await TemplateRepository(session).create(
        slug="t",
        display_name="T",
        docker_image="busybox:latest",
        default_env={},
        default_ports=[{"container": 12345, "protocol": "tcp"}],
        default_volumes=[],
        min_resources={"cpuCores": 1.0, "memMb": 512},
        is_public=True,
    )
    node = await NodeRepository(session).create(
        name="n1",
        endpoint_url="http://node-1:8080",
        api_key="test-key",
        capacity_cpu=Decimal("8.00"),
        capacity_mem_mb=16384,
    )
    server = await ServersRepository(session).create(
        owner_id=user.id,
        name="srv",
        template_id=tpl.id,
        env_overrides={},
        resources={"cpuCores": 1.0, "memMb": 512},
    )
    task = await TasksRepository(session).create(server_id=server.id, kind="provision")
    await session.commit()
    return {
        "user_id": user.id,
        "template_id": tpl.id,
        "node_id": node.id,
        "server_id": server.id,
        "task_id": task.id,
        "node_endpoint": node.endpoint_url,
        "container_id": "abcdef123456",
        "fake_uuid": uuid.uuid4(),
    }
