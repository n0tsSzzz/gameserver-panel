# Stage 2 — Templates + Nodes + bootstrap admin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add admin-managed `game_templates` (CRUD admin, public read) and `nodes` (admin-only CRUD with one-shot API key) on top of the Stage 1 scaffold, plus idempotent seeders for the first admin and the canonical 5 game templates.

**Architecture:** Same layered shape as Stage 1 — FastAPI router → use-case service → repository → SQLAlchemy async session. Admin gating via a router-level `require_admin` dep. Node API keys live as `argon2id` hashes; the plaintext is returned exactly once on `POST /nodes`. CRUD on `templates` excludes `DELETE` (use `is_public=false`); CRUD on `nodes` includes hard `DELETE`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2 + `CamelModel` (already in repo), argon2-cffi (reused via `core.security.hash_password`), `secrets.token_urlsafe`, pytest + httpx async.

**Spec:** `docs/superpowers/specs/2026-05-02-stage-2-templates-nodes-design.md`

**Branch:** `stage-2-templates-nodes` (already created and contains the spec commit).

---

## Conventions

- Run after each task: `make lint && make typecheck && make test`. Coverage gate stays at 70% global, ≥85% on `domain/`.
- All new endpoints register with `api/v1/__init__.py`. Errors are RFC 7807 (`application/problem+json`) — already wired through `core/errors.register_exception_handlers`.
- camelCase JSON via `CamelModel` from `gamehost_api.schemas.base`.
- Tests reuse `client` and `_clean_db` fixtures from `apps/api/tests/conftest.py`. Per-test truncate already covers the new tables once the migration adds them.
- Per-file-ignores already in `pyproject.toml`: `B008` for `**/api/**`, `N818` for `domain/exceptions.py`, `B` for `**/tests/**`. New files under those globs inherit them.

---

## Task 1: Migration `0002_templates_nodes` + ORM models

**Files:**
- Create: `apps/api/alembic/versions/0002_templates_nodes.py`
- Create: `apps/api/src/gamehost_api/db/models/game_template.py`
- Create: `apps/api/src/gamehost_api/db/models/node.py`
- Modify: `apps/api/src/gamehost_api/db/models/__init__.py`

- [ ] **Step 1.1: Add ORM models**

Create `apps/api/src/gamehost_api/db/models/game_template.py`:

```python
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from gamehost_api.db.base import Base, TimestampMixin


class GameTemplate(Base, TimestampMixin):
    __tablename__ = "game_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    docker_image: Mapped[str] = mapped_column(String, nullable=False)
    default_env: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    default_ports: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    default_volumes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    min_resources: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    is_public: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

Create `apps/api/src/gamehost_api/db/models/node.py`:

```python
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from gamehost_api.db.base import Base, TimestampMixin


