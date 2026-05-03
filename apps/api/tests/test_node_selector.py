from collections.abc import AsyncIterator
from decimal import Decimal

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.domain.node_selector import least_loaded
from gamehost_api.repositories.nodes import NodeRepository
from gamehost_api.repositories.servers import ServersRepository
from tests.factories import make_template, make_user


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_returns_none_when_no_nodes(session: AsyncSession) -> None:
    out = await least_loaded(session, {"cpuCores": 1.0, "memMb": 512})
    assert out is None


async def test_picks_least_loaded(session: AsyncSession) -> None:
    repo = NodeRepository(session)
    n1 = await repo.create(
        name="n1",
        endpoint_url="http://n1",
        api_key="k",
        capacity_cpu=Decimal("8.00"),
        capacity_mem_mb=16384,
    )
    n2 = await repo.create(
        name="n2",
        endpoint_url="http://n2",
        api_key="k",
        capacity_cpu=Decimal("8.00"),
        capacity_mem_mb=16384,
    )
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    # heavy load on n1
    srv_repo = ServersRepository(session)
    s = await srv_repo.create(
        owner_id=user.id,
        name="big",
        template_id=tpl.id,
        env_overrides={},
        resources={"cpuCores": 6.0, "memMb": 8000},
    )
    s.node_id = n1.id
    s.status = "running"
    await session.commit()
    out = await least_loaded(session, {"cpuCores": 1.0, "memMb": 512})
    assert out is not None
    assert out.id == n2.id


async def test_returns_none_when_no_capacity(session: AsyncSession) -> None:
    repo = NodeRepository(session)
    await repo.create(
        name="small",
        endpoint_url="http://x",
        api_key="k",
        capacity_cpu=Decimal("1.00"),
        capacity_mem_mb=512,
    )
    await session.commit()
    out = await least_loaded(session, {"cpuCores": 4.0, "memMb": 8192})
    assert out is None


async def test_skips_offline_nodes(session: AsyncSession) -> None:
    repo = NodeRepository(session)
    n = await repo.create(
        name="off",
        endpoint_url="http://x",
        api_key="k",
        capacity_cpu=Decimal("8.00"),
        capacity_mem_mb=16384,
    )
    n.status = "offline"
    await session.commit()
    out = await least_loaded(session, {"cpuCores": 1.0, "memMb": 512})
    assert out is None
