from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_current_user, get_session
from gamehost_api.db.models import User
from gamehost_api.domain.members import MemberService
from gamehost_api.schemas.members import InviteAcceptOut, InvitePreviewOut

router = APIRouter()


@router.get("/{token}", response_model=InvitePreviewOut)
async def preview_invite(
    token: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> InvitePreviewOut:
    return await MemberService(session).preview_invite(token)


@router.post("/{token}/accept", response_model=InviteAcceptOut)
async def accept_invite(
    token: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> InviteAcceptOut:
    return await MemberService(session).accept_invite(token, user)
