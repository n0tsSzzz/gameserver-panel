import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _secret_key() -> Iterator[None]:
    os.environ.setdefault("SECRET_KEY", "test-secret-key-32-bytes-minimum-aaaa")
    os.environ.setdefault("COOKIE_SECURE", "false")
    yield


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(_secret_key: None, postgres_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = postgres_url
    env["SECRET_KEY"] = os.environ["SECRET_KEY"]
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=str(API_DIR),
        env=env,
        check=True,
    )


@pytest.fixture(autouse=True)
def _override_db_url(monkeypatch: pytest.MonkeyPatch, postgres_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    from gamehost_api.core.config import get_settings

    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE refresh_tokens, users RESTART IDENTITY CASCADE"))
    await engine.dispose()
    yield


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from gamehost_api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c
