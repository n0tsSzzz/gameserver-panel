from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture(autouse=True)
def _override_db_url(monkeypatch: pytest.MonkeyPatch, postgres_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    from gamehost_api.core.config import get_settings

    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE audit_log, tasks, servers, refresh_tokens, "
                "nodes, game_templates, users RESTART IDENTITY CASCADE"
            )
        )
    await engine.dispose()
    yield


@pytest.fixture
def arq_pool_mock() -> object:
    from unittest.mock import AsyncMock, MagicMock

    return MagicMock(enqueue_job=AsyncMock(return_value=None))


@pytest_asyncio.fixture
async def client(arq_pool_mock: object) -> AsyncIterator[AsyncClient]:
    from gamehost_api.main import app

    app.state.arq_pool = arq_pool_mock
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c
