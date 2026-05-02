from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.repositories.refresh_tokens import RefreshTokenRepository
from gamehost_api.repositories.users import UserRepository


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_create_and_get_user_by_email_lower(session: AsyncSession) -> None:
    repo = UserRepository(session)
    user = await repo.create(email="A@B.test", password_hash="h", role="user")
    await session.commit()

    fetched = await repo.get_by_email("a@b.test")
    assert fetched is not None
    assert fetched.id == user.id

    fetched2 = await repo.get_by_email("A@b.TEST")
    assert fetched2 is not None
    assert fetched2.id == user.id


async def test_unique_email_lower_violation_raises(session: AsyncSession) -> None:
    repo = UserRepository(session)
    await repo.create(email="dup@x.test", password_hash="h", role="user")
    await session.commit()
    with pytest.raises(Exception):
        await repo.create(email="DUP@x.test", password_hash="h", role="user")
        await session.commit()


async def test_refresh_repo_create_find_revoke(session: AsyncSession) -> None:
    users = UserRepository(session)
    user = await users.create(email="r@x.test", password_hash="h", role="user")
    await session.commit()

    repo = RefreshTokenRepository(session)
    token_hash = "deadbeef" * 8
    expires = datetime.now(UTC) + timedelta(days=30)
    row = await repo.create(
        user_id=user.id, token_hash=token_hash, expires_at=expires, user_agent="ua", ip=None
    )
    await session.commit()

    fetched = await repo.get_by_token_hash(token_hash)
    assert fetched is not None
    assert fetched.id == row.id

    await repo.revoke(row)
    await session.commit()
    fetched2 = await repo.get_by_token_hash(token_hash)
    assert fetched2 is not None
    assert fetched2.revoked_at is not None


async def test_refresh_repo_revoke_all_for_user(session: AsyncSession) -> None:
    users = UserRepository(session)
    user = await users.create(email="m@x.test", password_hash="h", role="user")
    await session.commit()
    repo = RefreshTokenRepository(session)
    expires = datetime.now(UTC) + timedelta(days=30)
    for i in range(3):
        await repo.create(
            user_id=user.id,
            token_hash=f"h{i}" + "0" * 60,
            expires_at=expires,
            user_agent=None,
            ip=None,
        )
    await session.commit()
    n = await repo.revoke_all_for_user(user.id)
    await session.commit()
    assert n == 3
