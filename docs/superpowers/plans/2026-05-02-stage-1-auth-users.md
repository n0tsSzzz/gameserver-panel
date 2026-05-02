# Stage 1 — Auth + users — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the auth domain (register / login / refresh-with-rotation / logout / me) and the layered API scaffold (api/v1, domain, repositories, db, schemas, core) on top of the Stage 0 skeleton. Stack the cross-cutting basics (Alembic, structlog with secret-redaction, RFC 7807 errors, prometheus `/metrics`) so subsequent stages only add domains.

**Architecture:** FastAPI router → `AuthService` (use case) → repositories → SQLAlchemy async session. JWT access (HS256, 15 min) in JSON body; opaque refresh (32 bytes urlsafe, sha256-hashed in DB) in `HttpOnly Secure SameSite=Lax` cookie scoped to `/api/v1/auth`. Refresh rotates on use; reuse of revoked token revokes all of the user's refresh rows (defence-in-depth). All errors serialize as `application/problem+json`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic (async template), Pydantic v2, pydantic-settings, argon2-cffi, python-jose[cryptography], structlog, prometheus-fastapi-instrumentator, pytest + pytest-asyncio + httpx + testcontainers[postgres] + pytest-cov + polyfactory.

**Spec:** `docs/superpowers/specs/2026-05-02-stage-1-auth-users-design.md`

---

## Conventions used by every task

- Every code path that does I/O is `async`. No sync SQLAlchemy / requests / time.sleep.
- All public functions and methods carry full type annotations; `mypy --strict` must stay green.
- Tests live beside the api package in `apps/api/tests/`. Run with `make test` (uses session-scoped testcontainer Postgres + `alembic upgrade head` from `conftest.py`).
- Per-step "run X / expected Y": if the result diverges, stop and read the error before changing more files.
- Commits go to branch `stage-1-auth-users` (already created locally and contains the spec commit). Each task ends in 1+ commits with a conventional-commit message.

---

## Task 1: Bootstrap dependencies and settings

**Files:**
- Modify: `apps/api/pyproject.toml`
- Modify: `pyproject.toml` (root: dev deps for coverage + factories)
- Modify: `apps/api/src/gamehost_api/core/config.py`
- Modify: `.env.example`

- [ ] **Step 1.1: Add runtime + dev deps**

Edit `apps/api/pyproject.toml`, replace the `dependencies` array with:

```toml
dependencies = [
    "fastapi>=0.115,<1",
    "pydantic-settings>=2.6,<3",
    "uvicorn[standard]>=0.32,<1",
    "sqlalchemy[asyncio]>=2.0,<3",
    "asyncpg>=0.30,<1",
    "alembic>=1.13,<2",
    "argon2-cffi>=23,<26",
    "python-jose[cryptography]>=3.3,<4",
    "structlog>=24,<26",
    "prometheus-fastapi-instrumentator>=7,<8",
    "gamehost-shared",
]
```

Edit root `pyproject.toml`, in `[dependency-groups].dev` append:

```toml
    "pytest-cov>=5,<7",
    "polyfactory>=2.18,<3",
    "types-python-jose>=3.3,<4",
```

- [ ] **Step 1.2: Sync and ensure imports resolve**

Run: `uv sync --all-packages`
Expected: completes without error; lockfile updates.

- [ ] **Step 1.3: Extend `Settings` with auth/cookie/argon2/log fields**

Replace `apps/api/src/gamehost_api/core/config.py` with:

