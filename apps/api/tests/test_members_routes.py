import uuid
from collections.abc import AsyncIterator
from typing import Any

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


async def _create_server(session: AsyncSession, owner: User) -> Any:
    tpl = await make_template(session, slug=f"t-{uuid.uuid4().hex[:6]}")
    srv = await ServersRepository(session).create(
        owner_id=owner.id, name="srv", template_id=tpl.id, env_overrides={}, resources={}
    )
    await session.commit()
    return srv


async def test_invite_creates_token_and_member_can_accept(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = await make_user(session, email="owner@x.test", role="user")
    srv = await _create_server(session, owner)
    friend = await make_user(session, email="friend@x.test", role="user")

    inv = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(owner),
        json={"email": "friend@x.test", "role": "operator"},
    )
    assert inv.status_code == 201
    body = inv.json()
    assert body["token"] and body["expiresAt"] and "friend@x.test" not in body["token"]

    accept = await client.post(f"/api/v1/invites/{body['token']}/accept", headers=_bearer(friend))
    assert accept.status_code == 200
    assert accept.json()["serverId"] == str(srv.id)
    assert accept.json()["role"] == "operator"


async def test_accept_with_wrong_email_returns_403(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = await make_user(session, email="o2@x.test", role="user")
    srv = await _create_server(session, owner)
    other = await make_user(session, email="other@x.test", role="user")
    inv = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(owner),
        json={"email": "expected@x.test", "role": "viewer"},
    )
    token = inv.json()["token"]
    r = await client.post(f"/api/v1/invites/{token}/accept", headers=_bearer(other))
    assert r.status_code == 403
    assert r.json()["code"] == "invite_email_mismatch"


async def test_invite_duplicate_returns_409(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="o3@x.test", role="user")
    srv = await _create_server(session, owner)
    payload = {"email": "dup@x.test", "role": "viewer"}
    await client.post(
        f"/api/v1/servers/{srv.id}/members/invite", headers=_bearer(owner), json=payload
    )
    r = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite", headers=_bearer(owner), json=payload
    )
    assert r.status_code == 409
    assert r.json()["code"] == "invite_exists"


async def test_non_owner_cannot_invite(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="o4@x.test", role="user")
    srv = await _create_server(session, owner)
    other = await make_user(session, email="x@x.test", role="user")
    r = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(other),
        json={"email": "z@x.test", "role": "viewer"},
    )
    assert r.status_code == 404


async def test_admin_can_invite_on_any_server(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="o5@x.test", role="user")
    srv = await _create_server(session, owner)
    admin = await make_admin(session, email="adm-mem@x.test")
    r = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(admin),
        json={"email": "by-admin@x.test", "role": "viewer"},
    )
    assert r.status_code == 201


async def test_get_members_includes_owner(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="o6@x.test", role="user")
    srv = await _create_server(session, owner)
    r = await client.get(f"/api/v1/servers/{srv.id}/members", headers=_bearer(owner))
    assert r.status_code == 200
    members = r.json()
    assert any(m["role"] == "owner" and m["userId"] == str(owner.id) for m in members)


async def test_self_leave(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="o7@x.test", role="user")
    srv = await _create_server(session, owner)
    friend = await make_user(session, email="leaver@x.test", role="user")
    inv = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(owner),
        json={"email": "leaver@x.test", "role": "viewer"},
    )
    token = inv.json()["token"]
    await client.post(f"/api/v1/invites/{token}/accept", headers=_bearer(friend))
    r = await client.delete(
        f"/api/v1/servers/{srv.id}/members/{friend.id}", headers=_bearer(friend)
    )
    assert r.status_code == 204


async def test_viewer_cannot_start_operator_can(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="o8@x.test", role="user")
    srv = await _create_server(session, owner)
    # viewer
    viewer = await make_user(session, email="viewer@x.test", role="user")
    inv1 = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(owner),
        json={"email": "viewer@x.test", "role": "viewer"},
    )
    await client.post(f"/api/v1/invites/{inv1.json()['token']}/accept", headers=_bearer(viewer))
    # status=stopped to make /start admissible state-wise
    s = await ServersRepository(session).get(srv.id)
    assert s is not None
    s.status = "stopped"
    await session.commit()

    r = await client.post(f"/api/v1/servers/{srv.id}/start", headers=_bearer(viewer))
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"

    operator = await make_user(session, email="op@x.test", role="user")
    inv2 = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(owner),
        json={"email": "op@x.test", "role": "operator"},
    )
    await client.post(f"/api/v1/invites/{inv2.json()['token']}/accept", headers=_bearer(operator))
    r2 = await client.post(f"/api/v1/servers/{srv.id}/start", headers=_bearer(operator))
    assert r2.status_code == 202


async def test_viewer_can_get_server(client: AsyncClient, session: AsyncSession) -> None:
    owner = await make_user(session, email="o9@x.test", role="user")
    srv = await _create_server(session, owner)
    viewer = await make_user(session, email="vv@x.test", role="user")
    inv = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(owner),
        json={"email": "vv@x.test", "role": "viewer"},
    )
    await client.post(f"/api/v1/invites/{inv.json()['token']}/accept", headers=_bearer(viewer))
    r = await client.get(f"/api/v1/servers/{srv.id}", headers=_bearer(viewer))
    assert r.status_code == 200


async def test_list_servers_includes_member_servers(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = await make_user(session, email="o10@x.test", role="user")
    srv = await _create_server(session, owner)
    member = await make_user(session, email="m@x.test", role="user")
    inv = await client.post(
        f"/api/v1/servers/{srv.id}/members/invite",
        headers=_bearer(owner),
        json={"email": "m@x.test", "role": "viewer"},
    )
    await client.post(f"/api/v1/invites/{inv.json()['token']}/accept", headers=_bearer(member))
    r = await client.get("/api/v1/servers", headers=_bearer(member))
    assert r.status_code == 200
    assert any(s["id"] == str(srv.id) for s in r.json())
