from typing import Any

import redis.asyncio as aioredis


async def create_redis_pool(redis_url: str) -> Any:
    return aioredis.from_url(redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
