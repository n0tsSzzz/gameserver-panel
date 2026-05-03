import uuid
from typing import Any

from sqlalchemy import func, select
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
        t.updated_at = func.now()
        await self._s.flush()
        await self._s.refresh(t, ["updated_at"])
        return t
