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
