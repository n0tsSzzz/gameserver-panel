from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.repositories.templates import TemplateRepository


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _kw(slug: str, *, is_public: bool = True) -> dict[str, Any]:
    return dict(
        slug=slug,
        display_name="X",
        docker_image="img",
        default_env={},
        default_ports=[],
        default_volumes=[],
        min_resources={},
        is_public=is_public,
    )


async def test_create_then_get_by_slug(session: AsyncSession) -> None:
    repo = TemplateRepository(session)
    t = await repo.create(**_kw("mc"))
    await session.commit()
    fetched = await repo.get_by_slug("mc")
    assert fetched is not None and fetched.id == t.id


async def test_list_public_only_filters_hidden(session: AsyncSession) -> None:
    repo = TemplateRepository(session)
    await repo.create(**_kw("p1", is_public=True))
    await repo.create(**_kw("h1", is_public=False))
    await session.commit()
    public = await repo.list_(public_only=True)
    all_ = await repo.list_(public_only=False)
    assert {t.slug for t in public} == {"p1"}
    assert {t.slug for t in all_} == {"p1", "h1"}


async def test_update_changes_fields_and_updated_at(session: AsyncSession) -> None:
    repo = TemplateRepository(session)
    t = await repo.create(**_kw("u"))
    await session.commit()
    before = t.updated_at
    out = await repo.update(t, {"display_name": "B"})
    await session.commit()
    assert out.display_name == "B"
    assert out.updated_at >= before
