import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from tests.factories import DEFAULT_PASSWORD, make_user


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_register_returns_201_and_camel_me(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "new@x.test", "password": DEFAULT_PASSWORD},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@x.test"
    assert body["role"] == "user"
    assert body["isActive"] is True
    assert "createdAt" in body


async def test_register_duplicate_returns_409_problem(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "dup@x.test", "password": DEFAULT_PASSWORD},
    )
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "DUP@x.test", "password": DEFAULT_PASSWORD},
    )
    assert r.status_code == 409
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "email_taken"


async def test_register_short_password_returns_422(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "x@x.test", "password": "short"},
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_login_success_returns_access_and_sets_cookie(
    client: AsyncClient, session: AsyncSession
) -> None:
    await make_user(session, email="ok@x.test")
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "ok@x.test", "password": DEFAULT_PASSWORD},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accessToken"]
    assert body["tokenType"] == "bearer"
    assert "gh_refresh" in r.cookies


async def test_login_wrong_password_returns_401_invalid_credentials(
    client: AsyncClient, session: AsyncSession
) -> None:
    await make_user(session, email="wrong@x.test")
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "wrong@x.test", "password": "not-the-password"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_login_unknown_email_returns_same_invalid_credentials(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "missing@x.test", "password": "any"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_login_inactive_user_returns_401(client: AsyncClient, session: AsyncSession) -> None:
    await make_user(session, email="dead@x.test", is_active=False)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "dead@x.test", "password": DEFAULT_PASSWORD},
    )
    assert r.status_code == 401


async def test_refresh_rotates_and_invalidates_old(
    client: AsyncClient, session: AsyncSession
) -> None:
    await make_user(session, email="rot@x.test")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "rot@x.test", "password": DEFAULT_PASSWORD},
    )
    old_cookie = login.cookies["gh_refresh"]
    refresh1 = await client.post("/api/v1/auth/refresh")
    assert refresh1.status_code == 200
    new_cookie = refresh1.cookies.get("gh_refresh") or client.cookies.get("gh_refresh")
    assert new_cookie != old_cookie

    bad = await client.post("/api/v1/auth/refresh", cookies={"gh_refresh": old_cookie})
    assert bad.status_code == 401


async def test_refresh_without_cookie_returns_401(client: AsyncClient) -> None:
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 401
    assert r.json()["code"] == "refresh_invalid"


async def test_logout_revokes_and_subsequent_refresh_fails(
    client: AsyncClient, session: AsyncSession
) -> None:
    await make_user(session, email="lo@x.test")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "lo@x.test", "password": DEFAULT_PASSWORD},
    )
    cookie = login.cookies["gh_refresh"]
    out = await client.post("/api/v1/auth/logout")
    assert out.status_code == 204
    after = await client.post("/api/v1/auth/refresh", cookies={"gh_refresh": cookie})
    assert after.status_code == 401


async def test_me_with_access_returns_profile(client: AsyncClient, session: AsyncSession) -> None:
    await make_user(session, email="me@x.test")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "me@x.test", "password": DEFAULT_PASSWORD},
    )
    access = login.json()["accessToken"]
    r = await client.get("/api/v1/auth/me", headers={"authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert r.json()["email"] == "me@x.test"


async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_me_with_expired_token_returns_401(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "1")
    get_settings.cache_clear()
    await make_user(session, email="exp@x.test")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "exp@x.test", "password": DEFAULT_PASSWORD},
    )
    access = login.json()["accessToken"]
    await asyncio.sleep(2)
    r = await client.get("/api/v1/auth/me", headers={"authorization": f"Bearer {access}"})
    assert r.status_code == 401
