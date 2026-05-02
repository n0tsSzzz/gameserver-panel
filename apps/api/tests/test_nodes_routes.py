from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import create_access_token
from gamehost_api.db.models import User
from tests.factories import make_admin, make_user


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


def _create_payload(name: str = "node-1") -> dict[str, Any]:
    return {
        "name": name,
        "endpointUrl": "http://node-1:8080",
        "capacityCpu": "8.00",
        "capacityMemMb": 16384,
    }


async def test_post_as_user_returns_403(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    r = await client.post("/api/v1/nodes", headers=_bearer(user), json=_create_payload())
    assert r.status_code == 403


async def test_post_as_admin_returns_201_with_api_key(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    r = await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload())
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "node-1"
    assert isinstance(body["apiKey"], str) and len(body["apiKey"]) >= 32


async def test_subsequent_get_does_not_expose_api_key(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload("n2"))
    r = await client.get("/api/v1/nodes", headers=_bearer(admin))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert "apiKey" not in items[0]


async def test_patch_status_drain_returns_200(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    created = await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload("n3"))
    nid = created.json()["id"]
    r = await client.patch(f"/api/v1/nodes/{nid}", headers=_bearer(admin), json={"status": "drain"})
    assert r.status_code == 200
    assert r.json()["status"] == "drain"


async def test_patch_status_offline_returns_422(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    created = await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload("n4"))
    nid = created.json()["id"]
    r = await client.patch(
        f"/api/v1/nodes/{nid}", headers=_bearer(admin), json={"status": "offline"}
    )
    assert r.status_code == 422


async def test_delete_returns_204_then_404(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    created = await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload("n5"))
    nid = created.json()["id"]
    r1 = await client.delete(f"/api/v1/nodes/{nid}", headers=_bearer(admin))
    assert r1.status_code == 204
    r2 = await client.delete(f"/api/v1/nodes/{nid}", headers=_bearer(admin))
    assert r2.status_code == 404


async def test_post_duplicate_name_returns_409(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload("dup"))
    r = await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload("dup"))
    assert r.status_code == 409
    assert r.json()["code"] == "node_name_taken"
