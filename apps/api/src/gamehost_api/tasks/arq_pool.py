from typing import Any, Protocol


class ArqPoolLike(Protocol):
    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any: ...


async def create_arq_pool(redis_url: str) -> ArqPoolLike:
    from arq import create_pool
    from arq.connections import RedisSettings

    settings = RedisSettings.from_dsn(redis_url)
    return await create_pool(settings)
