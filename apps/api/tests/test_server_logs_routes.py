import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
import respx
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import (
    create_access_token,
    create_logs_stream_token,
    decode_logs_stream_token,
)
from gamehost_api.db.models import User
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


def test_logs_stream_token_roundtrip() -> None:
    sid = uuid.uuid4()
    token, _ = create_logs_stream_token(server_id=sid)
    claims = decode_logs_stream_token(token)
    assert claims["sub"] == str(sid)
    assert claims["type"] == "logs_stream"


def test_logs_stream_token_wrong_type_raises() -> None:
    user_id = uuid.uuid4()
    access = create_access_token(user_id=user_id, email="a@x.test", role="user")
    import pytest

    with pytest.raises(ValueError):
        decode_logs_stream_token(access)


async def test_get_logs_tail_unauth_returns_401(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/servers/{uuid.uuid4()}/logs?tail=10")
    assert r.status_code == 401


async def test_get_logs_tail_unprovisioned_returns_empty_list(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    srv = await ServersRepository(session).create(
        owner_id=user.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await session.commit()
    r = await client.get(f"/api/v1/servers/{srv.id}/logs?tail=10", headers=_bearer(user))
    assert r.status_code == 200
    assert r.json()["lines"] == []


async def test_post_stream_token_returns_token(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    srv = await ServersRepository(session).create(
        owner_id=user.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await session.commit()
    r = await client.post(f"/api/v1/servers/{srv.id}/logs/stream-token", headers=_bearer(user))
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert "expiresAt" in body


async def test_post_stream_token_for_others_server_returns_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    me = await make_user(session, email="me@x.test", role="user")
    other = await make_user(session, email="o@x.test", role="user")
    tpl = await make_template(session, slug="t")
    srv = await ServersRepository(session).create(
        owner_id=other.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await session.commit()
    r = await client.post(f"/api/v1/servers/{srv.id}/logs/stream-token", headers=_bearer(me))
    assert r.status_code == 404


async def test_stream_with_invalid_token_returns_401(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/servers/{uuid.uuid4()}/logs/stream?t=garbage")
    assert r.status_code == 401


async def test_stream_with_token_for_other_server_returns_401(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    srv = await ServersRepository(session).create(
        owner_id=user.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    await session.commit()
    other_token, _ = create_logs_stream_token(server_id=uuid.uuid4())
    r = await client.get(f"/api/v1/servers/{srv.id}/logs/stream?t={other_token}")
    assert r.status_code == 401


async def test_get_logs_tail_proxies_to_node_agent(
    client: AsyncClient, session: AsyncSession
) -> None:
    from decimal import Decimal

    from gamehost_api.repositories.nodes import NodeRepository

    user = await make_user(session, email="u@x.test", role="user")
    tpl = await make_template(session, slug="t")
    node = await NodeRepository(session).create(
        name="n1",
        endpoint_url="http://node-1:8080",
        api_key="k",
        capacity_cpu=Decimal("8.00"),
        capacity_mem_mb=16384,
    )
    srv = await ServersRepository(session).create(
        owner_id=user.id, name="x", template_id=tpl.id, env_overrides={}, resources={}
    )
    srv.node_id = node.id
    srv.container_id = "abc123"
    await session.commit()

    with respx.mock(assert_all_called=True) as r:
        r.get("http://node-1:8080/api/v1/containers/abc123/logs").mock(
            return_value=Response(200, json={"lines": ["alpha", "beta"]})
        )
        out = await client.get(f"/api/v1/servers/{srv.id}/logs?tail=50", headers=_bearer(user))
    assert out.status_code == 200
    assert out.json()["lines"] == ["alpha", "beta"]
