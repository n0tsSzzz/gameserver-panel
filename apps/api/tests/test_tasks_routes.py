import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import create_access_token
from gamehost_api.db.models import User
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.tasks import TasksRepository
from tests.factories import make_admin, make_template, make_user


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _bearer(user: User) -> dict[str, str]:
    t = create_access_token(user_id=user.id, email=user.email, role=user.role)
    return {"authorization": f"Bearer {t}"}


async def test_get_unknown_task_returns_404(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    r = await client.get(f"/api/v1/tasks/{uuid.uuid4()}", headers=_bearer(user))
    assert r.status_code == 404
    assert r.json()["code"] == "task_not_found"


async def test_get_my_task_returns_200(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    srv = await ServersRepository(session).create(
        owner_id=user.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    task = await TasksRepository(session).create(server_id=srv.id, kind="provision")
    await session.commit()
    r = await client.get(f"/api/v1/tasks/{task.id}", headers=_bearer(user))
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "provision"
    assert body["status"] == "pending"


async def test_get_others_task_returns_404(client: AsyncClient, session: AsyncSession) -> None:
    me = await make_user(session, email="me@x.test", role="user")
    other = await make_user(session, email="o@x.test", role="user")
    tpl = await make_template(session, slug="t")
    srv = await ServersRepository(session).create(
        owner_id=other.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    task = await TasksRepository(session).create(server_id=srv.id, kind="provision")
    await session.commit()
    r = await client.get(f"/api/v1/tasks/{task.id}", headers=_bearer(me))
    assert r.status_code == 404


async def test_admin_sees_any_task(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    other = await make_user(session, email="o@x.test", role="user")
    tpl = await make_template(session, slug="t")
    srv = await ServersRepository(session).create(
        owner_id=other.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    task = await TasksRepository(session).create(server_id=srv.id, kind="provision")
    await session.commit()
    r = await client.get(f"/api/v1/tasks/{task.id}", headers=_bearer(admin))
    assert r.status_code == 200
