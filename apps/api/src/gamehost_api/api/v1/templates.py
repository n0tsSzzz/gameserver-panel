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
