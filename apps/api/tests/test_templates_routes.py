import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import create_access_token
from gamehost_api.db.models import User
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


async def test_get_unauth_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/v1/templates")
    assert r.status_code == 401


async def test_get_as_user_filters_to_public_only(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    await make_template(session, slug="pub", is_public=True)
    await make_template(session, slug="hid", is_public=False)
    r = await client.get("/api/v1/templates", headers=_bearer(user))
    assert r.status_code == 200
    assert {t["slug"] for t in r.json()} == {"pub"}


async def test_get_as_admin_returns_all(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    await make_template(session, slug="pub", is_public=True)
    await make_template(session, slug="hid", is_public=False)
    r = await client.get("/api/v1/templates", headers=_bearer(admin))
    assert {t["slug"] for t in r.json()} == {"pub", "hid"}


async def test_post_as_user_returns_403(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u2@x.test", role="user")
    r = await client.post(
        "/api/v1/templates",
        headers=_bearer(user),
        json={"slug": "x", "displayName": "X", "dockerImage": "img"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


async def test_post_as_admin_creates(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    r = await client.post(
        "/api/v1/templates",
        headers=_bearer(admin),
        json={"slug": "minecraft", "displayName": "MC", "dockerImage": "itzg/minecraft-server"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == "minecraft"
    assert body["isPublic"] is True


async def test_post_duplicate_slug_returns_409(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    await make_template(session, slug="dup")
    r = await client.post(
        "/api/v1/templates",
        headers=_bearer(admin),
        json={"slug": "dup", "displayName": "X", "dockerImage": "x"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "slug_taken"


async def test_post_invalid_slug_returns_422(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    r = await client.post(
        "/api/v1/templates",
        headers=_bearer(admin),
        json={"slug": "BAD SLUG", "displayName": "X", "dockerImage": "x"},
    )
    assert r.status_code == 422


async def test_patch_as_admin_changes_field(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    t = await make_template(session, slug="upd", display_name="Old")
    r = await client.patch(
        f"/api/v1/templates/{t.id}",
        headers=_bearer(admin),
        json={"displayName": "New"},
    )
    assert r.status_code == 200
    assert r.json()["displayName"] == "New"


async def test_patch_unknown_returns_404(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    r = await client.patch(
        f"/api/v1/templates/{uuid.uuid4()}",
        headers=_bearer(admin),
        json={"displayName": "X"},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "template_not_found"
