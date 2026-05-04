from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI

from gamehost_node.api import api_v1, health
from gamehost_node.core.config import get_settings
from gamehost_node.core.errors import register_exception_handlers
from gamehost_node.core.logging import configure_logging
from gamehost_node.docker_facade import DockerFacade
from gamehost_node.log_publisher import LogPublisher
from gamehost_node.s3_client import ensure_bucket


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not hasattr(app.state, "docker_facade"):
        app.state.docker_facade = DockerFacade(base_url=settings.docker_host)
    if not hasattr(app.state, "log_publisher"):
        redis_client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, decode_responses=True
        )
        app.state.log_publisher = LogPublisher(redis_client, app.state.docker_facade)
    import contextlib

    with contextlib.suppress(Exception):
        await ensure_bucket(settings.s3_bucket)
    try:
        yield
    finally:
        publisher = getattr(app.state, "log_publisher", None)
        if publisher is not None:
            await publisher.shutdown()


app = FastAPI(title="GameHost node-agent", version="0.0.0", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(health.router)
app.include_router(api_v1)
