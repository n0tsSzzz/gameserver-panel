import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import generate_refresh_token, hash_refresh_token
from gamehost_api.db.models import Server, ServerInvite, User
from gamehost_api.domain.exceptions import (
    InviteAlreadyExists,
    InviteEmailMismatch,
    InviteNotFound,
    MemberAlreadyExists,
    NotServerMember,
    ServerNotFound,
)
from gamehost_api.repositories.audit_log import AuditLogRepository
from gamehost_api.repositories.server_invites import ServerInvitesRepository
from gamehost_api.repositories.server_members import ServerMembersRepository
from gamehost_api.repositories.servers import ServersRepository
from gamehost_api.repositories.users import UserRepository
from gamehost_api.schemas.members import (
    InviteAcceptOut,
    InvitePreviewOut,
    MemberOut,
)


class MemberService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._members = ServerMembersRepository(session)
        self._invites = ServerInvitesRepository(session)
        self._servers = ServersRepository(session)
        self._users = UserRepository(session)
        self._audit = AuditLogRepository(session)

    async def list_members(self, server: Server) -> list[MemberOut]:
        owner = await self._users.get(server.owner_id)
        out: list[MemberOut] = []
        if owner is not None:
            out.append(
                MemberOut(
                    user_id=owner.id,
                    email=owner.email,
                    role="owner",
                    invited_by=None,
                    created_at=server.created_at,
                )
            )
        for m in await self._members.list_for_server(server.id):
            user = await self._users.get(m.user_id)
            if user is None:
                continue
            out.append(
                MemberOut(
                    user_id=user.id,
                    email=user.email,
                    role="viewer" if m.role == "viewer" else "operator",
                    invited_by=m.invited_by,
                    created_at=m.created_at,
                )
            )
        return out

    async def list_open_invites(self, server_id: uuid.UUID) -> list[ServerInvite]:
        return await self._invites.list_open(server_id)

    async def invite(
        self, server: Server, email: str, role: str, by_user: User
    ) -> tuple[ServerInvite, str]:
        normalized = email.strip().lower()
        existing_user = await self._users.get_by_email(normalized)
        if existing_user is not None:
            existing_member = await self._members.get(server.id, existing_user.id)
            if existing_member is not None:
                raise MemberAlreadyExists(normalized)
            if server.owner_id == existing_user.id:
                raise MemberAlreadyExists(normalized)
        if await self._invites.find_open(server.id, normalized) is not None:
            raise InviteAlreadyExists(normalized)
        plain = generate_refresh_token()
        try:
            row = await self._invites.create(
                server_id=server.id,
                email=normalized,
                role=role,
                token_hash=hash_refresh_token(plain),
                ttl_days=get_settings().invite_ttl_days,
                created_by=by_user.id,
            )
        except IntegrityError as exc:
            raise InviteAlreadyExists(normalized) from exc
        await self._audit.record(
            actor_id=by_user.id,
            action="member.invited",
            target_type="server",
            target_id=str(server.id),
            meta={"email": normalized, "role": role, "invite_id": str(row.id)},
        )
        return row, plain

    async def revoke_invite(self, server: Server, invite_id: uuid.UUID, by_user: User) -> None:
        row = await self._invites.get(invite_id)
        if row is None or row.server_id != server.id:
            raise InviteNotFound(str(invite_id))
        if row.revoked_at is not None or row.accepted_at is not None:
            raise InviteNotFound(str(invite_id))
        await self._invites.revoke(row)
        await self._audit.record(
            actor_id=by_user.id,
            action="invite.revoked",
            target_type="server",
            target_id=str(server.id),
            meta={"invite_id": str(invite_id)},
        )

    async def remove_member(self, server: Server, user_id: uuid.UUID, actor: User) -> None:
        if server.owner_id == user_id:
            raise NotServerMember(str(user_id))
        member = await self._members.get(server.id, user_id)
        if member is None:
            raise NotServerMember(str(user_id))
        await self._members.delete(member)
        await self._audit.record(
            actor_id=actor.id,
            action="member.removed",
            target_type="server",
            target_id=str(server.id),
            meta={"user_id": str(user_id)},
        )

    async def preview_invite(self, plain_token: str) -> InvitePreviewOut:
        row = await self._lookup_open_invite(plain_token)
        srv = await self._servers.get(row.server_id)
        if srv is None:
            raise ServerNotFound(str(row.server_id))
        return InvitePreviewOut(
            server_id=srv.id,
            server_name=srv.name,
            role=row.role,  # type: ignore[arg-type]
            email=row.email,
            expires_at=row.expires_at,
        )

    async def accept_invite(self, plain_token: str, actor: User) -> InviteAcceptOut:
        row = await self._lookup_open_invite(plain_token)
        if actor.email.strip().lower() != row.email:
            raise InviteEmailMismatch(actor.email)
        srv = await self._servers.get(row.server_id)
        if srv is None:
            raise ServerNotFound(str(row.server_id))
        if srv.owner_id == actor.id or await self._members.get(srv.id, actor.id) is not None:
            raise MemberAlreadyExists(actor.email)
        await self._members.create(
            server_id=srv.id,
            user_id=actor.id,
            role=row.role,
            invited_by=row.created_by,
        )
        await self._invites.accept(row, actor.id)
        await self._audit.record(
            actor_id=actor.id,
            action="member.accepted",
            target_type="server",
            target_id=str(srv.id),
            meta={"invite_id": str(row.id), "role": row.role},
        )
        return InviteAcceptOut(server_id=srv.id, role=row.role)  # type: ignore[arg-type]

    async def _lookup_open_invite(self, plain_token: str) -> ServerInvite:
        row = await self._invites.get_by_token_hash(hash_refresh_token(plain_token))
        if row is None:
            raise InviteNotFound("token")
        now = datetime.now(UTC)
        if row.revoked_at is not None or row.accepted_at is not None or row.expires_at <= now:
            raise InviteNotFound("token")
        return row
