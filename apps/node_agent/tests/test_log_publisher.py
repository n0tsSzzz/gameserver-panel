import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import fakeredis.aioredis
import pytest

from gamehost_node.docker_facade import DockerFacade
from gamehost_node.log_publisher import LogPublisher


@pytest.fixture
def mock_facade() -> MagicMock:
    return MagicMock(spec=DockerFacade)


@pytest.fixture
async def fake_redis() -> AsyncIterator[Any]:
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield r
    finally:
        await r.aclose()


async def test_start_publishes_lines(fake_redis: Any, mock_facade: MagicMock) -> None:
    async def stream(_cid: str) -> AsyncIterator[str]:
        for line in ("first", "second"):
            yield line

    mock_facade.stream_logs = stream
    pub = LogPublisher(fake_redis, mock_facade)

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe("logs:c1")
    # drain initial subscribe message
    await pubsub.get_message(timeout=0.5, ignore_subscribe_messages=True)

    await pub.start("c1")

    received: list[str] = []
    while len(received) < 2:
        msg = await pubsub.get_message(timeout=2.0, ignore_subscribe_messages=True)
        if msg is None:
            break
        received.append(msg["data"])

    await pub.stop("c1")
    await pubsub.unsubscribe()
    await pubsub.aclose()

    assert received == ["first", "second"]


async def test_start_idempotent(fake_redis: Any, mock_facade: MagicMock) -> None:
    async def stream(_cid: str) -> AsyncIterator[str]:
        await asyncio.sleep(60)  # never yields
        yield ""  # pragma: no cover

    mock_facade.stream_logs = stream
    pub = LogPublisher(fake_redis, mock_facade)
    await pub.start("c2")
    first_task = pub._tasks["c2"]
    await pub.start("c2")
    assert pub._tasks["c2"] is first_task
    await pub.stop("c2")


async def test_stop_cancels_task(fake_redis: Any, mock_facade: MagicMock) -> None:
    async def stream(_cid: str) -> AsyncIterator[str]:
        await asyncio.sleep(60)
        yield ""  # pragma: no cover

    mock_facade.stream_logs = stream
    pub = LogPublisher(fake_redis, mock_facade)
    await pub.start("c3")
    await pub.stop("c3")
    assert "c3" not in pub._tasks


async def test_shutdown_cancels_all(fake_redis: Any, mock_facade: MagicMock) -> None:
    async def stream(_cid: str) -> AsyncIterator[str]:
        await asyncio.sleep(60)
        yield ""  # pragma: no cover

    mock_facade.stream_logs = stream
    pub = LogPublisher(fake_redis, mock_facade)
    await pub.start("a")
    await pub.start("b")
    await pub.shutdown()
    assert pub._tasks == {}