class Node(Base, TimestampMixin):
    __tablename__ = "nodes"
    __table_args__ = (
        CheckConstraint("status IN ('online','offline','drain')", name="ck_nodes_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    endpoint_url: Mapped[str] = mapped_column(String, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String, nullable=False)
    capacity_cpu: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    capacity_mem_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="online")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Update `apps/api/src/gamehost_api/db/models/__init__.py` to:

```python
from gamehost_api.db.models.game_template import GameTemplate
from gamehost_api.db.models.node import Node
from gamehost_api.db.models.refresh_token import RefreshToken
from gamehost_api.db.models.user import User

__all__ = ["GameTemplate", "Node", "RefreshToken", "User"]
```

- [ ] **Step 1.2: Hand-write migration**

Create `apps/api/alembic/versions/0002_templates_nodes.py`:

```python
"""templates + nodes

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-02

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("docker_image", sa.String(), nullable=False),
        sa.Column(
            "default_env",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "default_ports",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "default_volumes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "min_resources",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("slug", name="uq_game_templates_slug"),
    )

    op.create_table(
        "nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("endpoint_url", sa.String(), nullable=False),
        sa.Column("api_key_hash", sa.String(), nullable=False),
        sa.Column("capacity_cpu", sa.Numeric(5, 2), nullable=False),
        sa.Column("capacity_mem_mb", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="online"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_nodes_name"),
        sa.CheckConstraint("status IN ('online','offline','drain')", name="ck_nodes_status"),
    )


def downgrade() -> None:
    op.drop_table("nodes")
    op.drop_table("game_templates")
```

- [ ] **Step 1.3: Smoke verify migration roundtrip**

Make sure local Postgres is running and `.env` exists. Run from repo root:

```bash
cp -n .env.example .env || true
make migrate
cd apps/api && set -a && . ../../.env && set +a && uv run alembic downgrade base && uv run alembic upgrade head && cd ../..
```

Expected: each command logs `Running upgrade/downgrade` without errors.

- [ ] **Step 1.4: Verify lint + types**

Run: `make lint && make typecheck`
Expected: green.

- [ ] **Step 1.5: Update conftest TRUNCATE order**

Edit `apps/api/tests/conftest.py`. Find the `_clean_db` truncate statement and replace the table list to include the new tables, FK-respecting order:

```python
        await conn.execute(
            text(
                "TRUNCATE refresh_tokens, nodes, game_templates, users "
                "RESTART IDENTITY CASCADE"
            )
        )
```

- [ ] **Step 1.6: Run existing tests to confirm no regressions**

Run: `rm -f .coverage* && make test`
Expected: still 37 passed (all Stage 1 tests). The migration is applied automatically by `_apply_migrations`.

- [ ] **Step 1.7: Commit**

```bash
git add apps/api/alembic/versions/0002_templates_nodes.py apps/api/src/gamehost_api/db/models/ apps/api/tests/conftest.py
git commit -m "feat(api): add game_templates and nodes ORM models + migration 0002"
```

---

## Task 2: Settings + `.env.example` for bootstrap admin

**Files:**
- Modify: `apps/api/src/gamehost_api/core/config.py`
- Modify: `.env.example`

- [ ] **Step 2.1: Add bootstrap fields to `Settings`**

Open `apps/api/src/gamehost_api/core/config.py`. Find the `Settings` class and append two fields right before the trailing blank line / `lru_cache` block. The full class should now read:

```python
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
    bootstrap_admin_email: str | None = Field(default=None, alias="BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_admin_password: SecretStr | None = Field(
        default=None, alias="BOOTSTRAP_ADMIN_PASSWORD"
    )
```

- [ ] **Step 2.2: Append env keys**

Append to `.env.example`:

```
# Stage 2 — bootstrap first admin
BOOTSTRAP_ADMIN_EMAIL=admin@gh.local
BOOTSTRAP_ADMIN_PASSWORD=change-me-in-prod
```

- [ ] **Step 2.3: Verify**

Run: `make lint && make typecheck && make test`
Expected: green; existing tests still pass (the new fields have `None` defaults).

- [ ] **Step 2.4: Commit**

```bash
git add apps/api/src/gamehost_api/core/config.py .env.example
git commit -m "feat(api): bootstrap-admin settings fields"
```

---

## Task 3: `Forbidden` exception + `require_admin` dep

**Files:**
- Modify: `apps/api/src/gamehost_api/domain/exceptions.py`
- Modify: `apps/api/src/gamehost_api/api/v1/deps.py`
- Create: `apps/api/tests/test_require_admin.py`

- [ ] **Step 3.1: Add `Forbidden` exception**

Append to `apps/api/src/gamehost_api/domain/exceptions.py`:

```python


class Forbidden(DomainError):
    code = "forbidden"
    status_code = 403
    title = "Forbidden"
```

- [ ] **Step 3.2: Add `require_admin` dep**

Open `apps/api/src/gamehost_api/api/v1/deps.py` and append:

```python
from gamehost_api.domain.exceptions import Forbidden  # noqa: E402  late import keeps circulars at bay


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise Forbidden()
    return user
```

(If `Forbidden` import collides with existing imports at the top, just hoist it up — keep imports sorted by ruff.)

- [ ] **Step 3.3: Failing test for the dep**

Create `apps/api/tests/test_require_admin.py`:

```python
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import create_access_token
from gamehost_api.db.models import User
from tests.factories import DEFAULT_PASSWORD, make_user


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(user_id=user.id, email=user.email, role=user.role)
    return {"authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_require_admin_passes_for_admin(client, session: AsyncSession) -> None:
    admin = await make_user(session, email="adm@x.test", role="admin")
    # we'll hit GET /api/v1/templates which uses get_current_user — admin role passes through
    r = await client.get("/api/v1/templates", headers=_bearer(admin))
    # endpoint not yet exists at this point; if so, we'll see 404, but importantly NOT 403
    assert r.status_code != 403
```

- [ ] **Step 3.4: Run test (collection only — endpoint comes in Task 7/8)**

Run: `uv run pytest apps/api/tests/test_require_admin.py -v`
Expected: test runs and exits with `assert r.status_code != 403` after a 404 from FastAPI. **If it fails — that means `require_admin` produced a 403 on an admin user; investigate.**

If the test asserts and passes (because 404 != 403), great. If it errors due to no `/api/v1/templates`, that's expected — FastAPI returns 404, the assertion still holds.

- [ ] **Step 3.5: Commit**

```bash
git add apps/api/src/gamehost_api/domain/exceptions.py apps/api/src/gamehost_api/api/v1/deps.py apps/api/tests/test_require_admin.py
git commit -m "feat(api): Forbidden exception and require_admin dep"
```

---

## Task 4: Domain exceptions for templates/nodes + schemas

**Files:**
- Modify: `apps/api/src/gamehost_api/domain/exceptions.py`
- Create: `apps/api/src/gamehost_api/schemas/templates.py`
- Create: `apps/api/src/gamehost_api/schemas/nodes.py`

- [ ] **Step 4.1: Add domain errors**

Append to `apps/api/src/gamehost_api/domain/exceptions.py`:

```python


class TemplateNotFound(DomainError):
    code = "template_not_found"
    status_code = 404
    title = "Game template not found"


class SlugAlreadyTaken(DomainError):
    code = "slug_taken"
    status_code = 409
    title = "Template slug already taken"


class NodeNotFound(DomainError):
    code = "node_not_found"
    status_code = 404
    title = "Node not found"


class NodeNameTaken(DomainError):
    code = "node_name_taken"
    status_code = 409
    title = "Node name already taken"
```

- [ ] **Step 4.2: Templates schemas**

Create `apps/api/src/gamehost_api/schemas/templates.py`:

```python
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, Field

from gamehost_api.schemas.base import CamelModel

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _validate_slug(value: str) -> str:
    if not _SLUG_RE.fullmatch(value):
        raise ValueError("slug must match ^[a-z0-9][a-z0-9-]{0,63}$")
    return value


Slug = Annotated[str, AfterValidator(_validate_slug)]


class TemplateCreateIn(CamelModel):
    slug: Slug
    display_name: str = Field(min_length=1, max_length=200)
    docker_image: str = Field(min_length=1, max_length=500)
    default_env: dict[str, Any] = Field(default_factory=dict)
    default_ports: list[dict[str, Any]] = Field(default_factory=list)
    default_volumes: list[Any] = Field(default_factory=list)
    min_resources: dict[str, Any] = Field(default_factory=dict)
    is_public: bool = True


class TemplatePatchIn(CamelModel):
    slug: Slug | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    docker_image: str | None = Field(default=None, min_length=1, max_length=500)
    default_env: dict[str, Any] | None = None
    default_ports: list[dict[str, Any]] | None = None
    default_volumes: list[Any] | None = None
    min_resources: dict[str, Any] | None = None
    is_public: bool | None = None


class TemplateOut(CamelModel):
    id: uuid.UUID
    slug: str
    display_name: str
    docker_image: str
    default_env: dict[str, Any]
    default_ports: list[dict[str, Any]]
    default_volumes: list[Any]
    min_resources: dict[str, Any]
    is_public: bool
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4.3: Nodes schemas**

Create `apps/api/src/gamehost_api/schemas/nodes.py`:

```python
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, HttpUrl

from gamehost_api.schemas.base import CamelModel

WriteableStatus = Literal["online", "drain"]
ReadableStatus = Literal["online", "drain", "offline"]


class NodeCreateIn(CamelModel):
    name: str = Field(min_length=1, max_length=100)
    endpoint_url: HttpUrl
    capacity_cpu: Decimal = Field(gt=0, le=Decimal("999.99"))
    capacity_mem_mb: int = Field(gt=0, le=10_000_000)


class NodePatchIn(CamelModel):
    endpoint_url: HttpUrl | None = None
    capacity_cpu: Decimal | None = Field(default=None, gt=0, le=Decimal("999.99"))
    capacity_mem_mb: int | None = Field(default=None, gt=0, le=10_000_000)
    status: WriteableStatus | None = None


class NodeOut(CamelModel):
    id: uuid.UUID
    name: str
    endpoint_url: str
    capacity_cpu: Decimal
    capacity_mem_mb: int
    status: ReadableStatus
    last_seen_at: datetime | None
    created_at: datetime


class NodeCreateOut(NodeOut):
    api_key: str
```

- [ ] **Step 4.4: Verify**

Run: `make lint && make typecheck`
Expected: green.

- [ ] **Step 4.5: Commit**

```bash
git add apps/api/src/gamehost_api/domain/exceptions.py apps/api/src/gamehost_api/schemas
git commit -m "feat(api): template/node domain errors and DTOs"
```

---

## Task 5: Repositories — `TemplateRepository` and `NodeRepository`

**Files:**
- Create: `apps/api/src/gamehost_api/repositories/templates.py`
- Create: `apps/api/src/gamehost_api/repositories/nodes.py`
- Create: `apps/api/tests/test_templates_repo.py`
- Create: `apps/api/tests/test_nodes_repo.py`

- [ ] **Step 5.1: Failing tests — templates repo**

Create `apps/api/tests/test_templates_repo.py`:

```python
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.repositories.templates import TemplateRepository


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_create_then_get_by_slug(session: AsyncSession) -> None:
    repo = TemplateRepository(session)
    t = await repo.create(
        slug="minecraft-vanilla",
        display_name="Minecraft",
        docker_image="itzg/minecraft-server:latest",
        default_env={},
        default_ports=[],
        default_volumes=[],
        min_resources={"cpu": 1.0, "memMb": 2048},
        is_public=True,
    )
    await session.commit()
    fetched = await repo.get_by_slug("minecraft-vanilla")
    assert fetched is not None
    assert fetched.id == t.id


async def test_list_public_only_excludes_hidden(session: AsyncSession) -> None:
    repo = TemplateRepository(session)
    await repo.create(
        slug="public-game",
        display_name="A",
        docker_image="x",
        default_env={},
        default_ports=[],
        default_volumes=[],
        min_resources={},
        is_public=True,
    )
    await repo.create(
        slug="hidden-game",
        display_name="B",
        docker_image="x",
        default_env={},
        default_ports=[],
        default_volumes=[],
        min_resources={},
        is_public=False,
    )
    await session.commit()
    public = await repo.list_(public_only=True)
    all_ = await repo.list_(public_only=False)
    assert {t.slug for t in public} == {"public-game"}
    assert {t.slug for t in all_} == {"public-game", "hidden-game"}


async def test_update_partial_changes_updated_at(session: AsyncSession) -> None:
    repo = TemplateRepository(session)
    t = await repo.create(
        slug="upd",
        display_name="A",
        docker_image="x",
        default_env={},
        default_ports=[],
        default_volumes=[],
        min_resources={},
        is_public=True,
    )
    await session.commit()
    before = t.updated_at
    updated = await repo.update(t, {"display_name": "B"})
    await session.commit()
    assert updated.display_name == "B"
    assert updated.updated_at >= before
```

- [ ] **Step 5.2: Implement `TemplateRepository`**

Create `apps/api/src/gamehost_api/repositories/templates.py`:

```python
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import GameTemplate


class TemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_(self, *, public_only: bool) -> list[GameTemplate]:
        stmt = select(GameTemplate).order_by(GameTemplate.display_name.asc())
        if public_only:
            stmt = stmt.where(GameTemplate.is_public.is_(True))
        return list((await self._s.execute(stmt)).scalars().all())

    async def get(self, template_id: uuid.UUID) -> GameTemplate | None:
        return await self._s.get(GameTemplate, template_id)

    async def get_by_slug(self, slug: str) -> GameTemplate | None:
        stmt = select(GameTemplate).where(GameTemplate.slug == slug)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        slug: str,
        display_name: str,
        docker_image: str,
        default_env: dict[str, Any],
        default_ports: list[Any],
        default_volumes: list[Any],
        min_resources: dict[str, Any],
        is_public: bool,
    ) -> GameTemplate:
        t = GameTemplate(
            id=uuid.uuid4(),
            slug=slug,
            display_name=display_name,
            docker_image=docker_image,
            default_env=default_env,
            default_ports=default_ports,
            default_volumes=default_volumes,
            min_resources=min_resources,
            is_public=is_public,
        )
        self._s.add(t)
        await self._s.flush()
        return t

    async def update(self, t: GameTemplate, fields: dict[str, Any]) -> GameTemplate:
        for k, v in fields.items():
            setattr(t, k, v)
        t.updated_at = datetime.now(UTC)
        await self._s.flush()
        return t
