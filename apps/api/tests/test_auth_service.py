from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.domain.auth import AuthService
from gamehost_api.domain.exceptions import (
    EmailAlreadyTaken,
    InvalidCredentials,
    RefreshInvalid,
    UserInactive,
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_register_creates_user(session: AsyncSession) -> None:
    svc = AuthService(session)
    user = await svc.register(email="A@B.test", password="hunter22hunter22")
    await session.commit()
    assert user.email == "a@b.test"
    assert user.role == "user"


async def test_register_duplicate_email_raises(session: AsyncSession) -> None:
    svc = AuthService(session)
    await svc.register(email="dup@x.test", password="hunter22hunter22")
    await session.commit()
    with pytest.raises(EmailAlreadyTaken):
        await svc.register(email="DUP@x.test", password="hunter22hunter22")


async def test_login_returns_tokens_for_valid_creds(session: AsyncSession) -> None:
    svc = AuthService(session)
    await svc.register(email="ok@x.test", password="hunter22hunter22")
    await session.commit()
    pair = await svc.login(email="ok@x.test", password="hunter22hunter22", user_agent=None, ip=None)
    await session.commit()
    assert pair.access_token
    assert pair.refresh_token


async def test_login_wrong_password_raises_invalid_credentials(session: AsyncSession) -> None:
    svc = AuthService(session)
    await svc.register(email="x@x.test", password="hunter22hunter22")
    await session.commit()
    with pytest.raises(InvalidCredentials):
        await svc.login(email="x@x.test", password="wrong-password", user_agent=None, ip=None)


async def test_login_unknown_email_raises_invalid_credentials(session: AsyncSession) -> None:
    svc = AuthService(session)
    with pytest.raises(InvalidCredentials):
        await svc.login(email="missing@x.test", password="any", user_agent=None, ip=None)


async def test_login_inactive_user_raises(session: AsyncSession) -> None:
    svc = AuthService(session)
    user = await svc.register(email="dead@x.test", password="hunter22hunter22")
    user.is_active = False
    await session.commit()
    with pytest.raises(UserInactive):
        await svc.login(email="dead@x.test", password="hunter22hunter22", user_agent=None, ip=None)


async def test_refresh_rotates_and_revokes_old(session: AsyncSession) -> None:
    svc = AuthService(session)
    await svc.register(email="r@x.test", password="hunter22hunter22")
    await session.commit()
    pair1 = await svc.login(email="r@x.test", password="hunter22hunter22", user_agent=None, ip=None)
    await session.commit()
    pair2 = await svc.refresh(pair1.refresh_token, user_agent=None, ip=None)
    await session.commit()
    assert pair2.refresh_token != pair1.refresh_token
    with pytest.raises(RefreshInvalid):
        await svc.refresh(pair1.refresh_token, user_agent=None, ip=None)


async def test_refresh_reuse_revokes_all_user_tokens(session: AsyncSession) -> None:
    svc = AuthService(session)
    await svc.register(email="reuse@x.test", password="hunter22hunter22")
    await session.commit()
    pair1 = await svc.login(
        email="reuse@x.test", password="hunter22hunter22", user_agent=None, ip=None
    )
    await session.commit()
    pair2 = await svc.refresh(pair1.refresh_token, user_agent=None, ip=None)
    await session.commit()
    with pytest.raises(RefreshInvalid):
        await svc.refresh(pair1.refresh_token, user_agent=None, ip=None)
    await session.commit()
    with pytest.raises(RefreshInvalid):
        await svc.refresh(pair2.refresh_token, user_agent=None, ip=None)


async def test_logout_revokes_current_refresh(session: AsyncSession) -> None:
    svc = AuthService(session)
    await svc.register(email="lo@x.test", password="hunter22hunter22")
    await session.commit()
    pair = await svc.login(email="lo@x.test", password="hunter22hunter22", user_agent=None, ip=None)
    await session.commit()
    await svc.logout(pair.refresh_token)
    await session.commit()
    with pytest.raises(RefreshInvalid):
        await svc.refresh(pair.refresh_token, user_agent=None, ip=None)
