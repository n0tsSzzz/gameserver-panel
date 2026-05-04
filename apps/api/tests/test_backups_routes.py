import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import create_access_token
from gamehost_api.db.models import User
from gamehost_api.repositories.backups import BackupsRepository
from gamehost_api.repositories.servers import ServersRepository
from tests.factories import make_template, make_user


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


async def _create_server(session: AsyncSession, owner: User) -> Any:
    tpl = await make_template(session, slug=f"t-{uuid.uuid4().hex[:6]}")
    srv = await ServersRepository(session).create(
        owner_id=owner.id, name="srv", template_id=tpl.id, env_overrides={}, resources={}
    )
    await session.commit()
    return srv


async def test_owner_can_create_backup(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="bk-o@x.test", role="user")
    srv = await _create_server(session, owner)
    r = await client.post(f"/api/v1/servers/{srv.id}/backups", headers=_bearer(owner))
    assert r.status_code == 202
    body = r.json()
    assert "backupId" in body and "taskId" in body


async def test_list_backups(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="bk-l@x.test", role="user")
    srv = await _create_server(session, owner)
    await client.post(f"/api/v1/servers/{srv.id}/backups", headers=_bearer(owner))
    r = await client.get(f"/api/v1/servers/{srv.id}/backups", headers=_bearer(owner))
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["status"] == "creating"


async def test_random_user_cannot_list_backups(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="bk-x@x.test", role="user")
    srv = await _create_server(session, owner)
    other = await make_user(session, email="bk-other@x.test", role="user")
    r = await client.get(f"/api/v1/servers/{srv.id}/backups", headers=_bearer(other))
    assert r.status_code == 404


async def test_get_backup_404_for_non_member(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="bk-g@x.test", role="user")
    srv = await _create_server(session, owner)
    backup = await BackupsRepository(session).create_with_id(
        backup_id=uuid.uuid4(),
        server_id=srv.id,
        s3_key=f"{srv.id}/abc.tar.gz",
        created_by=owner.id,
    )
    await session.commit()
    other = await make_user(session, email="bk-otr@x.test", role="user")
    r = await client.get(f"/api/v1/backups/{backup.id}", headers=_bearer(other))
    assert r.status_code == 404


async def test_restore_requires_stopped(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="bk-r@x.test", role="user")
    srv = await _create_server(session, owner)
    backup = await BackupsRepository(session).create_with_id(
        backup_id=uuid.uuid4(),
        server_id=srv.id,
        s3_key=f"{srv.id}/r.tar.gz",
        created_by=owner.id,
    )
    await BackupsRepository(session).mark_available(backup.id, 1024)
    await session.commit()
    # default status from server factory is 'pending' — not stopped
    r = await client.post(f"/api/v1/backups/{backup.id}/restore", headers=_bearer(owner))
    assert r.status_code == 409
    assert r.json()["code"] == "restore_not_allowed"


async def test_restore_requires_available_backup(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = await make_user(session, email="bk-na@x.test", role="user")
    srv = await _create_server(session, owner)
    s = await ServersRepository(session).get(srv.id)
    assert s is not None
    s.status = "stopped"
    await session.commit()
    backup = await BackupsRepository(session).create_with_id(
        backup_id=uuid.uuid4(),
        server_id=srv.id,
        s3_key=f"{srv.id}/na.tar.gz",
        created_by=owner.id,
    )
    await session.commit()
    r = await client.post(f"/api/v1/backups/{backup.id}/restore", headers=_bearer(owner))
    assert r.status_code == 409
    assert r.json()["code"] == "backup_not_ready"


async def test_restore_owner_only(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="bk-oo@x.test", role="user")
    srv = await _create_server(session, owner)
    s = await ServersRepository(session).get(srv.id)
    assert s is not None
    s.status = "stopped"
    await session.commit()
    backup = await BackupsRepository(session).create_with_id(
        backup_id=uuid.uuid4(),
        server_id=srv.id,
        s3_key=f"{srv.id}/oo.tar.gz",
        created_by=owner.id,
    )
    await BackupsRepository(session).mark_available(backup.id, 1024)
    await session.commit()
    # invite operator
    operator = await make_user(session, email="bk-op@x.test", role="user")
    inv = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(owner),
        json={"email": "bk-op@x.test", "role": "operator"},
    )
    await client.post(f"/api/v1/invites/{inv.json()['token']}/accept", headers=_bearer(operator))
    r = await client.post(f"/api/v1/backups/{backup.id}/restore", headers=_bearer(operator))
    assert r.status_code == 403


async def test_viewer_cannot_create_backup(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="bk-vw@x.test", role="user")
    srv = await _create_server(session, owner)
    viewer = await make_user(session, email="bk-vwr@x.test", role="user")
    inv = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(owner),
        json={"email": "bk-vwr@x.test", "role": "viewer"},
    )
    await client.post(f"/api/v1/invites/{inv.json()['token']}/accept", headers=_bearer(viewer))
    r = await client.post(f"/api/v1/servers/{srv.id}/backups", headers=_bearer(viewer))
    assert r.status_code == 403