```

- [ ] **Step 5.3: Failing tests — nodes repo**

Create `apps/api/tests/test_nodes_repo.py`:

```python
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.repositories.nodes import NodeRepository


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


async def test_create_get_list(session: AsyncSession) -> None:
    repo = NodeRepository(session)
    n = await repo.create(
        name="node-1",
        endpoint_url="http://node-1:8080",
        api_key_hash="argon2hash",
        capacity_cpu=Decimal("8.00"),
        capacity_mem_mb=16384,
    )
    await session.commit()
    fetched = await repo.get(n.id)
    assert fetched is not None
    listing = await repo.list_()
    assert len(listing) == 1


async def test_update_and_delete(session: AsyncSession) -> None:
    repo = NodeRepository(session)
    n = await repo.create(
        name="node-2",
        endpoint_url="http://node-2:8080",
        api_key_hash="h",
        capacity_cpu=Decimal("4.00"),
        capacity_mem_mb=8192,
    )
    await session.commit()
    await repo.update(n, {"status": "drain", "capacity_mem_mb": 4096})
    await session.commit()
    refetched = await repo.get(n.id)
    assert refetched is not None
    assert refetched.status == "drain"
    assert refetched.capacity_mem_mb == 4096
    await repo.delete(n)
    await session.commit()
    assert await repo.get(n.id) is None
```

- [ ] **Step 5.4: Implement `NodeRepository`**

Create `apps/api/src/gamehost_api/repositories/nodes.py`:

```python
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import Node


class NodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_(self) -> list[Node]:
        stmt = select(Node).order_by(Node.name.asc())
        return list((await self._s.execute(stmt)).scalars().all())

    async def get(self, node_id: uuid.UUID) -> Node | None:
        return await self._s.get(Node, node_id)

    async def get_by_name(self, name: str) -> Node | None:
        stmt = select(Node).where(Node.name == name)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        name: str,
        endpoint_url: str,
        api_key_hash: str,
        capacity_cpu: Decimal,
        capacity_mem_mb: int,
    ) -> Node:
        node = Node(
            id=uuid.uuid4(),
            name=name,
            endpoint_url=endpoint_url,
            api_key_hash=api_key_hash,
            capacity_cpu=capacity_cpu,
            capacity_mem_mb=capacity_mem_mb,
        )
        self._s.add(node)
        await self._s.flush()
        return node

    async def update(self, node: Node, fields: dict[str, Any]) -> Node:
        for k, v in fields.items():
            setattr(node, k, v)
        await self._s.flush()
        return node

    async def delete(self, node: Node) -> None:
        await self._s.delete(node)
        await self._s.flush()
