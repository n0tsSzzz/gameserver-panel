from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from gamehost_api.api.v1 import api_v1
from gamehost_api.core.config import get_settings
from gamehost_api.core.errors import register_exception_handlers
from gamehost_api.core.logging import configure_logging
from gamehost_api.core.request_id import RequestIDMiddleware
from gamehost_api.db.session import make_engine, make_sessionmaker
from gamehost_api.tasks.arq_pool import create_arq_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine: AsyncEngine = make_engine()
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    if not hasattr(app.state, "arq_pool"):
        app.state.arq_pool = await create_arq_pool(settings.redis_url)
    try:
        yield
    finally:
        import contextlib

        close = getattr(app.state.arq_pool, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()
        await engine.dispose()


app = FastAPI(title="GameHost API", version="0.0.0", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)
register_exception_handlers(app)
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
app.include_router(api_v1)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(response: Response) -> dict[str, str]:
    engine: AsyncEngine = app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ready"}
