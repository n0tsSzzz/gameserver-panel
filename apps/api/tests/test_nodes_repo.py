from collections.abc import AsyncIterator
from decimal import Decimal

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.repositories.nodes import NodeRepository


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_create_get_list(session: AsyncSession) -> None:
    repo = NodeRepository(session)
    n = await repo.create(
        name="n1",
        endpoint_url="http://n1:8080",
        api_key_hash="h",
        capacity_cpu=Decimal("8.00"),
        capacity_mem_mb=16384,
    )
    await session.commit()
    fetched = await repo.get(n.id)
    assert fetched is not None
    listing = await repo.list_()
    assert len(listing) == 1


async def test_update_and_delete(session: AsyncSession) -> None:
    repo = NodeRepository(session)
    n = await repo.create(
        name="n2",
        endpoint_url="http://n2:8080",
        api_key_hash="h",
        capacity_cpu=Decimal("4.00"),
        capacity_mem_mb=8192,
    )
    await session.commit()
    await repo.update(n, {"status": "drain", "capacity_mem_mb": 4096})
    await session.commit()
    refetched = await repo.get(n.id)
    assert refetched is not None
    assert refetched.status == "drain"
    assert refetched.capacity_mem_mb == 4096
    await repo.delete(n)
    await session.commit()
    assert await repo.get(n.id) is None
