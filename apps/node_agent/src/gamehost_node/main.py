from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gamehost_node.api import api_v1, health
from gamehost_node.core.config import get_settings
from gamehost_node.core.errors import register_exception_handlers
from gamehost_node.core.logging import configure_logging
from gamehost_node.docker_facade import DockerFacade


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not hasattr(app.state, "docker_facade"):
        app.state.docker_facade = DockerFacade(base_url=settings.docker_host)
    yield


app = FastAPI(title="GameHost node-agent", version="0.0.0", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(health.router)
app.include_router(api_v1)