```

- [ ] **Step 5.5: Run repo tests**

Run: `uv run pytest apps/api/tests/test_templates_repo.py apps/api/tests/test_nodes_repo.py -v`
Expected: 5 passed.

- [ ] **Step 5.6: Commit**

```bash
git add apps/api/src/gamehost_api/repositories/templates.py apps/api/src/gamehost_api/repositories/nodes.py apps/api/tests/test_templates_repo.py apps/api/tests/test_nodes_repo.py
git commit -m "feat(api): TemplateRepository and NodeRepository"
```

---

## Task 6: `TemplateService` (use case)

**Files:**
- Create: `apps/api/src/gamehost_api/domain/templates.py`
- Create: `apps/api/tests/test_templates_service.py`

- [ ] **Step 6.1: Failing tests**

Create `apps/api/tests/test_templates_service.py`:

```python
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.domain.exceptions import SlugAlreadyTaken, TemplateNotFound
from gamehost_api.domain.templates import TemplateService
from gamehost_api.schemas.templates import TemplateCreateIn, TemplatePatchIn
from tests.factories import make_user


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _payload(slug: str = "x", *, is_public: bool = True) -> TemplateCreateIn:
    return TemplateCreateIn(
        slug=slug,
        display_name="X",
        docker_image="img",
        default_env={},
        default_ports=[],
        default_volumes=[],
        min_resources={},
        is_public=is_public,
    )


async def test_list_for_user_returns_only_public(session: AsyncSession) -> None:
    svc = TemplateService(session)
    user = await make_user(session, email="u@x.test", role="user")
    await svc.create(_payload("a", is_public=True))
    await svc.create(_payload("b", is_public=False))
    await session.commit()
    visible = await svc.list_(actor=user)
    assert {t.slug for t in visible} == {"a"}


async def test_list_for_admin_returns_all(session: AsyncSession) -> None:
    svc = TemplateService(session)
    admin = await make_user(session, email="a@x.test", role="admin")
    await svc.create(_payload("a", is_public=True))
    await svc.create(_payload("b", is_public=False))
    await session.commit()
    visible = await svc.list_(actor=admin)
    assert {t.slug for t in visible} == {"a", "b"}


async def test_create_duplicate_slug_raises(session: AsyncSession) -> None:
    svc = TemplateService(session)
    await svc.create(_payload("dup"))
    await session.commit()
    with pytest.raises(SlugAlreadyTaken):
        await svc.create(_payload("dup"))


async def test_update_unknown_raises(session: AsyncSession) -> None:
    import uuid

    svc = TemplateService(session)
    with pytest.raises(TemplateNotFound):
        await svc.update(uuid.uuid4(), TemplatePatchIn(display_name="x"))


async def test_update_partial(session: AsyncSession) -> None:
    svc = TemplateService(session)
    t = await svc.create(_payload("upd"))
    await session.commit()
    out = await svc.update(t.id, TemplatePatchIn(display_name="New"))
    await session.commit()
    assert out.display_name == "New"
    assert out.slug == "upd"
```

- [ ] **Step 6.2: Implement `TemplateService`**

Create `apps/api/src/gamehost_api/domain/templates.py`:

```python
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.db.models import GameTemplate, User
from gamehost_api.domain.exceptions import SlugAlreadyTaken, TemplateNotFound
from gamehost_api.repositories.templates import TemplateRepository
from gamehost_api.schemas.templates import TemplateCreateIn, TemplatePatchIn


class TemplateService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = TemplateRepository(session)

    async def list_(self, *, actor: User) -> list[GameTemplate]:
        return await self._repo.list_(public_only=actor.role != "admin")

    async def create(self, payload: TemplateCreateIn) -> GameTemplate:
        if await self._repo.get_by_slug(payload.slug) is not None:
            raise SlugAlreadyTaken(payload.slug)
        try:
            return await self._repo.create(
                slug=payload.slug,
                display_name=payload.display_name,
                docker_image=payload.docker_image,
                default_env=payload.default_env,
                default_ports=payload.default_ports,
                default_volumes=payload.default_volumes,
                min_resources=payload.min_resources,
                is_public=payload.is_public,
            )
        except IntegrityError as exc:
            raise SlugAlreadyTaken(payload.slug) from exc

    async def update(self, template_id: uuid.UUID, payload: TemplatePatchIn) -> GameTemplate:
        t = await self._repo.get(template_id)
        if t is None:
            raise TemplateNotFound(str(template_id))
        fields = payload.model_dump(exclude_unset=True)
        if "slug" in fields:
            existing = await self._repo.get_by_slug(fields["slug"])
            if existing is not None and existing.id != template_id:
                raise SlugAlreadyTaken(fields["slug"])
        return await self._repo.update(t, fields)
```

- [ ] **Step 6.3: Run tests**

Run: `uv run pytest apps/api/tests/test_templates_service.py -v`
Expected: 5 passed.

- [ ] **Step 6.4: Commit**

```bash
git add apps/api/src/gamehost_api/domain/templates.py apps/api/tests/test_templates_service.py
git commit -m "feat(api): TemplateService (list/create/update)"
```

---

## Task 7: `NodeService` + `verify_api_key`

**Files:**
- Create: `apps/api/src/gamehost_api/domain/nodes.py`
- Create: `apps/api/tests/test_nodes_service.py`

- [ ] **Step 7.1: Failing tests**

Create `apps/api/tests/test_nodes_service.py`:

```python
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.domain.exceptions import NodeNameTaken, NodeNotFound
from gamehost_api.domain.nodes import NodeService, verify_api_key
from gamehost_api.schemas.nodes import NodeCreateIn, NodePatchIn


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _payload(name: str = "node-a") -> NodeCreateIn:
    return NodeCreateIn(
        name=name,
        endpoint_url="http://node:8080",  # type: ignore[arg-type]
        capacity_cpu=Decimal("8.00"),
        capacity_mem_mb=16384,
    )


async def test_create_returns_plaintext_api_key_and_hashes_it(session: AsyncSession) -> None:
    svc = NodeService(session)
    node, plain = await svc.create(_payload())
    await session.commit()
    assert isinstance(plain, str) and len(plain) >= 32
    assert node.api_key_hash != plain
    assert verify_api_key(plain, node) is True
    assert verify_api_key("not-the-key", node) is False


async def test_create_duplicate_name_raises(session: AsyncSession) -> None:
    svc = NodeService(session)
    await svc.create(_payload("dup"))
    await session.commit()
    with pytest.raises(NodeNameTaken):
        await svc.create(_payload("dup"))


async def test_update_status_to_drain_succeeds(session: AsyncSession) -> None:
    svc = NodeService(session)
    node, _ = await svc.create(_payload("upd"))
    await session.commit()
    out = await svc.update(node.id, NodePatchIn(status="drain"))
    await session.commit()
    assert out.status == "drain"


async def test_update_unknown_raises(session: AsyncSession) -> None:
    svc = NodeService(session)
    with pytest.raises(NodeNotFound):
        await svc.update(uuid.uuid4(), NodePatchIn(status="drain"))


async def test_delete_unknown_raises(session: AsyncSession) -> None:
    svc = NodeService(session)
    with pytest.raises(NodeNotFound):
        await svc.delete(uuid.uuid4())


