import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.repositories.templates import TemplateRepository

API_DIR = Path(__file__).resolve().parents[1]


def _run_seed_templates() -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("COV_")}
    env.pop("COVERAGE_PROCESS_START", None)
    env.pop("COVERAGE_FILE", None)
    return subprocess.run(
        [sys.executable, "-m", "gamehost_api.scripts.seed_templates"],
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


async def test_seed_templates_inserts_5(session: AsyncSession) -> None:
    rc = _run_seed_templates()
    assert rc.returncode == 0, rc.stderr.decode()
    items = await TemplateRepository(session).list_(public_only=False)
    slugs = {t.slug for t in items}
    assert slugs == {"minecraft-vanilla", "valheim", "terraria", "cs2", "rust"}


async def test_seed_templates_idempotent(session: AsyncSession) -> None:
    _run_seed_templates()
    rc = _run_seed_templates()
    assert rc.returncode == 0
    items = await TemplateRepository(session).list_(public_only=False)
    assert len(items) == 5


async def test_seed_templates_does_not_overwrite_edits(session: AsyncSession) -> None:
    _run_seed_templates()
    repo = TemplateRepository(session)
    items = await repo.list_(public_only=False)
    target = next(t for t in items if t.slug == "minecraft-vanilla")
    await repo.update(target, {"display_name": "MY MC"})
    await session.commit()

    rc = _run_seed_templates()
    assert rc.returncode == 0
    refreshed = await repo.get(target.id)
    assert refreshed is not None
    assert refreshed.display_name == "MY MC"
