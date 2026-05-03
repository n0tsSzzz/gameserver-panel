import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer

ROOT = Path(__file__).resolve().parent
API_DIR = ROOT / "apps" / "api"


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
    env = {k: v for k, v in os.environ.items() if not k.startswith("COV_")}
    env.pop("COVERAGE_PROCESS_START", None)
    env.pop("COVERAGE_FILE", None)
    env["DATABASE_URL"] = postgres_url
    env["SECRET_KEY"] = os.environ["SECRET_KEY"]
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=str(API_DIR),
        env=env,
        check=True,
    )