async def test_delete_succeeds(session: AsyncSession) -> None:
    svc = NodeService(session)
    node, _ = await svc.create(_payload("del"))
    await session.commit()
    await svc.delete(node.id)
    await session.commit()
    with pytest.raises(NodeNotFound):
        await svc.update(node.id, NodePatchIn(status="drain"))
```

- [ ] **Step 7.2: Implement service + verify**

Create `apps/api/src/gamehost_api/domain/nodes.py`:

```python
import secrets
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.core.security import hash_password, verify_password
from gamehost_api.db.models import Node
from gamehost_api.domain.exceptions import NodeNameTaken, NodeNotFound
from gamehost_api.repositories.nodes import NodeRepository
from gamehost_api.schemas.nodes import NodeCreateIn, NodePatchIn


def verify_api_key(plain: str, node: Node) -> bool:
    return verify_password(node.api_key_hash, plain)


class NodeService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = NodeRepository(session)

    async def list_(self) -> list[Node]:
        return await self._repo.list_()

    async def create(self, payload: NodeCreateIn) -> tuple[Node, str]:
        if await self._repo.get_by_name(payload.name) is not None:
            raise NodeNameTaken(payload.name)
        plain = secrets.token_urlsafe(32)
        try:
            node = await self._repo.create(
                name=payload.name,
                endpoint_url=str(payload.endpoint_url),
                api_key_hash=hash_password(plain),
                capacity_cpu=payload.capacity_cpu,
                capacity_mem_mb=payload.capacity_mem_mb,
            )
        except IntegrityError as exc:
            raise NodeNameTaken(payload.name) from exc
        return node, plain

    async def update(self, node_id: uuid.UUID, payload: NodePatchIn) -> Node:
        node = await self._repo.get(node_id)
        if node is None:
            raise NodeNotFound(str(node_id))
        fields = payload.model_dump(exclude_unset=True)
        if "endpoint_url" in fields and fields["endpoint_url"] is not None:
            fields["endpoint_url"] = str(fields["endpoint_url"])
        return await self._repo.update(node, fields)

    async def delete(self, node_id: uuid.UUID) -> None:
        node = await self._repo.get(node_id)
        if node is None:
            raise NodeNotFound(str(node_id))
        await self._repo.delete(node)
```

- [ ] **Step 7.3: Run tests**

Run: `uv run pytest apps/api/tests/test_nodes_service.py -v`
Expected: 6 passed.

- [ ] **Step 7.4: Commit**

```bash
git add apps/api/src/gamehost_api/domain/nodes.py apps/api/tests/test_nodes_service.py
git commit -m "feat(api): NodeService and verify_api_key"
```

---

## Task 8: Templates router + factory helpers

**Files:**
- Create: `apps/api/src/gamehost_api/api/v1/templates.py`
- Modify: `apps/api/src/gamehost_api/api/v1/__init__.py`
- Modify: `apps/api/tests/factories.py`
- Create: `apps/api/tests/test_templates_routes.py`

- [ ] **Step 8.1: Add factory helpers**

Open `apps/api/tests/factories.py` and append:

```python


async def make_admin(session, *, email: str = "admin@x.test"):
    return await make_user(session, email=email, role="admin")


async def make_template(
    session,
    *,
    slug: str,
    display_name: str = "X",
    docker_image: str = "img",
    is_public: bool = True,
):
    from gamehost_api.repositories.templates import TemplateRepository

    repo = TemplateRepository(session)
    t = await repo.create(
        slug=slug,
        display_name=display_name,
        docker_image=docker_image,
        default_env={},
        default_ports=[],
        default_volumes=[],
        min_resources={},
        is_public=is_public,
    )
    await session.commit()
    return t


async def make_node(
    session,
    *,
    name: str,
    endpoint_url: str = "http://node:8080",
    api_key_hash: str = "x",
    capacity_cpu: float = 4.0,
    capacity_mem_mb: int = 8192,
):
    from decimal import Decimal

    from gamehost_api.repositories.nodes import NodeRepository

    repo = NodeRepository(session)
    n = await repo.create(
        name=name,
        endpoint_url=endpoint_url,
        api_key_hash=api_key_hash,
        capacity_cpu=Decimal(str(capacity_cpu)),
        capacity_mem_mb=capacity_mem_mb,
    )
    await session.commit()
    return n
```

- [ ] **Step 8.2: Templates router**

Create `apps/api/src/gamehost_api/api/v1/templates.py`:

```python
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_current_user, get_session, require_admin
from gamehost_api.db.models import User
from gamehost_api.domain.templates import TemplateService
from gamehost_api.schemas.templates import TemplateCreateIn, TemplateOut, TemplatePatchIn

router = APIRouter()


@router.get("", response_model=list[TemplateOut])
async def list_templates(
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TemplateOut]:
    items = await TemplateService(session).list_(actor=actor)
    return [TemplateOut.model_validate(t) for t in items]


@router.post(
    "",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_template(
    payload: TemplateCreateIn,
    session: AsyncSession = Depends(get_session),
) -> TemplateOut:
    t = await TemplateService(session).create(payload)
    return TemplateOut.model_validate(t)


@router.patch(
    "/{template_id}",
    response_model=TemplateOut,
    dependencies=[Depends(require_admin)],
)
async def patch_template(
    template_id: uuid.UUID,
    payload: TemplatePatchIn,
    session: AsyncSession = Depends(get_session),
) -> TemplateOut:
    t = await TemplateService(session).update(template_id, payload)
    return TemplateOut.model_validate(t)
```

- [ ] **Step 8.3: Wire router**

Open `apps/api/src/gamehost_api/api/v1/__init__.py`. Replace its contents with:

```python
from fastapi import APIRouter

from gamehost_api.api.v1 import auth, templates

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router, prefix="/auth", tags=["auth"])
api_v1.include_router(templates.router, prefix="/templates", tags=["templates"])
```

- [ ] **Step 8.4: Failing route tests**

Create `apps/api/tests/test_templates_routes.py`:

```python
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import create_access_token
from gamehost_api.db.models import User
from tests.factories import make_admin, make_template, make_user


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _bearer(user: User) -> dict[str, str]:
    return {"authorization": f"Bearer {create_access_token(user_id=user.id, email=user.email, role=user.role)}"}


async def test_get_unauth_returns_401(client: AsyncClient) -> None:
    r = await client.get("/api/v1/templates")
    assert r.status_code == 401


