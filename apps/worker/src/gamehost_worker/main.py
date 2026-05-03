from typing import Any

from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from gamehost_worker.core.config import get_settings
from gamehost_worker.core.logging import configure_logging
from gamehost_worker.jobs import delete, provision, restart, start, stop


async def _on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine: AsyncEngine = create_async_engine(settings.database_url, pool_pre_ping=True)
    ctx["engine"] = engine
    ctx["sm"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["node_agent_timeout_s"] = settings.node_agent_timeout_s


async def _on_shutdown(ctx: dict[str, Any]) -> None:
    engine: AsyncEngine | None = ctx.get("engine")
    if engine is not None:
        await engine.dispose()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    functions = [provision, start, stop, restart, delete]
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    keep_result = 600
    max_jobs = 10
