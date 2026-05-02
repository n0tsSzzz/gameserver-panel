import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.domain.exceptions import NodeNameTaken, NodeNotFound
from gamehost_api.domain.nodes import NodeService, verify_api_key
from gamehost_api.schemas.nodes import NodeCreateIn, NodePatchIn


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _payload(name: str = "node-a") -> NodeCreateIn:
    return NodeCreateIn.model_validate(
        {
            "name": name,
            "endpoint_url": "http://node:8080",
            "capacity_cpu": Decimal("8.00"),
            "capacity_mem_mb": 16384,
        }
    )


async def test_create_returns_plaintext_api_key_and_hashes_it(session: AsyncSession) -> None:
    svc = NodeService(session)
    node, plain = await svc.create(_payload())
    await session.commit()
    assert isinstance(plain, str) and len(plain) >= 32
    assert node.api_key_hash != plain
    assert verify_api_key(plain, node) is True
    assert verify_api_key("wrong-key", node) is False


async def test_create_duplicate_name_raises(session: AsyncSession) -> None:
    svc = NodeService(session)
    await svc.create(_payload("dup"))
    await session.commit()
    with pytest.raises(NodeNameTaken):
        await svc.create(_payload("dup"))


async def test_update_status_to_drain_succeeds(session: AsyncSession) -> None:
    svc = NodeService(session)
    node, _ = await svc.create(_payload("upd"))
    await session.commit()
    out = await svc.update(node.id, NodePatchIn(status="drain"))
    await session.commit()
    assert out.status == "drain"


async def test_update_unknown_raises(session: AsyncSession) -> None:
    svc = NodeService(session)
    with pytest.raises(NodeNotFound):
        await svc.update(uuid.uuid4(), NodePatchIn(status="drain"))


async def test_delete_unknown_raises(session: AsyncSession) -> None:
    svc = NodeService(session)
    with pytest.raises(NodeNotFound):
        await svc.delete(uuid.uuid4())


async def test_delete_succeeds(session: AsyncSession) -> None:
    svc = NodeService(session)
    node, _ = await svc.create(_payload("del"))
    await session.commit()
    await svc.delete(node.id)
    await session.commit()
    with pytest.raises(NodeNotFound):
        await svc.update(node.id, NodePatchIn(status="drain"))