async def test_get_as_user_filters_to_public_only(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    await make_template(session, slug="pub", is_public=True)
    await make_template(session, slug="hid", is_public=False)
    r = await client.get("/api/v1/templates", headers=_bearer(user))
    assert r.status_code == 200
    slugs = {t["slug"] for t in r.json()}
    assert slugs == {"pub"}


async def test_get_as_admin_returns_all(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    await make_template(session, slug="pub", is_public=True)
    await make_template(session, slug="hid", is_public=False)
    r = await client.get("/api/v1/templates", headers=_bearer(admin))
    slugs = {t["slug"] for t in r.json()}
    assert slugs == {"pub", "hid"}


async def test_post_as_user_returns_403(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u2@x.test", role="user")
    r = await client.post(
        "/api/v1/templates",
        headers=_bearer(user),
        json={"slug": "x", "displayName": "X", "dockerImage": "img"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


async def test_post_as_admin_creates(client: AsyncClient, session: AsyncSession) -> None:
    admin = await make_admin(session)
    r = await client.post(
        "/api/v1/templates",
        headers=_bearer(admin),
        json={"slug": "minecraft", "displayName": "MC", "dockerImage": "itzg/minecraft-server"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == "minecraft"
    assert body["isPublic"] is True


async def test_post_duplicate_slug_returns_409(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    await make_template(session, slug="dup")
    r = await client.post(
        "/api/v1/templates",
        headers=_bearer(admin),
        json={"slug": "dup", "displayName": "X", "dockerImage": "x"},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "slug_taken"


async def test_post_invalid_slug_returns_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    r = await client.post(
        "/api/v1/templates",
        headers=_bearer(admin),
        json={"slug": "BAD SLUG", "displayName": "X", "dockerImage": "x"},
    )
    assert r.status_code == 422


async def test_patch_as_admin_changes_field(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    t = await make_template(session, slug="upd", display_name="Old")
    r = await client.patch(
        f"/api/v1/templates/{t.id}",
        headers=_bearer(admin),
        json={"displayName": "New"},
    )
    assert r.status_code == 200
    assert r.json()["displayName"] == "New"


async def test_patch_unknown_returns_404(client: AsyncClient, session: AsyncSession) -> None:
    import uuid

    admin = await make_admin(session)
    r = await client.patch(
        f"/api/v1/templates/{uuid.uuid4()}",
        headers=_bearer(admin),
        json={"displayName": "X"},
    )
    assert r.status_code == 404
    assert r.json()["code"] == "template_not_found"
```

- [ ] **Step 8.5: Run tests**

Run: `uv run pytest apps/api/tests/test_templates_routes.py -v`
Expected: 9 passed.

- [ ] **Step 8.6: Commit**

```bash
git add apps/api/src/gamehost_api/api/v1/templates.py apps/api/src/gamehost_api/api/v1/__init__.py apps/api/tests/factories.py apps/api/tests/test_templates_routes.py
git commit -m "feat(api): /api/v1/templates router (admin CRUD + user read)"
```

---

## Task 9: Nodes router

**Files:**
- Create: `apps/api/src/gamehost_api/api/v1/nodes.py`
- Modify: `apps/api/src/gamehost_api/api/v1/__init__.py`
- Create: `apps/api/tests/test_nodes_routes.py`

- [ ] **Step 9.1: Nodes router**

Create `apps/api/src/gamehost_api/api/v1/nodes.py`:

```python
import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_session, require_admin
from gamehost_api.domain.nodes import NodeService
from gamehost_api.schemas.nodes import NodeCreateIn, NodeCreateOut, NodeOut, NodePatchIn

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("", response_model=list[NodeOut])
async def list_nodes(session: AsyncSession = Depends(get_session)) -> list[NodeOut]:
    items = await NodeService(session).list_()
    return [NodeOut.model_validate(n) for n in items]


@router.post("", response_model=NodeCreateOut, status_code=status.HTTP_201_CREATED)
async def create_node(
    payload: NodeCreateIn, session: AsyncSession = Depends(get_session)
) -> NodeCreateOut:
    node, plain_key = await NodeService(session).create(payload)
    out = NodeCreateOut.model_validate({**node.__dict__, "api_key": plain_key})
    return out


@router.patch("/{node_id}", response_model=NodeOut)
async def patch_node(
    node_id: uuid.UUID,
    payload: NodePatchIn,
    session: AsyncSession = Depends(get_session),
) -> NodeOut:
    node = await NodeService(session).update(node_id, payload)
    return NodeOut.model_validate(node)


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    await NodeService(session).delete(node_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 9.2: Wire router**

Open `apps/api/src/gamehost_api/api/v1/__init__.py`. Replace its contents with:

```python
from fastapi import APIRouter

from gamehost_api.api.v1 import auth, nodes, templates

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router, prefix="/auth", tags=["auth"])
api_v1.include_router(templates.router, prefix="/templates", tags=["templates"])
api_v1.include_router(nodes.router, prefix="/nodes", tags=["nodes"])
```

- [ ] **Step 9.3: Failing route tests**

Create `apps/api/tests/test_nodes_routes.py`:

```python
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import create_access_token
from gamehost_api.db.models import User
from tests.factories import make_admin, make_user


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _bearer(user: User) -> dict[str, str]:
    return {"authorization": f"Bearer {create_access_token(user_id=user.id, email=user.email, role=user.role)}"}


def _create_payload(name: str = "node-1") -> dict:
    return {
        "name": name,
        "endpointUrl": "http://node-1:8080",
        "capacityCpu": "8.00",
        "capacityMemMb": 16384,
    }


async def test_post_as_user_returns_403(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_user(session, email="u@x.test", role="user")
    r = await client.post("/api/v1/nodes", headers=_bearer(user), json=_create_payload())
    assert r.status_code == 403


async def test_post_as_admin_returns_201_with_api_key(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    r = await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload())
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "node-1"
    assert isinstance(body["apiKey"], str) and len(body["apiKey"]) >= 32


async def test_subsequent_get_does_not_expose_api_key(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload("n2"))
    r = await client.get("/api/v1/nodes", headers=_bearer(admin))
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert "apiKey" not in items[0]


async def test_patch_status_drain_returns_200(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    created = await client.post(
        "/api/v1/nodes", headers=_bearer(admin), json=_create_payload("n3")
    )
    nid = created.json()["id"]
    r = await client.patch(
        f"/api/v1/nodes/{nid}", headers=_bearer(admin), json={"status": "drain"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "drain"


async def test_patch_status_offline_returns_422(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    created = await client.post(
        "/api/v1/nodes", headers=_bearer(admin), json=_create_payload("n4")
    )
    nid = created.json()["id"]
    r = await client.patch(
        f"/api/v1/nodes/{nid}", headers=_bearer(admin), json={"status": "offline"}
    )
    assert r.status_code == 422


async def test_delete_returns_204_then_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    created = await client.post(
        "/api/v1/nodes", headers=_bearer(admin), json=_create_payload("n5")
    )
    nid = created.json()["id"]
    r1 = await client.delete(f"/api/v1/nodes/{nid}", headers=_bearer(admin))
    assert r1.status_code == 204
    r2 = await client.delete(f"/api/v1/nodes/{nid}", headers=_bearer(admin))
    assert r2.status_code == 404


async def test_post_duplicate_name_returns_409(
    client: AsyncClient, session: AsyncSession
) -> None:
    admin = await make_admin(session)
    await client.post("/api/v1/nodes", headers=_bearer(admin), json=_create_payload("dup"))
    r = await client.post(
        "/api/v1/nodes", headers=_bearer(admin), json=_create_payload("dup")
    )
    assert r.status_code == 409
    assert r.json()["code"] == "node_name_taken"
```

- [ ] **Step 9.4: Run tests**

Run: `uv run pytest apps/api/tests/test_nodes_routes.py -v`
Expected: 7 passed.

- [ ] **Step 9.5: Commit**

```bash
git add apps/api/src/gamehost_api/api/v1/nodes.py apps/api/src/gamehost_api/api/v1/__init__.py apps/api/tests/test_nodes_routes.py
git commit -m "feat(api): /api/v1/nodes router (admin CRUD + one-time apiKey)"
```

---

## Task 10: `seed_admin` script + Makefile

**Files:**
- Create: `apps/api/src/gamehost_api/scripts/seed_admin.py`
- Modify: `Makefile`
- Create: `apps/api/tests/test_seed_admin.py`

- [ ] **Step 10.1: Implement seeder**

Create `apps/api/src/gamehost_api/scripts/seed_admin.py`:

```python
import asyncio
import sys

import structlog

from gamehost_api.core.config import get_settings
from gamehost_api.core.logging import configure_logging
from gamehost_api.core.security import hash_password
from gamehost_api.db.session import make_engine, make_sessionmaker
from gamehost_api.repositories.users import UserRepository


async def _run() -> int:
    settings = get_settings()
    log = structlog.get_logger("seed_admin")
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        log.error("bootstrap_env_missing")
        return 1
    email = settings.bootstrap_admin_email.strip().lower()
    password = settings.bootstrap_admin_password.get_secret_value()

    engine = make_engine()
    sm = make_sessionmaker(engine)
    try:
        async with sm() as session:
            repo = UserRepository(session)
            existing = await repo.get_by_email(email)
            if existing is None:
                await repo.create(email=email, password_hash=hash_password(password), role="admin")
                await session.commit()
                log.info("created_admin", email=email)
            elif existing.role != "admin":
                existing.role = "admin"
                await session.commit()
                log.info("promoted_to_admin", email=email)
            else:
                log.info("already_admin", email=email)
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    configure_logging(get_settings().log_level)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 10.2: Update Makefile**

Open `Makefile`. Replace the `seed:` recipe with:

```make
seed: seed-admin seed-templates

seed-admin:
	set -a && . ./.env && set +a && cd apps/api && uv run python -m gamehost_api.scripts.seed_admin

seed-templates:
	set -a && . ./.env && set +a && cd apps/api && uv run python -m gamehost_api.scripts.seed_templates
```

Also append `seed-admin seed-templates` to the `.PHONY` line.

- [ ] **Step 10.3: Failing tests**

Create `apps/api/tests/test_seed_admin.py`:

```python
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamehost_api.core.config import get_settings
from gamehost_api.repositories.users import UserRepository
from tests.factories import make_user

API_DIR = Path(__file__).resolve().parents[1]


def _run_seed_admin(extra_env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("COV_")}
    env.pop("COVERAGE_PROCESS_START", None)
    env.pop("COVERAGE_FILE", None)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "gamehost_api.scripts.seed_admin"],
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


async def test_seed_admin_creates_when_missing(session: AsyncSession) -> None:
    rc = _run_seed_admin(
        {"BOOTSTRAP_ADMIN_EMAIL": "boot@x.test", "BOOTSTRAP_ADMIN_PASSWORD": "supersecret123"}
    )
    assert rc.returncode == 0, rc.stderr.decode()
    fetched = await UserRepository(session).get_by_email("boot@x.test")
    assert fetched is not None
    assert fetched.role == "admin"


async def test_seed_admin_promotes_existing_user(session: AsyncSession) -> None:
    await make_user(session, email="prom@x.test", role="user")
    rc = _run_seed_admin(
        {"BOOTSTRAP_ADMIN_EMAIL": "prom@x.test", "BOOTSTRAP_ADMIN_PASSWORD": "supersecret123"}
    )
    assert rc.returncode == 0, rc.stderr.decode()
    await session.commit()
    fetched = await UserRepository(session).get_by_email("prom@x.test")
    assert fetched is not None
    assert fetched.role == "admin"


async def test_seed_admin_idempotent_on_existing_admin(session: AsyncSession) -> None:
    await make_user(session, email="ad@x.test", role="admin")
    rc = _run_seed_admin(
        {"BOOTSTRAP_ADMIN_EMAIL": "ad@x.test", "BOOTSTRAP_ADMIN_PASSWORD": "supersecret123"}
    )
    assert rc.returncode == 0, rc.stderr.decode()


async def test_seed_admin_exits_when_env_missing() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("COV_")}
    env.pop("BOOTSTRAP_ADMIN_EMAIL", None)
    env.pop("BOOTSTRAP_ADMIN_PASSWORD", None)
    env.pop("COVERAGE_PROCESS_START", None)
    env.pop("COVERAGE_FILE", None)
    rc = subprocess.run(
        [sys.executable, "-m", "gamehost_api.scripts.seed_admin"],
        cwd=str(API_DIR),
        env=env,
        capture_output=True,
    )
    assert rc.returncode == 1
```

- [ ] **Step 10.4: Run tests**

Run: `uv run pytest apps/api/tests/test_seed_admin.py -v`
Expected: 4 passed.

- [ ] **Step 10.5: Commit**

```bash
git add apps/api/src/gamehost_api/scripts/seed_admin.py Makefile apps/api/tests/test_seed_admin.py
git commit -m "feat(api): seed_admin script + make seed-admin"
```

---

## Task 11: `seed_templates` script

**Files:**
- Create: `apps/api/src/gamehost_api/scripts/seed_templates.py`
- Create: `apps/api/tests/test_seed_templates.py`

- [ ] **Step 11.1: Implement seeder**

Create `apps/api/src/gamehost_api/scripts/seed_templates.py`:

```python
import asyncio
import sys
import uuid

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from gamehost_api.core.config import get_settings
from gamehost_api.core.logging import configure_logging
from gamehost_api.db.models import GameTemplate
from gamehost_api.db.session import make_engine, make_sessionmaker

_TEMPLATES = [
    {
        "slug": "minecraft-vanilla",
        "display_name": "Minecraft (Vanilla)",
        "docker_image": "itzg/minecraft-server:latest",
        "default_env": {},
        "default_ports": [{"container": 25565, "protocol": "tcp"}],
        "default_volumes": [],
        "min_resources": {"cpu": 1.0, "memMb": 2048},
    },
    {
        "slug": "valheim",
        "display_name": "Valheim",
        "docker_image": "lloesche/valheim-server:latest",
        "default_env": {},
        "default_ports": [
            {"container": 2456, "protocol": "udp"},
            {"container": 2457, "protocol": "udp"},
            {"container": 2458, "protocol": "udp"},
        ],
        "default_volumes": [],
        "min_resources": {"cpu": 2.0, "memMb": 4096},
    },
    {
        "slug": "terraria",
        "display_name": "Terraria",
        "docker_image": "ryshe/terraria:latest",
        "default_env": {},
        "default_ports": [{"container": 7777, "protocol": "tcp"}],
        "default_volumes": [],
        "min_resources": {"cpu": 1.0, "memMb": 1024},
    },
    {
        "slug": "cs2",
        "display_name": "Counter-Strike 2",
        "docker_image": "joedwards32/cs2:latest",
        "default_env": {},
        "default_ports": [
            {"container": 27015, "protocol": "tcp"},
            {"container": 27015, "protocol": "udp"},
        ],
        "default_volumes": [],
        "min_resources": {"cpu": 2.0, "memMb": 2048},
    },
    {
        "slug": "rust",
        "display_name": "Rust",
        "docker_image": "didstopia/rust-server:latest",
        "default_env": {},
        "default_ports": [
            {"container": 28015, "protocol": "udp"},
            {"container": 28016, "protocol": "tcp"},
        ],
        "default_volumes": [],
        "min_resources": {"cpu": 2.0, "memMb": 4096},
    },
]


async def _run() -> int:
    log = structlog.get_logger("seed_templates")
    engine = make_engine()
    sm = make_sessionmaker(engine)
    try:
        async with sm() as session:
            for tpl in _TEMPLATES:
                stmt = pg_insert(GameTemplate).values(id=uuid.uuid4(), is_public=True, **tpl)
                stmt = stmt.on_conflict_do_nothing(index_elements=["slug"])
                await session.execute(stmt)
            await session.commit()
        log.info("seeded_templates", count=len(_TEMPLATES))
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    configure_logging(get_settings().log_level)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 11.2: Failing tests**

Create `apps/api/tests/test_seed_templates.py`:

```python
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
```

- [ ] **Step 11.3: Run tests**

Run: `uv run pytest apps/api/tests/test_seed_templates.py -v`
Expected: 3 passed.

- [ ] **Step 11.4: Commit**

```bash
git add apps/api/src/gamehost_api/scripts/seed_templates.py apps/api/tests/test_seed_templates.py
git commit -m "feat(api): seed_templates script (5 canonical templates)"
```

---

## Task 12: README + final verification + push

**Files:**
- Modify: `README.md`

- [ ] **Step 12.1: README section**

Open `README.md`. Find the line `## Auth (Stage 1)` and insert this new section **immediately before** it (or right after, your call — preserve consistent ordering):

````markdown
## Stage 2: templates & nodes (admin)

```bash
# 1. bootstrap admin (reads BOOTSTRAP_ADMIN_* from .env)
make seed   # also seeds 5 canonical game templates

# 2. login as admin
ACCESS=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"admin@gh.local","password":"change-me-in-prod"}' | jq -r .accessToken)

# 3. list templates (admin sees all, user sees only is_public=true)
curl localhost:8000/api/v1/templates -H "authorization: Bearer $ACCESS"

# 4. create node (apiKey is returned exactly once)
curl -X POST localhost:8000/api/v1/nodes \
  -H "authorization: Bearer $ACCESS" -H 'content-type: application/json' \
  -d '{"name":"node-1","endpointUrl":"http://node-1:8080","capacityCpu":"8.00","capacityMemMb":16384}'
```

`make seed` is idempotent: existing admins stay admins, existing templates are not overwritten by the seeder. Templates can be edited via `PATCH /api/v1/templates/{id}`; to retire one, set `isPublic=false`.
````

- [ ] **Step 12.2: Full pipeline**

Run:

```bash
rm -f .coverage*
make lint
make typecheck
make test
make openapi
```

All four must pass. `make test` should report ≥70% global coverage. Check domain coverage:

```bash
rm -f .coverage*
uv run pytest --cov=apps/api/src/gamehost_api/domain --cov-fail-under=85 --no-cov-on-fail apps/api/tests
```

Expected: passes.

- [ ] **Step 12.3: Commit + push**

```bash
git add README.md
git commit -m "docs: README — Stage 2 templates & nodes section"
git push -u origin stage-2-templates-nodes
```

- [ ] **Step 12.4: Open PR**

Either via `gh pr create` (if `gh` is authenticated) or by visiting the URL printed by `git push`. PR title: `Stage 2: templates + nodes + bootstrap admin`. Body: link to spec.

---

## Self-Review

**1. Spec coverage:**
- `game_templates` ORM + migration → Task 1 ✓
- `nodes` ORM + migration → Task 1 ✓
- `bootstrap_admin_*` settings → Task 2 ✓
- `Forbidden` + `require_admin` → Task 3 ✓
- Domain errors `Template/Node*` → Task 4 ✓
- DTOs (TemplateCreate/Patch/Out, Node*In/Out/CreateOut) → Task 4 ✓
- Repositories → Task 5 ✓
- TemplateService (list filter / create / update / not-found / dup-slug) → Task 6 ✓
- NodeService + verify_api_key → Task 7 ✓
- Templates router (GET/POST/PATCH; admin gating; user filtering) → Task 8 ✓
- Nodes router (GET/POST/PATCH/DELETE; admin gating; one-time apiKey) → Task 9 ✓
- `PATCH /nodes` rejects `status="offline"` (422) → Task 9 ✓
- `seed_admin` (create / promote / idempotent / exit-on-missing-env) → Task 10 ✓
- `seed_templates` (5 canonical, idempotent, no overwrite) → Task 11 ✓
- Makefile `seed`, `seed-admin`, `seed-templates` → Task 10 ✓
- README Stage 2 section → Task 12 ✓
- Coverage gates remain ≥70% global / ≥85% domain → Task 12 verification ✓
- `_clean_db` truncate covers new tables → Task 1.5 ✓

**2. Placeholders:** none.

**3. Type/method consistency:**
- `TemplateRepository.list_(public_only=)` consistent across Tasks 5, 6.
- `TemplateService.list_(actor=)` / `create(payload)` / `update(id, payload)` consistent across Tasks 6, 8.
- `NodeService.create() -> tuple[Node, str]` consistent across Tasks 7, 9.
- `verify_api_key(plain, node)` consistent across Tasks 7 and (future) Stage 3 callers.
- DTO field names (`displayName`, `dockerImage`, `endpointUrl`, `capacityCpu`, `capacityMemMb`, `apiKey`, `isPublic`, `lastSeenAt`) match test expectations everywhere.
- Domain error codes (`forbidden`, `template_not_found`, `slug_taken`, `node_not_found`, `node_name_taken`) match what tests assert against `r.json()["code"]`.
