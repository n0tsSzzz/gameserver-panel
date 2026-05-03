import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.core.security import hash_password
from gamehost_api.db.models import GameTemplate, Node, User
from gamehost_api.repositories.nodes import NodeRepository
from gamehost_api.repositories.templates import TemplateRepository

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


async def make_admin(session: AsyncSession, *, email: str = "admin@x.test") -> User:
    return await make_user(session, email=email, role="admin")


async def make_template(
    session: AsyncSession,
    *,
    slug: str,
    display_name: str = "X",
    docker_image: str = "img",
    is_public: bool = True,
) -> GameTemplate:
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
    session: AsyncSession,
    *,
    name: str,
    endpoint_url: str = "http://node:8080",
    api_key: str = "x",
    capacity_cpu: float = 4.0,
    capacity_mem_mb: int = 8192,
) -> Node:
    repo = NodeRepository(session)
    n = await repo.create(
        name=name,
        endpoint_url=endpoint_url,
        api_key=api_key,
        capacity_cpu=Decimal(str(capacity_cpu)),
        capacity_mem_mb=capacity_mem_mb,
    )
    await session.commit()
    return n
