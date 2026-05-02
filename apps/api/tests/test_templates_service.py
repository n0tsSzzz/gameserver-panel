import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.domain.exceptions import SlugAlreadyTaken, TemplateNotFound
from gamehost_api.domain.templates import TemplateService
from gamehost_api.schemas.templates import TemplateCreateIn, TemplatePatchIn
from tests.factories import make_user


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _payload(slug: str = "x", *, is_public: bool = True) -> TemplateCreateIn:
    return TemplateCreateIn.model_validate(
        {
            "slug": slug,
            "display_name": "X",
            "docker_image": "img",
            "is_public": is_public,
        }
    )


async def test_list_for_user_returns_only_public(session: AsyncSession) -> None:
    svc = TemplateService(session)
    user = await make_user(session, email="u@x.test", role="user")
    await svc.create(_payload("a", is_public=True))
    await svc.create(_payload("b", is_public=False))
    await session.commit()
    visible = await svc.list_(actor=user)
    assert {t.slug for t in visible} == {"a"}


async def test_list_for_admin_returns_all(session: AsyncSession) -> None:
    svc = TemplateService(session)
    admin = await make_user(session, email="a@x.test", role="admin")
    await svc.create(_payload("a", is_public=True))
    await svc.create(_payload("b", is_public=False))
    await session.commit()
    visible = await svc.list_(actor=admin)
    assert {t.slug for t in visible} == {"a", "b"}


async def test_create_duplicate_slug_raises(session: AsyncSession) -> None:
    svc = TemplateService(session)
    await svc.create(_payload("dup"))
    await session.commit()
    with pytest.raises(SlugAlreadyTaken):
        await svc.create(_payload("dup"))


async def test_update_unknown_raises(session: AsyncSession) -> None:
    svc = TemplateService(session)
    with pytest.raises(TemplateNotFound):
        await svc.update(uuid.uuid4(), TemplatePatchIn.model_validate({"display_name": "x"}))


async def test_update_partial(session: AsyncSession) -> None:
    svc = TemplateService(session)
    t = await svc.create(_payload("upd"))
    await session.commit()
    out = await svc.update(t.id, TemplatePatchIn.model_validate({"display_name": "New"}))
    await session.commit()
    assert out.display_name == "New"
    assert out.slug == "upd"