```python
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = Field(
        default="postgresql+asyncpg://gamehost:gamehost@localhost:5432/gamehost",
        alias="DATABASE_URL",
    )
    secret_key: SecretStr = Field(alias="SECRET_KEY")
    access_token_ttl_seconds: int = Field(default=900, alias="ACCESS_TOKEN_TTL_SECONDS")
    refresh_token_ttl_seconds: int = Field(
        default=60 * 60 * 24 * 30, alias="REFRESH_TOKEN_TTL_SECONDS"
    )
    cookie_secure: bool = Field(default=True, alias="COOKIE_SECURE")
    cookie_domain: str | None = Field(default=None, alias="COOKIE_DOMAIN")
    argon2_memory_cost: int = Field(default=65536, alias="ARGON2_MEMORY_COST")
    argon2_time_cost: int = Field(default=3, alias="ARGON2_TIME_COST")
    argon2_parallelism: int = Field(default=4, alias="ARGON2_PARALLELISM")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 1.4: Append new env keys to `.env.example`**

Append:

```
# API auth
SECRET_KEY=dev-only-change-me-32bytes-min-aaaaa
ACCESS_TOKEN_TTL_SECONDS=900
REFRESH_TOKEN_TTL_SECONDS=2592000
COOKIE_SECURE=false
LOG_LEVEL=INFO
```

- [ ] **Step 1.5: Smoke check**

Run: `make typecheck`
Expected: `Success: no issues found in <N> source files`.

Run: `make test`
Expected: existing 2 health tests still pass (we did not touch them yet, but conftest will need `SECRET_KEY` — the `Settings()` call now requires it). If they fail because `SECRET_KEY` missing, that's expected and fixed in Task 5 — for now, set it in the shell before running:

```bash
SECRET_KEY=dev-only-change-me-32bytes-min-aaaaa make test
```

Expected: 2 tests pass.

- [ ] **Step 1.6: Commit**

```bash
git add apps/api/pyproject.toml pyproject.toml apps/api/src/gamehost_api/core/config.py .env.example uv.lock
git commit -m "chore(api): add auth deps and settings fields"
```

---

## Task 2: Shared `UserRole` enum + Pydantic camelCase base

**Files:**
- Modify: `packages/shared/src/gamehost_shared/enums.py`
- Modify: `packages/shared/src/gamehost_shared/__init__.py`
- Create: `apps/api/src/gamehost_api/schemas/__init__.py`
- Create: `apps/api/src/gamehost_api/schemas/base.py`

- [ ] **Step 2.1: Add `UserRole` to shared enums**

Replace `packages/shared/src/gamehost_shared/enums.py` with:

```python
from enum import StrEnum


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class ServerStatus(StrEnum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    DELETING = "deleting"


class NodeStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    DRAIN = "drain"
```

Edit `packages/shared/src/gamehost_shared/__init__.py`:

```python
from gamehost_shared.enums import NodeStatus, ServerStatus, UserRole

__all__ = ["NodeStatus", "ServerStatus", "UserRole"]
```

- [ ] **Step 2.2: Create `CamelModel` base**

Create `apps/api/src/gamehost_api/schemas/__init__.py` empty.

Create `apps/api/src/gamehost_api/schemas/base.py`:

```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
```

- [ ] **Step 2.3: Verify**

Run: `make lint && make typecheck`
Expected: green.

- [ ] **Step 2.4: Commit**

```bash
git add packages/shared apps/api/src/gamehost_api/schemas
git commit -m "feat(shared): add UserRole enum and CamelModel base"
```

---

## Task 3: DB base, session helper, and ORM models

**Files:**
- Create: `apps/api/src/gamehost_api/db/__init__.py`
- Create: `apps/api/src/gamehost_api/db/base.py`
- Create: `apps/api/src/gamehost_api/db/session.py`
- Create: `apps/api/src/gamehost_api/db/models/__init__.py`
- Create: `apps/api/src/gamehost_api/db/models/user.py`
- Create: `apps/api/src/gamehost_api/db/models/refresh_token.py`

- [ ] **Step 3.1: Create `db/base.py`**

Create `apps/api/src/gamehost_api/db/__init__.py` empty.

Create `apps/api/src/gamehost_api/db/base.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 3.2: Create `db/session.py`**

Create `apps/api/src/gamehost_api/db/session.py`:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings


def make_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def session_scope(
    sm: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

- [ ] **Step 3.3: Create `User` model**

Create `apps/api/src/gamehost_api/db/models/__init__.py`:

```python
from gamehost_api.db.models.refresh_token import RefreshToken
from gamehost_api.db.models.user import User

__all__ = ["RefreshToken", "User"]
```

Create `apps/api/src/gamehost_api/db/models/user.py`:

```python
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gamehost_api.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from gamehost_api.db.models.refresh_token import RefreshToken


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('user','admin')", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
```

- [ ] **Step 3.4: Create `RefreshToken` model**

Create `apps/api/src/gamehost_api/db/models/refresh_token.py`:

```python
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gamehost_api.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from gamehost_api.db.models.user import User


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_revoked", "user_id", "revoked_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")
```

- [ ] **Step 3.5: Verify**

Run: `make typecheck`
Expected: green.

- [ ] **Step 3.6: Commit**

```bash
git add apps/api/src/gamehost_api/db
git commit -m "feat(api): add db base, session, User and RefreshToken models"
```

---

## Task 4: Alembic init + first migration

**Files:**
- Create: `apps/api/alembic.ini`
- Create: `apps/api/alembic/env.py`
- Create: `apps/api/alembic/script.py.mako`
- Create: `apps/api/alembic/versions/0001_users_refresh_tokens.py`

- [ ] **Step 4.1: Create `alembic.ini`**

Create `apps/api/alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = src
file_template = %%(rev)s_%%(slug)s
version_path_separator = os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 4.2: Create `alembic/env.py`**

Create `apps/api/alembic/env.py`:

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from gamehost_api.core.config import get_settings
from gamehost_api.db.base import Base
from gamehost_api.db.models import RefreshToken, User  # noqa: F401  registers metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {}, prefix="sqlalchemy."
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4.3: Create `alembic/script.py.mako`**

Create `apps/api/alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4.4: Write the first migration**

Create `apps/api/alembic/versions/0001_users_refresh_tokens.py`:

```python
"""users + refresh_tokens

Revision ID: 0001
Revises:
Create Date: 2026-05-02

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("role IN ('user','admin')", name="ck_users_role"),
    )
    op.execute("CREATE UNIQUE INDEX ix_users_email_lower ON users (lower(email))")

    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index(
        "ix_refresh_tokens_user_revoked", "refresh_tokens", ["user_id", "revoked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_user_revoked", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.execute("DROP INDEX IF EXISTS ix_users_email_lower")
    op.drop_table("users")
```

- [ ] **Step 4.5: Update Makefile**

Replace the `migrate`, `revision`, `seed` rules in `Makefile`:

```make
migrate:
	cd apps/api && uv run alembic upgrade head

revision:
	cd apps/api && uv run alembic revision --autogenerate -m "$(m)"

seed:
	@echo "available from Stage 2 (seed templates)"
```

- [ ] **Step 4.6: Smoke run migration**

Run:

```bash
SECRET_KEY=dev-only-change-me-32bytes-min-aaaaa \
DATABASE_URL=postgresql+asyncpg://gamehost:gamehost@localhost:5432/gamehost \
make up && sleep 4 && make migrate
```

Expected: alembic ends with `INFO  [alembic.runtime.migration] Running upgrade  -> 0001`.

Run downgrade test:

```bash
cd apps/api && SECRET_KEY=... DATABASE_URL=... uv run alembic downgrade base && cd ../..
```

Expected: tables dropped, no errors.

Run upgrade again to leave DB in head state for tests, then `make down` (volumes persist; we only need head if you plan to dev-run; tests use their own container).

- [ ] **Step 4.7: Commit**

```bash
git add apps/api/alembic.ini apps/api/alembic Makefile
git commit -m "feat(api): alembic async setup + 0001 users/refresh_tokens migration"
```

---

## Task 5: Update test scaffolding (alembic + clean_db)

**Files:**
- Modify: `apps/api/tests/conftest.py`

- [ ] **Step 5.1: Replace conftest with the migration-aware version**

Replace `apps/api/tests/conftest.py` with:

```python
import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

API_DIR = Path(__file__).resolve().parents[1]


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
def _apply_migrations(postgres_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = postgres_url
    env["SECRET_KEY"] = os.environ["SECRET_KEY"]
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=str(API_DIR),
        env=env,
        check=True,
    )


@pytest.fixture(autouse=True)
def _override_db_url(monkeypatch: pytest.MonkeyPatch, postgres_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    from gamehost_api.core.config import get_settings

    get_settings.cache_clear()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(postgres_url: str) -> AsyncIterator[None]:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE refresh_tokens, users RESTART IDENTITY CASCADE"))
    await engine.dispose()
    yield


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from gamehost_api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            yield c
```

- [ ] **Step 5.2: Verify**

Run: `make test`
Expected: 2 health tests pass; testcontainer + alembic upgrade succeed in conftest.

- [ ] **Step 5.3: Commit**

```bash
git add apps/api/tests/conftest.py
git commit -m "test(api): apply alembic head and truncate per-test in conftest"
```

---

## Task 6: Password hashing (`core/security.py` — argon2)

**Files:**
- Create: `apps/api/src/gamehost_api/core/security.py`
- Create: `apps/api/tests/test_security_password.py`

- [ ] **Step 6.1: Write failing test for hash + verify roundtrip**

Create `apps/api/tests/test_security_password.py`:

```python
from gamehost_api.core.security import hash_password, verify_password


def test_hash_then_verify_returns_true() -> None:
    h = hash_password("hunter22hunter22")
    assert h != "hunter22hunter22"
    assert verify_password(h, "hunter22hunter22") is True


def test_verify_with_wrong_password_returns_false() -> None:
    h = hash_password("correct horse battery staple")
    assert verify_password(h, "incorrect horse battery staple") is False
```

- [ ] **Step 6.2: Run, expect ImportError fail**

Run: `uv run pytest apps/api/tests/test_security_password.py -v`
Expected: collection fails — module `gamehost_api.core.security` does not exist.

- [ ] **Step 6.3: Implement password helpers**

Create `apps/api/src/gamehost_api/core/security.py`:

```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from gamehost_api.core.config import get_settings


def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_cost,
        parallelism=s.argon2_parallelism,
    )


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher().verify(password_hash, password)
    except VerifyMismatchError:
        return False
```

- [ ] **Step 6.4: Tests pass**

Run: `uv run pytest apps/api/tests/test_security_password.py -v`
Expected: 2 passed.

- [ ] **Step 6.5: Commit**

```bash
git add apps/api/src/gamehost_api/core/security.py apps/api/tests/test_security_password.py
git commit -m "feat(api): argon2 password hashing helpers"
```

---

## Task 7: JWT helpers (access tokens)

**Files:**
- Modify: `apps/api/src/gamehost_api/core/security.py`
- Create: `apps/api/tests/test_security_jwt.py`

- [ ] **Step 7.1: Write failing tests**

Create `apps/api/tests/test_security_jwt.py`:

```python
import time
import uuid

import pytest

from gamehost_api.core.security import (
    create_access_token,
    decode_access_token,
)


def test_decode_access_token_returns_claims() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id=user_id, email="a@b.test", role="user")
    claims = decode_access_token(token)
    assert claims["sub"] == str(user_id)
    assert claims["email"] == "a@b.test"
    assert claims["role"] == "user"
    assert claims["type"] == "access"


def test_decode_expired_access_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "1")
    from gamehost_api.core.config import get_settings

    get_settings.cache_clear()
    token = create_access_token(user_id=uuid.uuid4(), email="x@y", role="user")
    time.sleep(2)
    with pytest.raises(ValueError):
        decode_access_token(token)
```

- [ ] **Step 7.2: Run, expect fail**

Run: `uv run pytest apps/api/tests/test_security_jwt.py -v`
Expected: import errors / undefined names.

- [ ] **Step 7.3: Implement JWT helpers**

Append to `apps/api/src/gamehost_api/core/security.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt


_ALGORITHM = "HS256"


def create_access_token(*, user_id: uuid.UUID, email: str, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise ValueError("invalid_or_expired_token") from exc
    if claims.get("type") != "access":
        raise ValueError("wrong_token_type")
    return claims
```

Re-arrange the file so all imports sit at the top. The full file should now be:

```python
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt

from gamehost_api.core.config import get_settings


_ALGORITHM = "HS256"


def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_cost,
        parallelism=s.argon2_parallelism,
    )


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher().verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(*, user_id: uuid.UUID, email: str, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.secret_key.get_secret_value(), algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise ValueError("invalid_or_expired_token") from exc
    if claims.get("type") != "access":
        raise ValueError("wrong_token_type")
    return claims
```

- [ ] **Step 7.4: Tests pass**

Run: `uv run pytest apps/api/tests/test_security_jwt.py -v`
Expected: 2 passed.

- [ ] **Step 7.5: Commit**

```bash
git add apps/api/src/gamehost_api/core/security.py apps/api/tests/test_security_jwt.py
git commit -m "feat(api): HS256 JWT access token create/decode"
```

---

## Task 8: Refresh-token helpers (opaque + sha256)

**Files:**
- Modify: `apps/api/src/gamehost_api/core/security.py`
- Create: `apps/api/tests/test_security_refresh.py`

- [ ] **Step 8.1: Failing test**

Create `apps/api/tests/test_security_refresh.py`:

```python
from gamehost_api.core.security import generate_refresh_token, hash_refresh_token


def test_generate_refresh_token_is_urlsafe_and_unique() -> None:
    a = generate_refresh_token()
    b = generate_refresh_token()
    assert a != b
    assert len(a) >= 32
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_refresh_token_is_deterministic_sha256_hex() -> None:
    h = hash_refresh_token("token-abc")
    assert len(h) == 64
    assert h == hash_refresh_token("token-abc")
    assert h != hash_refresh_token("token-abd")
```

- [ ] **Step 8.2: Implement**

Append to `apps/api/src/gamehost_api/core/security.py`:

```python
import hashlib
import secrets


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
```

- [ ] **Step 8.3: Tests pass**

Run: `uv run pytest apps/api/tests/test_security_refresh.py -v`
Expected: 2 passed.

- [ ] **Step 8.4: Full security suite passes**

Run: `uv run pytest apps/api/tests/test_security_*.py -v`
Expected: 6 passed.

- [ ] **Step 8.5: Commit**

```bash
git add apps/api/src/gamehost_api/core/security.py apps/api/tests/test_security_refresh.py
git commit -m "feat(api): opaque refresh token + sha256 hash helpers"
```

---

## Task 9: Domain exceptions + RFC 7807 handler

**Files:**
- Create: `apps/api/src/gamehost_api/domain/__init__.py`
- Create: `apps/api/src/gamehost_api/domain/exceptions.py`
- Create: `apps/api/src/gamehost_api/core/errors.py`
- Create: `apps/api/tests/test_errors.py`

- [ ] **Step 9.1: Domain exceptions**

Create `apps/api/src/gamehost_api/domain/__init__.py` empty.

Create `apps/api/src/gamehost_api/domain/exceptions.py`:

```python
class DomainError(Exception):
    code: str = "domain_error"
    status_code: int = 400
    title: str = "Domain error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title


class InvalidCredentials(DomainError):
    code = "invalid_credentials"
    status_code = 401
    title = "Invalid credentials"


class EmailAlreadyTaken(DomainError):
    code = "email_taken"
    status_code = 409
    title = "Email is already registered"


class RefreshInvalid(DomainError):
    code = "refresh_invalid"
    status_code = 401
    title = "Refresh token invalid or expired"


class UserInactive(DomainError):
    code = "user_inactive"
    status_code = 401
    title = "User is inactive"
```

- [ ] **Step 9.2: Failing test for problem+json shape**

Create `apps/api/tests/test_errors.py`:

```python
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from gamehost_api.core.errors import register_exception_handlers
from gamehost_api.domain.exceptions import EmailAlreadyTaken


async def test_domain_error_serialized_as_problem_details() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise EmailAlreadyTaken("a@b.test")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/boom")

    assert r.status_code == 409
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Email is already registered"
    assert body["status"] == 409
    assert body["code"] == "email_taken"
    assert body["detail"] == "a@b.test"


async def test_validation_error_serialized_as_problem_details() -> None:
    from pydantic import BaseModel

    app = FastAPI()
    register_exception_handlers(app)

    class In(BaseModel):
        x: int

    @app.post("/echo")
    async def echo(payload: In) -> dict[str, int]:
        return {"x": payload.x}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/echo", json={"x": "not-an-int"})

    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 422
    assert body["code"] == "validation_error"
    assert "errors" in body
```

- [ ] **Step 9.3: Run, expect fail**

Run: `uv run pytest apps/api/tests/test_errors.py -v`
Expected: ImportError for `gamehost_api.core.errors`.

- [ ] **Step 9.4: Implement handler**

Create `apps/api/src/gamehost_api/core/errors.py`:

```python
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from gamehost_api.domain.exceptions import DomainError

PROBLEM_JSON = "application/problem+json"


def _problem(
    *,
    status: int,
    title: str,
    code: str,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail is not None:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_JSON)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_request: Request, exc: DomainError) -> JSONResponse:
        return _problem(
            status=exc.status_code, title=exc.title, code=exc.code, detail=exc.detail
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            status=422,
            title="Validation error",
            code="validation_error",
            detail="Request payload failed validation",
            extra={"errors": exc.errors()},
        )
```

- [ ] **Step 9.5: Tests pass**

Run: `uv run pytest apps/api/tests/test_errors.py -v`
Expected: 2 passed.

- [ ] **Step 9.6: Commit**

```bash
git add apps/api/src/gamehost_api/domain apps/api/src/gamehost_api/core/errors.py apps/api/tests/test_errors.py
git commit -m "feat(api): domain exceptions and RFC 7807 problem+json handler"
```

---

## Task 10: structlog config + redact processor + request_id middleware

**Files:**
- Create: `apps/api/src/gamehost_api/core/logging.py`
- Create: `apps/api/src/gamehost_api/core/request_id.py`
- Create: `apps/api/tests/test_logging.py`

- [ ] **Step 10.1: Failing test for redaction**

Create `apps/api/tests/test_logging.py`:

```python
from gamehost_api.core.logging import redact_secrets


def test_redact_secrets_masks_known_keys_recursively() -> None:
    event = {
        "msg": "login",
        "password": "hunter22",
        "Authorization": "Bearer xyz",
        "nested": {"refresh_token": "abc", "fine": "value"},
        "list": [{"cookie": "x"}, {"ok": 1}],
    }
    out = redact_secrets(None, "info", event)
    assert out["password"] == "***"
    assert out["Authorization"] == "***"
    assert out["nested"]["refresh_token"] == "***"
    assert out["nested"]["fine"] == "value"
    assert out["list"][0]["cookie"] == "***"
    assert out["list"][1]["ok"] == 1
```

- [ ] **Step 10.2: Run fail**

Run: `uv run pytest apps/api/tests/test_logging.py -v`
Expected: ImportError.

- [ ] **Step 10.3: Implement structlog config + redactor**

Create `apps/api/src/gamehost_api/core/logging.py`:

```python
import logging
from typing import Any

import structlog

_REDACT_KEYS = {
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "set-cookie",
    "gh_refresh",
    "refresh_token",
    "access_token",
    "secret_key",
}


def redact_secrets(
    _logger: Any, _name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: ("***" if k.lower() in _REDACT_KEYS else walk(v)) for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        return obj

    return walk(event_dict)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_secrets,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        cache_logger_on_first_use=True,
    )
```

- [ ] **Step 10.4: Test passes**

Run: `uv run pytest apps/api/tests/test_logging.py -v`
Expected: 1 passed.

- [ ] **Step 10.5: Request ID middleware**

Create `apps/api/src/gamehost_api/core/request_id.py`:

```python
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
```

- [ ] **Step 10.6: Verify lint/types**

Run: `make lint && make typecheck`
Expected: green.

- [ ] **Step 10.7: Commit**

```bash
git add apps/api/src/gamehost_api/core/logging.py apps/api/src/gamehost_api/core/request_id.py apps/api/tests/test_logging.py
git commit -m "feat(api): structlog config with redact processor and request-id middleware"
```

---

## Task 11: Repositories — `UserRepository` and `RefreshTokenRepository`

**Files:**
- Create: `apps/api/src/gamehost_api/repositories/__init__.py`
- Create: `apps/api/src/gamehost_api/repositories/users.py`
- Create: `apps/api/src/gamehost_api/repositories/refresh_tokens.py`
- Create: `apps/api/tests/test_repositories.py`

- [ ] **Step 11.1: Failing tests**

Create `apps/api/tests/test_repositories.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.repositories.refresh_tokens import RefreshTokenRepository
from gamehost_api.repositories.users import UserRepository


@pytest.fixture
async def session():
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_create_and_get_user_by_email_lower(session) -> None:
    repo = UserRepository(session)
    user = await repo.create(email="A@B.test", password_hash="h", role="user")
    await session.commit()

    fetched = await repo.get_by_email("a@b.test")
    assert fetched is not None
    assert fetched.id == user.id

    # case-insensitive
    fetched2 = await repo.get_by_email("A@b.TEST")
    assert fetched2 is not None
    assert fetched2.id == user.id


async def test_unique_email_lower_violation_raises(session) -> None:
    repo = UserRepository(session)
    await repo.create(email="dup@x.test", password_hash="h", role="user")
    await session.commit()
    with pytest.raises(Exception):
        await repo.create(email="DUP@x.test", password_hash="h", role="user")
        await session.commit()


async def test_refresh_repo_create_find_revoke(session) -> None:
    users = UserRepository(session)
    user = await users.create(email="r@x.test", password_hash="h", role="user")
    await session.commit()

    repo = RefreshTokenRepository(session)
    token_hash = "deadbeef" * 8
    expires = datetime.now(UTC) + timedelta(days=30)
    row = await repo.create(
        user_id=user.id, token_hash=token_hash, expires_at=expires, user_agent="ua", ip=None
    )
    await session.commit()

    fetched = await repo.get_by_token_hash(token_hash)
    assert fetched is not None
    assert fetched.id == row.id

    await repo.revoke(row)
    await session.commit()
    fetched2 = await repo.get_by_token_hash(token_hash)
    assert fetched2 is not None
    assert fetched2.revoked_at is not None


async def test_refresh_repo_revoke_all_for_user(session) -> None:
    users = UserRepository(session)
    user = await users.create(email="m@x.test", password_hash="h", role="user")
    await session.commit()
    repo = RefreshTokenRepository(session)
    expires = datetime.now(UTC) + timedelta(days=30)
    for i in range(3):
        await repo.create(
            user_id=user.id, token_hash=f"h{i}" + "0" * 60, expires_at=expires, user_agent=None, ip=None
        )
    await session.commit()
    n = await repo.revoke_all_for_user(user.id)
    await session.commit()
    assert n == 3
```

- [ ] **Step 11.2: Run, expect fail**

Run: `uv run pytest apps/api/tests/test_repositories.py -v`
Expected: ImportError.

- [ ] **Step 11.3: Implement `UserRepository`**

Create `apps/api/src/gamehost_api/repositories/__init__.py` empty.

Create `apps/api/src/gamehost_api/repositories/users.py`:

```python
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, *, email: str, password_hash: str, role: str) -> User:
        user = User(
            id=uuid.uuid4(),
            email=email.strip(),
            password_hash=password_hash,
            role=role,
        )
        self._s.add(user)
        await self._s.flush()
        return user

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(func.lower(User.email) == email.strip().lower())
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._s.get(User, user_id)
```

- [ ] **Step 11.4: Implement `RefreshTokenRepository`**

Create `apps/api/src/gamehost_api/repositories/refresh_tokens.py`:

```python
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
        ip: str | None,
    ) -> RefreshToken:
        row = RefreshToken(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def revoke(self, row: RefreshToken) -> None:
        row.revoked_at = datetime.now(UTC)
        await self._s.flush()

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        result = await self._s.execute(stmt)
        return result.rowcount or 0
```

- [ ] **Step 11.5: Tests pass**

Run: `uv run pytest apps/api/tests/test_repositories.py -v`
Expected: 4 passed.

- [ ] **Step 11.6: Commit**

```bash
git add apps/api/src/gamehost_api/repositories apps/api/tests/test_repositories.py
git commit -m "feat(api): User and RefreshToken repositories"
```

---

## Task 12: `AuthService` use case

**Files:**
- Create: `apps/api/src/gamehost_api/domain/auth.py`
- Create: `apps/api/tests/test_auth_service.py`

- [ ] **Step 12.1: Failing tests**

Create `apps/api/tests/test_auth_service.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.domain.auth import AuthService
from gamehost_api.domain.exceptions import (
    EmailAlreadyTaken,
    InvalidCredentials,
    RefreshInvalid,
    UserInactive,
)


@pytest.fixture
async def session():
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_register_creates_user(session) -> None:
    svc = AuthService(session)
    user = await svc.register(email="A@B.test", password="hunter22hunter22")
    await session.commit()
    assert user.email.lower() == "a@b.test"
    assert user.role == "user"


async def test_register_duplicate_email_raises(session) -> None:
    svc = AuthService(session)
    await svc.register(email="dup@x.test", password="hunter22hunter22")
    await session.commit()
    with pytest.raises(EmailAlreadyTaken):
        await svc.register(email="DUP@x.test", password="hunter22hunter22")


async def test_login_returns_tokens_for_valid_creds(session) -> None:
    svc = AuthService(session)
    await svc.register(email="ok@x.test", password="hunter22hunter22")
    await session.commit()
    pair = await svc.login(email="ok@x.test", password="hunter22hunter22", user_agent=None, ip=None)
    await session.commit()
    assert pair.access_token
    assert pair.refresh_token


async def test_login_wrong_password_raises_invalid_credentials(session) -> None:
    svc = AuthService(session)
    await svc.register(email="x@x.test", password="hunter22hunter22")
    await session.commit()
    with pytest.raises(InvalidCredentials):
        await svc.login(email="x@x.test", password="wrong-password", user_agent=None, ip=None)


async def test_login_unknown_email_raises_invalid_credentials(session) -> None:
    svc = AuthService(session)
    with pytest.raises(InvalidCredentials):
        await svc.login(email="missing@x.test", password="any", user_agent=None, ip=None)


async def test_login_inactive_user_raises(session) -> None:
    svc = AuthService(session)
    user = await svc.register(email="dead@x.test", password="hunter22hunter22")
    user.is_active = False
    await session.commit()
    with pytest.raises(UserInactive):
        await svc.login(email="dead@x.test", password="hunter22hunter22", user_agent=None, ip=None)


async def test_refresh_rotates_and_revokes_old(session) -> None:
    svc = AuthService(session)
    await svc.register(email="r@x.test", password="hunter22hunter22")
    await session.commit()
    pair1 = await svc.login(email="r@x.test", password="hunter22hunter22", user_agent=None, ip=None)
    await session.commit()
    pair2 = await svc.refresh(pair1.refresh_token, user_agent=None, ip=None)
    await session.commit()
    assert pair2.refresh_token != pair1.refresh_token
    with pytest.raises(RefreshInvalid):
        await svc.refresh(pair1.refresh_token, user_agent=None, ip=None)


async def test_refresh_reuse_revokes_all_user_tokens(session) -> None:
    svc = AuthService(session)
    await svc.register(email="reuse@x.test", password="hunter22hunter22")
    await session.commit()
    pair1 = await svc.login(email="reuse@x.test", password="hunter22hunter22", user_agent=None, ip=None)
    await session.commit()
    pair2 = await svc.refresh(pair1.refresh_token, user_agent=None, ip=None)
    await session.commit()
    # reuse old → revokes all (including pair2)
    with pytest.raises(RefreshInvalid):
        await svc.refresh(pair1.refresh_token, user_agent=None, ip=None)
    await session.commit()
    with pytest.raises(RefreshInvalid):
        await svc.refresh(pair2.refresh_token, user_agent=None, ip=None)


async def test_logout_revokes_current_refresh(session) -> None:
    svc = AuthService(session)
    await svc.register(email="lo@x.test", password="hunter22hunter22")
    await session.commit()
    pair = await svc.login(email="lo@x.test", password="hunter22hunter22", user_agent=None, ip=None)
    await session.commit()
    await svc.logout(pair.refresh_token)
    await session.commit()
    with pytest.raises(RefreshInvalid):
        await svc.refresh(pair.refresh_token, user_agent=None, ip=None)
```

- [ ] **Step 12.2: Run, expect fail**

Run: `uv run pytest apps/api/tests/test_auth_service.py -v`
Expected: ImportError.

- [ ] **Step 12.3: Implement `AuthService`**

Create `apps/api/src/gamehost_api/domain/auth.py`:

```python
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from gamehost_api.db.models import User
from gamehost_api.domain.exceptions import (
    EmailAlreadyTaken,
    InvalidCredentials,
    RefreshInvalid,
    UserInactive,
)
from gamehost_api.repositories.refresh_tokens import RefreshTokenRepository
from gamehost_api.repositories.users import UserRepository


@dataclass(slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._users = UserRepository(session)
        self._refresh = RefreshTokenRepository(session)

    async def register(self, *, email: str, password: str) -> User:
        normalized = email.strip().lower()
        existing = await self._users.get_by_email(normalized)
        if existing is not None:
            raise EmailAlreadyTaken(normalized)
        try:
            user = await self._users.create(
                email=normalized, password_hash=hash_password(password), role="user"
            )
        except IntegrityError as exc:
            raise EmailAlreadyTaken(normalized) from exc
        return user

    async def login(
        self, *, email: str, password: str, user_agent: str | None, ip: str | None
    ) -> TokenPair:
        user = await self._users.get_by_email(email)
        if user is None or not verify_password(user.password_hash, password):
            raise InvalidCredentials()
        if not user.is_active:
            raise UserInactive()
        return await self._issue_pair(user=user, user_agent=user_agent, ip=ip)

    async def refresh(
        self, refresh_token: str, *, user_agent: str | None, ip: str | None
    ) -> TokenPair:
        token_hash = hash_refresh_token(refresh_token)
        row = await self._refresh.get_by_token_hash(token_hash)
        now = datetime.now(UTC)
        if row is None or row.expires_at <= now:
            raise RefreshInvalid()
        if row.revoked_at is not None:
            await self._refresh.revoke_all_for_user(row.user_id)
            raise RefreshInvalid()
        user = await self._users.get(row.user_id)
        if user is None or not user.is_active:
            raise RefreshInvalid()
        await self._refresh.revoke(row)
        return await self._issue_pair(user=user, user_agent=user_agent, ip=ip)

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        row = await self._refresh.get_by_token_hash(hash_refresh_token(refresh_token))
        if row is not None and row.revoked_at is None:
            await self._refresh.revoke(row)

    async def _issue_pair(
        self, *, user: User, user_agent: str | None, ip: str | None
    ) -> TokenPair:
        s = get_settings()
        access = create_access_token(user_id=user.id, email=user.email, role=user.role)
        refresh = generate_refresh_token()
        expires = datetime.now(UTC) + timedelta(seconds=s.refresh_token_ttl_seconds)
        await self._refresh.create(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh),
            expires_at=expires,
            user_agent=user_agent,
            ip=ip,
        )
        return TokenPair(access_token=access, refresh_token=refresh, refresh_expires_at=expires)
```

- [ ] **Step 12.4: Tests pass**

Run: `uv run pytest apps/api/tests/test_auth_service.py -v`
Expected: 9 passed.

- [ ] **Step 12.5: Commit**

```bash
git add apps/api/src/gamehost_api/domain/auth.py apps/api/tests/test_auth_service.py
git commit -m "feat(api): AuthService — register, login, refresh-with-rotation, logout"
```

---

## Task 13: Auth schemas (DTOs)

**Files:**
- Create: `apps/api/src/gamehost_api/schemas/auth.py`

- [ ] **Step 13.1: Define DTOs**

Create `apps/api/src/gamehost_api/schemas/auth.py`:

```python
import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from gamehost_api.schemas.base import CamelModel


class RegisterIn(CamelModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class LoginIn(CamelModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class AccessTokenOut(CamelModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(CamelModel):
    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime
```

- [ ] **Step 13.2: Add `email-validator` dep (transitive of `EmailStr`)**

Edit `apps/api/pyproject.toml`, add to `dependencies`:

```toml
    "email-validator>=2.2,<3",
```

Run: `uv sync --all-packages`
Expected: green.

- [ ] **Step 13.3: Verify**

Run: `make typecheck`
Expected: green.

- [ ] **Step 13.4: Commit**

```bash
git add apps/api/src/gamehost_api/schemas/auth.py apps/api/pyproject.toml uv.lock
git commit -m "feat(api): auth DTOs (RegisterIn, LoginIn, AccessTokenOut, MeOut)"
```

---

## Task 14: API deps (`get_session`, `get_current_user`)

**Files:**
- Create: `apps/api/src/gamehost_api/api/__init__.py`
- Create: `apps/api/src/gamehost_api/api/v1/__init__.py`
- Create: `apps/api/src/gamehost_api/api/v1/deps.py`

- [ ] **Step 14.1: Create routers and deps**

Create `apps/api/src/gamehost_api/api/__init__.py` empty.

Create `apps/api/src/gamehost_api/api/v1/__init__.py`:

```python
from fastapi import APIRouter

from gamehost_api.api.v1 import auth

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router, prefix="/auth", tags=["auth"])
```

Create `apps/api/src/gamehost_api/api/v1/deps.py`:

```python
from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gamehost_api.core.security import decode_access_token
from gamehost_api.db.models import User
from gamehost_api.repositories.users import UserRepository


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sm: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_user(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_access_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    import uuid as _uuid

    user = await UserRepository(session).get(_uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user_inactive")
    return user
```

- [ ] **Step 14.2: Verify types**

Run: `make typecheck`
Expected: green.

- [ ] **Step 14.3: Commit**

```bash
git add apps/api/src/gamehost_api/api
git commit -m "feat(api): v1 router scaffold and dependencies"
```

---

## Task 15: Auth router (`/api/v1/auth/*` endpoints)

**Files:**
- Create: `apps/api/src/gamehost_api/api/v1/auth.py`

- [ ] **Step 15.1: Implement router**

Create `apps/api/src/gamehost_api/api/v1/auth.py`:

```python
from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_current_user, get_session
from gamehost_api.core.config import get_settings
from gamehost_api.db.models import User
from gamehost_api.domain.auth import AuthService, TokenPair
from gamehost_api.schemas.auth import AccessTokenOut, LoginIn, MeOut, RegisterIn

router = APIRouter()

_REFRESH_COOKIE = "gh_refresh"
_REFRESH_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, pair: TokenPair) -> None:
    s = get_settings()
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=pair.refresh_token,
        max_age=s.refresh_token_ttl_seconds,
        path=_REFRESH_PATH,
        domain=s.cookie_domain,
        secure=s.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        key=_REFRESH_COOKIE,
        path=_REFRESH_PATH,
        domain=s.cookie_domain,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=MeOut)
async def register(
    payload: RegisterIn, session: AsyncSession = Depends(get_session)
) -> User:
    return await AuthService(session).register(email=payload.email, password=payload.password)


@router.post("/login", response_model=AccessTokenOut)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AccessTokenOut:
    pair = await AuthService(session).login(
        email=payload.email,
        password=payload.password,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, pair)
    return AccessTokenOut(access_token=pair.access_token)


@router.post("/refresh", response_model=AccessTokenOut)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    gh_refresh: str | None = Cookie(default=None),
) -> AccessTokenOut:
    from gamehost_api.domain.exceptions import RefreshInvalid

    if not gh_refresh:
        raise RefreshInvalid()
    pair = await AuthService(session).refresh(
        gh_refresh,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, pair)
    return AccessTokenOut(access_token=pair.access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: AsyncSession = Depends(get_session),
    gh_refresh: str | None = Cookie(default=None),
) -> Response:
    await AuthService(session).logout(gh_refresh)
    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeOut)
async def me(current: User = Depends(get_current_user)) -> User:
    return current
```

- [ ] **Step 15.2: Verify types**

Run: `make typecheck`
Expected: green.

- [ ] **Step 15.3: Commit**

```bash
git add apps/api/src/gamehost_api/api/v1/auth.py
git commit -m "feat(api): /api/v1/auth router (register/login/refresh/logout/me)"
```

---

## Task 16: Wire `main.py` (lifespan, handlers, middleware, instrumentator)

**Files:**
- Modify: `apps/api/src/gamehost_api/main.py`

- [ ] **Step 16.1: Replace `main.py`**

Replace `apps/api/src/gamehost_api/main.py` with:

```python
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    engine: AsyncEngine = make_engine()
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    try:
        yield
    finally:
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
```

- [ ] **Step 16.2: Verify**

Run: `make lint && make typecheck`
Expected: green.

Run: `make test`
Expected: existing tests pass; the new auth-service / repositories / errors / logging tests pass.

- [ ] **Step 16.3: Commit**

```bash
git add apps/api/src/gamehost_api/main.py
git commit -m "feat(api): wire lifespan, handlers, request-id middleware, /metrics, v1 router"
```

---

## Task 17: End-to-end auth route tests

**Files:**
- Create: `apps/api/tests/factories.py`
- Create: `apps/api/tests/test_auth_routes.py`

- [ ] **Step 17.1: Create user factory**

Create `apps/api/tests/factories.py`:

```python
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.core.security import hash_password
from gamehost_api.db.models import User

# argon2 hashing is intentionally slow; cache one hash for the default password
DEFAULT_PASSWORD = "hunter22hunter22"
_DEFAULT_HASH: str | None = None


def default_password_hash() -> str:
    global _DEFAULT_HASH
    if _DEFAULT_HASH is None:
        _DEFAULT_HASH = hash_password(DEFAULT_PASSWORD)
    return _DEFAULT_HASH


async def make_user(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str | None = None,
    role: str = "user",
    is_active: bool = True,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email.lower(),
        password_hash=password_hash or default_password_hash(),
        role=role,
        is_active=is_active,
    )
    session.add(user)
    await session.commit()
    return user
```

- [ ] **Step 17.2: Failing route tests**

Create `apps/api/tests/test_auth_routes.py`:

```python
import time

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from tests.factories import DEFAULT_PASSWORD, make_user


@pytest.fixture
async def session():
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_register_returns_201_and_camel_me(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "new@x.test", "password": DEFAULT_PASSWORD},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "new@x.test"
    assert body["role"] == "user"
    assert body["isActive"] is True
    assert "createdAt" in body


async def test_register_duplicate_returns_409_problem(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": "dup@x.test", "password": DEFAULT_PASSWORD},
    )
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "DUP@x.test", "password": DEFAULT_PASSWORD},
    )
    assert r.status_code == 409
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "email_taken"


async def test_register_short_password_returns_422(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "x@x.test", "password": "short"},
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_login_success_returns_access_and_sets_cookie(
    client: AsyncClient, session
) -> None:
    await make_user(session, email="ok@x.test")
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "ok@x.test", "password": DEFAULT_PASSWORD},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accessToken"]
    assert body["tokenType"] == "bearer"
    assert "gh_refresh" in r.cookies


async def test_login_wrong_password_returns_401_invalid_credentials(
    client: AsyncClient, session
) -> None:
    await make_user(session, email="wrong@x.test")
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "wrong@x.test", "password": "not-the-password"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_login_unknown_email_returns_same_invalid_credentials(
    client: AsyncClient,
) -> None:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "missing@x.test", "password": "any"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "invalid_credentials"


async def test_login_inactive_user_returns_401(client: AsyncClient, session) -> None:
    await make_user(session, email="dead@x.test", is_active=False)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "dead@x.test", "password": DEFAULT_PASSWORD},
    )
    assert r.status_code == 401


async def test_refresh_rotates_and_invalidates_old(client: AsyncClient, session) -> None:
    await make_user(session, email="rot@x.test")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "rot@x.test", "password": DEFAULT_PASSWORD},
    )
    old_cookie = login.cookies["gh_refresh"]
    refresh1 = await client.post("/api/v1/auth/refresh")
    assert refresh1.status_code == 200
    new_cookie = refresh1.cookies.get("gh_refresh") or client.cookies.get("gh_refresh")
    assert new_cookie != old_cookie

    # use OLD cookie explicitly
    bad = await client.post(
        "/api/v1/auth/refresh", cookies={"gh_refresh": old_cookie}
    )
    assert bad.status_code == 401


async def test_refresh_without_cookie_returns_401(client: AsyncClient) -> None:
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 401
    assert r.json()["code"] == "refresh_invalid"


async def test_logout_revokes_and_subsequent_refresh_fails(
    client: AsyncClient, session
) -> None:
    await make_user(session, email="lo@x.test")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "lo@x.test", "password": DEFAULT_PASSWORD},
    )
    cookie = login.cookies["gh_refresh"]
    out = await client.post("/api/v1/auth/logout")
    assert out.status_code == 204
    after = await client.post(
        "/api/v1/auth/refresh", cookies={"gh_refresh": cookie}
    )
    assert after.status_code == 401


async def test_me_with_access_returns_profile(client: AsyncClient, session) -> None:
    await make_user(session, email="me@x.test")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "me@x.test", "password": DEFAULT_PASSWORD},
    )
    access = login.json()["accessToken"]
    r = await client.get("/api/v1/auth/me", headers={"authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert r.json()["email"] == "me@x.test"


async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_me_with_expired_token_returns_401(
    client: AsyncClient, session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "1")
    get_settings.cache_clear()
    await make_user(session, email="exp@x.test")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "exp@x.test", "password": DEFAULT_PASSWORD},
    )
    access = login.json()["accessToken"]
    time.sleep(2)
    r = await client.get("/api/v1/auth/me", headers={"authorization": f"Bearer {access}"})
    assert r.status_code == 401
```

- [ ] **Step 17.3: Run, expect green**

Run: `uv run pytest apps/api/tests/test_auth_routes.py -v`
Expected: 13 passed (slow due to argon2; ~30–60s).

- [ ] **Step 17.4: Run full suite**

Run: `make test`
Expected: all tests pass (≈ 30 tests including existing health, security, errors, logging, repositories, auth-service, auth-routes).

- [ ] **Step 17.5: Commit**

```bash
git add apps/api/tests/factories.py apps/api/tests/test_auth_routes.py
git commit -m "test(api): end-to-end auth route tests through AsyncClient"
```

---

## Task 18: Coverage gate, OpenAPI export, README, final verify

**Files:**
- Modify: `pyproject.toml` (add coverage config)
- Modify: `Makefile` (add `openapi`, switch test to use coverage)
- Create: `apps/api/src/gamehost_api/scripts/__init__.py`
- Create: `apps/api/src/gamehost_api/scripts/export_openapi.py`
- Modify: `README.md`

- [ ] **Step 18.1: Coverage config**

Add to root `pyproject.toml`:

```toml
[tool.coverage.run]
branch = true
source = ["apps/api/src/gamehost_api"]

[tool.coverage.report]
show_missing = true
skip_empty = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
]
```

In `[tool.pytest.ini_options]`, replace `addopts` with:

```toml
addopts = "-ra --strict-markers --cov --cov-report=term-missing --cov-fail-under=70"
```

- [ ] **Step 18.2: Domain coverage check**

Run: `uv run pytest --cov=apps/api/src/gamehost_api/domain --cov-fail-under=85 apps/api/tests`
Expected: passes (domain has only `auth.py` and `exceptions.py`, both heavily tested).

If it fails, look at uncovered lines and add a focused test. Do not lower the bar.

- [ ] **Step 18.3: OpenAPI export script**

Create `apps/api/src/gamehost_api/scripts/__init__.py` empty.

Create `apps/api/src/gamehost_api/scripts/export_openapi.py`:

```python
import json
import sys

from gamehost_api.main import app


def main() -> None:
    sys.stdout.write(json.dumps(app.openapi(), indent=2, sort_keys=True))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
```

Append to `Makefile`:

```make
openapi:
	mkdir -p docs
	SECRET_KEY=dummy-for-export uv run --package gamehost-api python -m gamehost_api.scripts.export_openapi > docs/openapi.json
```

- [ ] **Step 18.4: Generate openapi.json**

Run: `make openapi`
Expected: `docs/openapi.json` written and is valid JSON. Verify:

```bash
python -c "import json; json.load(open('docs/openapi.json'))"
```

Expected: no error.

- [ ] **Step 18.5: README auth section**

Append to `README.md` (before Troubleshooting):

````markdown
## Auth (Stage 1)

```bash
# 1. register
curl -X POST localhost:8000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"a@b.test","password":"hunter22hunter22"}'

# 2. login (saves refresh cookie to c.txt)
curl -c c.txt -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"a@b.test","password":"hunter22hunter22"}'

# 3. authenticated request
ACCESS=$(curl -s -c c.txt -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"a@b.test","password":"hunter22hunter22"}' | jq -r .accessToken)
curl localhost:8000/api/v1/auth/me -H "authorization: Bearer $ACCESS"

# 4. rotate refresh
curl -b c.txt -c c.txt -X POST localhost:8000/api/v1/auth/refresh

# 5. logout
curl -b c.txt -X POST localhost:8000/api/v1/auth/logout
```

Errors are RFC 7807 (`application/problem+json`), e.g. `{"type":"about:blank","title":"Invalid credentials","status":401,"code":"invalid_credentials"}`.
````

- [ ] **Step 18.6: Full pipeline check**

Run:

```bash
make lint
make typecheck
make test
make openapi
```

All four must succeed. Note: `make test` now enforces 70% global coverage.

- [ ] **Step 18.7: Update `.env.example` with `DATABASE_URL` examples for migrations and add to README**

`.env.example` already has `DATABASE_URL`. Verify by inspection that all keys referenced by `Settings` are present: `DATABASE_URL`, `SECRET_KEY`, `ACCESS_TOKEN_TTL_SECONDS`, `REFRESH_TOKEN_TTL_SECONDS`, `COOKIE_SECURE`, `LOG_LEVEL`. (Optional keys with defaults — `COOKIE_DOMAIN`, `ARGON2_*` — do not need to be in `.env.example`.)

- [ ] **Step 18.8: Commit**

```bash
git add pyproject.toml Makefile docs/openapi.json README.md \
  apps/api/src/gamehost_api/scripts
git commit -m "feat(api): coverage gate, openapi export, README auth section"
```

---

## Task 19: Open the PR

- [ ] **Step 19.1: Push branch**

```bash
git push -u origin stage-1-auth-users
```

Expected: branch pushed.

- [ ] **Step 19.2: Open PR**

```bash
gh pr create --base main --head stage-1-auth-users \
  --title "Stage 1: auth + users" \
  --body "$(cat docs/superpowers/specs/2026-05-02-stage-1-auth-users-design.md | head -40)"
```

If `gh` is not authenticated, print the URL `https://github.com/n0tsSzzz/gameserver-panel/pull/new/stage-1-auth-users` and ask the user to open it manually. Wait for their confirmation that the PR is created and merged before reporting completion.

---

## Self-review (run on your own output before handoff)

1. **Spec coverage:**
   - Layered scaffold (api/v1, domain, repositories, schemas, db, core) → Tasks 2, 3, 9, 11, 12, 13, 14, 15. ✓
   - Alembic init + first migration with `lower(email)` unique + role CHECK → Task 4. ✓
   - argon2id passwords → Task 6. ✓
   - HS256 access JWT → Task 7. ✓
   - Opaque refresh + sha256 hash → Task 8. ✓
   - RFC 7807 errors → Task 9. ✓
   - structlog + redact + request-id → Task 10. ✓
   - `/metrics` via prometheus-fastapi-instrumentator → Task 16. ✓
   - 5 endpoints with the documented behavior including rotation + reuse-revoke-all → Tasks 12, 15, 17. ✓
   - Coverage gates → Task 18. ✓
   - OpenAPI export → Task 18. ✓
   - README auth section → Task 18. ✓
2. **Placeholders:** none.
3. **Type/method consistency:** `AuthService.register/login/refresh/logout`, `TokenPair {access_token, refresh_token, refresh_expires_at}`, `UserRepository.{create,get_by_email,get}`, `RefreshTokenRepository.{create,get_by_token_hash,revoke,revoke_all_for_user}` referenced consistently across Tasks 11–17. Cookie name `gh_refresh` consistent. Cookie path `/api/v1/auth` consistent.
