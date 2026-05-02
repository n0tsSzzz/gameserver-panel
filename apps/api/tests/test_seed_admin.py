import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.repositories.users import UserRepository
from tests.factories import make_user

API_DIR = Path(__file__).resolve().parents[1]


def _run_seed_admin(extra_env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("COV_")}
    env.pop("COVERAGE_PROCESS_START", None)
    env.pop("COVERAGE_FILE", None)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "gamehost_api.scripts.seed_admin"],
        cwd=str(API_DIR),
        env=env,
        capture_output=True,
    )


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_seed_admin_creates_when_missing(session: AsyncSession) -> None:
    rc = _run_seed_admin(
        {"BOOTSTRAP_ADMIN_EMAIL": "boot@x.test", "BOOTSTRAP_ADMIN_PASSWORD": "supersecret123"}
    )
    assert rc.returncode == 0, rc.stderr.decode()
    fetched = await UserRepository(session).get_by_email("boot@x.test")
    assert fetched is not None
    assert fetched.role == "admin"


async def test_seed_admin_promotes_existing_user(session: AsyncSession) -> None:
    await make_user(session, email="prom@x.test", role="user")
    rc = _run_seed_admin(
        {"BOOTSTRAP_ADMIN_EMAIL": "prom@x.test", "BOOTSTRAP_ADMIN_PASSWORD": "supersecret123"}
    )
    assert rc.returncode == 0, rc.stderr.decode()
    await session.commit()
    fetched = await UserRepository(session).get_by_email("prom@x.test")
    assert fetched is not None
    assert fetched.role == "admin"


async def test_seed_admin_idempotent_on_existing_admin(session: AsyncSession) -> None:
    await make_user(session, email="ad@x.test", role="admin")
    rc = _run_seed_admin(
        {"BOOTSTRAP_ADMIN_EMAIL": "ad@x.test", "BOOTSTRAP_ADMIN_PASSWORD": "supersecret123"}
    )
    assert rc.returncode == 0, rc.stderr.decode()


def test_seed_admin_exits_when_env_missing() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("COV_")}
    env.pop("BOOTSTRAP_ADMIN_EMAIL", None)
    env.pop("BOOTSTRAP_ADMIN_PASSWORD", None)
    env.pop("COVERAGE_PROCESS_START", None)
    env.pop("COVERAGE_FILE", None)
    rc = subprocess.run(
        [sys.executable, "-m", "gamehost_api.scripts.seed_admin"],
        cwd=str(API_DIR),
        env=env,
        capture_output=True,
    )
    assert rc.returncode == 1
