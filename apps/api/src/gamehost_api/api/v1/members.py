import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_current_user, get_session, require_server_role
from gamehost_api.core.config import get_settings
from gamehost_api.db.models import Server, User
from gamehost_api.domain.exceptions import ServerNotFound
from gamehost_api.domain.members import MemberService
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.schemas.members import (
    InviteOut,
    MemberInviteIn,
    MemberInviteOut,
    MemberOut,
)

router = APIRouter()


def _service(session: AsyncSession) -> MemberService:
    return MemberService(session)


async def _get_server(session: AsyncSession, server_id: uuid.UUID) -> Server:
    srv = await ServersRepository(session).get(server_id)
    if srv is None:
        raise ServerNotFound(str(server_id))
    return srv


@router.get(
    "/servers/{server_id}/members",
    response_model=list[MemberOut],
    dependencies=[Depends(require_server_role("viewer"))],
)
async def list_members(
    server_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> list[MemberOut]:
    srv = await _get_server(session, server_id)
    return await _service(session).list_members(srv)


@router.post(
    "/servers/{server_id}/members/invite",
    response_model=MemberInviteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_server_role("owner"))],
)
async def create_invite(
    server_id: uuid.UUID,
    payload: MemberInviteIn,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MemberInviteOut:
    srv = await _get_server(session, server_id)
    invite, token = await _service(session).invite(srv, payload.email, payload.role, user)
    settings = get_settings()
    invite_url = f"{settings.api_public_url.rstrip('/')}/api/v1/invites/{token}"
    return MemberInviteOut(
        invite_id=invite.id, token=token, expires_at=invite.expires_at, invite_url=invite_url
    )


@router.delete(
    "/servers/{server_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    server_id: uuid.UUID,
    user_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Owner can remove anyone (except themselves); members can self-leave."""
    srv = await _get_server(session, server_id)
    is_self_leave = user_id == user.id
    if not is_self_leave and srv.owner_id != user.id and user.role != "admin":
        raise ServerNotFound(str(server_id))
    await _service(session).remove_member(srv, user_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/servers/{server_id}/invites",
    response_model=list[InviteOut],
    dependencies=[Depends(require_server_role("owner"))],
)
async def list_invites(
    server_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[InviteOut]:
    rows = await _service(session).list_open_invites(server_id)
    return [InviteOut.model_validate(r) for r in rows]


@router.delete(
    "/servers/{server_id}/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_server_role("owner"))],
)
async def revoke_invite(
    server_id: uuid.UUID,
    invite_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    srv = await _get_server(session, server_id)
    await _service(session).revoke_invite(srv, invite_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
