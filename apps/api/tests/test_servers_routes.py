import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import create_access_token
from gamehost_api.db.models import User
from gamehost_api.repositories.servers import ServersRepository
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


def _create_payload(name: str, template_id: uuid.UUID) -> dict[str, Any]:
    return {
        "name": name,
        "templateId": str(template_id),
        "envOverrides": {},
        "resources": {"cpuCores": 1.0, "memMb": 1024},
    }


async def test_get_unauth_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/v1/servers")
    assert r.status_code == 401


async def test_post_creates_server_and_task_returns_202(
    client: AsyncClient, session: AsyncSession, arq_pool_mock: MagicMock
) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="mc")
    r = await client.post(
        "/api/v1/servers",
        headers=_bearer(user),
        json=_create_payload("my-srv", tpl.id),
    )
    assert r.status_code == 202
    body = r.json()
    assert "serverId" in body and "taskId" in body
    arq_pool_mock.enqueue_job.assert_awaited_once()


async def test_post_unknown_template_returns_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    r = await client.post(
        "/api/v1/servers",
        headers=_bearer(user),
        json=_create_payload("x", uuid.uuid4()),
    )
    assert r.status_code == 404
    assert r.json()["code"] == "template_not_found"


async def test_get_returns_only_my_servers(client: AsyncClient, session: AsyncSession) -> None:
    me = await make_user(session, email="me@x.test", role="user")
    other = await make_user(session, email="other@x.test", role="user")
    tpl = await make_template(session, slug="t")
    repo = ServersRepository(session)
    await repo.create(
        owner_id=me.id, name="mine", template_id=tpl.id, env_overrides={}, resources={}
    )
    await repo.create(
        owner_id=other.id,
        name="not-mine",
        template_id=tpl.id,
        env_overrides={},
        resources={},
    )
    await session.commit()
    r = await client.get("/api/v1/servers", headers=_bearer(me))
    assert r.status_code == 200
    assert {s["name"] for s in r.json()} == {"mine"}


async def test_admin_sees_all_servers(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    other = await make_user(session, email="o@x.test", role="user")
    tpl = await make_template(session, slug="t")
    repo = ServersRepository(session)
    await repo.create(
        owner_id=other.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await session.commit()
    r = await client.get("/api/v1/servers", headers=_bearer(admin))
    assert {s["name"] for s in r.json()} == {"x"}


async def test_get_others_server_returns_404(client: AsyncClient, session: AsyncSession) -> None:
    me = await make_user(session, email="me2@x.test", role="user")
    other = await make_user(session, email="other2@x.test", role="user")
    tpl = await make_template(session, slug="t")
    srv = await ServersRepository(session).create(
        owner_id=other.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await session.commit()
    r = await client.get(f"/api/v1/servers/{srv.id}", headers=_bearer(me))
    assert r.status_code == 404


async def test_start_when_pending_returns_409(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    srv = await ServersRepository(session).create(
        owner_id=user.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await session.commit()
    r = await client.post(f"/api/v1/servers/{srv.id}/start", headers=_bearer(user))
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_server_state"


async def test_patch_when_running_returns_409(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    repo = ServersRepository(session)
    srv = await repo.create(
        owner_id=user.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await repo.set_status(srv.id, "running")
    await session.commit()
    r = await client.patch(
        f"/api/v1/servers/{srv.id}",
        headers=_bearer(user),
        json={"envOverrides": {"K": "v"}},
    )
    assert r.status_code == 409


async def test_patch_when_stopped_changes_env(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    repo = ServersRepository(session)
    srv = await repo.create(
        owner_id=user.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await repo.set_status(srv.id, "stopped")
    await session.commit()
    r = await client.patch(
        f"/api/v1/servers/{srv.id}",
        headers=_bearer(user),
        json={"envOverrides": {"K": "v"}},
    )
    assert r.status_code == 200
    assert r.json()["envOverrides"] == {"K": "v"}


async def test_delete_sets_deleting_and_enqueues(
    client: AsyncClient, session: AsyncSession, arq_pool_mock: MagicMock
) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    repo = ServersRepository(session)
    srv = await repo.create(
        owner_id=user.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await repo.set_status(srv.id, "running")
    await session.commit()
    r = await client.delete(f"/api/v1/servers/{srv.id}", headers=_bearer(user))
    assert r.status_code == 202
    arq_pool_mock.enqueue_job.assert_awaited_once()


async def test_unknown_id_returns_404(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    r = await client.get(f"/api/v1/servers/{uuid.uuid4()}", headers=_bearer(user))
    assert r.status_code == 404
